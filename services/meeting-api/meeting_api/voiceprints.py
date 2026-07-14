"""Voiceprint enrollment/consent/profile API (issue #27 Phase 4).

Endpoints:
  POST   /voiceprints/preview-from-segments — exact selected-audio preview
  POST   /voiceprints/enroll-from-segments  — hash-bound selected-audio enrollment
  POST   /voiceprints/enroll-from-audio     — reviewed pre-recorded enrollment
  POST   /voiceprints/enroll-from-cluster   — disabled legacy endpoint (410)
  GET    /speaker-profiles                 — list the user's profiles
  DELETE /speaker-profiles/{id}            — delete a profile (FK cascade)

Every enrollment creates the profile (if needed), the consent row, and the
voiceprint row in ONE transaction (plan §7: "acceptance = consent" per
PII policy — the consent record and the voiceprint it authorizes must never
be split across commits, or a crash between them could leave one without
the other even though the DB-level NOT NULL FK prevents the voiceprint half
of that from ever landing without a consent row).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import os
import secrets
import threading
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import func as sa_func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .auth import get_user_and_token
from .database import get_db
from .final_transcription import FinalTranscriptionSource
from .models import (
    MediaFile,
    Meeting,
    Recording,
    SpeakerProfile,
    Transcription,
    Voiceprint,
    VoiceprintAuditLog,
    VoiceprintConsent,
)
from .voiceprint_crypto import get_voiceprint_crypto
from .voiceprint_matching import (
    VOICEPRINT_MAX_CLIP_SECONDS,
    VOICEPRINT_MAX_DIRECT_AUDIO_BYTES,
    VOICEPRINT_MIN_CLIP_SECONDS,
    VoiceprintServiceUnavailable,
    embed_wav_bytes,
    extract_exact_clip_wav,
    normalize_direct_audio_to_wav,
    wav_duration_seconds,
)

logger = logging.getLogger("meeting_api.voiceprints")
router = APIRouter()
VOICEPRINT_CONSENT_SCOPE = "今後の会議での話者候補提示"
_SOURCE_DURATION_MISMATCH_TOLERANCE_SECONDS = 0.25
_DIRECT_AUDIO_BASE64_MAX_CHARS = ((VOICEPRINT_MAX_DIRECT_AUDIO_BYTES + 2) // 3) * 4
_DIRECT_AUDIO_REQUEST_MAX_BYTES = _DIRECT_AUDIO_BASE64_MAX_CHARS + (64 * 1024)
_DIRECT_AUDIO_FORMATS = {"wav", "webm", "ogg", "opus", "mp3", "m4a", "mp4"}
VOICEPRINT_MAX_ACTIVE_DIRECT_ENROLLMENTS = max(
    1, int(os.getenv("VOICEPRINT_MAX_ACTIVE_DIRECT_ENROLLMENTS", "1"))
)


class _ImmediateAdmissionGate:
    """Thread-safe, non-waiting admission gate for request-sized resources."""

    def __init__(self, max_active: int):
        self._slots = threading.BoundedSemaphore(value=max_active)

    def try_acquire(self) -> bool:
        return self._slots.acquire(blocking=False)

    def release(self) -> None:
        self._slots.release()


_DIRECT_ENROLLMENT_ADMISSION_GATE = _ImmediateAdmissionGate(
    VOICEPRINT_MAX_ACTIVE_DIRECT_ENROLLMENTS
)


async def _admit_direct_enrollment_request() -> AsyncIterator[None]:
    """Reject before reading a large direct-audio body when capacity is full."""
    if not _DIRECT_ENROLLMENT_ADMISSION_GATE.try_acquire():
        raise HTTPException(
            status_code=429,
            detail="Direct voiceprint enrollment capacity is busy; retry shortly",
            headers={"Retry-After": "1", "Cache-Control": "no-store"},
        )
    try:
        yield
    finally:
        _DIRECT_ENROLLMENT_ADMISSION_GATE.release()


class _StrictVoiceprintRequest(BaseModel):
    model_config = {"extra": "forbid", "str_strip_whitespace": True}


class SelectedSegmentsRequest(_StrictVoiceprintRequest):
    meeting_id: int = Field(..., gt=0)
    segment_ids: List[str] = Field(..., min_length=1, max_length=20)

    @field_validator("segment_ids")
    @classmethod
    def validate_segment_ids(cls, values: List[str]) -> List[str]:
        if any(not isinstance(value, str) or not value or value != value.strip() for value in values):
            raise ValueError("segment_ids must contain non-empty, unpadded strings")
        if any(len(value) > 255 for value in values):
            raise ValueError("segment_ids entries must be at most 255 characters")
        if len(values) != len(set(values)):
            raise ValueError("segment_ids must not contain duplicates")
        return values


class EnrollFromSegmentsRequest(SelectedSegmentsRequest):
    display_name: str = Field(..., min_length=1, max_length=255)
    clip_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    source_fingerprint: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    audio_review_confirmed: Literal[True]
    consent_confirmed: Literal[True]


class EnrollFromAudioRequest(_StrictVoiceprintRequest):
    audio_base64: str = Field(..., min_length=1, max_length=_DIRECT_AUDIO_BASE64_MAX_CHARS)
    media_format: str = Field(..., min_length=1, max_length=32)
    display_name: str = Field(..., min_length=1, max_length=255)
    audio_review_confirmed: Literal[True]
    consent_confirmed: Literal[True]

    @field_validator("media_format")
    @classmethod
    def validate_media_format(cls, value: str) -> str:
        normalized = value.strip().lower()
        if ";" in normalized:
            normalized = normalized.split(";", 1)[0]
        if "/" in normalized:
            normalized = normalized.rsplit("/", 1)[-1]
        normalized = {"x-wav": "wav", "mpeg": "mp3"}.get(normalized, normalized)
        if normalized not in _DIRECT_AUDIO_FORMATS:
            raise ValueError(f"unsupported media_format: {value}")
        return normalized


async def _parse_direct_audio_request(request: Request) -> EnrollFromAudioRequest:
    """Read a direct-audio JSON body with a hard cap and sanitized errors."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _DIRECT_AUDIO_REQUEST_MAX_BYTES:
                raise HTTPException(status_code=413, detail="Voiceprint audio request is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _DIRECT_AUDIO_REQUEST_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Voiceprint audio request is too large")
        body.extend(chunk)
    try:
        payload = json.loads(bytes(body))
        return EnrollFromAudioRequest.model_validate(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError, ValueError) as exc:
        # Never return Pydantic's `errors()` here: it can echo the submitted
        # base64 value in `input`, leaking raw biometric audio to clients/logs.
        raise HTTPException(
            status_code=422,
            detail="Invalid direct voiceprint enrollment request",
        ) from exc


