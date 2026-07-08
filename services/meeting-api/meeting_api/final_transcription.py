"""Post-meeting final transcript generation.

This module owns the issue #2 flow: once a recording master is finalized,
generate a deferred transcript from that master and replace the realtime rows
only after the new transcript has succeeded.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import attributes

from .models import MediaFile, Meeting, Recording, Transcription
from .schemas import MeetingStatus
from .storage import create_storage_client
from .drive_export import queue_drive_export_if_needed

logger = logging.getLogger("meeting_api.final_transcription")

FinalTranscriptionMode = Literal["reject_if_exists", "replace"]

FINAL_TRANSCRIPTION_STATUSES = {"queued", "running", "succeeded", "failed", "skipped", "skipped_no_speaker_events"}
FINAL_TRANSCRIPTION_MAX_ATTEMPTS = int(os.getenv("FINAL_TRANSCRIPTION_MAX_ATTEMPTS", "24"))
FINAL_TRANSCRIPTION_SWEEP_LIMIT = int(os.getenv("FINAL_TRANSCRIPTION_SWEEP_LIMIT", "10"))
FINAL_TRANSCRIPTION_DEFAULT_LANGUAGE = os.getenv("FINAL_TRANSCRIPTION_DEFAULT_LANGUAGE", "ja")


@dataclass(frozen=True)
class FinalTranscriptionSource:
    storage_path: str
    media_format: str
    session_uid: Optional[str] = None
    storage_backend: Optional[str] = None
    source: str = "meeting.data"


@dataclass(frozen=True)
class DeferredTranscriptionResult:
    meeting_id: int
    segment_count: int
    speakers: List[str]
    source_recording_path: str
    replaced_realtime_count: int


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def _meeting_data(meeting: Meeting) -> Dict[str, Any]:
    return dict(meeting.data or {}) if isinstance(meeting.data, dict) else {}


def _set_final_transcription_state(meeting: Meeting, **updates: Any) -> Dict[str, Any]:
    data = _meeting_data(meeting)
    current = dict(data.get("final_transcription") or {})
    current.update(updates)
    status = current.get("status")
    if status and status not in FINAL_TRANSCRIPTION_STATUSES:
        raise ValueError(f"Invalid final_transcription status: {status!r}")
    data["final_transcription"] = current
    if status:
        data["final_transcription_status"] = status
    meeting.data = data
    attributes.flag_modified(meeting, "data")
    return current


def queue_final_transcription(meeting: Meeting, *, triggered_by: str = "post_meeting") -> bool:
    """Queue a final transcript job in meeting.data.

    Returns True when the state changed. The caller owns commit.
    """
    data = _meeting_data(meeting)
    if data.get("transcribe_enabled") is False:
        _set_final_transcription_state(
            meeting,
            status="skipped",
            skipped_at=_utcnow_iso(),
            skipped_reason="transcription_disabled",
            triggered_by=triggered_by,
        )
        return True
    if data.get("recording_enabled") is False:
        _set_final_transcription_state(
            meeting,
            status="skipped",
            skipped_at=_utcnow_iso(),
            skipped_reason="recording_disabled",
            triggered_by=triggered_by,
        )
        return True

    current = dict(data.get("final_transcription") or {})
    if current.get("status") in {"queued", "running", "succeeded"}:
        return False
    if current.get("status") == "failed" and not current.get("retryable"):
        return False

    now = _utcnow_iso()
    _set_final_transcription_state(
        meeting,
        status="queued",
        queued_at=current.get("queued_at") or now,
        updated_at=now,
        attempts=current.get("attempts") or 0,
        last_error=None,
        retryable=True,
        triggered_by=triggered_by,
    )
    return True


def _is_master_audio_media_file(mf: Dict[str, Any], *, require_finalizer_mark: bool = True) -> bool:
    if not isinstance(mf, dict):
        return False
    if mf.get("type") != "audio":
        return False
    if require_finalizer_mark and mf.get("finalized_by") != "recording_finalizer.master":
        return False
    storage_path = str(mf.get("storage_path") or "")
    if not storage_path:
        return False
    return _is_audio_master_path(storage_path)


def _is_audio_master_path(storage_path: str) -> bool:
    return storage_path.endswith("/master.webm") or storage_path.endswith("/master.wav")


def _source_from_meeting_data(meeting: Meeting) -> Optional[FinalTranscriptionSource]:
    data = _meeting_data(meeting)
    recordings = data.get("recordings") or []
    if isinstance(recordings, dict):
        recordings = [recordings]
    for rec in recordings:
        if not isinstance(rec, dict):
            continue
        if rec.get("status") == "failed":
            continue
        for mf in rec.get("media_files") or []:
            if _is_master_audio_media_file(mf, require_finalizer_mark=True):
                return FinalTranscriptionSource(
                    storage_path=str(mf["storage_path"]),
                    media_format=str(mf.get("format") or "webm").lower(),
                    session_uid=rec.get("session_uid"),
                    storage_backend=mf.get("storage_backend"),
                    source="meeting.data",
                )
    return None


async def find_final_transcription_source(
    meeting: Meeting,
    db: AsyncSession,
) -> Optional[FinalTranscriptionSource]:
    """Find a finalized audio master for final transcription.

    JSONB meeting.data is canonical for recording finalizer state. The table
    fallback is intentionally stricter about `master.*` paths so chunks cannot
    be accidentally used as final transcript input.
    """
    source = _source_from_meeting_data(meeting)
    if source is not None:
        return source

    recordings = (await db.execute(
        select(Recording).where(
            Recording.meeting_id == meeting.id,
            Recording.status == "completed",
        )
    )).scalars().all()

    for recording in recordings:
        media_files = (await db.execute(
            select(MediaFile).where(
                MediaFile.recording_id == recording.id,
                MediaFile.type == "audio",
            )
        )).scalars().all()
        for mf in media_files:
            storage_path = str(getattr(mf, "storage_path", "") or "")
            if not _is_audio_master_path(storage_path):
                continue
            return FinalTranscriptionSource(
                storage_path=storage_path,
                media_format=str(getattr(mf, "format", None) or "webm").lower(),
                session_uid=getattr(recording, "session_uid", None),
                storage_backend=getattr(mf, "storage_backend", None),
                source="media_files",
            )
    return None


def _speaking_ranges(speaker_events: List[Dict[str, Any]]) -> List[tuple]:
    """Build (name, start_sec, end_sec) speaking ranges from DOM speaker events."""
    ranges: List[tuple] = []
    active: Dict[str, float] = {}
    for event in sorted(speaker_events, key=lambda e: e.get("relative_timestamp_ms", 0)):
        name = event.get("participant_name", "Unknown")
        ts_sec = event.get("relative_timestamp_ms", 0) / 1000.0
        event_type = event.get("event_type", "")
        if event_type in ("SPEAKER_START", "speaking_start"):
            active[name] = ts_sec
        elif event_type in ("SPEAKER_END", "speaking_stop") and name in active:
            ranges.append((name, active.pop(name), ts_sec))
    for name, start in active.items():
        ranges.append((name, start, float("inf")))
    return ranges


def map_speakers_to_segments(
    speaker_events: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Map speaker names to transcription segments using speaking ranges."""
    ranges = _speaking_ranges(speaker_events)

    for seg in segments:
        best_speaker = "Unknown"
        best_overlap = 0.0
        for speaker, range_start, range_end in ranges:
            overlap = max(
                0.0,
                min(float(seg["end"]), range_end) - max(float(seg["start"]), range_start),
            )
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = speaker
        seg["speaker"] = best_speaker
    return segments


