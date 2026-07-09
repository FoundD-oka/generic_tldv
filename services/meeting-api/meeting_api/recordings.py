"""/recordings/* and /internal/recordings/upload endpoints.

Recording management — /recordings/* and /internal/recordings/upload endpoints.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import uuid as uuid_lib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import attributes

from .database import get_db
from .media_types import is_audio_like_media_type, is_lane_media_type
from .models import Meeting, MeetingSession
from .schemas import (
    RecordingResponse,
    RecordingListResponse,
    RecordingStatus,
    RecordingSource,
)
from .storage import create_storage_client

from .auth import get_user_and_token
from .collector.processors import verify_meeting_token
from .webhooks import send_event_webhook

logger = logging.getLogger("meeting_api.recordings")

router = APIRouter()

# --- Recording retention metadata (issue #1) ---
# The GCS bucket lifecycle does the real work (14d Standard -> Nearline,
# delete at 60d — see deploy/gcs/lifecycle.json). These constants stamp
# advisory/auditable metadata onto each media_file so operators and sweeps
# can reason about retention without reading bucket config.
RECORDING_STORAGE_CLASS_POLICY = "standard_14d_nearline_until_60d"
RECORDING_DELETE_AFTER_DAYS = 60


def _compute_delete_after(anchor_iso: Optional[str]) -> Optional[str]:
    """delete_after = anchor (≈ recording start) + retention window, ISO.

    Advisory only — never used to delete; the bucket lifecycle deletes by
    object age. Returns None if the anchor can't be parsed.
    """
    if not anchor_iso:
        return None
    try:
        return (datetime.fromisoformat(anchor_iso) + timedelta(days=RECORDING_DELETE_AFTER_DAYS)).isoformat()
    except Exception:
        return None


# --- Storage client (lazy init) ---
_storage_client = None


def get_storage_client():
    global _storage_client
    if _storage_client is None:
        _storage_client = create_storage_client()
    return _storage_client


# Per-backend client cache for migration safety (issue #1). A media_file
# persists the storage_backend it was WRITTEN with, so reads/deletes must
# dispatch on that value — not the current default. Without this, flipping
# STORAGE_BACKEND to "gcs" would orphan every historical MinIO object on the
# playback path. Writes still use get_storage_client() (current default).
_storage_clients_by_backend: Dict[str, Any] = {}


def get_storage_client_for(backend: Optional[str]):
    """Return a storage client for a media_file's persisted backend.

    The common case — the file's backend matches the current default — reuses
    the shared singleton (so a single deployment keeps one client and existing
    callers/tests are unaffected). Only a file written under a DIFFERENT backend
    (e.g. a legacy MinIO object read after the cutover to gcs) gets a dedicated,
    cached client. Missing/unknown backends fall back to the default.
    """
    default_backend = os.environ.get("STORAGE_BACKEND", "minio")
    if not backend or backend == default_backend:
        return get_storage_client()
    cached = _storage_clients_by_backend.get(backend)
    if cached is not None:
        return cached
    try:
        client = create_storage_client(backend)
    except Exception as e:
        logger.warning(
            "[recordings] storage client init failed for backend=%s (%s); "
            "falling back to default backend",
            backend, e,
        )
        return get_storage_client()
    _storage_clients_by_backend[backend] = client
    return client


async def require_recording_upload_token(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Missing recording upload token")
    token_claims = verify_meeting_token(auth.removeprefix("Bearer ").strip())
    if not token_claims:
        raise HTTPException(status_code=403, detail="Invalid recording upload token")
    return token_claims


def _new_recording_numeric_id() -> int:
    return int(uuid_lib.uuid4().int % 900000000000 + 100000000000)


def _to_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _normalize_meeting_recording(recording: Dict[str, Any], meeting_id: int) -> Dict[str, Any]:
    rec = dict(recording or {})
    rec["meeting_id"] = rec.get("meeting_id") or meeting_id
    rec["source"] = rec.get("source") or RecordingSource.BOT.value
    rec["status"] = rec.get("status") or RecordingStatus.COMPLETED.value
    rec["media_files"] = rec.get("media_files") or []
    return rec


def media_content_type(media_type: str, media_format: str) -> str:
    fmt = str(media_format or "").lower()
    typ = str(media_type or "").lower()
    if fmt == "webm":
        return "audio/webm" if is_audio_like_media_type(typ) else "video/webm"
    content_types = {
        "wav": "audio/wav",
        "opus": "audio/opus",
        "mp3": "audio/mpeg",
        "jpg": "image/jpeg",
        "png": "image/png",
    }
    return content_types.get(fmt, "application/octet-stream")


def _parse_range_header(range_header: str, total_size: int) -> tuple[int, int]:
    if total_size <= 0:
        raise HTTPException(
            status_code=416,
            detail="Range not satisfiable",
            headers={"Content-Range": "bytes */0"},
        )
    if not range_header.startswith("bytes="):
        raise HTTPException(status_code=416, detail="Invalid Range header")

    spec = range_header[6:].split(",", 1)[0].strip()
    start_s, separator, end_s = spec.partition("-")
    if not separator:
        raise HTTPException(status_code=416, detail="Invalid Range header")

    try:
        if start_s:
            start = int(start_s)
            end = int(end_s) if end_s else total_size - 1
        else:
            suffix_length = int(end_s)
            if suffix_length <= 0:
                raise ValueError
            start = max(total_size - suffix_length, 0)
            end = total_size - 1
    except ValueError:
        raise HTTPException(status_code=416, detail="Invalid Range header")

    if start < 0 or start >= total_size or end < start:
        raise HTTPException(
            status_code=416,
            detail="Range not satisfiable",
            headers={"Content-Range": f"bytes */{total_size}"},
        )
    return start, min(end, total_size - 1)


def _find_media_file(recording: Dict[str, Any], media_file_id: int) -> Optional[Dict[str, Any]]:
    for media_file in recording.get("media_files") or []:
        if int(media_file.get("id", -1)) == media_file_id:
            return media_file
    return None


def _mp3_storage_path(storage_path: str) -> str:
    base, _ = os.path.splitext(storage_path)
    return f"{base}.mp3"


def _ensure_mp3_media_file(storage, source_storage_path: str, source_format: str) -> str:
    """Return an MP3 storage path, converting and caching it when needed."""
    source_format = str(source_format or "").lower()
    if source_format == "mp3":
        if not storage.file_exists(source_storage_path):
            raise FileNotFoundError(source_storage_path)
        return source_storage_path

    mp3_storage_path = _mp3_storage_path(source_storage_path)
    if storage.file_exists(mp3_storage_path):
        return mp3_storage_path
    if not storage.file_exists(source_storage_path):
        raise FileNotFoundError(source_storage_path)

    bitrate = os.environ.get("RECORDING_MP3_BITRATE", "128k")
    timeout_seconds = int(os.environ.get("RECORDING_MP3_TIMEOUT_SECONDS", "900"))
    _, source_ext = os.path.splitext(source_storage_path)
    source_ext = source_ext or ".media"

    with tempfile.TemporaryDirectory(prefix="vexa-mp3-") as tmpdir:
        source_path = os.path.join(tmpdir, f"source{source_ext}")
        mp3_path = os.path.join(tmpdir, "audio.mp3")
        storage.download_file_to_path(source_storage_path, source_path)
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                source_path,
                "-vn",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                bitrate,
                mp3_path,
            ],
            capture_output=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")[:500]
            logger.error("MP3 conversion failed for %s: %s", source_storage_path, stderr)
            raise RuntimeError("MP3 conversion failed")
        if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) <= 0:
            logger.error("MP3 conversion produced an empty file for %s", source_storage_path)
            raise RuntimeError("MP3 conversion produced an empty file")
        storage.upload_file_path(mp3_storage_path, mp3_path, content_type="audio/mpeg")
    return mp3_storage_path


def _build_storage_media_response(storage, storage_path: str, content_type: str, filename: str, request: Request) -> Response:
    headers = {"Content-Disposition": f'inline; filename="{filename}"', "Accept-Ranges": "bytes"}
    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        total = storage.get_file_size(storage_path)
        start, end = _parse_range_header(range_header, total)
        chunk = storage.download_file_range(storage_path, start, end)
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        headers["Content-Length"] = str(len(chunk))
        return Response(content=chunk, media_type=content_type, status_code=206, headers=headers)

    data = storage.download_file(storage_path)
    return Response(content=data, media_type=content_type, headers=headers)


def _public_recording_view(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Strip lane media files from API responses (issue #25: lanes are
    internal-only). MediaFileResponse.type is an enum without lane-*, and
    lanes are not a playback surface. Only the response boundary filters —
    deletion and finalizer paths read the raw JSONB so lane objects are
    still cleaned up and concatenated.
    """
    view = dict(rec)
    view["media_files"] = [
        mf for mf in (rec.get("media_files") or [])
        if not (isinstance(mf, dict) and is_lane_media_type(mf.get("type")))
    ]
    return view


