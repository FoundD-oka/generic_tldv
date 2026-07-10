import sqlalchemy
from sqlalchemy import (
    Column, String, Text, Integer, DateTime, Float, LargeBinary,
    ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func, text
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime
from typing import Optional

from .schemas import Platform

Base = declarative_base()


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    platform = Column(String(100), nullable=False)
    platform_specific_id = Column(String(255), index=True, nullable=True)
    status = Column(String(50), nullable=False, default='requested', index=True)
    bot_container_id = Column(String(255), nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    data = Column(JSONB, nullable=False, default=text("'{}'::jsonb"))
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    transcriptions = relationship("Transcription", back_populates="meeting")
    sessions = relationship("MeetingSession", back_populates="meeting", cascade="all, delete-orphan")
    recordings = relationship("Recording", back_populates="meeting", cascade="all, delete-orphan")

    __table_args__ = (
        Index('ix_meeting_user_platform_native_id_created_at',
              'user_id', 'platform', 'platform_specific_id', 'created_at'),
        Index('ix_meeting_data_gin', 'data', postgresql_using='gin'),
    )

    @property
    def native_meeting_id(self):
        return self.platform_specific_id

    @native_meeting_id.setter
    def native_meeting_id(self, value):
        self.platform_specific_id = value

    @property
    def constructed_meeting_url(self) -> Optional[str]:
        if self.platform and self.platform_specific_id:
            passcode = (
                (self.data or {}).get('passcode')
                if isinstance(self.data, dict) else None
            )
            return Platform.construct_meeting_url(
                self.platform, self.platform_specific_id, passcode=passcode,
            )
        return None


class Transcription(Base):
    __tablename__ = "transcriptions"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, index=True)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    text = Column(Text, nullable=False)
    speaker = Column(String(255), nullable=True)
    # Acoustic cluster id from STT diarization (stt.v1 optional `speaker`
    # field). Anonymous within one deferred transcription; NULL for realtime
    # rows and non-diarizing backends.
    speaker_cluster = Column(String(64), nullable=True)
    # Original auto-assigned label (undo baseline). `speaker` may be manually
    # corrected; `speaker_auto` never is.
    speaker_auto = Column(String(255), nullable=True)
    language = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    session_uid = Column(String, nullable=True, index=True)
    segment_id = Column(String, nullable=True)

    meeting = relationship("Meeting", back_populates="transcriptions")

    __table_args__ = (
        Index('ix_transcription_meeting_start', 'meeting_id', 'start_time'),
        Index('ix_transcription_meeting_segment', 'meeting_id', 'segment_id',
              unique=True, postgresql_where=segment_id.isnot(None)),
        # online_only: startup schema-sync must NOT build this synchronously
        # on the ~507K-row prod table; scripts/migrations/
        # 20260708_add_speaker_cluster.py creates it with CONCURRENTLY.
        # Fresh installs still get it via create_all (new table = no lock).
        Index('ix_transcription_meeting_cluster', 'meeting_id', 'speaker_cluster',
              info={'online_only': True}),
    )


class MeetingSession(Base):
    __tablename__ = 'meeting_sessions'

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey('meetings.id'), nullable=False, index=True)
    session_uid = Column(String, nullable=False, index=True)
    session_start_time = Column(
        sqlalchemy.DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    meeting = relationship("Meeting", back_populates="sessions")

    __table_args__ = (
        UniqueConstraint('meeting_id', 'session_uid', name='_meeting_session_uc'),
    )


class Recording(Base):
    __tablename__ = "recordings"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    session_uid = Column(String, nullable=True, index=True)
    source = Column(String(50), nullable=False, default='bot')
    status = Column(String(50), nullable=False, default='in_progress', index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    completed_at = Column(DateTime, nullable=True)

    meeting = relationship("Meeting", back_populates="recordings")
    media_files = relationship("MediaFile", back_populates="recording", cascade="all, delete-orphan")

    __table_args__ = (
        Index('ix_recording_meeting_session', 'meeting_id', 'session_uid'),
        Index('ix_recording_user_created', 'user_id', 'created_at'),
    )


class MediaFile(Base):
    __tablename__ = "media_files"

    id = Column(Integer, primary_key=True, index=True)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=False, index=True)
    type = Column(String(50), nullable=False)
    format = Column(String(20), nullable=False)
    storage_path = Column(String(1024), nullable=False)
    storage_backend = Column(String(50), nullable=False, default='minio')
    file_size_bytes = Column(Integer, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    extra_metadata = Column(
        "metadata", JSONB, nullable=False,
        server_default=text("'{}'::jsonb"), default=lambda: {},
    )
    created_at = Column(DateTime, server_default=func.now())

    recording = relationship("Recording", back_populates="media_files")


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    external_event_id = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    start_time = Column(sqlalchemy.DateTime(timezone=True), nullable=False)
    end_time = Column(sqlalchemy.DateTime(timezone=True), nullable=True)
    meeting_url = Column(Text, nullable=True)
    platform = Column(Text, nullable=True)
    status = Column(Text, nullable=False, server_default='pending', default='pending')
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=True)
    sync_token = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    meeting = relationship("Meeting")

    __table_args__ = (
        UniqueConstraint('user_id', 'external_event_id', name='uq_calendar_event_user_ext_id'),
        Index('ix_calendar_events_start_time', 'start_time'),
        Index('ix_calendar_events_status', 'status'),
    )


# --- Issue #27 Phase 4 — voiceprint biometric PII (speaker auto-naming) ---
#
# Consent invariant (plan §3 AC, PII policy §7-3): `voiceprints.consent_id`
# is NOT NULL + FK, so inserting a voiceprint row without a corresponding
# consent row is impossible at the DB level — not just an application check.
# `voiceprints.consent_id` and `voiceprint_consents.subject_profile_id` both
# cascade ON DELETE from speaker_profiles, so `DELETE /speaker-profiles/{id}`
# fully removes voiceprints via EITHER FK path without depending on
# Postgres's internal delete ordering between the two cascade routes
# (Codex critique Adopted-Recommended Change #1).
#
# `voiceprint_audit_log` is intentionally NOT cascaded — it survives profile
# deletion (subject_profile_id → SET NULL) so a `delete` audit event remains
# queryable after the biometric data itself is gone (PII policy §4/§6).


class SpeakerProfile(Base):
    __tablename__ = "speaker_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    voiceprints = relationship("Voiceprint", back_populates="profile", cascade="all, delete-orphan")
    consents = relationship("VoiceprintConsent", back_populates="profile", cascade="all, delete-orphan")

    __table_args__ = (
        Index('ix_speaker_profile_user_id', 'user_id'),
        # BUG-009: enroll-from-cluster's find-or-create SELECT (voiceprints.py)
        # has no row lock, so two concurrent enroll calls with the same
        # display_name (double-click, or enrolling from two meetings' clusters
        # around the same time) could both pass the SELECT and both INSERT
        # without this constraint, producing duplicate identity profiles.
        UniqueConstraint('user_id', 'display_name', name='uq_speaker_profile_user_display_name'),
    )


class VoiceprintConsent(Base):
    """Consent record (PII policy §2). Required before any voiceprint for
    `subject_profile_id` may be persisted — see the NOT NULL FK on
    `Voiceprint.consent_id` below, which makes this a DB-level invariant."""
    __tablename__ = "voiceprint_consents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    subject_profile_id = Column(
        Integer, ForeignKey("speaker_profiles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    scope = Column(String(64), nullable=False, default="会議内自動命名")
    # "explicit_enroll" | "implicit_suggest_accept" — PII policy §2.
    method = Column(String(32), nullable=False)
    consented_at = Column(DateTime, server_default=func.now())
    consented_by = Column(Integer, nullable=False)
    revoked_at = Column(DateTime, nullable=True)

    profile = relationship("SpeakerProfile", back_populates="consents")
    voiceprints = relationship("Voiceprint", back_populates="consent")

    __table_args__ = (
        Index('ix_voiceprint_consent_user_profile', 'user_id', 'subject_profile_id'),
    )


class Voiceprint(Base):
    __tablename__ = "voiceprints"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    profile_id = Column(
        Integer, ForeignKey("speaker_profiles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Consent invariant — see module docstring. NOT NULL + FK (not just an
    # application-level check) means a bug in the enroll transaction cannot
    # produce an orphan voiceprint with no consent record.
    consent_id = Column(
        Integer, ForeignKey("voiceprint_consents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Reserved for future key-ring rotation (plan §4); Phase 4 always writes
    # the single fixed key id from voiceprint_crypto.VoiceprintCrypto.key_id.
    key_id = Column(String(32), nullable=False, default="default")
    embedding_encrypted = Column(LargeBinary, nullable=False)
    embedding_dim = Column(Integer, nullable=False, default=192)
    embedding_model = Column(String(64), nullable=False, default="speechbrain-ecapa-tdnn")
    # "explicit_enroll" | "implicit_suggest_accept"
    source = Column(String(32), nullable=False)
    quality = Column(Float, nullable=True)
    # Analysis-only reference (PII policy §3) — deliberately NOT a FK to
    # meetings/recordings; a voiceprint must never resolve back to audio.
    source_meeting_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    last_matched_at = Column(DateTime, nullable=True)

    profile = relationship("SpeakerProfile", back_populates="voiceprints")
    consent = relationship("VoiceprintConsent", back_populates="voiceprints")

    __table_args__ = (
        Index('ix_voiceprint_user_profile', 'user_id', 'profile_id'),
    )


class VoiceprintAuditLog(Base):
    """Standalone audit trail (PII policy §6) — deliberately NOT cascaded
    with speaker_profiles (subject_profile_id is SET NULL on delete) so a
    `delete` event stays queryable after the biometric data is gone."""
    __tablename__ = "voiceprint_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    # enroll | match_attempt | suggest | confirm | delete | skip
    event = Column(String(32), nullable=False)
    actor_user_id = Column(Integer, nullable=True)
    subject_profile_id = Column(
        Integer, ForeignKey("speaker_profiles.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    meeting_id = Column(Integer, nullable=True, index=True)
    # Score/reason metadata ONLY — embeddings and raw vectors must never be
    # written here (plan §2/§6, PII policy §6).
    detail = Column(JSONB, nullable=False, default=lambda: {}, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index('ix_voiceprint_audit_user_event', 'user_id', 'event'),
    )
