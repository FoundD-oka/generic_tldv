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

from .media_types import is_lane_media_type
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

# Issue #25 — per-participant lane STT (cost caps from the approved plan).
LANE_STT_CONCURRENCY = max(1, int(os.getenv("LANE_STT_CONCURRENCY", "2")))
MAX_LANE_TOTAL_DURATION_SECONDS = float(
    os.getenv("MAX_LANE_TOTAL_DURATION_SECONDS", str(4 * 3600))
)

# Issue #26 — false-split guard (AC3/AC4): a lane cluster only counts toward
# K_stable once its TOTAL duration/tokens across the lane clear these bars,
# so one stray token or a half-second interjection cannot flip a solo lane
# into a false "shared mic" split. See _stable_lane_clusters.
LANE_SHARED_MIC_MIN_CLUSTER_DURATION_S = float(
    os.getenv("LANE_SHARED_MIC_MIN_CLUSTER_DURATION_S", "2.0")
)
# BUG-004 — int(float(...)) so a decimal-looking value (e.g. "5.0", a
# plausible typo by analogy with the float-typed sibling above) is accepted
# instead of crashing the whole process at import time with ValueError.
LANE_SHARED_MIC_MIN_CLUSTER_TOKENS = int(float(
    os.getenv("LANE_SHARED_MIC_MIN_CLUSTER_TOKENS", "5")
))


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


@dataclass(frozen=True)
class LaneTranscriptionSource:
    """A finalized per-participant lane master (issue #25)."""
    storage_path: str
    media_format: str
    session_uid: Optional[str]
    storage_backend: Optional[str]
    lane_key: str
    lane_label: Optional[str]
    lane_id_source: Optional[str]
    # BUG-002 — ms between the mixed recording's start and this lane's own
    # recorder start (0.0 for lanes present from t=0). A late joiner's lane
    # otherwise has its own t=0 mid-meeting on the merged timeline.
    start_offset_seconds: float = 0.0


class LaneTranscriptionFallback(Exception):
    """Lane STT cannot be used for this meeting — fall back to the mixed
    master (all-or-nothing policy from the approved plan: one failed lane
    means the whole lane path is abandoned, so lane and mixed transcripts
    never mix)."""


def _lane_start_offset_seconds(lane: Dict[str, Any]) -> float:
    offset_ms = lane.get("lane_start_offset_ms")
    if offset_ms is None:
        return 0.0
    try:
        return float(offset_ms) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def _lane_master_sources(
    meeting: Meeting,
    *,
    recording_session_uid: Optional[str] = None,
) -> List[LaneTranscriptionSource]:
    """Collect finalized per-participant lane masters (issue #25).

    All-or-nothing at the lane-set level (BUG-012): if ANY lane-* media_files
    entry (in the matched recording) is not yet finalized, the lane path as a
    whole is unavailable — silently transcribing a subset would drop that
    participant's speech from the transcript with no error. When
    `recording_session_uid` is given, only that recording's lanes are
    considered (BUG-012/BUG-023 — lanes must ride the same recording/session
    as the chosen mixed master; lanes from a different bot session must never
    merge onto one timeline).
    """
    data = _meeting_data(meeting)
    recordings = data.get("recordings") or []
    if isinstance(recordings, dict):
        recordings = [recordings]
    sources: List[LaneTranscriptionSource] = []
    for rec in recordings:
        if not isinstance(rec, dict) or rec.get("status") == "failed":
            continue
        if recording_session_uid is not None and rec.get("session_uid") != recording_session_uid:
            continue
        for mf in rec.get("media_files") or []:
            if not isinstance(mf, dict):
                continue
            mf_type = str(mf.get("type") or "")
            if not is_lane_media_type(mf_type):
                continue
            if mf.get("finalized_by") != "recording_finalizer.master":
                raise LaneTranscriptionFallback(
                    f"lane {mf_type} not finalized yet — lane path unavailable "
                    f"for recording {rec.get('id')!r} (all-or-nothing)"
                )
            storage_path = str(mf.get("storage_path") or "")
            if not storage_path or not _is_audio_master_path(storage_path):
                continue
            lane = mf.get("lane") or {}
            sources.append(LaneTranscriptionSource(
                storage_path=storage_path,
                media_format=str(mf.get("format") or "webm").lower(),
                session_uid=rec.get("session_uid"),
                storage_backend=mf.get("storage_backend"),
                lane_key=mf_type[len("lane-"):],
                lane_label=(str(lane.get("lane_label")).strip() or None) if lane.get("lane_label") else None,
                lane_id_source=lane.get("lane_id_source"),
                start_offset_seconds=_lane_start_offset_seconds(lane),
            ))
    return sources


