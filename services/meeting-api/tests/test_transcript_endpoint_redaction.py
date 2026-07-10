"""Issue #27 Phase 4 plan §6 — the transcript endpoint builds `data` by hand
(not through MeetingResponse's field_serializer), so it needs its own
redaction call. This exercises the full route."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from .conftest import TEST_MEETING_ID, TEST_NATIVE_MEETING_ID, TEST_PLATFORM, MockResult, make_meeting


class _EmptyScalarsResult:
    def scalars(self):
        return self

    def all(self):
        return []


@pytest.mark.asyncio
async def test_transcript_endpoint_strips_speaker_suggestions_from_data(client, mock_db, mock_redis):
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status="completed",
        data={
            "notes": "keep me",
            "webhook_secret": "shh",
            "speaker_suggestions": {"lane:aaaaaaaaaa:spk0": {"candidate_display_name": "田中"}},
        },
    )
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),   # meeting lookup
        _EmptyScalarsResult(),   # MeetingSession query
        _EmptyScalarsResult(),   # Transcription query
    ])
    mock_redis.hgetall = AsyncMock(return_value={})

    resp = await client.get(f"/transcripts/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == {"notes": "keep me"}
    assert "speaker_suggestions" not in body["data"]
    assert "webhook_secret" not in body["data"]