async def _list_meeting_data_recordings(db: AsyncSession, user_id: int, meeting_id: Optional[int] = None) -> List[Dict]:
    stmt = select(Meeting).where(Meeting.user_id == user_id)
    if meeting_id is not None:
        stmt = stmt.where(Meeting.id == meeting_id)
    result = await db.execute(stmt)
    meetings = result.scalars().all()
    recordings: List[Dict] = []
    for m in meetings:
        if not isinstance(m.data, dict):
            continue
        for rec in m.data.get("recordings") or []:
            if isinstance(rec, dict):
                recordings.append(_normalize_meeting_recording(rec, m.id))
    recordings.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return recordings


async def _find_meeting_data_recording(db: AsyncSession, user_id: int, recording_id: int):
    # Use JSONB containment to find only meetings whose data->'recordings' array
    # contains an object with the target id, instead of scanning all user meetings.
    stmt = (
        select(Meeting)
        .where(
            Meeting.user_id == user_id,
            Meeting.data.isnot(None),
            Meeting.data["recordings"].cast(JSONB).isnot(None),
        )
        .where(
            text("data->'recordings' @> cast(:pattern as jsonb)").bindparams(
                pattern=json.dumps([{"id": recording_id}])
            )
        )
    )
    result = await db.execute(stmt)
    for m in result.scalars().all():
        if not isinstance(m.data, dict):
            continue
        for rec in m.data.get("recordings") or []:
            if isinstance(rec, dict) and int(rec.get("id", -1)) == recording_id:
                return m, _normalize_meeting_recording(rec, m.id)
    return None, None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/internal/recordings/upload", status_code=201, include_in_schema=False)