def _lane_masters_available(meeting: Meeting) -> bool:
    """Best-effort presence check — used only to decide whether the
    no-speaker-events replace guard (BUG-011) is unnecessary. A lane path
    that would raise LaneTranscriptionFallback anyway is not "available"."""
    try:
        return bool(_lane_master_sources(meeting))
    except LaneTranscriptionFallback:
        return False


def _stable_lane_clusters(
    segments: List[Dict[str, Any]],
    min_duration_s: float,
    min_tokens: int,
) -> set:
    """Return the set of `speaker_cluster` ids whose TOTAL duration (and,
    when available, TOTAL token count) across the lane clear the false-split
    guard thresholds (issue #26 AC3/AC4, B-2).

    `token_count` is an optional additive stt.v1 field the Soniox adapter
    folds per segment; when NOT A SINGLE segment in the lane carries it, the
    backend simply doesn't emit it and the token check is skipped entirely
    — duration alone decides stability. This must NOT be confused with a
    cluster whose segments carry `token_count` but sum to 0; that cluster is
    correctly judged unstable.

    Never raises: any bad/missing field degrades toward "0" (i.e. toward
    "unstable"), never toward an exception — the caller relies on this so a
    malformed segment can never escape into LaneTranscriptionFallback
    (ARC-6: the filter must not turn a good lane into a mixed-master
    fallback).
    """
    has_token_counts = any(seg.get("token_count") is not None for seg in segments)
    duration_by_cluster: Dict[str, float] = {}
    tokens_by_cluster: Dict[str, float] = {}
    for seg in segments:
        cluster = seg.get("speaker_cluster")
        if not cluster:
            continue
        try:
            duration = max(0.0, float(seg.get("end", 0)) - float(seg.get("start", 0)))
        except (TypeError, ValueError):
            duration = 0.0
        duration_by_cluster[cluster] = duration_by_cluster.get(cluster, 0.0) + duration
        if has_token_counts:
            try:
                tokens = float(seg.get("token_count") or 0)
            except (TypeError, ValueError):
                tokens = 0.0
            tokens_by_cluster[cluster] = tokens_by_cluster.get(cluster, 0.0) + tokens
    stable = set()
    for cluster, duration in duration_by_cluster.items():
        if duration < min_duration_s:
            continue
        if has_token_counts and tokens_by_cluster.get(cluster, 0.0) < min_tokens:
            continue
        stable.add(cluster)
    return stable


