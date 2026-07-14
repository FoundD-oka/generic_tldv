import logging
import os
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Dict, Tuple

from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func, distinct, text
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from ..database import get_db, async_session_local
from ..models import Meeting, Transcription, MeetingSession, Recording
from ..storage import create_storage_client
from ..schemas import (
    MeetingResponse,
    MeetingListResponse,
    TranscriptionResponse,
    Platform,
    TranscriptionSegment,
    MeetingUpdate,
    MeetingCreate,
    MeetingStatus,
)
from ..auth import UserProxy
from ..redaction import redact_secrets
from ..schemas import redact_meeting_data

from .config import IMMUTABILITY_THRESHOLD
from .filters import TranscriptionFilter
from .auth import get_current_user, require_internal_secret

logger = logging.getLogger(__name__)
router = APIRouter()
_URL_RE = re.compile(r"https?://[^\s<>()\"']+")


def _extract_storage_targets_from_meeting_data(data: Optional[Dict]) -> List[Tuple[str, str]]:
    """Collect (storage_backend, storage_path) from meeting.data['recordings'] payload."""
    targets: List[Tuple[str, str]] = []
    if not isinstance(data, dict):
        return targets

    for rec in (data.get("recordings") or []):
        if not isinstance(rec, dict):
            continue
        for mf in (rec.get("media_files") or []):
            if not isinstance(mf, dict):
                continue
            path = mf.get("storage_path")
            if not isinstance(path, str) or not path:
                continue
            backend = mf.get("storage_backend")
            if not isinstance(backend, str) or not backend:
                backend = os.getenv("STORAGE_BACKEND", "minio")
            targets.append((str(backend).strip().lower(), path))

    return targets


def _mp3_sidecar_path(storage_path: str) -> Optional[str]:
    base, _ = os.path.splitext(storage_path)
    if not base:
        return None
    sidecar = f"{base}.mp3"
    return None if sidecar == storage_path else sidecar


def _add_storage_delete_targets(
    targets_by_backend: Dict[str, set[str]],
    backend: Optional[str],
    path: str,
) -> None:
    backend_name = backend if isinstance(backend, str) and backend else os.getenv("STORAGE_BACKEND", "minio")
    backend_name = str(backend_name).strip().lower()
    targets_by_backend.setdefault(backend_name, set()).add(path)
    mp3_sidecar = _mp3_sidecar_path(path)
    if mp3_sidecar:
        targets_by_backend[backend_name].add(mp3_sidecar)


async def _purge_recordings_for_meeting(
    db: AsyncSession,
    meeting: Meeting,
    user_id: int,
) -> Dict[str, int]:
    """
    Delete recording DB rows and storage objects for a meeting.
    Handles both meeting.data metadata mode and normalized Recording model mode.
    """
    # backend -> set(paths)
    targets_by_backend: Dict[str, set[str]] = {}
    for backend, path in _extract_storage_targets_from_meeting_data(meeting.data):
        _add_storage_delete_targets(targets_by_backend, backend, path)

    # Collect normalized recording rows/media paths and mark rows for deletion.
    table_exists_result = await db.execute(text("SELECT to_regclass('public.recordings') IS NOT NULL"))
    recordings_table_exists = bool(table_exists_result.scalar())
    if recordings_table_exists:
        stmt_recordings = select(Recording).where(
            Recording.meeting_id == meeting.id,
            Recording.user_id == user_id,
        )
        result_recordings = await db.execute(stmt_recordings)
        recordings = result_recordings.scalars().all()
    else:
        logger.info("[API] recordings table unavailable in this environment; skipping model recording cleanup")
        recordings = []
    model_recordings_deleted = 0

    for recording in recordings:
        await db.refresh(recording, ["media_files"])
        for media_file in (recording.media_files or []):
            if media_file.storage_path:
                backend = (media_file.storage_backend or os.getenv("STORAGE_BACKEND", "minio")).strip().lower()
                _add_storage_delete_targets(targets_by_backend, backend, media_file.storage_path)
        await db.delete(recording)
        model_recordings_deleted += 1

    storage_files_deleted = 0
    storage_files_targeted = sum(len(v) for v in targets_by_backend.values())
    if storage_files_targeted:
        clients: Dict[str, object] = {}

        for backend in list(targets_by_backend.keys()):
            if backend not in ("minio", "s3", "gcs", "local"):
                logger.warning(f"[API] Unknown storage backend '{backend}', defaulting to 'minio'")
                targets_by_backend.setdefault("minio", set()).update(targets_by_backend.pop(backend))

        for backend in targets_by_backend.keys():
            try:
                clients[backend] = create_storage_client(backend)
            except Exception as e:
                logger.warning(f"[API] Failed to initialize storage client for backend '{backend}': {e}")

        for backend, paths in targets_by_backend.items():
            client = clients.get(backend)
            if client is None:
                continue
            for path in paths:
                try:
                    client.delete_file(path)
                    storage_files_deleted += 1
                except Exception as e:
                    logger.warning(f"[API] Failed deleting recording media from storage ({backend}:{path}): {e}")

    return {
        "model_recordings_deleted": model_recordings_deleted,
        "storage_files_deleted": storage_files_deleted,
        "storage_files_targeted": storage_files_targeted,
    }


