"""Issue #27 Phase 4 — DB-level consent invariant + delete cascade.

Plan §3 AC: `voiceprints.consent_id` is NOT NULL + FK, so a voiceprint
without a consent row is impossible at the DB level, not just an
application-level check. These tests exercise the actual SQLAlchemy table
DDL against an in-memory SQLite database (PRAGMA foreign_keys=ON) — a real
constraint violation, not a mock assertion — for exactly the three tables
involved (SpeakerProfile/VoiceprintConsent/Voiceprint have no JSONB/Postgres-
only columns, unlike Meeting/Transcription, so they're portable to SQLite
DDL as-is).
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from meeting_api.models import SpeakerProfile, Voiceprint, VoiceprintConsent


@pytest.fixture
def sqlite_session():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    # Only the three tables under test — Meeting/Transcription/
    # VoiceprintAuditLog carry JSONB + Postgres-only server_default text()
    # that SQLite DDL cannot compile.
    for table in (SpeakerProfile.__table__, VoiceprintConsent.__table__, Voiceprint.__table__):
        table.metadata.create_all(engine, tables=[table])

    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_profile(session, *, user_id=1, display_name="Alice"):
    profile = SpeakerProfile(user_id=user_id, display_name=display_name)
    session.add(profile)
    session.flush()
    return profile


def _make_consent(session, profile, *, user_id=1):
    consent = VoiceprintConsent(
        user_id=user_id,
        subject_profile_id=profile.id,
        scope="会議内自動命名",
        method="explicit_enroll",
        consented_by=user_id,
    )
    session.add(consent)
    session.flush()
    return consent


def test_voiceprint_insert_without_consent_id_is_rejected(sqlite_session):
    """NOT NULL alone makes a consent-less insert impossible."""
    profile = _make_profile(sqlite_session)
    vp = Voiceprint(
        user_id=1, profile_id=profile.id, consent_id=None,
        embedding_encrypted=b"ciphertext", source="explicit_enroll",
    )
    sqlite_session.add(vp)
    with pytest.raises(IntegrityError):
        sqlite_session.flush()


def test_voiceprint_insert_with_nonexistent_consent_id_is_rejected(sqlite_session):
    """FK alone makes a forged/dangling consent_id impossible, independent
    of the NOT NULL check above."""
    profile = _make_profile(sqlite_session)
    vp = Voiceprint(
        user_id=1, profile_id=profile.id, consent_id=999_999,
        embedding_encrypted=b"ciphertext", source="explicit_enroll",
    )
    sqlite_session.add(vp)
    with pytest.raises(IntegrityError):
        sqlite_session.flush()


def test_voiceprint_insert_with_valid_consent_succeeds(sqlite_session):
    profile = _make_profile(sqlite_session)
    consent = _make_consent(sqlite_session, profile)
    vp = Voiceprint(
        user_id=1, profile_id=profile.id, consent_id=consent.id,
        embedding_encrypted=b"ciphertext", source="explicit_enroll",
    )
    sqlite_session.add(vp)
    sqlite_session.flush()  # no exception
    assert vp.id is not None


def test_deleting_profile_cascades_to_voiceprints_and_consents_at_db_level(sqlite_session):
    """Raw SQL DELETE (bypassing the ORM's own cascade="all, delete-orphan")
    proves the FK's ON DELETE CASCADE — not just Python-side cascade logic —
    removes both the voiceprint and the consent row (plan §3 / Codex
    critique Adopted-Recommended Change #1: two independent cascade paths,
    profile_id and subject_profile_id, both CASCADE)."""
    profile = _make_profile(sqlite_session)
    consent = _make_consent(sqlite_session, profile)
    vp = Voiceprint(
        user_id=1, profile_id=profile.id, consent_id=consent.id,
        embedding_encrypted=b"ciphertext", source="explicit_enroll",
    )
    sqlite_session.add(vp)
    sqlite_session.commit()

    sqlite_session.execute(text("DELETE FROM speaker_profiles WHERE id = :id"), {"id": profile.id})
    sqlite_session.commit()

    assert sqlite_session.execute(text("SELECT COUNT(*) FROM voiceprints")).scalar() == 0
    assert sqlite_session.execute(text("SELECT COUNT(*) FROM voiceprint_consents")).scalar() == 0