def _apply_lane_identity(
    lane: LaneTranscriptionSource,
    segments: List[Dict[str, Any]],
) -> bool:
    """Post-process one lane's parsed segments. Returns True iff this lane
    was treated as shared-mic (K_stable >= 2) — callers use this to record
    `shared_mic_lanes` in the final_transcription success state.

    Stability filter (issue #26 AC3/AC4, B-2): a cluster only counts toward
    K_stable once `_stable_lane_clusters` clears it. Unstable clusters are
    NEVER absorbed into the nearest stable cluster — there is no well-
    defined "closest" cluster to absorb into (plan B-2) — so they either
    ride along with the solo lane (K_stable <= 1) or keep their own
    needs_review sub-cluster id (K_stable >= 2). They are never silently
    merged.

    K_stable <= 1 (0 or 1, including the all-unstable edge case) → SOLO
    lane, same as Phase 2: the whole lane — unstable-cluster segments
    included — becomes one speaker ("lane:{laneKey}", named by the DOM
    lane_label when present).

    K_stable >= 2 → SHARED MIC lane (issue #26 AC1/AC5): every cluster,
    stable AND unstable, keeps its OWN namespaced id
    "lane:{laneKey}:{cluster}". `seg["speaker"]` is forced to None on
    EVERY segment of the lane so the DOM vote `_parse_segments` already ran
    (name_clusters_by_dom_vote) can never resurface as a sub-speaker's
    name — AC5 requires the room's shared-mic sub-speakers to stay
    "needs_review" rather than silently wear a DOM-guessed name. This must
    happen (and does, since it runs synchronously here) before
    run_deferred_transcription captures `speaker_auto = seg.get("speaker")`
    after `_transcribe_lanes` returns, so `speaker_auto` also ends up None,
    not the discarded DOM name.
    """
    clusters = {s.get("speaker_cluster") for s in segments if s.get("speaker_cluster")}
    shared_mic = False
    if len(clusters) > 1:
        try:
            stable = _stable_lane_clusters(
                segments,
                LANE_SHARED_MIC_MIN_CLUSTER_DURATION_S,
                LANE_SHARED_MIC_MIN_CLUSTER_TOKENS,
            )
        except Exception:
            # ARC-6 — a filter bug must degrade to the safe solo treatment,
            # never propagate into LaneTranscriptionFallback.
            logger.warning(
                "lane %s stability filter raised — treating as solo",
                lane.lane_key, exc_info=True,
            )
            stable = set()
        shared_mic = len(stable) >= 2

    if shared_mic:
        for seg in segments:
            cluster = seg.get("speaker_cluster")
            if cluster:
                seg["speaker_cluster"] = f"lane:{lane.lane_key}:{cluster}"
            else:
                # BUG-001 — a segment Soniox could not diarize (no cluster
                # tag) must not collapse into the shared meeting-wide blank
                # identity just because its lane happens to be shared-mic.
                # Namespace it too, under its own stable sub-cluster id, so
                # it keeps a distinct identity and still matches
                # _LANE_SUB_CLUSTER_RE downstream (_derive_speaker_mapping_
                # status in collector/endpoints.py), i.e. it is flagged
                # needs_review exactly like a named sub-cluster segment
                # instead of silently disappearing into "".
                seg["speaker_cluster"] = f"lane:{lane.lane_key}:unclustered"
            seg["speaker"] = None
    else:
        for seg in segments:
            seg["speaker_cluster"] = f"lane:{lane.lane_key}"
            if lane.lane_label:
                seg["speaker"] = lane.lane_label
    for seg in segments:
        seg["_lane_key"] = lane.lane_key
        # BUG-023 — each lane segment carries its OWN recording's session_uid
        # so persisted Transcription rows are stamped correctly even when
        # lanes span a multi-session (bot rejoin) meeting.
        seg["_lane_session_uid"] = lane.session_uid
    return shared_mic


def _shift_speaker_events(
    speaker_events: List[Dict[str, Any]], offset_seconds: float
) -> List[Dict[str, Any]]:
    """Return a COPY of speaker_events shifted by -offset (ms).

    BUG-002: speaker_events are timestamped relative to the mixed
    recording's start, but a lane's STT segments are relative to that
    lane's OWN recorder start. Shifting a copy of the events into the
    lane's local clock (instead of mutating the shared list) is what lets
    _parse_segments' DOM cluster-naming vote line up with lane-relative
    segment times without corrupting the events for other lanes.
    """
    if not offset_seconds:
        return speaker_events
    offset_ms = offset_seconds * 1000.0
    return [
        {**event, "relative_timestamp_ms": event.get("relative_timestamp_ms", 0) - offset_ms}
        for event in speaker_events
    ]


def _shift_segment_times(segments: List[Dict[str, Any]], offset_seconds: float) -> None:
    """Shift each segment's start/end by +offset so the merged transcript
    lands on the master (mixed-recording) timeline (BUG-002)."""
    if not offset_seconds:
        return
    for seg in segments:
        seg["start"] = float(seg.get("start", 0)) + offset_seconds
        seg["end"] = float(seg.get("end", 0)) + offset_seconds


