"""Legacy cluster enrollment deprecation and speaker-profile API tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api.models import SpeakerProfile

from .conftest import MockResult


@pytest.mark.asyncio
async def test_enroll_from_cluster_returns_fixed_410_without_db_or_embedding(
    client, mock_db,
):
    with patch("meeting_api.voiceprints.embed_wav_bytes", new=AsyncMock()) as embed:
        response = await client.post(
            "/voiceprints/enroll-from-cluster",
            json={
                "meeting_id": 42,
                "cluster_id": "legacy-cluster",
                "display_name": "田中",
                "consent_confirmed": True,
            },
        )

    assert response.status_code == 410
    assert response.json()["detail"] == (
        "Cluster-based enrollment is disabled; review selected audio first"
    )
    mock_db.execute.assert_not_awaited()
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_awaited()
    embed.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_speaker_profiles_excludes_embeddings(client, mock_db):
    mock_db.execute = AsyncMock(return_value=MockResult([
        (MagicMock(id=1, display_name="田中", created_at=None), 2),
    ]))
    response = await client.get("/speaker-profiles")
    assert response.status_code == 200
    body = response.json()
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

    response = await client.delete("/speaker-profiles/1")

    assert response.status_code == 200
    mock_db.delete.assert_awaited_once_with(profile)
    assert len(added) == 1
    assert added[0].event == "delete"
    assert added[0].subject_profile_id == 1


@pytest.mark.asyncio
async def test_delete_speaker_profile_404_for_other_users_profile(client, mock_db):
    mock_db.execute = AsyncMock(return_value=MockResult([]))
    response = await client.delete("/speaker-profiles/999")
    assert response.status_code == 404