# Minimum share of a cluster's speech time the winning DOM name must cover;
# below this the cluster stays "Unknown" instead of guessing.
SPEAKER_CLUSTER_MIN_OVERLAP_RATIO = float(
    os.getenv("SPEAKER_CLUSTER_MIN_OVERLAP_RATIO", "0.2")
)


def name_clusters_by_dom_vote(
    segments: List[Dict[str, Any]],
    speaker_events: List[Dict[str, Any]],
    *,
    min_overlap_ratio: Optional[float] = None,
) -> Dict[str, str]:
    """Name acoustic clusters by an overlap-seconds-weighted DOM vote.

    Unlike per-segment mapping (jittery on boundaries), the vote sums DOM
    overlap seconds across ALL segments of a cluster and names the whole
    cluster at once. A cluster falls back to "Unknown" when the best name
    covers less than min_overlap_ratio of the cluster's speech time. Ties
    resolve deterministically by name ascending.
    """
    ratio = SPEAKER_CLUSTER_MIN_OVERLAP_RATIO if min_overlap_ratio is None else min_overlap_ratio
    ranges = _speaking_ranges(speaker_events or [])

    totals: Dict[str, float] = {}
    votes: Dict[str, Dict[str, float]] = {}
    for seg in segments:
        cluster = seg.get("speaker_cluster")
        if not cluster:
            continue
        try:
            start = float(seg["start"])
            end = float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        totals[cluster] = totals.get(cluster, 0.0) + max(0.0, end - start)
        for name, range_start, range_end in ranges:
            overlap = max(0.0, min(end, range_end) - max(start, range_start))
            if overlap > 0:
                cluster_votes = votes.setdefault(cluster, {})
                cluster_votes[name] = cluster_votes.get(name, 0.0) + overlap

    names: Dict[str, str] = {}
    for cluster, total in totals.items():
        best_name = None
        best_overlap = 0.0
        for name in sorted((votes.get(cluster) or {}).keys()):
            overlap = votes[cluster][name]
            if overlap > best_overlap:
                best_name = name
                best_overlap = overlap
        if (
            best_name is None
            or best_name == "Unknown"
            or total <= 0.0
            or (best_overlap / total) < ratio
        ):
            names[cluster] = "Unknown"
        else:
            names[cluster] = best_name
    return names