async def _transcribe_lanes(
    lane_sources: List[LaneTranscriptionSource],
    *,
    language: str,
    speaker_events: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], str, List[str]]:
    """All-or-nothing lane STT. Returns (merged segments, detected language,
    shared-mic lane keys — issue #26: the lane_keys whose _apply_lane_identity
    call found K_stable >= 2, for the caller to record as
    `shared_mic_lanes` in the final_transcription success state).

    Raises LaneTranscriptionFallback when any lane fails or the duration
    budget is exceeded — the caller then runs the unchanged mixed-master path.

    BUG-010: lanes are downloaded/converted under a bounded semaphore and the
    running duration total is checked as each lane finishes, so a
    meeting that is already over budget aborts (and cancels any lanes not
    yet downloaded) before paying full download+ffmpeg cost for every lane.
    Decoded audio is dropped from `prepared` before transcription starts, so
    at most one lane's buffer is referenced per in-flight STT call instead of
    every lane's audio staying resident for the whole all-lanes STT gather.
    """
    semaphore = asyncio.Semaphore(LANE_STT_CONCURRENCY)

    async def _prepare(lane: LaneTranscriptionSource):
        async with semaphore:
            audio = await _download_recording_audio(FinalTranscriptionSource(
                storage_path=lane.storage_path,
                media_format=lane.media_format,
                session_uid=lane.session_uid,
                storage_backend=lane.storage_backend,
                source="lane",
            ))
            audio, fmt = await asyncio.to_thread(
                _convert_audio_to_wav, audio, lane.media_format
            )
            duration = _audio_duration_seconds(audio, fmt) or 0.0
            return lane, audio, fmt, duration

    pending = {asyncio.ensure_future(_prepare(lane)): lane for lane in lane_sources}
    prepared: List[tuple] = []
    total_duration = 0.0
    budget_reason: Optional[str] = None
    try:
        for task in asyncio.as_completed(list(pending.keys())):
            try:
                result = await task
            except Exception as exc:
                lane = pending.get(task)
                raise LaneTranscriptionFallback(
                    f"lane download/convert failed for {lane.lane_key if lane else '?'}: {exc}"
                ) from exc
            prepared.append(result)
            total_duration += result[3]
            if total_duration > MAX_LANE_TOTAL_DURATION_SECONDS:
                # Cap already breached by lanes measured so far — stop the
                # loop immediately instead of waiting for every remaining
                # lane to finish downloading; `finally` below cancels
                # whatever is still in flight so we stop paying ffmpeg cost
                # for lanes we already know we will not transcribe.
                budget_reason = (
                    f"lane duration budget exceeded: {total_duration:.0f}s > "
                    f"{MAX_LANE_TOTAL_DURATION_SECONDS:.0f}s"
                )
                break
    finally:
        for task in pending:
            if not task.done():
                task.cancel()
        # Retrieve every task's result/exception exactly once, even the ones
        # we abandoned above — otherwise asyncio logs "exception was never
        # retrieved" for a sibling lane that also failed/was cancelled.
        await asyncio.gather(*pending.keys(), return_exceptions=True)

    if budget_reason:
        raise LaneTranscriptionFallback(budget_reason)

    async def _transcribe(lane: LaneTranscriptionSource, audio: bytes, fmt: str, duration: float):
        async with semaphore:
            tx = await _call_transcription_service(audio, fmt, language=language)
            shifted_events = _shift_speaker_events(speaker_events, lane.start_offset_seconds)
            segments, detected = _parse_segments(
                tx,
                language=language,
                speaker_events=shifted_events,
                fallback_duration=duration,
            )
            shared_mic = _apply_lane_identity(lane, segments)
            _shift_segment_times(segments, lane.start_offset_seconds)
            # NOTE: don't rely on a task-object → lane dict lookup in the
            # merge loop below — asyncio.as_completed() does not guarantee
            # yielding the identical Future/Task object that was used as a
            # dict key, so the lane_key is threaded through the return
            # value itself instead.
            # BUG-006 (monitor) — this relies on Python truthiness, so an
            # empty-string lane_key would be conflated with "no shared-mic
            # lane" (None) below and in the `if shared_mic_lane_key:` check
            # further down. lane_key is always a 10-char sha1 slug generated
            # bot-side (vexa-bot browser.ts: sha1Hex(track.id).slice(0,10))
            # and is never empty through normal bot operation, so this is
            # acceptable while the internal upload endpoint is the only
            # producer of `media_type="lane-..."` — see tribunal BUG-006.
            return segments, detected, (lane.lane_key if shared_mic else None)

    transcribe_tasks = {
        asyncio.ensure_future(_transcribe(lane, audio, fmt, duration)): lane
        for lane, audio, fmt, duration in prepared
    }
    prepared.clear()  # drop the (lane, audio, ...) tuples; each task's own
    # frame is now the only thing keeping its audio buffer alive, so it is
    # released as soon as that lane's own STT call returns — not held until
    # every lane's transcription finishes.

    errors: List[BaseException] = []
    merged: List[Dict[str, Any]] = []
    shared_mic_lane_keys: List[str] = []
    # BUG-018 — duration-weighted vote across lanes instead of last-lane-wins:
    # one minority-language participant should not flip the whole meeting's
    # recorded language.
    language_durations: Dict[str, float] = {}
    for task in asyncio.as_completed(list(transcribe_tasks.keys())):
        try:
            segments, detected, shared_mic_lane_key = await task
        except Exception as exc:
            errors.append(exc)
            continue
        merged.extend(segments)
        # BUG-006 (monitor) — truthiness check paired with the one in
        # `_transcribe` above; see the comment there for the invariant this
        # relies on (lane_key is always a non-empty bot-generated slug).
        if shared_mic_lane_key:
            shared_mic_lane_keys.append(shared_mic_lane_key)
        if detected and detected != "unknown":
            lane_duration = sum(
                max(0.0, float(seg.get("end", 0)) - float(seg.get("start", 0)))
                for seg in segments
            )
            language_durations[detected] = language_durations.get(detected, 0.0) + lane_duration
    if errors:
        raise LaneTranscriptionFallback(
            f"lane STT failed for {len(errors)}/{len(lane_sources)} lanes: {errors[0]}"
        )

    detected_language = (
        max(language_durations.items(), key=lambda kv: kv[1])[0]
        if language_durations
        else (language or "unknown")
    )
    merged.sort(key=lambda s: (float(s.get("start", 0)), str(s.get("_lane_key", ""))))
    return merged, detected_language, shared_mic_lane_keys


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