async def _require_owned_ended_meeting(
    meeting_id: int,
    current_user: Any,
    db: AsyncSession,
    *,
    for_update: bool = False,
) -> Meeting:
    stmt = select(Meeting).where(
        Meeting.id == meeting_id,
        Meeting.user_id == current_user.id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    meeting = (await db.execute(stmt)).scalars().first()
    if not meeting:
        # Deliberately hide whether the id belongs to another user.
        raise HTTPException(status_code=404, detail="Meeting not found")
    if meeting.status not in {"completed", "failed"}:
        raise HTTPException(
            status_code=409,
            detail="Voiceprint enrollment is available only after the meeting has ended",
        )
    state = (
        dict((meeting.data or {}).get("final_transcription") or {})
        if isinstance(meeting.data, dict) else {}
    )
    if state.get("status") in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail="Transcript is being replaced; select the audio again after it finishes",
        )
    return meeting


def _canonical_source_identity(
    source: FinalTranscriptionSource,
) -> Tuple[str, str, str, str]:
    """Return the stable, non-secret identity of one finalized audio source."""
    storage_path = str(source.storage_path or "")
    inferred_format = storage_path.rsplit(".", 1)[-1] if "." in storage_path else "webm"
    return (
        str(source.storage_backend or os.getenv("STORAGE_BACKEND", "minio")).strip().lower(),
        storage_path,
        str(source.media_format or inferred_format).strip().lower(),
        str(source.session_uid or ""),
    )


