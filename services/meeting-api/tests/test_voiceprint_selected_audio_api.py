"""Explicit voiceprint enrollment from reviewed segments/direct audio."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from meeting_api.models import (
    SpeakerProfile,
    Transcription,
    Voiceprint,
    VoiceprintAuditLog,
    VoiceprintConsent,
)
from meeting_api.voiceprint_crypto import VoiceprintCrypto
from meeting_api import voiceprints as voiceprints_mod

from .conftest import TEST_MEETING_ID, TEST_SESSION_UID, MockResult, make_meeting


def _enabled_crypto():
    crypto = VoiceprintCrypto(key=None)
    crypto._fernet = MagicMock()
    crypto.encrypt_embedding = MagicMock(return_value=b"cipher-bytes")
    return crypto


def _meeting_with_session_master(*, session_uid: str = TEST_SESSION_UID, recordings=None):
    if recordings is None:
        recordings = [
            {
                "id": 1001,
                "session_uid": session_uid,
                "status": "completed",
                "media_files": [
                    {
                        "id": 2001,
                        "type": "audio",
                        "format": "wav",
                        "storage_backend": "minio",
                        "storage_path": f"recordings/5/1001/{session_uid}/audio/master.wav",
                        "finalized_by": "recording_finalizer.master",
                    }
                ],
            }
        ]
    return make_meeting(
        id=TEST_MEETING_ID,
        status="completed",
        data={"recordings": recordings},
    )


def _segment(segment_id: str, start: float, end: float, *, session_uid: str = TEST_SESSION_UID):
    row = MagicMock(spec=Transcription)
    row.segment_id = segment_id
    row.start_time = start
    row.end_time = end
    row.session_uid = session_uid
    return row


def _source_fingerprint(
    *,
    storage_path: str | None = None,
    media_format: str = "wav",
    storage_backend: str = "minio",
    session_uid: str = TEST_SESSION_UID,
) -> str:
    path = storage_path or f"recordings/5/1001/{session_uid}/audio/master.wav"
    canonical = json.dumps(
        (storage_backend, path, media_format, session_uid),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _wire_existing_profile_persistence(mock_db):
    profile = MagicMock(spec=SpeakerProfile)
    profile.id = 7
    profile.user_id = 5
    profile.display_name = "田中"
    added = []

    def add(obj):
        if isinstance(obj, VoiceprintConsent):
            obj.id = 12
        elif isinstance(obj, Voiceprint):
            obj.id = 99
        added.append(obj)

    mock_db.add = MagicMock(side_effect=add)
    mock_db.flush = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.commit = AsyncMock()
    return profile, added


@pytest.mark.asyncio
async def test_preview_from_segments_returns_exact_wav_hash_without_persisting(client, mock_db):
    meeting = _meeting_with_session_master()
    rows = [_segment("seg-1", 0.0, 5.0), _segment("seg-2", 6.0, 11.0)]
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]), MockResult(rows), MockResult([]),
    ])
    wav = b"deterministic-preview-wav"
    events = []

    async def rollback():
        events.append("rollback")

    async def extract_clip(*_args):
        events.append("extract")
        return wav

    mock_db.rollback = AsyncMock(side_effect=rollback)

    with patch(
        "meeting_api.voiceprints.extract_exact_clip_wav",
        new=AsyncMock(side_effect=extract_clip),
    ) as extract, patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=10.0,
    ):
        response = await client.post(
            "/voiceprints/preview-from-segments",
            json={"meeting_id": TEST_MEETING_ID, "segment_ids": ["seg-1", "seg-2"]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "audio_base64": base64.b64encode(wav).decode("ascii"),
        "media_format": "wav",
        "content_type": "audio/wav",
        "duration_seconds": 10.0,
        "selection_count": 2,
        "clip_sha256": hashlib.sha256(wav).hexdigest(),
        "source_fingerprint": _source_fingerprint(),
    }
    assert response.headers["cache-control"] == "no-store"
    assert f"recordings/5/1001/{TEST_SESSION_UID}" not in response.text
    source, ranges = extract.await_args.args
    assert source.session_uid == TEST_SESSION_UID
    assert source.storage_path.endswith(f"/{TEST_SESSION_UID}/audio/master.wav")
    assert ranges == [(0.0, 5.0), (6.0, 11.0)]
    assert events == ["rollback", "extract"]
    mock_db.rollback.assert_awaited_once()
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_clamps_negative_vexa_start_to_exact_master_audio_zero(client, mock_db):
    meeting = _meeting_with_session_master()
    rows = [_segment("seg-1", -0.8, 12.2)]
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]), MockResult(rows), MockResult([]),
    ])
    wav = b"negative-start-preview-wav"

    with patch(
        "meeting_api.voiceprints.extract_exact_clip_wav",
        new=AsyncMock(return_value=wav),
    ) as extract, patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=12.2,
    ):
        response = await client.post(
            "/voiceprints/preview-from-segments",
            json={"meeting_id": TEST_MEETING_ID, "segment_ids": ["seg-1"]},
        )

    assert response.status_code == 200
    assert response.json()["duration_seconds"] == 12.2
    assert extract.await_args.args[1] == [(0.0, 12.2)]


@pytest.mark.asyncio
async def test_preview_resolves_master_for_selected_session_not_first_recording(client, mock_db):
    recordings = [
        {
            "id": 1,
            "session_uid": "wrong-session",
            "status": "completed",
            "media_files": [{
                "type": "audio", "format": "wav", "storage_backend": "minio",
                "storage_path": "recordings/wrong/audio/master.wav",
                "finalized_by": "recording_finalizer.master",
            }],
        },
        {
            "id": 2,
            "session_uid": TEST_SESSION_UID,
            "status": "completed",
            "media_files": [{
                "type": "audio", "format": "webm", "storage_backend": "minio",
                "storage_path": "recordings/right/audio/master.webm",
                "finalized_by": "recording_finalizer.master",
            }],
        },
    ]
    meeting = _meeting_with_session_master(recordings=recordings)
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]), MockResult([_segment("seg-1", 0.0, 10.0)]), MockResult([]),
    ])

    with patch(
        "meeting_api.voiceprints.extract_exact_clip_wav",
        new=AsyncMock(return_value=b"wav"),
    ) as extract, patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=10.0,
    ):
        response = await client.post(
            "/voiceprints/preview-from-segments",
            json={"meeting_id": TEST_MEETING_ID, "segment_ids": ["seg-1"]},
        )

    assert response.status_code == 200
    assert extract.await_args.args[0].storage_path == "recordings/right/audio/master.webm"
    assert response.json()["source_fingerprint"] == _source_fingerprint(
        storage_path="recordings/right/audio/master.webm",
        media_format="webm",
    )


@pytest.mark.asyncio
async def test_preview_rejects_multiple_distinct_masters_for_selected_session(
    client, mock_db,
):
    meeting = _meeting_with_session_master(recordings=[
        {
            "id": 1,
            "session_uid": TEST_SESSION_UID,
            "status": "completed",
            "media_files": [{
                "type": "audio", "format": "wav", "storage_backend": "minio",
                "storage_path": "recordings/first/audio/master.wav",
                "finalized_by": "recording_finalizer.master",
            }],
        },
        {
            "id": 2,
            "session_uid": TEST_SESSION_UID,
            "status": "completed",
            "media_files": [{
                "type": "audio", "format": "wav", "storage_backend": "minio",
                "storage_path": "recordings/second/audio/master.wav",
                "finalized_by": "recording_finalizer.master",
            }],
        },
    ])
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]), MockResult([_segment("seg-1", 0.0, 10.0)]),
    ])
    with patch(
        "meeting_api.voiceprints.extract_exact_clip_wav", new=AsyncMock(),
    ) as extract:
        response = await client.post(
            "/voiceprints/preview-from-segments",
            json={"meeting_id": TEST_MEETING_ID, "segment_ids": ["seg-1"]},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Multiple finalized audio masters exist for the selected session"
    )
    assert "recordings/" not in response.text
    extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_hides_unowned_or_missing_meeting(client, mock_db):
    mock_db.execute = AsyncMock(return_value=MockResult([]))

    response = await client.post(
        "/voiceprints/preview-from-segments",
        json={"meeting_id": TEST_MEETING_ID, "segment_ids": ["seg-1"]},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Meeting not found"


@pytest.mark.asyncio
async def test_preview_rejects_active_meeting_before_reading_segments(client, mock_db):
    meeting = make_meeting(id=TEST_MEETING_ID, status="running")
    mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

    response = await client.post(
        "/voiceprints/preview-from-segments",
        json={"meeting_id": TEST_MEETING_ID, "segment_ids": ["seg-1"]},
    )

    assert response.status_code == 409
    assert "only after the meeting has ended" in response.json()["detail"]
    assert mock_db.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows, expected_detail",
    [
        (
            [_segment("seg-1", -2.0, 5.0), _segment("seg-2", 4.0, 9.0)],
            "overlapping audio",
        ),
        (
            [_segment("seg-1", 0.0, 3.0), _segment("seg-2", 4.0, 5.0)],
            "must total 5-30 seconds",
        ),
        (
            [_segment("seg-1", 0.0, 5.0), _segment("seg-2", 6.0, 11.0, session_uid="other")],
            "one recording session",
        ),
    ],
)
async def test_preview_rejects_unsafe_segment_selection(
    client, mock_db, rows, expected_detail,
):
    meeting = _meeting_with_session_master()
    mock_db.execute = AsyncMock(side_effect=[MockResult([meeting]), MockResult(rows)])
    with patch(
        "meeting_api.voiceprints.extract_exact_clip_wav", new=AsyncMock(),
    ) as extract:
        response = await client.post(
            "/voiceprints/preview-from-segments",
            json={"meeting_id": TEST_MEETING_ID, "segment_ids": ["seg-1", "seg-2"]},
        )
    assert response.status_code == 422
    assert expected_detail in response.json()["detail"]
    extract.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "end"),
    [
        (float("nan"), 10.0),
        (float("inf"), 10.0),
        (float("-inf"), 10.0),
        (-2.0, 0.0),
        (-2.0, -0.1),
    ],
)
async def test_preview_rejects_non_finite_or_non_positive_clamped_ranges(
    client, mock_db, start, end,
):
    meeting = _meeting_with_session_master()
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]), MockResult([_segment("seg-1", start, end)]),
    ])

    with patch(
        "meeting_api.voiceprints.extract_exact_clip_wav", new=AsyncMock(),
    ) as extract:
        response = await client.post(
            "/voiceprints/preview-from-segments",
            json={"meeting_id": TEST_MEETING_ID, "segment_ids": ["seg-1"]},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "Selected segment has invalid timing"
    extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_rejects_stale_or_replaced_segment_ids(client, mock_db):
    meeting = _meeting_with_session_master()
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]), MockResult([_segment("seg-1", 0.0, 10.0)]),
    ])
    response = await client.post(
        "/voiceprints/preview-from-segments",
        json={"meeting_id": TEST_MEETING_ID, "segment_ids": ["seg-1", "missing"]},
    )
    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"meeting_id": TEST_MEETING_ID, "segment_ids": ["seg-1", "seg-1"]},
        {"meeting_id": TEST_MEETING_ID, "segment_ids": ["seg-1"], "start": 0, "end": 10},
        {"meeting_id": TEST_MEETING_ID, "segment_ids": [f"seg-{i}" for i in range(21)]},
    ],
)
async def test_preview_contract_rejects_duplicates_ranges_and_oversized_selection(
    client, mock_db, payload,
):
    response = await client.post("/voiceprints/preview-from-segments", json=payload)
    assert response.status_code == 422
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_enroll_from_segments_hash_binds_reviewed_audio_and_persists_once(client, mock_db):
    meeting = _meeting_with_session_master()
    rows = [_segment("seg-1", 0.0, 5.0), _segment("seg-2", 6.0, 11.0)]
    profile, added = _wire_existing_profile_persistence(mock_db)
    mock_db.execute = AsyncMock(side_effect=[
        # Initial selection/source check, then the lock-bound save-time
        # revalidation, then the existing profile lookup.
        MockResult([meeting]), MockResult(rows), MockResult([]),
        MockResult([meeting]), MockResult(rows), MockResult([]),
        MockResult([profile]),
    ])
    wav = b"reviewed-wav"

    with patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.extract_exact_clip_wav", new=AsyncMock(return_value=wav),
    ), patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=10.0,
    ), patch(
        "meeting_api.voiceprints.embed_wav_bytes",
        new=AsyncMock(return_value=[0.1] * 192),
    ) as embed:
        response = await client.post(
            "/voiceprints/enroll-from-segments",
            json={
                "meeting_id": TEST_MEETING_ID,
                "segment_ids": ["seg-1", "seg-2"],
                "display_name": "田中",
                "clip_sha256": hashlib.sha256(wav).hexdigest(),
                "source_fingerprint": _source_fingerprint(),
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "profile_id": 7,
        "display_name": "田中",
        "voiceprint_id": 99,
        "consent_id": 12,
    }
    embed.assert_awaited_once_with(wav)
    by_type = {type(item).__name__: item for item in added}
    assert set(by_type) == {"VoiceprintConsent", "Voiceprint", "VoiceprintAuditLog"}
    assert by_type["Voiceprint"].source == "explicit_selected_audio"
    assert by_type["Voiceprint"].source_meeting_id == TEST_MEETING_ID
    assert by_type["Voiceprint"].embedding_encrypted == b"cipher-bytes"
    audit = by_type["VoiceprintAuditLog"]
    assert audit.detail["selection_count"] == 2
    assert audit.detail["clip_seconds"] == 10.0
    assert "segment_ids" not in audit.detail
    assert "audio_base64" not in str(audit.detail)
    assert "clip_sha256" not in audit.detail
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_enroll_clamps_negative_start_for_exact_extract_and_save_revalidation(
    client, mock_db,
):
    meeting = _meeting_with_session_master()
    initial_rows = [_segment("seg-1", -0.8, 12.2)]
    # A different negative Vexa offset still identifies the same master-audio
    # range after clamping, so save-time binding must remain stable.
    revalidated_rows = [_segment("seg-1", -4.0, 12.2)]
    profile, added = _wire_existing_profile_persistence(mock_db)
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]), MockResult(initial_rows), MockResult([]),
        MockResult([meeting]), MockResult(revalidated_rows), MockResult([]),
        MockResult([profile]),
    ])
    wav = b"negative-start-enrollment-wav"

    with patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.extract_exact_clip_wav",
        new=AsyncMock(return_value=wav),
    ) as extract, patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=12.2,
    ), patch(
        "meeting_api.voiceprints.embed_wav_bytes",
        new=AsyncMock(return_value=[0.1] * 192),
    ):
        response = await client.post(
            "/voiceprints/enroll-from-segments",
            json={
                "meeting_id": TEST_MEETING_ID,
                "segment_ids": ["seg-1"],
                "display_name": "田中",
                "clip_sha256": hashlib.sha256(wav).hexdigest(),
                "source_fingerprint": _source_fingerprint(),
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )

    assert response.status_code == 200
    assert extract.await_args.args[1] == [(0.0, 12.2)]
    audit = next(item for item in added if isinstance(item, VoiceprintAuditLog))
    assert audit.detail["clip_seconds"] == 12.2
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_enroll_from_segments_revalidates_selection_after_embedding_before_save(
    client, mock_db,
):
    meeting = _meeting_with_session_master()
    initial_rows = [_segment("seg-1", 0.0, 10.0)]
    changed_rows = [_segment("seg-1", 0.0, 11.0)]
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]), MockResult(initial_rows), MockResult([]),
        MockResult([meeting]), MockResult(changed_rows), MockResult([]),
    ])
    wav = b"reviewed-wav"

    with patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.extract_exact_clip_wav", new=AsyncMock(return_value=wav),
    ), patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=10.0,
    ), patch(
        "meeting_api.voiceprints.embed_wav_bytes",
        new=AsyncMock(return_value=[0.1] * 192),
    ) as embed:
        response = await client.post(
            "/voiceprints/enroll-from-segments",
            json={
                "meeting_id": TEST_MEETING_ID,
                "segment_ids": ["seg-1"],
                "display_name": "田中",
                "clip_sha256": hashlib.sha256(wav).hexdigest(),
                "source_fingerprint": _source_fingerprint(),
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )

    assert response.status_code == 409
    assert "changed during enrollment" in response.json()["detail"]
    embed.assert_awaited_once_with(wav)
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "expected_status"),
    [
        ("ownership_lost", 404),
        ("transcript_requeued", 409),
        ("source_replaced", 409),
    ],
)
async def test_enroll_from_segments_revalidates_meeting_and_source_after_embedding(
    client, mock_db, change, expected_status,
):
    initial_meeting = _meeting_with_session_master()
    rows = [_segment("seg-1", 0.0, 10.0)]
    execute_results = [
        MockResult([initial_meeting]), MockResult(rows), MockResult([]),
    ]
    if change == "ownership_lost":
        execute_results.append(MockResult([]))
    elif change == "transcript_requeued":
        requeued = _meeting_with_session_master()
        requeued.data = {
            **requeued.data,
            "final_transcription": {"status": "queued"},
        }
        execute_results.append(MockResult([requeued]))
    else:
        replaced = _meeting_with_session_master(recordings=[{
            "id": 2002,
            "session_uid": TEST_SESSION_UID,
            "status": "completed",
            "media_files": [{
                "type": "audio",
                "format": "wav",
                "storage_backend": "minio",
                "storage_path": (
                    f"recordings/5/2002/{TEST_SESSION_UID}/audio/master.wav"
                ),
                "finalized_by": "recording_finalizer.master",
            }],
        }])
        execute_results.extend([
            MockResult([replaced]), MockResult(rows), MockResult([]),
        ])
    mock_db.execute = AsyncMock(side_effect=execute_results)
    wav = b"reviewed-wav"

    with patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.extract_exact_clip_wav", new=AsyncMock(return_value=wav),
    ), patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=10.0,
    ), patch(
        "meeting_api.voiceprints.embed_wav_bytes",
        new=AsyncMock(return_value=[0.1] * 192),
    ) as embed:
        response = await client.post(
            "/voiceprints/enroll-from-segments",
            json={
                "meeting_id": TEST_MEETING_ID,
                "segment_ids": ["seg-1"],
                "display_name": "田中",
                "clip_sha256": hashlib.sha256(wav).hexdigest(),
                "source_fingerprint": _source_fingerprint(),
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )

    assert response.status_code == expected_status
    embed.assert_awaited_once_with(wav)
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_create_race_uses_savepoint_without_releasing_revalidation_lock(
    client, mock_db,
):
    meeting = _meeting_with_session_master()
    rows = [_segment("seg-1", 0.0, 10.0)]
    existing_profile = MagicMock(spec=SpeakerProfile)
    existing_profile.id = 7
    existing_profile.user_id = 5
    existing_profile.display_name = "田中"
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]), MockResult(rows), MockResult([]),
        MockResult([meeting]), MockResult(rows), MockResult([]),
        MockResult([]), MockResult([existing_profile]),
    ])
    savepoint = AsyncMock()
    mock_db.begin_nested = AsyncMock(return_value=savepoint)
    mock_db.flush = AsyncMock(side_effect=[
        IntegrityError("INSERT speaker_profiles", {}, Exception("duplicate")),
        None,
    ])
    added = []

    def add(obj):
        if isinstance(obj, VoiceprintConsent):
            obj.id = 12
        elif isinstance(obj, Voiceprint):
            obj.id = 99
        added.append(obj)

    mock_db.add = MagicMock(side_effect=add)
    wav = b"reviewed-wav"

    with patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.extract_exact_clip_wav", new=AsyncMock(return_value=wav),
    ), patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=10.0,
    ), patch(
        "meeting_api.voiceprints.embed_wav_bytes",
        new=AsyncMock(return_value=[0.1] * 192),
    ):
        response = await client.post(
            "/voiceprints/enroll-from-segments",
            json={
                "meeting_id": TEST_MEETING_ID,
                "segment_ids": ["seg-1"],
                "display_name": "田中",
                "clip_sha256": hashlib.sha256(wav).hexdigest(),
                "source_fingerprint": _source_fingerprint(),
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["profile_id"] == 7
    revalidation_meeting_stmt = mock_db.execute.await_args_list[3].args[0]
    assert revalidation_meeting_stmt._for_update_arg is not None
    mock_db.begin_nested.assert_awaited_once()
    savepoint.rollback.assert_awaited_once()
    savepoint.commit.assert_not_awaited()
    mock_db.rollback.assert_awaited_once()  # initial read-only extraction boundary only
    mock_db.commit.assert_awaited_once()
    assert sum(isinstance(item, Voiceprint) for item in added) == 1


@pytest.mark.asyncio
async def test_enroll_from_segments_rejects_preview_hash_mismatch_before_embedding(client, mock_db):
    meeting = _meeting_with_session_master()
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]), MockResult([_segment("seg-1", 0.0, 10.0)]), MockResult([]),
    ])
    with patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.extract_exact_clip_wav",
        new=AsyncMock(return_value=b"changed-wav"),
    ), patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=10.0,
    ), patch(
        "meeting_api.voiceprints.embed_wav_bytes", new=AsyncMock(),
    ) as embed:
        response = await client.post(
            "/voiceprints/enroll-from-segments",
            json={
                "meeting_id": TEST_MEETING_ID,
                "segment_ids": ["seg-1"],
                "display_name": "田中",
                "clip_sha256": "0" * 64,
                "source_fingerprint": _source_fingerprint(),
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )
    assert response.status_code == 409
    embed.assert_not_awaited()
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_enroll_from_segments_rejects_replaced_source_before_extract_or_persist(
    client, mock_db,
):
    new_path = f"recordings/5/2002/{TEST_SESSION_UID}/audio/master.wav"
    meeting = _meeting_with_session_master(recordings=[{
        "id": 2002,
        "session_uid": TEST_SESSION_UID,
        "status": "completed",
        "media_files": [{
            "type": "audio", "format": "wav", "storage_backend": "minio",
            "storage_path": new_path,
            "finalized_by": "recording_finalizer.master",
        }],
    }])
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]), MockResult([_segment("seg-1", 0.0, 10.0)]), MockResult([]),
    ])
    with patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.extract_exact_clip_wav", new=AsyncMock(),
    ) as extract, patch(
        "meeting_api.voiceprints.embed_wav_bytes", new=AsyncMock(),
    ) as embed:
        response = await client.post(
            "/voiceprints/enroll-from-segments",
            json={
                "meeting_id": TEST_MEETING_ID,
                "segment_ids": ["seg-1"],
                "display_name": "田中",
                "clip_sha256": "0" * 64,
                "source_fingerprint": _source_fingerprint(),
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Selected audio source changed after preview; review it again before enrollment"
    )
    assert new_path not in response.text
    mock_db.rollback.assert_awaited_once()
    extract.assert_not_awaited()
    embed.assert_not_awaited()
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmation", ["audio_review_confirmed", "consent_confirmed"])
async def test_enroll_from_segments_requires_both_confirmations(client, mock_db, confirmation):
    payload = {
        "meeting_id": TEST_MEETING_ID,
        "segment_ids": ["seg-1"],
        "display_name": "田中",
        "clip_sha256": "0" * 64,
        "source_fingerprint": "1" * 64,
        "audio_review_confirmed": True,
        "consent_confirmed": True,
    }
    payload[confirmation] = False
    response = await client.post("/voiceprints/enroll-from-segments", json=payload)
    assert response.status_code == 422
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_enroll_from_audio_normalizes_bounds_and_persists_without_meeting(client, mock_db):
    profile, added = _wire_existing_profile_persistence(mock_db)
    mock_db.execute = AsyncMock(return_value=MockResult([profile]))
    raw = b"browser-webm"
    normalized_wav = b"normalized-wav"

    with patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.normalize_direct_audio_to_wav",
        new=AsyncMock(return_value=normalized_wav),
    ) as normalize, patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=12.5,
    ), patch(
        "meeting_api.voiceprints.embed_wav_bytes",
        new=AsyncMock(return_value=[0.2] * 192),
    ) as embed:
        response = await client.post(
            "/voiceprints/enroll-from-audio",
            json={
                "audio_base64": base64.b64encode(raw).decode("ascii"),
                "media_format": "audio/webm;codecs=opus",
                "display_name": "田中",
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )

    assert response.status_code == 200
    normalize.assert_awaited_once_with(raw, "webm")
    embed.assert_awaited_once_with(normalized_wav)
    by_type = {type(item).__name__: item for item in added}
    assert by_type["Voiceprint"].source == "explicit_prerecorded_audio"
    assert by_type["Voiceprint"].source_meeting_id is None
    assert by_type["VoiceprintAuditLog"].meeting_id is None
    assert "selection_count" not in by_type["VoiceprintAuditLog"].detail
    mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_audio_admission_rejects_before_body_read_then_accepts_after_release(
    client, mock_db,
):
    payload = {
        "audio_base64": base64.b64encode(b"audio").decode("ascii"),
        "media_format": "wav",
        "display_name": "田中",
        "audio_review_confirmed": True,
        "consent_confirmed": True,
    }
    normalize_entered = asyncio.Event()
    normalize_release = asyncio.Event()
    normalize_calls = 0

    async def normalize(_audio, _format):
        nonlocal normalize_calls
        normalize_calls += 1
        if normalize_calls == 1:
            normalize_entered.set()
            await normalize_release.wait()
        return b"normalized-wav"

    rejected_body_reads = 0

    async def rejected_body():
        nonlocal rejected_body_reads
        rejected_body_reads += 1
        yield json.dumps(payload).encode("utf-8")

    gate = voiceprints_mod._ImmediateAdmissionGate(1)
    with patch(
        "meeting_api.voiceprints._DIRECT_ENROLLMENT_ADMISSION_GATE", gate,
    ), patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.normalize_direct_audio_to_wav",
        new=AsyncMock(side_effect=normalize),
    ), patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=10.0,
    ), patch(
        "meeting_api.voiceprints.embed_wav_bytes",
        new=AsyncMock(return_value=[0.2] * 192),
    ), patch(
        "meeting_api.voiceprints._persist_explicit_voiceprint",
        new=AsyncMock(return_value={"ok": True}),
    ):
        first = asyncio.create_task(
            client.post("/voiceprints/enroll-from-audio", json=payload)
        )
        await asyncio.wait_for(normalize_entered.wait(), timeout=1.0)

        busy = await client.post(
            "/voiceprints/enroll-from-audio",
            content=rejected_body(),
            headers={"content-type": "application/json"},
        )
        assert busy.status_code == 429
        assert busy.headers["retry-after"] == "1"
        assert busy.headers["cache-control"] == "no-store"
        assert rejected_body_reads == 0

        normalize_release.set()
        assert (await asyncio.wait_for(first, timeout=1.0)).status_code == 200
        accepted = await client.post("/voiceprints/enroll-from-audio", json=payload)

    assert accepted.status_code == 200
    assert normalize_calls == 2
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_direct_audio_admission_releases_on_cancel_and_exception(client, mock_db):
    payload = {
        "audio_base64": base64.b64encode(b"audio").decode("ascii"),
        "media_format": "wav",
        "display_name": "田中",
        "audio_review_confirmed": True,
        "consent_confirmed": True,
    }
    normalize_entered = asyncio.Event()
    normalize_calls = 0

    async def cancel_then_fail_then_succeed(_audio, _format):
        nonlocal normalize_calls
        normalize_calls += 1
        if normalize_calls == 1:
            normalize_entered.set()
            await asyncio.Event().wait()
        if normalize_calls == 2:
            raise RuntimeError("simulated normalize failure")
        return b"normalized-wav"

    gate = voiceprints_mod._ImmediateAdmissionGate(1)
    with patch(
        "meeting_api.voiceprints._DIRECT_ENROLLMENT_ADMISSION_GATE", gate,
    ), patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.normalize_direct_audio_to_wav",
        new=AsyncMock(side_effect=cancel_then_fail_then_succeed),
    ), patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=10.0,
    ), patch(
        "meeting_api.voiceprints.embed_wav_bytes",
        new=AsyncMock(return_value=[0.2] * 192),
    ), patch(
        "meeting_api.voiceprints._persist_explicit_voiceprint",
        new=AsyncMock(return_value={"ok": True}),
    ):
        cancelled = asyncio.create_task(
            client.post("/voiceprints/enroll-from-audio", json=payload)
        )
        await asyncio.wait_for(normalize_entered.wait(), timeout=1.0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(cancelled, timeout=1.0)

        failed = await client.post("/voiceprints/enroll-from-audio", json=payload)
        assert failed.status_code == 503
        accepted = await client.post("/voiceprints/enroll-from-audio", json=payload)

    assert accepted.status_code == 200
    assert normalize_calls == 3
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, expected_status",
    [
        (
            {
                "audio_base64": "not-base64!",
                "media_format": "webm",
                "display_name": "田中",
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
            422,
        ),
        (
            {
                "audio_base64": base64.b64encode(b"audio").decode("ascii"),
                "media_format": "exe",
                "display_name": "田中",
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
            422,
        ),
    ],
)
async def test_enroll_from_audio_rejects_invalid_input(client, mock_db, payload, expected_status):
    with patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ):
        response = await client.post("/voiceprints/enroll-from-audio", json=payload)
    assert response.status_code == expected_status
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_enroll_from_audio_sanitizes_validation_error_without_echoing_input(
    client, mock_db,
):
    sentinel = "SENSITIVE-AUDIO-NAME-" * 20
    with patch(
        "meeting_api.voiceprints.normalize_direct_audio_to_wav", new=AsyncMock(),
    ) as normalize:
        response = await client.post(
            "/voiceprints/enroll-from-audio",
            json={
                "audio_base64": base64.b64encode(b"audio").decode("ascii"),
                "media_format": "wav",
                "display_name": sentinel,
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Invalid direct voiceprint enrollment request",
    }
    assert sentinel not in response.text
    normalize.assert_not_awaited()
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_enroll_from_audio_rejects_oversized_body_without_echo_or_normalize(
    client, mock_db,
):
    sentinel = "SENSITIVE-AUDIO-SENTINEL"
    with patch(
        "meeting_api.voiceprints._DIRECT_AUDIO_REQUEST_MAX_BYTES", 64,
    ), patch(
        "meeting_api.voiceprints.normalize_direct_audio_to_wav", new=AsyncMock(),
    ) as normalize:
        response = await client.post(
            "/voiceprints/enroll-from-audio",
            json={
                "audio_base64": sentinel * 10,
                "media_format": "wav",
                "display_name": "田中",
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )

    assert response.status_code == 413
    assert sentinel not in response.text
    normalize.assert_not_awaited()
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload_update",
    [
        {"audio_review_confirmed": False},
        {"consent_confirmed": False},
        {"source": "client-supplied"},
    ],
)
async def test_enroll_from_audio_requires_confirmations_and_forbids_extra_fields(
    client, mock_db, payload_update,
):
    payload = {
        "audio_base64": base64.b64encode(b"audio").decode("ascii"),
        "media_format": "wav",
        "display_name": "田中",
        "audio_review_confirmed": True,
        "consent_confirmed": True,
    }
    payload.update(payload_update)

    response = await client.post("/voiceprints/enroll-from-audio", json=payload)

    assert response.status_code == 422
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_enroll_from_audio_enforces_decoded_byte_limit(client, mock_db):
    with patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.VOICEPRINT_MAX_DIRECT_AUDIO_BYTES", 3,
    ), patch(
        "meeting_api.voiceprints.normalize_direct_audio_to_wav", new=AsyncMock(),
    ) as normalize:
        response = await client.post(
            "/voiceprints/enroll-from-audio",
            json={
                "audio_base64": base64.b64encode(b"four").decode("ascii"),
                "media_format": "wav",
                "display_name": "田中",
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )
    assert response.status_code == 413
    normalize.assert_not_awaited()
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [4.999, 30.001])
async def test_enroll_from_audio_rejects_out_of_range_normalized_duration(
    client, mock_db, duration,
):
    with patch(
        "meeting_api.voiceprints.get_voiceprint_crypto", return_value=_enabled_crypto(),
    ), patch(
        "meeting_api.voiceprints.normalize_direct_audio_to_wav",
        new=AsyncMock(return_value=b"wav"),
    ), patch(
        "meeting_api.voiceprints.wav_duration_seconds", return_value=duration,
    ), patch(
        "meeting_api.voiceprints.embed_wav_bytes", new=AsyncMock(),
    ) as embed:
        response = await client.post(
            "/voiceprints/enroll-from-audio",
            json={
                "audio_base64": base64.b64encode(b"audio").decode("ascii"),
                "media_format": "wav",
                "display_name": "田中",
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        )
    assert response.status_code == 422
    embed.assert_not_awaited()
    mock_db.execute.assert_not_called()