async def _skip_no_speaker_events(
    meeting: Meeting,
    db: AsyncSession,
    meeting_id: int,
    *,
    triggered_by: str,
) -> DeferredTranscriptionResult:
    """Abort mode="replace" without touching existing rows.

    Shared by the pre-flight guard and the BUG-011 post-lane-fallback guard:
    both protect the same invariant (never rewrite meaningful existing
    speaker labels to "Unknown" when no speaker_events are available to
    re-derive them, and no lane master identity will stand in for them).
    """
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
        # Lane masters carry their own identity (lane_label), so the
        # no-speaker-events protection is unnecessary when lanes exist AND
        # are actually usable (BUG-011/BUG-012: an unfinalized lane makes
        # the lane path unavailable too — see _lane_masters_available).
        and not _lane_masters_available(meeting)
        and await _has_meaningful_existing_speakers(db, meeting_id)
    ):
        return await _skip_no_speaker_events(meeting, db, meeting_id, triggered_by=triggered_by)

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

    lane_used = False
    lane_fallback_reason: Optional[str] = None
    shared_mic_lane_keys: List[str] = []
    try:
        meeting_data = _meeting_data(meeting)
        speaker_events = meeting_data.get("speaker_events", [])
        if not isinstance(speaker_events, list):
            speaker_events = []

        segments: List[Dict[str, Any]] = []
        detected_language = resolved_language or "unknown"

        lane_sources: List[LaneTranscriptionSource] = []
        try:
            # BUG-012/BUG-023 — restrict lanes to the SAME recording/session
            # as the chosen mixed source; lanes from a different bot session
            # (rejoin) must never merge onto this transcript's timeline.
            lane_sources = _lane_master_sources(meeting, recording_session_uid=source.session_uid)
        except LaneTranscriptionFallback as exc:
            lane_fallback_reason = str(exc)

        # Issue #25 — lane-first: when finalized per-participant lane masters
        # exist, transcribe each lane (solo lane ⇒ auto-named). All-or-nothing:
        # any lane failure abandons the lane path entirely and the unchanged
        # mixed-master path below runs instead — lane and mixed segments are
        # never mixed in one transcript.
        if lane_sources:
            try:
                segments, detected_language, shared_mic_lane_keys = await _transcribe_lanes(
                    lane_sources,
                    language=resolved_language,
                    speaker_events=speaker_events,
                )
                lane_used = True
                logger.info(
                    "Deferred transcription used %d lane master(s) for meeting %s",
                    len(lane_sources), meeting_id,
                )
            except LaneTranscriptionFallback as exc:
                lane_fallback_reason = str(exc)
                logger.warning(
                    "Lane transcription unavailable for meeting %s — falling back "
                    "to mixed master: %s",
                    meeting_id, exc,
                )

        if not lane_used:
            if (
                # BUG-011 — the lane path was not used (whether because it
                # failed, because no lane masters exist at all, or because
                # the only finalized lane masters belong to a DIFFERENT
                # session than the chosen mixed source — see F1/Fable
                # consultation), so the same protection the pre-flight guard
                # applies must still apply here: without it, the mixed path
                # below would delete good existing speaker labels and
                # rewrite them mostly as "Unknown" (no speaker_events).
                # This must NOT require lane_fallback_reason to be set —
                # session-scoped lane lookup can leave lane_sources empty
                # (and lane_fallback_reason None) even though lanes "exist"
                # somewhere in the meeting, which is exactly the bypass the
                # pre-flight guard's meeting-wide _lane_masters_available
                # check cannot catch.
                mode == "replace"
                and not force
                and not speaker_events
                and await _has_meaningful_existing_speakers(db, meeting_id)
            ):
                return await _skip_no_speaker_events(meeting, db, meeting_id, triggered_by=triggered_by)

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
    # Issue #26 AC5 — for shared-mic lane segments, _apply_lane_identity
    # already forced seg["speaker"] to None (discarding the DOM vote) before
    # _transcribe_lanes returned, so speaker_auto below captures None, not
    # the discarded DOM name. A saved rename keyed "lane:{laneKey}:{cluster}"
    # still applies here exactly like any other cluster key — the sub-
    # cluster's own id is untouched by the None overwrite, only `speaker`
    # was cleared, so a prior human rename for that sub-cluster still wins.
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
        # Lane segments carry the laneKey in segment_id so re-runs stay
        # deterministic even if two lanes emit identical (idx, start) pairs.
        lane_key = seg.get("_lane_key")
        segment_id = (
            f"deferred:{meeting_id}:lane-{lane_key}:{idx}:{start:.3f}"
            if lane_key else
            f"deferred:{meeting_id}:{idx}:{start:.3f}"
        )
        db.add(Transcription(
            meeting_id=meeting_id,
            start_time=start,
            end_time=end,
            text=text,
            speaker=seg.get("speaker"),
            speaker_cluster=seg.get("speaker_cluster"),
            speaker_auto=seg.get("speaker_auto"),
            language=detected_language,
            # BUG-023 — a lane segment is stamped with its OWN recording's
            # session_uid, not the mixed source's; they can differ for a
            # multi-session (bot rejoin) meeting.
            session_uid=seg.get("_lane_session_uid") or source.session_uid,
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
        "source": "deferred_lane_masters" if lane_used else "deferred_recording_master",
        "lane_count": len(lane_sources) if lane_used else 0,
        "lane_keys": [lane.lane_key for lane in lane_sources] if lane_used else [],
        # BUG-023 — lane master storage paths, so an operator auditing "which
        # audio produced this transcript" isn't pointed at the (unused, in
        # the lane_used branch) mixed master path alone.
        "source_lane_paths": [lane.storage_path for lane in lane_sources] if lane_used else [],
        # Issue #26 — lane_keys whose K_stable >= 2 (shared mic detected):
        # these lanes' sub-cluster segments are speaker=None / needs_review
        # until a human names them via the rename correction API.
        "shared_mic_lanes": shared_mic_lane_keys if lane_used else [],
        "lane_fallback_reason": lane_fallback_reason,
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

    # Issue #27 Phase 4 — voiceprint matching follow-up. Runs AFTER the
    # success commit + finalized-notification above, in its OWN commit, so a
    # slow (up to VOICEPRINT_MATCH_TOTAL_BUDGET_S) or failing matching pass can never
    # affect this function's success/failure result or delay the
    # transcript.finalized notification (plan §6, Codex critique FC-4/5/20).
    # run_voiceprint_matching_followup already catches everything internally
    # (never raises); this try/except is belt-and-suspenders so a bug in
    # that isolation can never propagate into "final transcription failed".
    try:
        from .voiceprint_matching import run_voiceprint_matching_followup

        await run_voiceprint_matching_followup(
            meeting, db,
            segments=segments,
            mixed_source=source,
            lane_sources=lane_sources,
            mode=mode,
        )
    except Exception:
        logger.exception(
            "voiceprint matching followup crashed for meeting %s — "
            "transcript success is unaffected",
            meeting_id,
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