def _source_fingerprint(source: FinalTranscriptionSource) -> str:
    """Bind preview/enrollment to a source without exposing its storage path."""
    canonical = json.dumps(
        _canonical_source_identity(source),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _selection_binding_fingerprint(
    segment_ids: List[str],
    session_uid: str,
    ranges: List[Tuple[float, float]],
    source_fingerprint: str,
) -> str:
    """Bind the reviewed clip to the exact DB selection without exposing it."""
    canonical = json.dumps(
        {
            "segment_ids": sorted(segment_ids),
            "session_uid": session_uid,
            "ranges": [[round(start, 6), round(end, 6)] for start, end in ranges],
            "source_fingerprint": source_fingerprint,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _session_master_candidates_from_meeting_data(
    meeting: Meeting, session_uid: str,
) -> List[FinalTranscriptionSource]:
    data = meeting.data if isinstance(meeting.data, dict) else {}
    recordings = data.get("recordings") or []
    if isinstance(recordings, dict):
        recordings = [recordings]
    candidates: List[FinalTranscriptionSource] = []
    for recording in recordings:
        if not isinstance(recording, dict):
            continue
        if str(recording.get("session_uid") or "") != session_uid:
            continue
        if recording.get("status") != "completed":
            continue
        for media_file in recording.get("media_files") or []:
            if not isinstance(media_file, dict):
                continue
            path = str(media_file.get("storage_path") or "")
            if (
                media_file.get("type") != "audio"
                or media_file.get("finalized_by") != "recording_finalizer.master"
                or not (path.endswith("/master.wav") or path.endswith("/master.webm"))
            ):
                continue
            candidates.append(FinalTranscriptionSource(
                storage_path=path,
                media_format=str(
                    media_file.get("format") or path.rsplit(".", 1)[-1]
                ).lower(),
                session_uid=session_uid,
                storage_backend=media_file.get("storage_backend"),
                source="meeting.data.selected_segments",
            ))
    return candidates


async def _resolve_session_master(
    meeting: Meeting, session_uid: str, db: AsyncSession,
) -> Optional[FinalTranscriptionSource]:
    """Resolve exactly one finalized mixed master for the selected session."""
    candidates = {
        _canonical_source_identity(source): source
        for source in _session_master_candidates_from_meeting_data(meeting, session_uid)
    }
    if len(candidates) > 1:
        raise HTTPException(
            status_code=422,
            detail="Multiple finalized audio masters exist for the selected session",
        )

    rows = (await db.execute(
        select(Recording, MediaFile)
        .join(MediaFile, MediaFile.recording_id == Recording.id)
        .where(
            Recording.meeting_id == meeting.id,
            Recording.session_uid == session_uid,
            Recording.status == "completed",
            MediaFile.type == "audio",
        )
    )).all()
    for _recording, media_file in rows:
        path = str(getattr(media_file, "storage_path", "") or "")
        if not (path.endswith("/master.wav") or path.endswith("/master.webm")):
            continue
        source = FinalTranscriptionSource(
            storage_path=path,
            media_format=str(
                getattr(media_file, "format", None) or path.rsplit(".", 1)[-1]
            ).lower(),
            session_uid=session_uid,
            storage_backend=getattr(media_file, "storage_backend", None),
            source="media_files.selected_segments",
        )
        candidates[_canonical_source_identity(source)] = source

    if not candidates:
        return None
    if len(candidates) > 1:
        raise HTTPException(
            status_code=422,
            detail="Multiple finalized audio masters exist for the selected session",
        )
    return next(iter(candidates.values()))


def _strict_duration(duration: float) -> bool:
    return VOICEPRINT_MIN_CLIP_SECONDS <= duration <= VOICEPRINT_MAX_CLIP_SECONDS


def _selected_ranges(rows: List[Transcription]) -> Tuple[str, List[Tuple[float, float]], float]:
    session_uids = {str(getattr(row, "session_uid", "") or "") for row in rows}
    if len(session_uids) != 1 or "" in session_uids:
        raise HTTPException(
            status_code=422,
            detail="Selected segments must belong to one recording session",
        )

    ranges: List[Tuple[float, float]] = []
    for row in rows:
        try:
            raw_start = float(row.start_time)
            end = float(row.end_time)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Selected segment has invalid timing") from exc
        if not math.isfinite(raw_start) or not math.isfinite(end):
            raise HTTPException(status_code=422, detail="Selected segment has invalid timing")
        # Vexa can emit a slightly negative start for the first segment.  The
        # finalized master has no negative timeline, so bind extraction and
        # save-time revalidation to the exact same 0-second-clamped range.
        start = max(0.0, raw_start)
        if end <= start:
            raise HTTPException(status_code=422, detail="Selected segment has invalid timing")
        ranges.append((start, end))
    ranges.sort(key=lambda item: (item[0], item[1]))

    previous_end: Optional[float] = None
    for start, end in ranges:
        if previous_end is not None and start < previous_end - 1e-6:
            raise HTTPException(
                status_code=422,
                detail="Selected segments contain overlapping audio; choose non-overlapping segments",
            )
        previous_end = end

    duration = sum(end - start for start, end in ranges)
    if not _strict_duration(duration):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Selected speech must total {VOICEPRINT_MIN_CLIP_SECONDS:g}-"
                f"{VOICEPRINT_MAX_CLIP_SECONDS:g} seconds without truncation"
            ),
        )
    return session_uids.pop(), ranges, duration


async def _prepare_selected_segment_clip(
    meeting: Meeting,
    segment_ids: List[str],
    db: AsyncSession,
    *,
    expected_source_fingerprint: Optional[str] = None,
) -> Tuple[bytes, float, str, str]:
    meeting_id = meeting.id
    rows = (await db.execute(
        select(Transcription).where(
            Transcription.meeting_id == meeting.id,
            Transcription.segment_id.in_(segment_ids),
        )
    )).scalars().all()
    by_id = {str(getattr(row, "segment_id", "") or ""): row for row in rows}
    if len(by_id) != len(segment_ids) or any(
        segment_id not in by_id for segment_id in segment_ids
    ):
        raise HTTPException(
            status_code=409,
            detail="Selected transcript segments changed; reload and select them again",
        )

    ordered_rows = [by_id[segment_id] for segment_id in segment_ids]
    session_uid, ranges, selected_duration = _selected_ranges(ordered_rows)
    source = await _resolve_session_master(meeting, session_uid, db)
    if source is None:
        raise HTTPException(
            status_code=422,
            detail="No finalized audio master is available for the selected session",
        )
    source_fingerprint = _source_fingerprint(source)
    selection_binding = _selection_binding_fingerprint(
        segment_ids,
        session_uid,
        ranges,
        source_fingerprint,
    )

    # The following download/ffmpeg work can be slow.  End the read-only
    # transaction before it begins; this session uses expire_on_commit=False,
    # and persistence (for enrollment) starts a fresh transaction afterwards.
    await db.rollback()
    if (
        expected_source_fingerprint is not None
        and not secrets.compare_digest(source_fingerprint, expected_source_fingerprint)
    ):
        raise HTTPException(
            status_code=409,
            detail="Selected audio source changed after preview; review it again before enrollment",
        )

    try:
        wav_bytes = await extract_exact_clip_wav(source, ranges)
        actual_duration = wav_duration_seconds(wav_bytes)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning(
            "selected voiceprint preview preparation failed for meeting %s",
            meeting_id,
        )
        raise HTTPException(status_code=503, detail="Selected audio could not be prepared") from exc

    if not _strict_duration(actual_duration):
        raise HTTPException(
            status_code=422,
            detail="Extracted audio duration is outside the voiceprint enrollment limit",
        )
    if abs(actual_duration - selected_duration) > _SOURCE_DURATION_MISMATCH_TOLERANCE_SECONDS:
        raise HTTPException(
            status_code=422,
            detail="Selected transcript timing does not match the finalized audio",
        )
    return wav_bytes, actual_duration, source_fingerprint, selection_binding


async def _revalidate_selected_segment_enrollment(
    *,
    meeting_id: int,
    segment_ids: List[str],
    expected_source_fingerprint: str,
    expected_selection_binding: str,
    current_user: Any,
    db: AsyncSession,
) -> None:
    """Lock and revalidate ownership/selection/source immediately before save.

    Preview extraction and embedding intentionally run outside a DB
    transaction.  Reacquiring the meeting row lock here prevents a deferred
    transcript replacement from committing between this check and the
    consent/voiceprint transaction.
    """
    meeting = await _require_owned_ended_meeting(
        meeting_id,
        current_user,
        db,
        for_update=True,
    )
    rows = (await db.execute(
        select(Transcription).where(
            Transcription.meeting_id == meeting_id,
            Transcription.segment_id.in_(segment_ids),
        )
    )).scalars().all()
    by_id = {str(getattr(row, "segment_id", "") or ""): row for row in rows}
    if len(by_id) != len(segment_ids) or any(segment_id not in by_id for segment_id in segment_ids):
        raise HTTPException(
            status_code=409,
            detail="Selected transcript segments changed; review the audio again before enrollment",
        )

    ordered_rows = [by_id[segment_id] for segment_id in segment_ids]
    session_uid, ranges, _duration = _selected_ranges(ordered_rows)
    source = await _resolve_session_master(meeting, session_uid, db)
    if source is None:
        raise HTTPException(
            status_code=409,
            detail="Selected audio source changed; review it again before enrollment",
        )
    source_fingerprint = _source_fingerprint(source)
    selection_binding = _selection_binding_fingerprint(
        segment_ids,
        session_uid,
        ranges,
        source_fingerprint,
    )
    if (
        not secrets.compare_digest(source_fingerprint, expected_source_fingerprint)
        or not secrets.compare_digest(selection_binding, expected_selection_binding)
    ):
        raise HTTPException(
            status_code=409,
            detail="Selected audio changed during enrollment; review it again before retrying",
        )


def _decode_direct_audio(audio_base64: str) -> bytes:
    try:
        audio_bytes = base64.b64decode(audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="audio_base64 is invalid") from exc
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="audio_base64 is empty")
    if len(audio_bytes) > VOICEPRINT_MAX_DIRECT_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"audio exceeds the {VOICEPRINT_MAX_DIRECT_AUDIO_BYTES} byte limit",
        )
    return audio_bytes


