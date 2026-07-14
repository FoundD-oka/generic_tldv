from unittest.mock import AsyncMock, patch

import pytest

from meeting_api import post_meeting as post_meeting_module
from meeting_api.participant_roster import (
    merge_participant_roster_data,
    normalize_participant_roster,
)

from .conftest import make_meeting


RAW_ROSTER = [
    {
        "participant_id": "google-panel-alice",
        "participant_name": "Alice",
        "first_seen_at_ms": 1000,
        "last_seen_at_ms": 5000,
        "source": "participant_tile",
    },
    {
        "participant_id": "google-internal-alice",
        "participant_name": "Alice",
        "first_seen_at_ms": 500,
        "last_seen_at_ms": 6000,
        "source": "people_panel",
    },
    {
        "participant_id": "google-internal-bob",
        "participant_name": "Bob",
        "first_seen_at_ms": 2000,
        "last_seen_at_ms": 3000,
        "source": "people_panel",
    },
]


def test_normalize_roster_merges_observations_and_hides_platform_ids():
    roster = normalize_participant_roster(RAW_ROSTER, id_salt="meeting:1")

    assert [entry["participant_name"] for entry in roster] == ["Alice", "Bob"]
    assert roster[0]["first_seen_at_ms"] == 500
    assert roster[0]["last_seen_at_ms"] == 6000
    assert roster[0]["source"] == "people_panel"
    assert all(entry["participant_id"].startswith("participant:") for entry in roster)
    assert all("google-internal" not in entry["participant_id"] for entry in roster)


def test_roster_projection_preserves_manually_supplied_participants():
    data = merge_participant_roster_data(
        {"participants": ["手動設定した参加者"]},
        RAW_ROSTER,
        id_salt="meeting:1",
    )

    assert data["participants"] == ["手動設定した参加者"]
    assert data["observed_participants"] == ["Alice", "Bob"]
    assert len(data["participant_roster"]) == 2


def test_capacity_still_allows_existing_participant_updates():
    full_roster = [
        {
            "participant_id": f"participant-{index}",
            "participant_name": f"Participant {index}",
            "first_seen_at_ms": 1000,
            "last_seen_at_ms": 1000,
            "source": "participant_tile",
        }
        for index in range(250)
    ]
    full_roster.append({
        "participant_id": "panel-participant-0",
        "participant_name": "Participant 0",
        "first_seen_at_ms": 1000,
        "last_seen_at_ms": 9999,
        "source": "people_panel",
    })

    roster = normalize_participant_roster(full_roster, id_salt="meeting:1")

    assert len(roster) == 250
    updated = next(item for item in roster if item["participant_name"] == "Participant 0")
    assert updated["last_seen_at_ms"] == 9999
    assert updated["source"] == "people_panel"


def test_future_timestamps_and_special_names_are_safe():
    roster = normalize_participant_roster([{
        "participant_id": "special-name",
        "participant_name": "__proto__",
        "first_seen_at_ms": 10**30,
        "last_seen_at_ms": 10**30,
        "source": "people_panel",
    }], id_salt="meeting:1")

    assert roster[0]["participant_name"] == "__proto__"
    assert roster[0]["first_seen_at_ms"] == 0
    assert roster[0]["last_seen_at_ms"] == 0


def test_raw_ids_are_opaque_per_meeting():
    first = normalize_participant_roster(RAW_ROSTER, id_salt="meeting:1")
    second = normalize_participant_roster(RAW_ROSTER, id_salt="meeting:2")

    assert first[0]["participant_id"] != second[0]["participant_id"]


class _Response:
    status_code = 200

    def __init__(self, segments):
        self._segments = segments

    def json(self):
        return self._segments


class _Client:
    def __init__(self, segments):
        self._segments = segments

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        return _Response(self._segments)


@pytest.mark.asyncio
async def test_post_meeting_projects_roster_even_when_transcript_is_empty(mock_db):
    meeting = make_meeting(id=1, data={"participant_roster": RAW_ROSTER})

    with patch.object(post_meeting_module.httpx, "AsyncClient", return_value=_Client([])), \
         patch("sqlalchemy.orm.attributes.flag_modified"):
        result = await post_meeting_module.aggregate_transcription(meeting, mock_db)

    assert result is True
    assert meeting.data["participants"] == ["Alice", "Bob"]
    assert meeting.data["participants_source"] == "participant_roster"
    mock_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_post_meeting_keeps_speaker_fallback_for_legacy_bots(mock_db):
    meeting = make_meeting(data={})
    segments = [
        {"speaker": "Bob", "language": "ja"},
        {"speaker": "Alice", "language": "ja"},
    ]

    with patch.object(post_meeting_module.httpx, "AsyncClient", return_value=_Client(segments)), \
         patch("sqlalchemy.orm.attributes.flag_modified"):
        result = await post_meeting_module.aggregate_transcription(meeting, mock_db)

    assert result is True
    assert meeting.data["participants"] == ["Alice", "Bob"]
    assert meeting.data["participants_source"] == "transcript_speakers"