async def _download_recording_audio(source: FinalTranscriptionSource) -> bytes:
    storage = create_storage_client(source.storage_backend)
    return await asyncio.to_thread(storage.download_file, source.storage_path)


def _convert_audio_to_wav(audio_data: bytes, media_format: str) -> tuple[bytes, str]:
    media_format = (media_format or "webm").lower()
    if media_format not in ("webm", "opus", "ogg", "mp4", "m4a"):
        return audio_data, media_format

    src_path = None
    dst_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=f".{media_format}", delete=False) as src:
            src.write(audio_data)
            src_path = src.name
        dst_path = src_path.rsplit(".", 1)[0] + ".wav"
        result = subprocess.run(
            ["ffmpeg", "-i", src_path, "-ar", "16000", "-ac", "1", "-f", "wav", dst_path, "-y"],
            capture_output=True,
            timeout=float(os.getenv("DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS", "120")),
        )
        if result.returncode != 0:
            logger.error("ffmpeg conversion failed: %s", result.stderr.decode(errors="ignore")[:500])
            raise HTTPException(status_code=500, detail="Audio conversion failed")
        with open(dst_path, "rb") as converted:
            return converted.read(), "wav"
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=500, detail="Audio conversion timed out") from exc
    finally:
        for path in (src_path, dst_path):
            if path:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass


def _deferred_transcription_endpoint() -> tuple[str, str]:
    """Resolve the STT endpoint for the deferred (post-meeting) path.

    TRANSCRIPTION_SERVICE_URL is shared with the realtime bot pipeline, so a
    deferred-only override lets the post-meeting path use a different backend
    (e.g. the Soniox-capable transcription-service) without touching realtime.
    """
    url = (
        os.environ.get("DEFERRED_TRANSCRIPTION_SERVICE_URL", "").strip()
        or os.environ.get("TRANSCRIPTION_SERVICE_URL", "")
    )
    token = (
        os.environ.get("DEFERRED_TRANSCRIPTION_SERVICE_TOKEN", "").strip()
        or os.environ.get("TRANSCRIPTION_SERVICE_TOKEN", "")
    )
    return url, token