def _enabled_crypto_or_503():
    crypto = get_voiceprint_crypto()
    if not crypto.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="Voiceprint feature is disabled (VOICEPRINT_ENCRYPTION_KEY not configured)",
        )
    return crypto


async def _persist_explicit_voiceprint(
    *,
    db: AsyncSession,
    current_user: Any,
    crypto: Any,
    embedding: List[float],
    display_name: str,
    source: str,
    source_meeting_id: Optional[int],
    clip_seconds: float,
    selection_count: Optional[int],
) -> Dict[str, Any]:
    """Persist consent + encrypted embedding in one transaction."""
    profile = (await db.execute(
        select(SpeakerProfile).where(
            SpeakerProfile.user_id == current_user.id,
            SpeakerProfile.display_name == display_name,
        )
    )).scalars().first()
    if profile is None:
        profile = SpeakerProfile(user_id=current_user.id, display_name=display_name)
        # Keep any caller-held meeting row lock alive if two enrollments race
        # to create the same profile.  A full session rollback here would
        # silently discard the save-time ownership/source revalidation.
        savepoint = await db.begin_nested()
        try:
            db.add(profile)
            await db.flush()
        except IntegrityError:
            await savepoint.rollback()
            profile = (await db.execute(
                select(SpeakerProfile).where(
                    SpeakerProfile.user_id == current_user.id,
                    SpeakerProfile.display_name == display_name,
                )
            )).scalars().first()
            if profile is None:
                raise HTTPException(
                    status_code=409,
                    detail="Speaker profile enrollment conflict — please retry",
                )
        else:
            await savepoint.commit()

    now = datetime.utcnow()
    consent = VoiceprintConsent(
        user_id=current_user.id,
        subject_profile_id=profile.id,
        scope=VOICEPRINT_CONSENT_SCOPE,
        method="explicit_enroll",
        consented_at=now,
        consented_by=current_user.id,
    )
    db.add(consent)
    await db.flush()

    voiceprint = Voiceprint(
        user_id=current_user.id,
        profile_id=profile.id,
        consent_id=consent.id,
        key_id=crypto.key_id,
        embedding_encrypted=crypto.encrypt_embedding(embedding),
        embedding_dim=len(embedding),
        embedding_model="speechbrain-ecapa-tdnn",
        source=source,
        source_meeting_id=source_meeting_id,
    )
    db.add(voiceprint)

    detail: Dict[str, Any] = {
        "method": "explicit_enroll",
        "scope": VOICEPRINT_CONSENT_SCOPE,
        "source": source,
        "clip_seconds": round(clip_seconds, 3),
        "audio_review_confirmed": True,
        "consent_confirmed": True,
    }
    if selection_count is not None:
        detail["selection_count"] = selection_count
    db.add(VoiceprintAuditLog(
        user_id=current_user.id,
        event="enroll",
        actor_user_id=current_user.id,
        subject_profile_id=profile.id,
        meeting_id=source_meeting_id,
        detail=detail,
    ))

    await db.commit()
    await db.refresh(profile)
    await db.refresh(voiceprint)
    await db.refresh(consent)
    return {
        "profile_id": profile.id,
        "display_name": profile.display_name,
        "voiceprint_id": voiceprint.id,
        "consent_id": consent.id,
    }