class WsMeetingRef(BaseModel):
    """Schema for WS subscription meeting reference — only platform + native_meeting_id needed."""
    platform: str
    native_meeting_id: str

class WsAuthorizeSubscribeRequest(BaseModel):
    meetings: List[WsMeetingRef]

class WsAuthorizeSubscribeResponse(BaseModel):
    authorized: List[Dict[str, str]]
    errors: List[str] = []
    user_id: Optional[int] = None  # Include user_id for channel isolation


def _extract_urls_from_texts(texts: List[str]) -> List[str]:
    urls: list[str] = []
    for text in texts:
        for url in _URL_RE.findall(text or ""):
            cleaned = url.rstrip(".,、。)")
            if cleaned not in urls:
                urls.append(cleaned)
    return urls


def _segment_to_assistant_context(segment: TranscriptionSegment) -> Dict[str, Any]:
    return {
        "speaker": redact_secrets(str(segment.speaker or "Unknown")),
        "text": redact_secrets(str(segment.text or "")),
        "start_time": segment.start_time,
        "end_time": segment.end_time,
        "absolute_start_time": segment.absolute_start_time.isoformat()
        if segment.absolute_start_time
        else None,
        "language": segment.language or "ja",
        "completed": segment.completed,
        "segment_id": segment.segment_id,
    }


