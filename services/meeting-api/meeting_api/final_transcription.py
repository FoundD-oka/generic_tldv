"""Post-meeting final transcript generation.

This module owns the issue #2 flow: once a recording master is finalized,
generate a deferred transcript from that master and replace the realtime rows
only after the new transcript has succeeded.
"""
from __future__ import annotations

import asyncio
import io
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

logger = logging.getLogger("meeting_api.final_transcription")

FinalTranscriptionMode = Literal["reject_if_exists", "replace"]

FINAL_TRANSCRIPTION_STATUSES = {"queued", "running", "succeeded", "failed", "skipped"}
FINAL_TRANSCRIPTION_MAX_ATTEMPTS = int(os.getenv("FINAL_TRANSCRIPTION_MAX_ATTEMPTS", "24"))
FINAL_TRANSCRIPTION_SWEEP_LIMIT = int(os.getenv("FINAL_TRANSCRIPTION_SWEEP_LIMIT", "10"))


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


def map_speakers_to_segments(
    speaker_events: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Map speaker names to transcription segments using speaking ranges."""
    ranges = []
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


async def _call_transcription_service(
    audio_data: bytes,
    media_format: str,
    *,
    language: Optional[str],
) -> Dict[str, Any]:
    tx_url = os.environ.get("TRANSCRIPTION_SERVICE_URL", "")
    tx_token = os.environ.get("TRANSCRIPTION_SERVICE_TOKEN", "")
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
    if speaker_events:
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


def _is_retryable_http_error(exc: HTTPException) -> bool:
    return exc.status_code in {500, 502, 503, 504}


async def run_deferred_transcription(
    meeting_id: int,
    db: AsyncSession,
    *,
    mode: FinalTranscriptionMode = "reject_if_exists",
    language: Optional[str] = None,
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
        tx_result = await _call_transcription_service(
            audio_data,
            media_format,
            language=language,
        )
        meeting_data = _meeting_data(meeting)
        speaker_events = meeting_data.get("speaker_events", [])
        if not isinstance(speaker_events, list):
            speaker_events = []
        segments, detected_language = _parse_segments(
            tx_result,
            language=language,
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
    await db.commit()

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