@router.post(
    "/voiceprints/preview-from-segments",
    summary="Preview an exact, explicitly selected voice sample",
    dependencies=[Depends(get_user_and_token)],
)
async def preview_from_segments(
    req: SelectedSegmentsRequest,
    response: Response,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    meeting = await _require_owned_ended_meeting(req.meeting_id, current_user, db)
    (
        wav_bytes,
        duration,
        source_fingerprint,
        _selection_binding,
    ) = await _prepare_selected_segment_clip(meeting, req.segment_ids, db)
    response.headers["Cache-Control"] = "no-store"
    return {
        "audio_base64": base64.b64encode(wav_bytes).decode("ascii"),
        "media_format": "wav",
        "content_type": "audio/wav",
        "duration_seconds": round(duration, 3),
        "selection_count": len(req.segment_ids),
        "clip_sha256": hashlib.sha256(wav_bytes).hexdigest(),
        "source_fingerprint": source_fingerprint,
    }


@router.post(
    "/voiceprints/enroll-from-segments",
    summary="Enroll a voiceprint from human-reviewed transcript segments",
    dependencies=[Depends(get_user_and_token)],
)
async def enroll_from_segments(
    req: EnrollFromSegmentsRequest,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    crypto = _enabled_crypto_or_503()
    meeting = await _require_owned_ended_meeting(req.meeting_id, current_user, db)
    (
        wav_bytes,
        duration,
        _source_fingerprint_value,
        selection_binding,
    ) = await _prepare_selected_segment_clip(
        meeting,
        req.segment_ids,
        db,
        expected_source_fingerprint=req.source_fingerprint,
    )
    actual_sha256 = hashlib.sha256(wav_bytes).hexdigest()
    if not secrets.compare_digest(actual_sha256, req.clip_sha256):
        raise HTTPException(
            status_code=409,
            detail="Selected audio changed after preview; review it again before enrollment",
        )
    try:
        embedding = await embed_wav_bytes(wav_bytes)
    except VoiceprintServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await _revalidate_selected_segment_enrollment(
        meeting_id=req.meeting_id,
        segment_ids=req.segment_ids,
        expected_source_fingerprint=req.source_fingerprint,
        expected_selection_binding=selection_binding,
        current_user=current_user,
        db=db,
    )
    return await _persist_explicit_voiceprint(
        db=db,
        current_user=current_user,
        crypto=crypto,
        embedding=embedding,
        display_name=req.display_name,
        source="explicit_selected_audio",
        source_meeting_id=req.meeting_id,
        clip_seconds=duration,
        selection_count=len(req.segment_ids),
    )


@router.post(
    "/voiceprints/enroll-from-audio",
    summary="Enroll a voiceprint from a reviewed direct recording",
    dependencies=[Depends(get_user_and_token)],
)
async def enroll_from_audio(
    request: Request,
    auth_data: tuple = Depends(get_user_and_token),
    _admission: None = Depends(_admit_direct_enrollment_request),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    req = await _parse_direct_audio_request(request)
    crypto = _enabled_crypto_or_503()
    audio_bytes = _decode_direct_audio(req.audio_base64)
    try:
        wav_bytes = await normalize_direct_audio_to_wav(audio_bytes, req.media_format)
        duration = wav_duration_seconds(wav_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("direct voiceprint audio preparation failed")
        raise HTTPException(status_code=503, detail="Recorded audio could not be prepared") from exc
    if not _strict_duration(duration):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Recorded speech must be {VOICEPRINT_MIN_CLIP_SECONDS:g}-"
                f"{VOICEPRINT_MAX_CLIP_SECONDS:g} seconds"
            ),
        )
    try:
        embedding = await embed_wav_bytes(wav_bytes)
    except VoiceprintServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return await _persist_explicit_voiceprint(
        db=db,
        current_user=current_user,
        crypto=crypto,
        embedding=embedding,
        display_name=req.display_name,
        source="explicit_prerecorded_audio",
        source_meeting_id=None,
        clip_seconds=duration,
        selection_count=None,
    )


@router.post(
    "/voiceprints/enroll-from-cluster",
    summary="Disabled legacy cluster-based voiceprint enrollment",
    dependencies=[Depends(get_user_and_token)],
)
async def enroll_from_cluster(
    _auth_data: tuple = Depends(get_user_and_token),
):
    raise HTTPException(
        status_code=410,
        detail="Cluster-based enrollment is disabled; review selected audio first",
    )


@router.get(
    "/speaker-profiles",
    summary="List the current user's enrolled speaker profiles",
    dependencies=[Depends(get_user_and_token)],
)
async def list_speaker_profiles(
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    rows = (await db.execute(
        select(SpeakerProfile, sa_func.count(Voiceprint.id))
        .outerjoin(Voiceprint, Voiceprint.profile_id == SpeakerProfile.id)
        .where(SpeakerProfile.user_id == current_user.id)
        .group_by(SpeakerProfile.id)
        .order_by(SpeakerProfile.display_name)
    )).all()
    # embeddings are never included — profiles are the only unencrypted
    # surface (display_name, count), consistent with plan §6 minimal payload.
    return {
        "profiles": [
            {
                "id": profile.id,
                "display_name": profile.display_name,
                "created_at": profile.created_at,
                "voiceprint_count": count,
            }
            for profile, count in rows
        ]
    }


@router.delete(
    "/speaker-profiles/{profile_id}",
    summary="Delete a speaker profile and all its voiceprints/consents",
    dependencies=[Depends(get_user_and_token)],
)
async def delete_speaker_profile(
    profile_id: int,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    profile = (await db.execute(
        select(SpeakerProfile).where(
            SpeakerProfile.id == profile_id, SpeakerProfile.user_id == current_user.id,
        )
    )).scalars().first()
    if not profile:
        raise HTTPException(status_code=404, detail="Speaker profile not found")

    # Recorded BEFORE the delete so subject_profile_id is still valid at
    # insert time; the FK's ON DELETE SET NULL (not CASCADE) means this row
    # survives the cascade below with subject_profile_id nulled out (PII
    # policy §4/§6 — the audit trail outlives the biometric data).
    db.add(VoiceprintAuditLog(
        user_id=current_user.id,
        event="delete",
        actor_user_id=current_user.id,
        subject_profile_id=profile.id,
        detail={"display_name": profile.display_name},
    ))
    # FK cascades handle the rest: profile -> voiceprints (CASCADE) and
    # profile -> voiceprint_consents (CASCADE) -> voiceprints.consent_id
    # (also CASCADE) both remove every voiceprint row, order-independent
    # (plan §3 / Codex critique Adopted-Recommended Change #1).
    await db.delete(profile)
    await db.commit()
    return {"message": f"Speaker profile {profile_id} deleted"}
