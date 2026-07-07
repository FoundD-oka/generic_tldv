from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import sync


class MockResult:
    def __init__(self, items=None):
        self._items = items or []

    def scalars(self):
        return self

    def all(self):
        return self._items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


@pytest.mark.asyncio
async def test_schedule_upcoming_bots_uses_kabosu_defaults_and_attaches_calendar_metadata(monkeypatch):
    event = SimpleNamespace(
        id=7,
        user_id=5,
        external_event_id="gcal-1",
        title="週次定例",
        start_time=datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc),
        end_time=datetime(2026, 7, 3, 11, 0, tzinfo=timezone.utc),
        meeting_url="https://meet.google.com/abc-defg-hij",
        platform="google_meet",
    )
    meeting = SimpleNamespace(id=42, data={})
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([event]),
        MockResult([meeting]),
        MockResult(),
    ])
    db.commit = AsyncMock()
    captured = {}

    class FakeResponse:
        status_code = 201
        text = "created"

        def json(self):
            return {"id": 42}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, json, headers, timeout):
            captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
            return FakeResponse()

    monkeypatch.setattr(sync.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(sync.attributes, "flag_modified", MagicMock())
    monkeypatch.setattr(sync, "KABOSU_BOT_OWNER_USER_ID", "3")

    scheduled = await sync.schedule_upcoming_bots(db)

    assert scheduled == 1
    assert captured["json"]["bot_name"] == "カボス"
    assert captured["json"]["language"] == "ja"
    assert captured["json"]["voice_agent_enabled"] is True
    assert captured["json"]["native_meeting_id"] == "abc-defg-hij"
    assert captured["headers"]["X-API-Key"] == sync.BOT_API_TOKEN
    assert captured["headers"]["X-User-ID"] == str(sync.KABOSU_BOT_OWNER_USER_ID)
    assert captured["headers"]["X-User-Scopes"] == "bot,tx,browser"
    assert meeting.data["calendar_event"]["source"] == "google_calendar"
    assert meeting.data["calendar_event"]["title"] == "週次定例"
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_schedule_upcoming_bots_keeps_ongoing_pending_events_eligible(monkeypatch):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult([]))
    db.commit = AsyncMock()

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(sync.httpx, "AsyncClient", FakeClient)

    scheduled = await sync.schedule_upcoming_bots(db)

    assert scheduled == 0
    query_text = str(db.execute.await_args_list[0].args[0])
    assert "calendar_events.end_time >" in query_text
    assert "calendar_events.start_time >=" not in query_text