async def _get_assistant_chat_messages(
    redis_c: aioredis.Redis | None,
    meeting: Meeting,
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if redis_c:
        try:
            raw_messages = await redis_c.lrange(f"meeting:{meeting.id}:chat_messages", -limit, -1)
            for raw in raw_messages:
                try:
                    decoded = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    value = json.loads(decoded)
                except Exception:
                    continue
                if isinstance(value, dict):
                    messages.append(value)
        except Exception as exc:
            logger.warning("[AssistantContext] Redis chat fetch failed for meeting %s: %s", meeting.id, exc)

    if not messages and isinstance(meeting.data, dict):
        fallback = meeting.data.get("chat_messages")
        if isinstance(fallback, list):
            messages = [message for message in fallback[-limit:] if isinstance(message, dict)]

    return [
        {
            "sender": redact_secrets(str(message.get("sender") or "")),
            "text": redact_secrets(str(message.get("text") or message.get("message") or "")),
            "timestamp": message.get("timestamp") or message.get("ts"),
            "is_from_bot": bool(message.get("is_from_bot") or message.get("isFromBot")),
        }
        for message in messages[-limit:]
    ]


# Issue #26 — lane SUB-cluster shape: "lane:{laneKey}:{cluster}". A solo
# lane's own cluster id ("lane:{laneKey}", no second colon) is NOT a
# sub-cluster and must not be flagged.
_LANE_SUB_CLUSTER_RE = re.compile(r"^lane:[^:]+:.+$")
# Geminiの会議ごと匿名話者ID。`speaker_mapping_status`自体はlane共有
# マイク専用の意味を維持し、声紋候補overlayだけでこの形を追加許可する。
_GEMINI_CLUSTER_RE = re.compile(r"^g:[0-9a-f]{8}:s[1-9][0-9]*$")


def _is_unconfirmed_gemini_cluster(
    speaker_cluster: Optional[str], speaker: Optional[str]
) -> bool:
    if not speaker_cluster or not _GEMINI_CLUSTER_RE.match(speaker_cluster):
        return False
    normalized = str(speaker or "").strip()
    return not normalized or normalized.casefold() == "unknown"


def _derive_speaker_mapping_status(
    speaker_cluster: Optional[str], speaker: Optional[str]
) -> Optional[str]:
    """Read-time derivation (no DB column, no migration — issue #26 ARC-3):
    a lane sub-cluster segment with no confirmed speaker name is
    "needs_review". This mirrors the K_stable>=2 shared-mic branch of
    `_apply_lane_identity` (services/meeting-api/meeting_api/
    final_transcription.py), which forces `speaker=None` on every
    shared-mic sub-cluster segment so a DOM-vote guess never survives as
    the sub-speaker's identity (AC5); once a human renames the cluster via
    the correction API, `speaker` is set and this stops flagging it.
    """
    if not speaker_cluster or not _LANE_SUB_CLUSTER_RE.match(speaker_cluster):
        return None
    if speaker and speaker.strip():
        return None
    return "needs_review"


def _overlay_speaker_suggestions(
    segments: List[TranscriptionSegment],
    suggestions: Dict[str, Any],
) -> None:
    """Overlay voiceprint auto-naming suggestions (issue #27 Phase 4) onto
    already-merged segments — called AFTER the PG/Redis merge below so
    Redis-wins semantics are preserved (Codex critique FC-8: adding this
    only to the PG branch would make a live-cache hit lose the badge).
    Mutates `segments` in place. Never touches `Transcription.speaker` or
    any persisted row — only adds the minimal read-time
    `speaker_suggestion` payload, and never includes `profile_id` (plan §6
    露出制御 — profile_id must never reach a transcript response)."""
    if not suggestions:
        return
    for seg in segments:
        if (
            seg.speaker_mapping_status != "needs_review"
            and not _is_unconfirmed_gemini_cluster(seg.speaker_cluster, seg.speaker)
        ):
            continue
        cluster = seg.speaker_cluster
        entry = suggestions.get(cluster) if cluster else None
        if not isinstance(entry, dict) or entry.get("status") != "suggested":
            continue
        seg.speaker_suggestion = {
            "candidate_display_name": entry.get("candidate_display_name"),
            "similarity": entry.get("similarity"),
            "status": entry.get("status"),
        }


async def _get_full_transcript_segments(
    internal_meeting_id: int,
    db: AsyncSession,
    redis_c: aioredis.Redis,
    meeting: Optional[Meeting] = None,
) -> List[TranscriptionSegment]:
    """
    Fetch and merge transcript segments from Postgres and Redis by segment_id.
    No heuristic dedup — segment_id is the identity.
    Redis segments (live) take precedence over Postgres (persisted).
    """
    # 1. Session start times (for absolute time computation on legacy PG rows)
    stmt_sessions = select(MeetingSession).where(MeetingSession.meeting_id == internal_meeting_id)
    result_sessions = await db.execute(stmt_sessions)
    sessions = result_sessions.scalars().all()
    session_times: Dict[str, datetime] = {s.session_uid: s.session_start_time for s in sessions}

    # 2. Postgres segments (immutable, persisted)
    stmt = select(Transcription).where(Transcription.meeting_id == internal_meeting_id)
    result = await db.execute(stmt)
    db_segments = result.scalars().all()

    # 3. Redis segments (mutable, live)
    hash_key = f"meeting:{internal_meeting_id}:segments"
    redis_raw = {}
    if redis_c:
        try:
            redis_raw = await redis_c.hgetall(hash_key)
        except Exception as e:
            logger.error(f"[Segments] Redis fetch failed for {hash_key}: {e}")

    # 4. Merge by segment_id — Redis wins on conflict
    merged: Dict[str, TranscriptionSegment] = {}

    for seg in db_segments:
        key = seg.segment_id or f"pg:{seg.speaker or ''}:{seg.start_time:.3f}"
        session_start = session_times.get(seg.session_uid)
        if session_start:
            if session_start.tzinfo is None:
                session_start = session_start.replace(tzinfo=timezone.utc)
            abs_start = session_start + timedelta(seconds=seg.start_time)
            abs_end = session_start + timedelta(seconds=seg.end_time)
        else:
            abs_start = abs_end = None

        try:
            pg_speaker_cluster = getattr(seg, "speaker_cluster", None)
            merged[key] = TranscriptionSegment(
                start_time=seg.start_time, end_time=seg.end_time,
                text=seg.text, language=seg.language, speaker=seg.speaker,
                created_at=seg.created_at, completed=True,
                absolute_start_time=abs_start, absolute_end_time=abs_end,
                segment_id=seg.segment_id,
                session_uid=seg.session_uid,
                speaker_cluster=pg_speaker_cluster,
                speaker_auto=getattr(seg, "speaker_auto", None),
                speaker_mapping_status=_derive_speaker_mapping_status(
                    pg_speaker_cluster, seg.speaker
                ),
            )
        except Exception as e:
            logger.error(f"[Segments] PG segment error {key}: {e}")

    for seg_key, segment_json in redis_raw.items():
        try:
            d = json.loads(segment_json)
            if not d.get('text', '').strip():
                continue

            key = d.get('segment_id') or seg_key

            # Compute absolute times from segment data or session start
            abs_start = abs_end = None
            abs_from_data = d.get("absolute_start_time")
            if abs_from_data:
                try:
                    s = abs_from_data if not abs_from_data.endswith('Z') else abs_from_data[:-1] + '+00:00'
                    abs_start = datetime.fromisoformat(s)
                    if abs_start.tzinfo is None:
                        abs_start = abs_start.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            abs_end_data = d.get("absolute_end_time")
            if abs_end_data:
                try:
                    s = abs_end_data if not abs_end_data.endswith('Z') else abs_end_data[:-1] + '+00:00'
                    abs_end = datetime.fromisoformat(s)
                    if abs_end.tzinfo is None:
                        abs_end = abs_end.replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            # Fallback: compute from session start
            if not abs_start:
                uid = d.get("session_uid")
                if uid:
                    # Strip platform prefix if present
                    clean_uid = uid
                    for p in Platform:
                        pref = f"{p.value}_"
                        if uid.startswith(pref):
                            clean_uid = uid[len(pref):]
                            break
                    ss = session_times.get(clean_uid)
                    if ss:
                        if ss.tzinfo is None:
                            ss = ss.replace(tzinfo=timezone.utc)
                        abs_start = ss + timedelta(seconds=float(d.get("start_time", 0)))
                        abs_end = ss + timedelta(seconds=float(d.get("end_time", 0)))

            # BUG-005 — derive speaker_mapping_status the same way the PG
            # branch above does, instead of only trusting whatever happens
            # to be in the Redis JSON blob. Redis segments take precedence
            # over Postgres on key collision (see docstring above), so if a
            # future change ever writes a lane sub-cluster id into the live
            # Redis hash, it must still be flagged needs_review here rather
            # than silently bypassing derivation. An explicitly-set wire
            # value is kept when derivation itself yields nothing (e.g. a
            # non-lane status a future producer might set directly).
            redis_speaker_cluster = d.get("speaker_cluster")
            derived_status = _derive_speaker_mapping_status(
                redis_speaker_cluster, d.get("speaker")
            )
            merged[key] = TranscriptionSegment(
                start_time=float(d.get("start_time", 0)),
                end_time=float(d.get("end_time", 0)),
                text=d['text'], language=d.get('language'),
                speaker=d.get('speaker'),
                completed=bool(d.get("completed", False)),
                absolute_start_time=abs_start, absolute_end_time=abs_end,
                segment_id=d.get('segment_id'),
                session_uid=d.get("session_uid"),
                speaker_mapping_status=derived_status if derived_status is not None else d.get("speaker_mapping_status"),
                track_id=d.get("track_id") or d.get("speaker_track_id"),
                speaker_cluster=redis_speaker_cluster,
                speaker_auto=d.get("speaker_auto"),
            )
        except Exception as e:
            logger.error(f"[Segments] Redis segment error {seg_key}: {e}")

    # 5. Overlay voiceprint suggestions (issue #27 Phase 4) — after the
    # Redis-wins merge above, so a live cache hit still carries the badge.
    suggestions: Dict[str, Any] = {}
    meeting_data = getattr(meeting, "data", None)
    if isinstance(meeting_data, dict):
        raw_suggestions = meeting_data.get("speaker_suggestions")
        if isinstance(raw_suggestions, dict):
            suggestions = raw_suggestions
    segment_list = list(merged.values())
    _overlay_speaker_suggestions(segment_list, suggestions)

    # 6. Sort by absolute_start_time (or start_time as fallback)
    def sort_key(seg: TranscriptionSegment):
        if seg.absolute_start_time:
            return seg.absolute_start_time
        return datetime.min.replace(tzinfo=timezone.utc)

    return sorted(segment_list, key=sort_key)

@router.get("/meetings",
            response_model=MeetingListResponse,
            summary="Get list of all meetings for the current user",
            dependencies=[Depends(get_current_user)])
async def get_meetings(
    current_user: UserProxy = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: Optional[int] = Query(None, ge=1, le=100, description="Max meetings to return"),
    offset: Optional[int] = Query(None, ge=0, description="Number of meetings to skip"),
    status: Optional[str] = Query(None, description="Filter by status (active, completed, failed)"),
    platform: Optional[str] = Query(None, description="Filter by platform (google_meet, teams, zoom)"),
):
    """Returns a list of meetings initiated by the authenticated user."""
    stmt = select(Meeting).where(Meeting.user_id == current_user.id)
    if status:
        stmt = stmt.where(Meeting.status == status)
    if platform:
        stmt = stmt.where(Meeting.platform == platform)
    stmt = stmt.order_by(Meeting.created_at.desc())
    if limit:
        stmt = stmt.limit(limit)
    if offset:
        stmt = stmt.offset(offset)
    result = await db.execute(stmt)
    meetings = result.scalars().all()
    return MeetingListResponse(meetings=[MeetingResponse.model_validate(m) for m in meetings])

@router.get("/transcripts/{platform}/{native_meeting_id}",
            response_model=TranscriptionResponse,
            response_model_exclude_none=False,
            summary="Get transcript for a specific meeting by platform and native ID",
            dependencies=[Depends(get_current_user)])
async def get_transcript_by_native_id(
    platform: Platform,
    native_meeting_id: str,
    request: Request,
    meeting_id: Optional[int] = Query(None, description="Optional specific database meeting ID."),
    current_user: UserProxy = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieves the meeting details and transcript segments for a meeting specified by its platform and native ID."""
    logger.debug(f"[API] User {current_user.id} requested transcript for {platform.value} / {native_meeting_id}, meeting_id={meeting_id}")
    redis_c = getattr(request.app.state, 'redis_client', None)

    if meeting_id is not None:
        stmt_meeting = select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
            Meeting.platform_specific_id == native_meeting_id
        )
    else:
        stmt_meeting = select(Meeting).where(
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
            Meeting.platform_specific_id == native_meeting_id
        ).order_by(Meeting.created_at.desc())

    result_meeting = await db.execute(stmt_meeting)
    meeting = result_meeting.scalars().first()

    if not meeting:
        if meeting_id is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting not found for platform {platform.value}, ID {native_meeting_id}, and meeting_id {meeting_id}"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Meeting not found for platform {platform.value} and ID {native_meeting_id}"
            )

    internal_meeting_id = meeting.id
    sorted_segments = await _get_full_transcript_segments(internal_meeting_id, db, redis_c, meeting=meeting)

    logger.info(f"[API Meet {internal_meeting_id}] Merged and sorted into {len(sorted_segments)} total segments.")

    meeting_details = MeetingResponse.model_validate(meeting)
    response_data = meeting_details.model_dump()
    response_data["recordings"] = (meeting.data or {}).get("recordings", []) if isinstance(meeting.data, dict) else []
    response_data["notes"] = (meeting.data or {}).get("notes") if isinstance(meeting.data, dict) else None
    # speaker_suggestions must never reach a generic API response (plan §6
    # 露出制御) — same redaction the MeetingResponse serializer applies to
    # `data`, applied here too since this endpoint builds `data` by hand
    # instead of going through that serializer.
    response_data["data"] = redact_meeting_data(dict(meeting.data)) if isinstance(meeting.data, dict) else {}
    response_data["speaker_events"] = (meeting.data or {}).get("speaker_events", []) if isinstance(meeting.data, dict) else []
    response_data["segments"] = sorted_segments
    return TranscriptionResponse(**response_data)


@router.get("/meetings/{platform}/{native_meeting_id}/assistant-context",
            summary="Get redacted assistant context for a meeting",
            dependencies=[Depends(get_current_user)])
async def get_meeting_assistant_context(
    platform: Platform,
    native_meeting_id: str,
    request: Request,
    meeting_id: Optional[int] = Query(None, description="Optional specific database meeting ID."),
    limit: int = Query(50, ge=1, le=200, description="Maximum transcript/chat items to include."),
    current_user: UserProxy = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if meeting_id is not None:
        stmt = select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
        )
    else:
        stmt = select(Meeting).where(
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
            Meeting.platform_specific_id == native_meeting_id,
        ).order_by(Meeting.created_at.desc())

    result = await db.execute(stmt)
    meetings = result.scalars().all()
    if meeting_id is None and len(meetings) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Multiple meetings match this platform/native ID. Pass meeting_id.",
                "meeting_ids": [meeting.id for meeting in meetings],
            },
        )
    meeting = meetings[0] if meetings else None
    if not meeting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    if meeting_id is not None and meeting.platform_specific_id != native_meeting_id:
        data = meeting.data if isinstance(meeting.data, dict) else {}
        if not data.get("redacted"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meeting not found")

    redis_c = getattr(request.app.state, "redis_client", None)
    segments = await _get_full_transcript_segments(meeting.id, db, redis_c, meeting=meeting)
    latest_segments = [_segment_to_assistant_context(segment) for segment in segments[-limit:]]
    chat_messages = await _get_assistant_chat_messages(redis_c, meeting, limit=limit)
    url_texts = [segment["text"] for segment in latest_segments] + [
        message["text"] for message in chat_messages
    ]

    meeting_data = meeting.data if isinstance(meeting.data, dict) else {}
    participants = meeting_data.get("participants")
    if not isinstance(participants, list):
        participants = []

    return {
        "meeting": {
            "id": meeting.id,
            "platform": meeting.platform,
            "native_meeting_id": redact_secrets(str(meeting.platform_specific_id or native_meeting_id)),
            "status": meeting.status,
            "title": redact_secrets(str(meeting_data.get("title") or meeting_data.get("name") or "")),
            "participants": [redact_secrets(str(participant)) for participant in participants],
        },
        "latest_segments": latest_segments,
        "chat_messages": chat_messages,
        "shared_urls": [redact_secrets(url) for url in _extract_urls_from_texts(url_texts)],
        "limits": {
            "transcript_segments": limit,
            "chat_messages": limit,
        },
    }


@router.post("/ws/authorize-subscribe",
            response_model=WsAuthorizeSubscribeResponse,
            summary="Authorize WS subscription for meetings",
            dependencies=[Depends(get_current_user)])
async def ws_authorize_subscribe(
    payload: WsAuthorizeSubscribeRequest,
    current_user: UserProxy = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    authorized: List[Dict[str, str]] = []
    errors: List[str] = []

    meetings = payload.meetings or []
    if not meetings:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="'meetings' must be a non-empty list")

    for idx, meeting_ref in enumerate(meetings):
        platform_value = meeting_ref.platform.value if isinstance(meeting_ref.platform, Platform) else str(meeting_ref.platform)
        native_id = meeting_ref.native_meeting_id

        try:
            constructed = Platform.construct_meeting_url(platform_value, native_id)
        except Exception:
            constructed = None
        if not constructed:
            errors.append(f"meetings[{idx}] invalid native_meeting_id for platform '{platform_value}'")
            continue

        stmt_meeting = select(Meeting).where(
            Meeting.user_id == current_user.id,
            Meeting.platform == platform_value,
            Meeting.platform_specific_id == native_id
        ).order_by(Meeting.created_at.desc()).limit(1)

        result = await db.execute(stmt_meeting)
        meeting = result.scalars().first()
        if not meeting:
            errors.append(f"meetings[{idx}] not authorized or not found for user")
            continue

        authorized.append({
            "platform": platform_value,
            "native_id": native_id,
            "user_id": str(current_user.id),
            "meeting_id": str(meeting.id)
        })

    return WsAuthorizeSubscribeResponse(authorized=authorized, errors=errors, user_id=current_user.id)


@router.get("/internal/transcripts/{meeting_id}",
            response_model=List[TranscriptionSegment],
            response_model_exclude_none=False,
            summary="[Internal] Get all transcript segments for a meeting",
            include_in_schema=False,
            dependencies=[Depends(require_internal_secret)])
async def get_transcript_internal(
    meeting_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Internal endpoint for services to fetch all transcript segments for a given meeting ID."""
    logger.debug(f"[Internal API] Transcript segments requested for meeting {meeting_id}")
    redis_c = getattr(request.app.state, 'redis_client', None)

    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting with ID {meeting_id} not found."
        )

    segments = await _get_full_transcript_segments(meeting_id, db, redis_c, meeting=meeting)
    return segments

@router.patch("/meetings/{platform}/{native_meeting_id}",
             response_model=MeetingResponse,
             summary="Update meeting data by platform and native ID",
             dependencies=[Depends(get_current_user)])
async def update_meeting_data(
    platform: Platform,
    native_meeting_id: str,
    meeting_update: MeetingUpdate,
    current_user: UserProxy = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Updates the user-editable data (name, participants, languages, notes) for the latest meeting."""

    logger.info(f"[API] User {current_user.id} updating meeting {platform.value}/{native_meeting_id}")

    stmt = select(Meeting).where(
        Meeting.user_id == current_user.id,
        Meeting.platform == platform.value,
        Meeting.platform_specific_id == native_meeting_id
    ).order_by(Meeting.created_at.desc())

    result = await db.execute(stmt)
    meetings = result.scalars().all()
    meeting = meetings[0] if meetings else None

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting not found for platform {platform.value} and ID {native_meeting_id}"
        )

    # Extract update data from the MeetingDataUpdate object
    try:
        if hasattr(meeting_update.data, 'dict'):
            update_data = meeting_update.data.model_dump(exclude_unset=True)
        else:
            update_data = meeting_update.data
    except AttributeError:
        update_data = meeting_update.data

    # Remove None values from update_data
    update_data = {k: v for k, v in update_data.items() if v is not None}

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No data provided for update."
        )

    if meeting.data is None:
        meeting.data = {}

    # Only allow updating restricted fields: name, participants, languages, notes
    allowed_fields = {'name', 'participants', 'languages', 'notes'}
    updated_fields = []

    # Create a new copy of the data dict to ensure SQLAlchemy detects the change
    new_data = dict(meeting.data) if meeting.data else {}

    for key, value in update_data.items():
        if key in allowed_fields and value is not None:
            new_data[key] = value
            updated_fields.append(f"{key}={value}")

    # Assign the new dict to ensure SQLAlchemy detects the change
    meeting.data = new_data

    # Mark the field as modified to ensure SQLAlchemy detects the change
    from sqlalchemy.orm import attributes
    attributes.flag_modified(meeting, "data")

    logger.info(f"[API] Updated fields: {', '.join(updated_fields) if updated_fields else 'none'}")

    await db.commit()
    await db.refresh(meeting)

    return MeetingResponse.model_validate(meeting)

@router.delete("/meetings/{platform}/{native_meeting_id}",
              summary="Delete meeting transcripts and anonymize meeting data",
              dependencies=[Depends(get_current_user)])
async def delete_meeting(
    platform: Platform,
    native_meeting_id: str,
    request: Request,
    meeting_id: Optional[int] = Query(None, description="Specific internal meeting ID to delete."),
    current_user: UserProxy = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Purges transcripts and anonymizes meeting data for finalized meetings.
    Only allows deletion for meetings in finalized states (completed, failed).
    """

    if meeting_id is not None:
        stmt = select(Meeting).where(
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
            Meeting.id == meeting_id,
        )
    else:
        stmt = select(Meeting).where(
            Meeting.user_id == current_user.id,
            Meeting.platform == platform.value,
            Meeting.platform_specific_id == native_meeting_id,
        ).order_by(Meeting.created_at.desc())

    result = await db.execute(stmt)
    meetings = result.scalars().all()
    if meeting_id is None and len(meetings) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "Multiple meetings match this platform/native ID. "
                    "Pass the specific meeting_id query parameter to delete one."
                ),
                "meeting_ids": [candidate.id for candidate in meetings],
            },
        )
    meeting = meetings[0] if meetings else None

    if not meeting:
        target_detail = (
            f"meeting ID {meeting_id} for platform {platform.value} and ID {native_meeting_id}"
            if meeting_id is not None
            else f"platform {platform.value} and ID {native_meeting_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting not found for {target_detail}"
        )

    internal_meeting_id = meeting.id
    original_data = dict(meeting.data or {})
    is_redacted = bool(meeting.data and meeting.data.get('redacted'))

    if meeting_id is not None and meeting.platform_specific_id != native_meeting_id and not is_redacted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting not found for meeting ID {meeting_id}, platform {platform.value}, and ID {native_meeting_id}"
        )

    # Check if already redacted (idempotency)
    if is_redacted:
        logger.info(f"[API] Meeting {internal_meeting_id} already redacted, returning success")
        return {"message": f"Meeting {platform.value}/{native_meeting_id} artifacts already deleted and data anonymized"}

    # Check if meeting is in finalized state
    finalized_states = {MeetingStatus.COMPLETED.value, MeetingStatus.FAILED.value}
    if meeting.status not in finalized_states:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Meeting not finalized; cannot delete transcripts. Current status: {meeting.status}"
        )

    logger.info(f"[API] User {current_user.id} purging transcripts/recordings and anonymizing meeting {internal_meeting_id}")

    # Delete transcripts from PostgreSQL
    stmt_transcripts = select(Transcription).where(Transcription.meeting_id == internal_meeting_id)
    result_transcripts = await db.execute(stmt_transcripts)
    transcripts = result_transcripts.scalars().all()

    for transcript in transcripts:
        await db.delete(transcript)

    # Delete transcript/chat caches from Redis and remove from active meetings
    redis_c = getattr(request.app.state, 'redis_client', None)
    if redis_c:
        try:
            hash_key = f"meeting:{internal_meeting_id}:segments"
            chat_key = f"meeting:{internal_meeting_id}:chat_messages"
            async with redis_c.pipeline(transaction=True) as pipe:
                pipe.delete(hash_key)
                pipe.delete(chat_key)
                pipe.srem("active_meetings", str(internal_meeting_id))
                results = await pipe.execute()
            logger.debug(f"[API] Deleted Redis keys {hash_key}, {chat_key} and removed from active_meetings")
        except Exception as e:
            logger.error(f"[API] Failed to delete Redis data for meeting {internal_meeting_id}: {e}")

    # Delete recordings artifacts (DB rows + storage files)
    recording_cleanup = await _purge_recordings_for_meeting(db, meeting, current_user.id)

    # Scrub PII from meeting record while preserving telemetry
    telemetry_fields = {'status_transition', 'completion_reason', 'error', 'diagnostics'}
    scrubbed_data = {k: v for k, v in original_data.items() if k in telemetry_fields}

    # Add redaction marker for idempotency
    scrubbed_data['redacted'] = True

    # Update meeting record with scrubbed data
    meeting.platform_specific_id = None
    meeting.data = scrubbed_data

    await db.commit()

    logger.info(
        f"[API] Successfully purged meeting {internal_meeting_id}: "
        f"{len(transcripts)} transcripts, "
        f"{recording_cleanup['model_recordings_deleted']} recording rows, "
        f"{recording_cleanup['storage_files_deleted']}/{recording_cleanup['storage_files_targeted']} recording files; "
        f"meeting anonymized"
    )

    return {
        "message": (
            f"Meeting {platform.value}/{native_meeting_id} transcripts and recording artifacts deleted; "
            "meeting data anonymized"
        )
    }