async def internal_upload_recording(
    token_claims: dict = Depends(require_recording_upload_token),
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(default=None),
    session_uid: Optional[str] = Form(default=None),
    media_type: str = Form(default="audio"),
    media_format: str = Form(default="wav"),
    duration_seconds: Optional[float] = Form(default=None),
    sample_rate: Optional[int] = Form(default=None),
    is_final: bool = Form(default=True),
    # Incremental-upload support (Pack B / issue #218). When the bot uploads
    # per-chunk, `chunk_seq` is incremented per chunk and `is_final` stays
    # False until the last one. Each chunk gets its own object in MinIO under
    # a per-session prefix; each creates a distinct `media_files[]` entry
    # in meeting.data.recordings[].media_files. Recording status stays
    # IN_PROGRESS until is_final=True — that's the "partial recording" marker.
    # Legacy one-shot callers that don't pass chunk_seq default to 0 with
    # is_final=True; behavior is byte-identical to today for that path.
    chunk_seq: int = Form(default=0),
    db: AsyncSession = Depends(get_db),
):
    lane_id: Optional[str] = None
    lane_label: Optional[str] = None
    lane_id_source: Optional[str] = None
    lane_start_offset_ms: Optional[float] = None
    if metadata:
        try:
            meta = json.loads(metadata)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="Invalid JSON in metadata")
        session_uid = session_uid or meta.get("session_uid")
        media_type = meta.get("media_type", media_type)
        media_format = meta.get("format", media_format)
        duration_seconds = meta.get("duration_seconds", duration_seconds)
        sample_rate = meta.get("sample_rate", sample_rate)
        lane_id = meta.get("lane_id")
        lane_label = meta.get("lane_label")
        lane_id_source = meta.get("lane_id_source")
        # Issue #25 BUG-002 — delta (ms) between the mixed recording's start
        # and this lane's own recorder start. A late joiner's lane audio
        # otherwise lands at t=0 on the merged transcript timeline.
        lane_start_offset_ms = meta.get("lane_start_offset_ms")
        if "is_final" in meta:
            is_final = _to_bool(meta.get("is_final"), default=True)
        if "chunk_seq" in meta:
            try:
                chunk_seq = int(meta.get("chunk_seq"))
            except (TypeError, ValueError):
                pass

    if not session_uid:
        raise HTTPException(status_code=422, detail="session_uid is required")

    session_stmt = select(MeetingSession).where(MeetingSession.session_uid == session_uid)
    meeting_session = (await db.execute(session_stmt)).scalars().first()

    if not meeting_session:
        if not is_final:
            return {"status": "pending", "detail": f"Meeting session not ready yet: {session_uid}"}
        raise HTTPException(status_code=404, detail=f"Meeting session not found: {session_uid}")

    if not isinstance(token_claims, dict):
        # Direct unit tests call the endpoint function without FastAPI dependency injection.
        token_claims = {"meeting_id": meeting_session.meeting_id}

    if int(token_claims.get("meeting_id")) != int(meeting_session.meeting_id):
        raise HTTPException(status_code=403, detail="Recording token does not match meeting")

    meeting = await db.get(Meeting, meeting_session.meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail=f"Meeting not found for session: {session_uid}")

    user_id = meeting.user_id
    # TODO(v0.10.7): bound UploadFile body size for defense-in-depth against
    # compromised internal callers. Route is JWT-authed via
    # require_recording_upload_token (HS256, aud/iss/scope/exp) and meeting-api
    # is on Docker `expose:` not `ports:` — not externally reachable today.
    file_data = await file.read()
    file_size = len(file_data)

    meeting_data_dict = dict(meeting.data or {})
    recordings_list = list(meeting_data_dict.get("recordings") or [])
    existing_rec = None
    existing_idx = None
    recording_id = _new_recording_numeric_id()

    for idx, rec in enumerate(recordings_list):
        if isinstance(rec, dict) and rec.get("session_uid") == session_uid and rec.get("source") == RecordingSource.BOT.value:
            existing_rec = rec
            existing_idx = idx
            recording_id = rec.get("id") or recording_id
            break

    storage_id = recording_id

    # Incremental-upload storage path: per-session + per-media-type directory
    # + zero-padded chunk index. media_type is part of the path because audio
    # chunks and a video blob often share the same format (webm) — without
    # the type prefix they'd collide on chunk_seq=0, and the second upload
    # would silently overwrite the first (Bug C 2026-04-21: dashboard
    # showed video-player UI but the MinIO object was an audio-only blob
    # because audio overwrote video at .../000000.webm).
    storage_path = f"recordings/{user_id}/{storage_id}/{session_uid}/{media_type}/{chunk_seq:06d}.{media_format}"
    content_type = media_content_type(media_type, media_format)

    try:
        storage = get_storage_client()
        storage.upload_file(storage_path, file_data, content_type=content_type)
    except Exception as e:
        logger.error(f"Storage upload failed for {session_uid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to upload recording to storage")

    # JSONB-only path (v0.10.6.1): write recording metadata into meeting.data.
    # Media-file materialization policy:
    #   - Every successful chunk upload UPDATES the per-type media_files entry
    #     with the latest chunk's metadata (one entry per media_type per
    #     recording — see Pack E.1.a history).
    #   - Concurrency: SELECT ... FOR UPDATE held BEFORE we snapshot
    #     meeting.data, so concurrent audio+video uploads serialize on the
    #     meeting row and don't lose each other's media_files entries.
    from sqlalchemy import select as _sql_select
    locked_meeting = (await db.execute(
        _sql_select(Meeting)
        .where(Meeting.id == meeting_session.meeting_id)
        .with_for_update()
    )).scalar_one()
    meeting = locked_meeting
    meeting_data_dict = dict(meeting.data or {})
    recordings_list = list(meeting_data_dict.get("recordings") or [])
    # Re-find existing_rec under the FRESH (locked) snapshot. Adopt the
    # canonical recording_id from the existing rec if present.
    existing_rec = None
    existing_idx = None
    for idx, rec in enumerate(recordings_list):
        if isinstance(rec, dict) and rec.get("session_uid") == session_uid and rec.get("source") == RecordingSource.BOT.value:
            existing_rec = rec
            existing_idx = idx
            recording_id = rec.get("id") or recording_id
            break

    # BUG-003 — a lane chunk's is_final finalizes only that lane's own
    # media_files entry (below); it must never be treated as completing the
    # whole recording, whether this is the first chunk ever seen for the
    # session or a later chunk on an already-existing recording.
    is_final_completes_recording = is_final and not is_lane_media_type(media_type)

    if existing_rec is None:
        rec_payload = {
            "id": recording_id,
            "meeting_id": meeting.id,
            "user_id": user_id,
            "session_uid": session_uid,
            "source": RecordingSource.BOT.value,
            "status": RecordingStatus.COMPLETED.value if is_final_completes_recording else RecordingStatus.IN_PROGRESS.value,
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat() if is_final_completes_recording else None,
            "media_files": [],
        }
        existing_idx = len(recordings_list)
        recordings_list.append(rec_payload)
        was_completed = False
    else:
        rec_payload = dict(existing_rec)
        was_completed = rec_payload.get("status") == RecordingStatus.COMPLETED.value

    status_transitioned_to_completed = False
    prior_media_files = list(rec_payload.get("media_files") or [])
    prior_types = {mf.get("type") for mf in prior_media_files}
    chunk_action = "appended" if media_type not in prior_types else "in_place"
    prior_same_type = next(
        (mf for mf in prior_media_files if mf.get("type") == media_type),
        None,
    )
    prior_bytes = int((prior_same_type or {}).get("file_size_bytes") or 0) if prior_same_type else 0
    prior_chunk_count = int((prior_same_type or {}).get("chunk_count") or (1 if prior_same_type else 0))
    prior_first_chunk_at = (prior_same_type or {}).get("first_chunk_at") if prior_same_type else None
    cumulative_bytes = (prior_bytes + file_size) if prior_same_type else file_size
    cumulative_chunk_count = (prior_chunk_count + 1) if prior_same_type else 1
    first_chunk_at = prior_first_chunk_at or datetime.utcnow().isoformat()
    existing_media_files = [
        mf for mf in prior_media_files
        if mf.get("type") != media_type
    ]
    # Pack U.7 — preserve master path against late-chunk overwrite.
    # Lane masters live under their own /{lane-*}/ prefix, so the guard must
    # match the entry's own media_type dir, not just /audio/ (issue #25).
    prior_sp = (prior_same_type or {}).get("storage_path") or ""
    prior_is_final = bool((prior_same_type or {}).get("is_final"))
    master_finalized = (
        prior_sp.endswith("/audio/master.webm")
        or prior_sp.endswith("/audio/master.wav")
        or (
            is_lane_media_type(media_type)
            and (
                prior_sp.endswith(f"/{media_type}/master.webm")
                or prior_sp.endswith(f"/{media_type}/master.wav")
            )
        )
        or prior_is_final
    )
    new_storage_path = prior_sp if master_finalized else storage_path
    new_is_final = True if master_finalized else is_final
    if master_finalized and not is_final:
        logger.warning(
            "[E1A] late_chunk_after_finalize meeting_id=%s recording_id=%s media_type=%s "
            "chunk_seq=%s — preserving master storage_path=%s",
            meeting.id, recording_id, media_type, chunk_seq, prior_sp,
        )
    # Lane identity survives chunks that omit metadata: later chunks inherit
    # the prior entry's lane object unless this chunk supplies fresh values.
    lane_info = None
    if is_lane_media_type(media_type):
        fresh = {
            "lane_id": lane_id,
            "lane_label": lane_label,
            "lane_id_source": lane_id_source,
            "lane_start_offset_ms": lane_start_offset_ms,
        }
        prior_lane = (prior_same_type or {}).get("lane") or {}
        lane_info = {**prior_lane, **{k: v for k, v in fresh.items() if v is not None}}
    existing_media_files.append({
        "id": (prior_same_type or {}).get("id") or _new_recording_numeric_id(),
        "type": media_type,
        "format": media_format,
        **({"lane": lane_info} if lane_info is not None else {}),
        "storage_path": new_storage_path,
        "storage_backend": os.environ.get("STORAGE_BACKEND", "minio"),
        "file_size_bytes": cumulative_bytes,
        "last_chunk_size_bytes": file_size,
        "chunk_count": cumulative_chunk_count,
        "duration_seconds": duration_seconds,
        "chunk_seq": chunk_seq,
        "first_chunk_at": first_chunk_at,
        "metadata": {"sample_rate": sample_rate} if sample_rate else {},
        "created_at": datetime.utcnow().isoformat(),
        "is_final": new_is_final,
        "finalized_at": (prior_same_type or {}).get("finalized_at"),
        "finalized_by": (prior_same_type or {}).get("finalized_by"),
        # Issue #1 — retention/storage metadata. content_type is stored so the
        # download path doesn't have to re-derive it; storage_class_policy and
        # delete_after are advisory (lifecycle does the real Nearline/delete).
        # No signed URL is ever persisted here — playback mints a short-TTL URL
        # on demand and playback_url stays a stable route.
        "content_type": content_type,
        "storage_class_policy": RECORDING_STORAGE_CLASS_POLICY,
        "delete_after": _compute_delete_after(first_chunk_at),
        "upload_status": "uploaded",
    })
    rec_payload["media_files"] = existing_media_files
    logger.info(
        "[E1A] chunk_write meeting_id=%s recording_id=%s media_type=%s "
        "chunk_seq=%s prior_chunks=%s action=%s is_final=%s",
        meeting.id, rec_payload.get("id"), media_type,
        chunk_seq, prior_chunk_count, chunk_action, is_final,
    )
    if is_lane_media_type(media_type):
        # BUG-003 — a lane chunk's is_final only finalizes that lane's own
        # media_files entry (handled above); it must never flip the whole
        # recording's status to COMPLETED or fire recording.completed. A
        # mid-meeting participant lane departing is not the meeting ending.
        pass
    elif is_final_completes_recording:
        rec_payload["status"] = RecordingStatus.COMPLETED.value
        rec_payload["completed_at"] = datetime.utcnow().isoformat()
        status_transitioned_to_completed = not was_completed
    else:
        # v0.10.5 R2 — defense-in-depth: terminal state is sticky; never downgrade COMPLETED → IN_PROGRESS
        # if a stray late chunk arrives after reconciler finalization.
        if not was_completed:
            rec_payload["status"] = RecordingStatus.IN_PROGRESS.value
    recordings_list[existing_idx] = rec_payload
    meeting_data_dict["recordings"] = recordings_list
    meeting.data = meeting_data_dict
    attributes.flag_modified(meeting, "data")
    await db.commit()
    if status_transitioned_to_completed:
        asyncio.create_task(send_event_webhook(meeting.id, "recording.completed", {"recording": rec_payload}))
    final_media = rec_payload.get("media_files") or []
    mf_id = final_media[-1]["id"] if (is_final and final_media) else None
    return {"recording_id": rec_payload["id"], "media_file_id": mf_id, "storage_path": storage_path, "status": rec_payload["status"], "chunk_seq": chunk_seq}


