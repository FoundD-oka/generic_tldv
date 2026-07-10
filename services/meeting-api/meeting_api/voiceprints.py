"""Voiceprint enrollment/consent/profile API (issue #27 Phase 4).

Endpoints:
  POST   /voiceprints/enroll-from-cluster  — explicit or implicit enrollment
  GET    /speaker-profiles                 — list the user's profiles
  DELETE /speaker-profiles/{id}            — delete a profile (FK cascade)

`enroll-from-cluster` creates the profile (if needed), the consent row, and
the voiceprint row in ONE transaction (plan §7: "acceptance = consent" per
PII policy — the consent record and the voiceprint it authorizes must never
be split across commits, or a crash between them could leave one without
the other even though the DB-level NOT NULL FK prevents the voiceprint half
of that from ever landing without a consent row).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .auth import get_user_and_token
from .database import get_db
from .final_transcription import (
    LaneTranscriptionFallback,
    _lane_master_sources,
    find_final_transcription_source,
)
from .models import Meeting, SpeakerProfile, Transcription, Voiceprint, VoiceprintAuditLog, VoiceprintConsent
from .voiceprint_crypto import get_voiceprint_crypto
from .voiceprint_matching import (
    InsufficientAudioError,
    VoiceprintServiceUnavailable,
    cluster_local_time_ranges,
    embed_clip_from_ranges,
    resolve_cluster_audio_source,
)

logger = logging.getLogger("meeting_api.voiceprints")
router = APIRouter()


class EnrollFromClusterRequest(BaseModel):
    meeting_id: int
    cluster_id: str
    display_name: str = Field(..., min_length=1, max_length=255)


@router.post(
    "/voiceprints/enroll-from-cluster",
    summary="Enroll a voiceprint from one meeting's speaker cluster",
    dependencies=[Depends(get_user_and_token)],
)
async def enroll_from_cluster(
    req: EnrollFromClusterRequest,
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    crypto = get_voiceprint_crypto()
    if not crypto.is_enabled():
        raise HTTPException(
            status_code=503,
            detail="Voiceprint feature is disabled (VOICEPRINT_ENCRYPTION_KEY not configured)",
        )

    meeting = (await db.execute(
        select(Meeting).where(Meeting.id == req.meeting_id, Meeting.user_id == current_user.id)
    )).scalars().first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    rows = (await db.execute(
        select(Transcription.start_time, Transcription.end_time, Transcription.speaker_cluster)
        .where(
            Transcription.meeting_id == req.meeting_id,
            Transcription.speaker_cluster == req.cluster_id,
        )
    )).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Cluster not found for this meeting")
    segments = [
        {"start": start, "end": end, "speaker_cluster": cluster}
        for start, end, cluster in rows
    ]

    mixed_source = await find_final_transcription_source(meeting, db)
    try:
        lane_sources = _lane_master_sources(meeting)
    except LaneTranscriptionFallback:
        lane_sources = []

    source = resolve_cluster_audio_source(
        req.cluster_id, mixed_source=mixed_source, lane_sources=lane_sources,
    )
    if source is None:
        raise HTTPException(status_code=422, detail="No audio source available for this cluster")

    offset = getattr(source, "start_offset_seconds", 0.0)
    ranges = cluster_local_time_ranges(req.cluster_id, segments, offset_seconds=offset)

    try:
        embedding = await embed_clip_from_ranges(source, ranges)
    except InsufficientAudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except VoiceprintServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    profile = (await db.execute(
        select(SpeakerProfile).where(
            SpeakerProfile.user_id == current_user.id,
            SpeakerProfile.display_name == req.display_name,
        )
    )).scalars().first()
    if profile is None:
        profile = SpeakerProfile(user_id=current_user.id, display_name=req.display_name)
        db.add(profile)
        try:
            await db.flush()  # need profile.id for the consent FK below
        except IntegrityError:
            # BUG-009: no row lock guards the find-or-create SELECT above, so
            # a concurrent enroll-from-cluster call for the same
            # (user_id, display_name) — a double-click, or enrolling from two
            # meetings' clusters for the same person around the same time —
            # can win this race and commit first. The
            # uq_speaker_profile_user_display_name constraint (models.py)
            # catches the loser's duplicate INSERT here; roll back this
            # failed attempt and re-SELECT the profile the winner created,
            # rather than 500ing or producing two profiles for one identity.
            await db.rollback()
            profile = (await db.execute(
                select(SpeakerProfile).where(
                    SpeakerProfile.user_id == current_user.id,
                    SpeakerProfile.display_name == req.display_name,
                )
            )).scalars().first()
            if profile is None:
                # Should not happen (the constraint violation implies a row
                # with this (user_id, display_name) exists) — surface a
                # clear, retryable error instead of proceeding without a
                # profile.
                raise HTTPException(
                    status_code=409,
                    detail="Speaker profile enrollment conflict — please retry",
                )

    now = datetime.utcnow()
    consent = VoiceprintConsent(
        user_id=current_user.id,
        subject_profile_id=profile.id,
        scope="会議内自動命名",
        method="implicit_suggest_accept",
        consented_at=now,
        consented_by=current_user.id,
    )
    db.add(consent)
    await db.flush()  # need consent.id for voiceprints.consent_id (NOT NULL FK)

    voiceprint = Voiceprint(
        user_id=current_user.id,
        profile_id=profile.id,
        consent_id=consent.id,
        key_id=crypto.key_id,
        embedding_encrypted=crypto.encrypt_embedding(embedding),
        embedding_dim=len(embedding),
        embedding_model="speechbrain-ecapa-tdnn",
        source="implicit_suggest_accept",
        source_meeting_id=req.meeting_id,
    )
    db.add(voiceprint)

    db.add(VoiceprintAuditLog(
        user_id=current_user.id,
        event="enroll",
        actor_user_id=current_user.id,
        subject_profile_id=profile.id,
        meeting_id=req.meeting_id,
        detail={"cluster_id": req.cluster_id, "method": "implicit_suggest_accept"},
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
