"""Issue #27 Phase 4 — /voiceprints/enroll-from-cluster, /speaker-profiles.

Verification contract: enroll-from-cluster happy path writes the profile,
consent, and voiceprint in ONE transaction (plan §7 "acceptance = consent"),
and the whole feature 503s when VOICEPRINT_ENCRYPTION_KEY is unset.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from meeting_api.models import SpeakerProfile
from meeting_api.voiceprint_crypto import VoiceprintCrypto

from .conftest import TEST_MEETING_ID, MockResult, make_meeting


def _enabled_crypto():
    crypto = VoiceprintCrypto(key=None)
    crypto._fernet = MagicMock()
    crypto.encrypt_embedding = MagicMock(return_value=b"cipher-bytes")
    return crypto


def _wire_id_assignment(mock_db):
    """The real ORM objects created inside the endpoint (SpeakerProfile,
    VoiceprintConsent, Voiceprint) need .id populated after flush/refresh,
    same as the shared `_fake_refresh` in conftest.py does for Meeting."""
    added = []
    counter = {"n": 100}

    def _add(obj):
        added.append(obj)

    def _assign(obj=None, *args, **kwargs):
        targets = [obj] if obj is not None else added
        for target in targets:
            if getattr(target, "id", None) is None:
                counter["n"] += 1
                target.id = counter["n"]

    mock_db.add = MagicMock(side_effect=_add)
    mock_db.flush = AsyncMock(side_effect=_assign)
    mock_db.refresh = AsyncMock(side_effect=_assign)
    return added


def _meeting_with_master(**data_overrides):
    data = {
        "recordings": [{
            "id": 1001,
            "session_uid": "sess-1",
            "status": "completed",
            "media_files": [{
                "id": 2001,
                "type": "audio",
                "format": "wav",
                "storage_backend": "minio",
                "storage_path": "recordings/5/1001/sess-1/audio/master.wav",
                "finalized_by": "recording_finalizer.master",
            }],
        }],
    }
    data.update(data_overrides)
    return make_meeting(id=TEST_MEETING_ID, status="completed", data=data)


@pytest.mark.asyncio
async def test_enroll_from_cluster_returns_503_when_encryption_disabled(client, mock_db):
    with patch("meeting_api.voiceprints.get_voiceprint_crypto",
               return_value=VoiceprintCrypto(key=None)):
        resp = await client.post(
            "/voiceprints/enroll-from-cluster",
            json={"meeting_id": TEST_MEETING_ID, "cluster_id": "mixed-1", "display_name": "田中"},
        )
    assert resp.status_code == 503
    mock_db.execute.assert_not_called()  # fails fast, before any query


@pytest.mark.asyncio
async def test_enroll_from_cluster_happy_path_one_transaction(client, mock_db):
    meeting = _meeting_with_master()
    added = _wire_id_assignment(mock_db)
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),                       # meeting lookup
        MockResult([(0.0, 10.0, "mixed-1")]),         # Transcription rows for the cluster
        MockResult([]),                               # no existing profile with this display_name
    ])
    mock_db.commit = AsyncMock()

    with patch("meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto()), \
         patch("meeting_api.voiceprints.embed_clip_from_ranges",
               new=AsyncMock(return_value=[0.1] * 192)):
        resp = await client.post(
            "/voiceprints/enroll-from-cluster",
            json={"meeting_id": TEST_MEETING_ID, "cluster_id": "mixed-1", "display_name": "田中"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "田中"
    assert body["profile_id"] is not None
    assert body["consent_id"] is not None
    assert body["voiceprint_id"] is not None

    by_type = {type(obj).__name__: obj for obj in added}
    assert set(by_type) == {"SpeakerProfile", "VoiceprintConsent", "VoiceprintAuditLog", "Voiceprint"}

    profile = by_type["SpeakerProfile"]
    consent = by_type["VoiceprintConsent"]
    voiceprint = by_type["Voiceprint"]
    audit = by_type["VoiceprintAuditLog"]

    # Consent invariant, application-level mirror of the DB constraint:
    # the voiceprint's consent_id points at THIS consent row.
    assert voiceprint.consent_id == consent.id
    assert voiceprint.profile_id == profile.id
    assert consent.subject_profile_id == profile.id
    assert consent.method == "implicit_suggest_accept"
    assert voiceprint.embedding_encrypted == b"cipher-bytes"
    assert audit.event == "enroll"
    assert audit.subject_profile_id == profile.id

    # One transaction: exactly one commit call for the whole enroll flow.
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_enroll_from_cluster_concurrent_duplicate_reselects_existing_profile(client, mock_db):
    """BUG-009 regression: enroll-from-cluster's find-or-create SELECT has no
    row lock. Simulate a concurrent request winning the race — the profile
    INSERT's flush raises IntegrityError against
    uq_speaker_profile_user_display_name (models.py) — and assert the
    handler rolls back, re-SELECTs the winner's profile, and completes with
    ITS id rather than 500ing or fabricating a second profile."""
    meeting = _meeting_with_master()
    added = _wire_id_assignment(mock_db)
    existing_profile = MagicMock(spec=SpeakerProfile, id=777, display_name="田中", user_id=5)

    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),                        # meeting lookup
        MockResult([(0.0, 10.0, "mixed-1")]),          # Transcription rows for the cluster
        MockResult([]),                                 # find-or-create: no existing profile seen yet
        MockResult([existing_profile]),                 # re-SELECT after IntegrityError: the winner's row
    ])
    mock_db.commit = AsyncMock()

    flush_calls = {"n": 0}
    inner_assign = mock_db.flush.side_effect  # the id-assigning side_effect from _wire_id_assignment

    async def _flush_first_call_conflicts(*args, **kwargs):
        flush_calls["n"] += 1
        if flush_calls["n"] == 1:
            raise IntegrityError(
                "duplicate key value violates unique constraint "
                '"uq_speaker_profile_user_display_name"',
                None, None,
            )
        return inner_assign()

    mock_db.flush = AsyncMock(side_effect=_flush_first_call_conflicts)

    with patch("meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto()), \
         patch("meeting_api.voiceprints.embed_clip_from_ranges",
               new=AsyncMock(return_value=[0.1] * 192)):
        resp = await client.post(
            "/voiceprints/enroll-from-cluster",
            json={"meeting_id": TEST_MEETING_ID, "cluster_id": "mixed-1", "display_name": "田中"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["profile_id"] == 777
    mock_db.rollback.assert_awaited_once()

    # No SECOND SpeakerProfile must have been created for this identity —
    # only the one attempt that lost the race (and was rolled back) exists
    # in `added`; the response must key off the re-SELECTed winner.
    profile_adds = [obj for obj in added if type(obj).__name__ == "SpeakerProfile"]
    assert len(profile_adds) == 1
    assert profile_adds[0] is not existing_profile


@pytest.mark.asyncio
async def test_enroll_from_cluster_404_when_cluster_not_found(client, mock_db):
    meeting = _meeting_with_master()
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult([]),  # no Transcription rows for this cluster
    ])
    with patch("meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto()):
        resp = await client.post(
            "/voiceprints/enroll-from-cluster",
            json={"meeting_id": TEST_MEETING_ID, "cluster_id": "no-such-cluster", "display_name": "田中"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_enroll_from_cluster_service_unavailable_bubbles_as_503(client, mock_db):
    from meeting_api.voiceprint_matching import VoiceprintServiceUnavailable

    meeting = _meeting_with_master()
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult([(0.0, 10.0, "mixed-1")]),
    ])
    with patch("meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto()), \
         patch("meeting_api.voiceprints.embed_clip_from_ranges",
               new=AsyncMock(side_effect=VoiceprintServiceUnavailable("down"))):
        resp = await client.post(
            "/voiceprints/enroll-from-cluster",
            json={"meeting_id": TEST_MEETING_ID, "cluster_id": "mixed-1", "display_name": "田中"},
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_list_speaker_profiles_excludes_embeddings(client, mock_db):
    mock_db.execute = AsyncMock(return_value=MockResult([
        (MagicMock(id=1, display_name="田中", created_at=None), 2),
    ]))
    resp = await client.get("/speaker-profiles")
    assert resp.status_code == 200
    body = resp.json()
    assert body["profiles"][0]["display_name"] == "田中"
    assert body["profiles"][0]["voiceprint_count"] == 2
    assert "embedding" not in str(body)


@pytest.mark.asyncio
async def test_delete_speaker_profile_cascades_and_audits(client, mock_db):
    profile = MagicMock(spec=SpeakerProfile, id=1, display_name="田中")
    mock_db.execute = AsyncMock(return_value=MockResult([profile]))
    mock_db.delete = AsyncMock()
    added = []
    mock_db.add = MagicMock(side_effect=added.append)

    resp = await client.delete("/speaker-profiles/1")

    assert resp.status_code == 200
    mock_db.delete.assert_awaited_once_with(profile)
    assert len(added) == 1
    assert added[0].event == "delete"
    assert added[0].subject_profile_id == 1


@pytest.mark.asyncio
async def test_delete_speaker_profile_404_for_other_users_profile(client, mock_db):
    mock_db.execute = AsyncMock(return_value=MockResult([]))
    resp = await client.delete("/speaker-profiles/999")
    assert resp.status_code == 404