@router.get("/recordings", response_model=RecordingListResponse, summary="List recordings for the authenticated user")
async def list_recordings(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    meeting_id: Optional[int] = Query(default=None),
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth
    recs = await _list_meeting_data_recordings(db, user.id, meeting_id=meeting_id)
    page = recs[offset:offset + limit]
    return RecordingListResponse(recordings=[RecordingResponse.model_validate(_public_recording_view(r)) for r in page])


@router.get("/recordings/{recording_id}", response_model=RecordingResponse, summary="Get a single recording")
async def get_recording(
    recording_id: int,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth
    _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return RecordingResponse.model_validate(_public_recording_view(rec))


@router.get("/recordings/{recording_id}/master", summary="Get presigned URL for the canonical master media file of a given type")
async def get_recording_master(
    recording_id: int,
    type: str = Query(..., regex="^(audio|video)$", description="Media type: audio | video"),
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """v0.10.6.1 — Canonical playback endpoint (ADR-2).

    The dashboard reads `recording.playback_url.audio` (or `.video`) from
    the meeting payload — a stable route. Hitting this endpoint resolves
    the route to the master media file's presigned URL on each call.

    Producer-writes / consumer-reads model: `recording_finalizer` writes
    `playback_url` onto the JSONB recording element once master assembly
    completes. The dashboard reads it. Selection logic
    (`pickMasterMediaFile()`) is deleted; the dashboard no longer reasons
    about which media_files[] entry is the master.

    Returns 404 when no master exists yet for the requested type (meeting
    still in progress, finalizer crashed, no-such-type recording). The
    dashboard renders "finalizing" on 404 — explicit state, NOT a silent
    fallback (principle 5).
    """
    _, user = auth

    _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    # Find the master media file of the requested type. Master =
    # finalized_by == "recording_finalizer.master".
    master_mf = None
    for mf in rec.get("media_files") or []:
        if mf.get("type") == type and mf.get("finalized_by") == "recording_finalizer.master":
            master_mf = mf
            break
    if not master_mf:
        raise HTTPException(status_code=404, detail=f"No master {type} file for recording {recording_id} (still finalizing or not produced)")
    media_file_id = master_mf.get("id")
    if media_file_id is None:
        raise HTTPException(status_code=404, detail="Master media file id missing")

    # Delegate to the existing per-id endpoint logic, then enrich with
    # duration_seconds (v0.10.6.1 Task 9). Dashboard reads duration from
    # the master response directly so it no longer needs to peek into
    # media_files[] for duration.
    response = await download_media_file(recording_id, media_file_id, auth, db)
    response["media_file_id"] = media_file_id
    response["raw_url"] = f"/recordings/{recording_id}/media/{media_file_id}/raw"
    response["duration_seconds"] = master_mf.get("duration_seconds")
    return response


@router.get("/recordings/{recording_id}/master/mp3", summary="Download the canonical master audio as MP3")
async def download_recording_master_mp3(
    recording_id: int,
    request: Request,
    type: str = Query("audio", regex="^audio$", description="Media type: audio"),
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """Resolve the finalized master audio file and return an MP3 download.

    The MP3 is generated lazily on first request, cached in storage next to
    the master media, and served with Range support so Cloud-hosted dashboards
    can download it in bounded chunks.
    """
    _, user = auth

    _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")

    master_mf = None
    for mf in rec.get("media_files") or []:
        if mf.get("type") == type and mf.get("finalized_by") == "recording_finalizer.master":
            master_mf = mf
            break
    if not master_mf:
        raise HTTPException(status_code=404, detail=f"No master audio file for recording {recording_id} (still finalizing or not produced)")
    media_file_id = master_mf.get("id")
    if media_file_id is None:
        raise HTTPException(status_code=404, detail="Master media file id missing")

    return await download_media_file_mp3(recording_id, int(media_file_id), request, auth, db)


@router.get("/recordings/{recording_id}/media/{media_file_id}/download", summary="Get presigned download URL for a media file")
async def download_media_file(
    recording_id: int, media_file_id: int,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    """Return a short-lived presigned URL pointing at the master media file in MinIO.

    Pack U.8 (v0.10.6) contract:
    - After Pack U.5+U.6, `recording_finalizer` builds a single
      `<prefix>/master.{webm|wav}` server-side at bot_exit_callback and
      rewrites `media_file.storage_path` to point at it. This endpoint
      hands the dashboard a 1-hour presigned URL to that master so the
      browser can stream directly from MinIO with native HTTP Range
      (no in-process proxying through meeting-api).
    - Option B chosen: return HTTP 200 + JSON `{"url": "<presigned>", ...}`
      rather than a 302 redirect. Keeps the API stable and lets the
      dashboard control the `<audio>` lifecycle (preload, autoplay).
    - TTL: 3600s (1 hour). Long enough for browser playback even on long
      meetings, short enough to limit credential-leak blast radius if
      the URL escapes the dashboard session.
    - `local` storage backend (dev-only) cannot mint presigned URLs; the
      response surfaces a `/raw` fallback path (still proxied in-process).
      For `minio`/`s3` backends, returns the presigned URL directly.
    - 404 when the master file does not yet exist (meeting still in
      progress, finalizer crashed, etc.). Callers MUST handle this.
    """
    _, user = auth

    _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    mf = _find_media_file(rec, media_file_id)
    if not mf:
        raise HTTPException(status_code=404, detail="Media file not found")
    fmt = str(mf.get("format", "bin")).lower()
    ct = media_content_type(str(mf.get("type", "audio")), fmt)
    storage_path = mf.get("storage_path")
    storage_backend = mf.get("storage_backend")
    type_label = mf.get("type", "audio")
    file_size = mf.get("file_size_bytes")

    if not storage_path:
        raise HTTPException(status_code=404, detail="Media file storage path not set")

    storage = get_storage_client_for(storage_backend)
    # Master may not exist yet: meeting still in progress, or finalizer
    # crashed before producing the concatenated master. Surface a 404 so
    # the dashboard can fall back to /raw (Pack P: this is the LAST
    # allowed fallback in the playback path until master_ready flag exists).
    try:
        master_present = storage.file_exists(storage_path)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"file_exists check failed for {storage_path}: {e}")
        master_present = False
    if not master_present:
        raise HTTPException(status_code=404, detail="Media file content not found in storage")

    raw_fallback = f"/recordings/{recording_id}/media/{media_file_id}/raw"
    if storage_backend == "local":
        # Local backend can't mint presigned URLs (no signed-URL semantics
        # on filesystem). Fall back to the legacy /raw proxy path. This is
        # an explicit per-deployment decision (Pack P), not a runtime
        # fallback — local storage is dev-only.
        url = raw_fallback
    else:
        url = storage.get_presigned_url(storage_path, expires=3600)
        # Issue #1: signing may be unavailable (e.g. GCS signBlob not granted
        # to the runtime SA — get_presigned_url returns None and logs a
        # warning). Fall back to the authenticated /raw proxy here so the
        # endpoint never hands the client a null url; this keeps playback
        # working without depending on the consumer to interpret null.
        if not url:
            url = raw_fallback

    return {
        "url": url,
        "download_url": url,  # legacy alias kept for back-compat with v0.10.5 clients
        "filename": f"{recording_id}_{type_label}.{fmt}",
        "content_type": ct,
        "file_size_bytes": file_size,
        "expires_in": 3600,
    }


# Legacy: in-process proxy through meeting-api. Kept for back-compat with
# clients pre-Pack U.8 (v0.10.6). The new playback path uses /download +
# presigned URLs so the browser streams directly from MinIO with native
# HTTP Range. /raw remains as the LAST allowed fallback when /download
# returns 404 (master not yet built — Pack P).
@router.get("/recordings/{recording_id}/media/{media_file_id}/raw", summary="Download media file content")
async def download_media_file_raw(
    recording_id: int, media_file_id: int,
    request: Request,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth

    # Resolve the storage path and content type
    storage_path = None
    ct = "application/octet-stream"
    filename = ""

    storage_backend = None
    _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    mf = _find_media_file(rec, media_file_id)
    if mf:
        storage_path = mf.get("storage_path")
        storage_backend = mf.get("storage_backend")
        fmt = str(mf.get("format", "bin")).lower()
        type_label = str(mf.get("type", "audio"))
        ct = media_content_type(type_label, fmt)
        filename = f"{recording_id}_{type_label}.{fmt}"

    if not storage_path:
        raise HTTPException(status_code=404, detail="Media file not found")

    storage = get_storage_client_for(storage_backend)
    try:
        return _build_storage_media_response(storage, storage_path, ct, filename, request)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Media file content not found in storage")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download media file {media_file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read media file")


@router.get("/recordings/{recording_id}/media/{media_file_id}/mp3", summary="Download an audio media file as MP3")
async def download_media_file_mp3(
    recording_id: int, media_file_id: int,
    request: Request,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth

    _, rec = await _find_meeting_data_recording(db, user.id, recording_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    mf = _find_media_file(rec, media_file_id)
    if not mf:
        raise HTTPException(status_code=404, detail="Media file not found")
    if str(mf.get("type", "audio")).lower() != "audio":
        raise HTTPException(status_code=400, detail="MP3 download is only available for audio media")

    storage_path = mf.get("storage_path")
    if not storage_path:
        raise HTTPException(status_code=404, detail="Media file storage path not set")

    storage = get_storage_client_for(mf.get("storage_backend"))
    try:
        mp3_storage_path = await asyncio.to_thread(
            _ensure_mp3_media_file,
            storage,
            storage_path,
            str(mf.get("format", "bin")).lower(),
        )
        return _build_storage_media_response(
            storage,
            mp3_storage_path,
            "audio/mpeg",
            f"{recording_id}_audio.mp3",
            request,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Media file content not found in storage")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to prepare MP3 media file {media_file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to prepare MP3 media file")


@router.delete("/recordings/{recording_id}", summary="Delete a recording and its media files")
async def delete_recording(
    recording_id: int,
    auth: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, user = auth
    meeting, rec = await _find_meeting_data_recording(db, user.id, recording_id)
    if meeting is None or rec is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    for mf in rec.get("media_files") or []:
        path = mf.get("storage_path")
        if path:
            try:
                storage = get_storage_client_for(mf.get("storage_backend"))
                storage.delete_file(path)
                mp3_path = _mp3_storage_path(path)
                if mp3_path != path:
                    storage.delete_file(mp3_path)
            except Exception as e:
                logger.warning(f"Failed to delete {path}: {e}")
    current = dict(meeting.data or {})
    current["recordings"] = [r for r in (current.get("recordings") or []) if not (isinstance(r, dict) and int(r.get("id", -1)) == recording_id)]
    meeting.data = current
    attributes.flag_modified(meeting, "data")
    await db.commit()
    return {"status": "deleted", "recording_id": recording_id}