async def _call_transcription_service(
    audio_data: bytes,
    media_format: str,
    *,
    language: Optional[str],
) -> Dict[str, Any]:
    tx_url, tx_token = _deferred_transcription_endpoint()
    if not tx_url:
        raise HTTPException(status_code=503, detail="TRANSCRIPTION_SERVICE_URL not configured")

    timeout = float(os.getenv("DEFERRED_TRANSCRIPTION_TIMEOUT_SECONDS", "120"))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            files = {"file": (f"recording.{media_format}", audio_data, f"audio/{media_format}")}
            form_data = {
                "model": os.getenv("DEFERRED_TRANSCRIPTION_MODEL", "large-v3-turbo"),
                "transcription_tier": "deferred",
            }
            if language:
                form_data["language"] = language
            headers = {"X-Transcription-Tier": "deferred"}
            if tx_token:
                headers["Authorization"] = f"Bearer {tx_token}"

            response = await client.post(
                tx_url,
                files=files,
                data=form_data,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        logger.error("Transcription service error: %s %s", status_code, exc.response.text)
        if status_code == 503 or 500 <= status_code < 600:
            raise HTTPException(status_code=502, detail=f"Transcription service error: {status_code}") from exc
        raise HTTPException(status_code=400, detail=f"Transcription service rejected request: {status_code}") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Transcription service request failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Transcription service unavailable: {exc}") from exc


def _parse_segments(
    tx_result: Dict[str, Any],
    *,
    language: Optional[str],
    speaker_events: List[Dict[str, Any]],
    fallback_duration: Optional[float] = None,
) -> tuple[List[Dict[str, Any]], str]:
    segments = tx_result.get("segments", [])
    segments = [
        dict(segment)
        for segment in segments
        if "start" in segment and "end" in segment and str(segment.get("text", "")).strip()
    ]
    if not segments:
        text = str(tx_result.get("text") or "").strip()
        if text:
            end = tx_result.get("duration") or fallback_duration or 0.0
            try:
                end = float(end)
            except (TypeError, ValueError):
                end = 0.0
            segments = [{"start": 0.0, "end": max(end, 0.001), "text": text}]
    detected_language = tx_result.get("language", language or "unknown")

    # stt.v1 diarization extension: when the STT already labeled segments with
    # acoustic cluster ids (`speaker`), those clusters are the source of truth.
    # DOM speaker_events then only NAME clusters (whole-cluster weighted vote)
    # and never overwrite per-segment attribution. Backends without
    # diarization fall back to the legacy per-segment DOM overlap mapping.
    has_clusters = any(seg.get("speaker") not in (None, "") for seg in segments)
    if has_clusters:
        for seg in segments:
            cluster = seg.get("speaker")
            seg["speaker_cluster"] = str(cluster) if cluster not in (None, "") else None
        cluster_names = name_clusters_by_dom_vote(segments, speaker_events or [])
        for seg in segments:
            cluster = seg.get("speaker_cluster")
            seg["speaker"] = cluster_names.get(cluster, "Unknown") if cluster else "Unknown"
    elif speaker_events:
        segments = map_speakers_to_segments(speaker_events, segments)
    return segments, detected_language


def _audio_duration_seconds(audio_data: bytes, media_format: str) -> Optional[float]:
    if (media_format or "").lower() != "wav":
        return None
    try:
        with wave.open(io.BytesIO(audio_data), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            if frame_rate <= 0:
                return None
            return wav_file.getnframes() / float(frame_rate)
    except Exception:
        return None


async def _clear_live_transcript_cache(meeting_id: int) -> bool:
    try:
        from .meetings import get_redis

        redis_client = get_redis()
        if not redis_client:
            return False
        await redis_client.delete(f"meeting:{meeting_id}:segments")
        return True
    except Exception as exc:
        logger.warning(
            "Failed to clear live transcript cache for meeting %s: %s",
            meeting_id,
            str(exc)[:200],
        )
        return False


def _resolve_final_transcription_language(meeting: Meeting, explicit_language: Optional[str]) -> str:
    if explicit_language:
        return explicit_language

    data = _meeting_data(meeting)
    language = data.get("language") or data.get("transcription_language")
    if isinstance(language, str) and language.strip():
        return language.strip()
    return FINAL_TRANSCRIPTION_DEFAULT_LANGUAGE or "ja"


def _saved_cluster_corrections(meeting: Meeting) -> Dict[str, str]:
    """Manual cluster→name corrections persisted by the speaker-update API.

    Stored in meeting.data["speaker_corrections"]["clusters"] so they survive
    a mode="replace" re-transcription (rows are deleted and rebuilt, but the
    cluster ids from diarization are re-derived and the names re-applied).
    """
    data = _meeting_data(meeting)
    saved = data.get("speaker_corrections")
    if not isinstance(saved, dict):
        return {}
    clusters = saved.get("clusters")
    if not isinstance(clusters, dict):
        return {}
    return {
        str(cluster): str(name).strip()
        for cluster, name in clusters.items()
        if isinstance(name, str) and name.strip()
    }


async def _has_meaningful_existing_speakers(db: AsyncSession, meeting_id: int) -> bool:
    count = (await db.execute(
        select(func.count(Transcription.id)).where(
            Transcription.meeting_id == meeting_id,
            Transcription.speaker.isnot(None),
            Transcription.speaker != "",
            func.lower(Transcription.speaker) != "unknown",
        )
    )).scalar() or 0
    return count > 0


async def _publish_transcript_finalized(
    meeting_id: int,
    *,
    segment_count: int,
    triggered_by: str,
) -> bool:
    try:
        from .meetings import get_redis

        redis_client = get_redis()
        if not redis_client:
            return False
        payload = {
            "type": "transcript.finalized",
            "meeting": {"id": meeting_id},
            "payload": {
                "segment_count": segment_count,
                "triggered_by": triggered_by,
            },
            "ts": _utcnow_iso(),
        }
        await redis_client.publish(
            f"tc:meeting:{meeting_id}:mutable",
            json.dumps(payload, ensure_ascii=False),
        )
        return True
    except Exception as exc:
        logger.warning(
            "Failed to publish transcript.finalized for meeting %s: %s",
            meeting_id,
            str(exc)[:200],
        )
        return False


def _is_retryable_http_error(exc: HTTPException) -> bool:
    return exc.status_code in {500, 502, 503, 504}


async def run_deferred_transcription(
    meeting_id: int,
    db: AsyncSession,
    *,
    mode: FinalTranscriptionMode = "reject_if_exists",
    language: Optional[str] = None,
    force: bool = False,
    triggered_by: str = "manual_api",
) -> DeferredTranscriptionResult:
    """Generate a deferred transcript and optionally replace existing rows."""
    if mode not in ("reject_if_exists", "replace"):
        raise HTTPException(status_code=422, detail="mode must be 'reject_if_exists' or 'replace'")

    meeting = (await db.execute(
        select(Meeting)
        .where(Meeting.id == meeting_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalars().first()
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if meeting.status not in (MeetingStatus.COMPLETED.value, MeetingStatus.FAILED.value):
        raise HTTPException(
            status_code=400,
            detail=f"Meeting status is '{meeting.status}', expected 'completed' or 'failed'",
        )

    existing_count = (await db.execute(
        select(func.count(Transcription.id)).where(Transcription.meeting_id == meeting_id)
    )).scalar() or 0
    if existing_count > 0 and mode == "reject_if_exists":
        raise HTTPException(
            status_code=409,
            detail=f"This meeting is already transcribed ({existing_count} segments). Use mode='replace' to regenerate the final transcript.",
        )

    data = _meeting_data(meeting)
    speaker_events = data.get("speaker_events", [])
    if not isinstance(speaker_events, list):
        speaker_events = []
    if (
        mode == "replace"
        and not force
        and existing_count > 0
        and not speaker_events
        and await _has_meaningful_existing_speakers(db, meeting_id)
    ):
        _set_final_transcription_state(
            meeting,
            status="skipped_no_speaker_events",
            skipped_at=_utcnow_iso(),
            updated_at=_utcnow_iso(),
            skipped_reason="no_speaker_events",
            segment_count=0,
            replaced_realtime_count=0,
            retryable=False,
            triggered_by=triggered_by,
        )
        queue_drive_export_if_needed(meeting, triggered_by=triggered_by)
        await db.commit()
        logger.warning(
            "Deferred final transcription skipped for meeting %s: no speaker_events; existing speaker labels preserved",
            meeting_id,
        )
        return DeferredTranscriptionResult(
            meeting_id=meeting_id,
            segment_count=0,
            speakers=[],
            source_recording_path="",
            replaced_realtime_count=0,
        )

    source = await find_final_transcription_source(meeting, db)
    if source is None:
        _set_final_transcription_state(
            meeting,
            status="queued",
            updated_at=_utcnow_iso(),
            last_error="recording_master_not_ready",
            retryable=True,
            triggered_by=triggered_by,
        )
        await db.commit()
        raise HTTPException(status_code=404, detail="No finalized audio master found for this meeting")

    resolved_language = _resolve_final_transcription_language(meeting, language)
    data = _meeting_data(meeting)
    current_state = dict(data.get("final_transcription") or {})
    attempts = int(current_state.get("attempts") or 0) + 1
    _set_final_transcription_state(
        meeting,
        status="running",
        started_at=_utcnow_iso(),
        updated_at=_utcnow_iso(),
        attempts=attempts,
        last_error=None,
        retryable=True,
        source_recording_path=source.storage_path,
        source_recording_backend=source.storage_backend,
        language=resolved_language,
        triggered_by=triggered_by,
    )
    await db.commit()

    try:
        audio_data = await _download_recording_audio(source)
        audio_data, media_format = await asyncio.to_thread(
            _convert_audio_to_wav,
            audio_data,
            source.media_format,
        )
        fallback_duration = _audio_duration_seconds(audio_data, media_format)
        logger.info(
            "Calling deferred transcription service for meeting %s with language=%s",
            meeting_id,
            resolved_language,
        )
        tx_result = await _call_transcription_service(
            audio_data,
            media_format,
            language=resolved_language,
        )
        meeting_data = _meeting_data(meeting)
        speaker_events = meeting_data.get("speaker_events", [])
        if not isinstance(speaker_events, list):
            speaker_events = []
        segments, detected_language = _parse_segments(
            tx_result,
            language=resolved_language,
            speaker_events=speaker_events,
            fallback_duration=fallback_duration,
        )
    except HTTPException as exc:
        _set_final_transcription_state(
            meeting,
            status="failed",
            failed_at=_utcnow_iso(),
            updated_at=_utcnow_iso(),
            last_error=str(exc.detail),
            retryable=_is_retryable_http_error(exc),
            source_recording_path=source.storage_path,
            source_recording_backend=source.storage_backend,
            language=resolved_language,
            triggered_by=triggered_by,
        )
        await db.commit()
        raise
    except Exception as exc:
        _set_final_transcription_state(
            meeting,
            status="failed",
            failed_at=_utcnow_iso(),
            updated_at=_utcnow_iso(),
            last_error=str(exc),
            retryable=True,
            source_recording_path=source.storage_path,
            source_recording_backend=source.storage_backend,
            language=resolved_language,
            triggered_by=triggered_by,
        )
        await db.commit()
        raise HTTPException(status_code=502, detail=f"Deferred transcription failed: {exc}") from exc

    replaced_count = 0
    if mode == "replace":
        replaced_count = (await db.execute(
            select(func.count(Transcription.id)).where(Transcription.meeting_id == meeting_id)
        )).scalar() or 0
        await db.execute(delete(Transcription).where(Transcription.meeting_id == meeting_id))

    # Re-apply saved manual cluster corrections so replace never silently
    # discards human edits; the auto label stays in speaker_auto for undo.
    corrections = _saved_cluster_corrections(meeting)
    for seg in segments:
        seg["speaker_auto"] = seg.get("speaker")
        cluster = seg.get("speaker_cluster")
        if cluster and cluster in corrections:
            seg["speaker"] = corrections[cluster]

    stored = 0
    for idx, seg in enumerate(segments):
        start = float(seg.get("start", 0))
        end = float(seg.get("end", 0))
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        segment_id = f"deferred:{meeting_id}:{idx}:{start:.3f}"
        db.add(Transcription(
            meeting_id=meeting_id,
            start_time=start,
            end_time=end,
            text=text,
            speaker=seg.get("speaker"),
            speaker_cluster=seg.get("speaker_cluster"),
            speaker_auto=seg.get("speaker_auto"),
            language=detected_language,
            session_uid=source.session_uid,
            segment_id=segment_id,
            created_at=datetime.utcnow(),
        ))
        stored += 1

    speakers = sorted({
        str(seg.get("speaker") or "Unknown")
        for seg in segments
        if str(seg.get("text", "")).strip()
    })
    redis_cache_cleared = False
    if mode == "replace":
        redis_cache_cleared = await _clear_live_transcript_cache(meeting_id)

    meeting_data = _meeting_data(meeting)
    meeting_data["transcribed_at"] = _utcnow_iso()
    current_state = dict(meeting_data.get("final_transcription") or {})
    current_state.update({
        "status": "succeeded",
        "completed_at": _utcnow_iso(),
        "updated_at": _utcnow_iso(),
        "source": "deferred_recording_master",
        "source_recording_path": source.storage_path,
        "source_recording_backend": source.storage_backend,
        "segment_count": stored,
        "replaced_realtime_count": replaced_count,
        "detected_language": detected_language,
        "language": resolved_language,
        "speakers": speakers,
        "redis_cache_cleared": redis_cache_cleared,
        "last_error": None,
        "retryable": False,
        "triggered_by": triggered_by,
    })
    meeting_data["final_transcription"] = current_state
    meeting_data["final_transcription_status"] = "succeeded"
    meeting.data = meeting_data
    attributes.flag_modified(meeting, "data")
    queue_drive_export_if_needed(meeting, triggered_by=triggered_by)
    await db.commit()
    if mode == "replace":
        await _publish_transcript_finalized(
            meeting_id,
            segment_count=stored,
            triggered_by=triggered_by,
        )

    logger.info(
        "Deferred final transcription succeeded for meeting %s: stored=%s replaced=%s speakers=%s",
        meeting_id,
        stored,
        replaced_count,
        speakers,
    )
    return DeferredTranscriptionResult(
        meeting_id=meeting_id,
        segment_count=stored,
        speakers=speakers,
        source_recording_path=source.storage_path,
        replaced_realtime_count=replaced_count,
    )


def final_transcription_retry_eligible(job: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    attempts = int(job.get("attempts") or 0)
    if attempts <= 0:
        return True
    last_at = job.get("updated_at") or job.get("failed_at") or job.get("started_at")
    if not last_at:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
        if last_dt.tzinfo is not None:
            last_dt = last_dt.replace(tzinfo=None)
    except (TypeError, ValueError):
        return True
    now = now or datetime.utcnow()
    backoff_seconds = min(60 * (2 ** min(attempts - 1, 10)), 86400)
    return now - last_dt >= timedelta(seconds=backoff_seconds)
