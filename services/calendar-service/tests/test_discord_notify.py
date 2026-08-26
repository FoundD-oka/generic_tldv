from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# meeting_api.database (imported by app.main) validates DB env at import time.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("DB_PASSWORD", "test_pass")

import httpx
import pytest

from app import discord_notify
from app.discord_notify import (
    DiscordDeliveryError,
    DiscordHTTPError,
    build_message,
    candidate_channels,
    handle_drive_export_completed,
    parse_model_selection,
    resolve_target,
    select_channel_with_model,
    verify_webhook_signature,
)

SECRET = "hook-secret"


class MockResult:
    def __init__(self, items=None):
        self._items = items or []

    def scalars(self):
        return self

    def first(self):
        return self._items[0] if self._items else None


def _sign(body: bytes, secret: str = SECRET, ts: int | None = None) -> dict:
    ts = int(time.time()) if ts is None else ts
    signed = f"{ts}.".encode() + body
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return {"X-Webhook-Signature": f"sha256={sig}", "X-Webhook-Timestamp": str(ts)}


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def test_verify_webhook_signature_accepts_sender_signature():
    body = b'{"event_type": "drive_export.completed"}'
    headers = _sign(body)
    assert verify_webhook_signature(
        body,
        headers["X-Webhook-Signature"],
        headers["X-Webhook-Timestamp"],
        SECRET,
        tolerance=300,
    ) is True


def test_verify_webhook_signature_rejects_tampered_body():
    body = b'{"event_type": "drive_export.completed"}'
    headers = _sign(body)
    assert verify_webhook_signature(
        body + b" ",
        headers["X-Webhook-Signature"],
        headers["X-Webhook-Timestamp"],
        SECRET,
        tolerance=300,
    ) is False


def test_verify_webhook_signature_rejects_expired_timestamp():
    body = b"{}"
    ts = int(time.time()) - 3600
    headers = _sign(body, ts=ts)
    assert verify_webhook_signature(
        body,
        headers["X-Webhook-Signature"],
        headers["X-Webhook-Timestamp"],
        SECRET,
        tolerance=300,
    ) is False


def test_verify_webhook_signature_rejects_missing_headers():
    body = b"{}"
    assert verify_webhook_signature(body, None, None, SECRET, tolerance=300) is False
    headers = _sign(body)
    assert verify_webhook_signature(
        body, headers["X-Webhook-Signature"], None, SECRET, tolerance=300
    ) is False
    assert verify_webhook_signature(
        body, None, headers["X-Webhook-Timestamp"], SECRET, tolerance=300
    ) is False


# ---------------------------------------------------------------------------
# Channel candidates
# ---------------------------------------------------------------------------


def _channels():
    return [
        {"id": "100", "name": "運営", "type": 4, "position": 0},
        {"id": "1", "name": "general", "type": 0, "position": 2, "topic": "雑談", "parent_id": "100"},
        {"id": "2", "name": "announce", "type": 5, "position": 1, "parent_id": "100"},
        {"id": "3", "name": "voice", "type": 2, "position": 3},
        {"id": "4", "name": "thread", "type": 11, "position": 4, "parent_id": "1"},
    ]


def test_candidate_channels_keeps_postable_types_and_resolves_category():
    candidates = candidate_channels(_channels())

    assert [c["id"] for c in candidates] == ["2", "1"]  # position order
    general = candidates[1]
    assert general == {"id": "1", "name": "general", "topic": "雑談", "category": "運営"}
    assert candidates[0]["topic"] == ""


# ---------------------------------------------------------------------------
# Model output parsing / target resolution
# ---------------------------------------------------------------------------


def test_parse_model_selection_accepts_strict_json():
    parsed = parse_model_selection('{"channel_id": "1", "confidence": 0.91, "reason": "定例"}')
    assert parsed == {"channel_id": "1", "confidence": 0.91, "reason": "定例"}


def test_parse_model_selection_rejects_invalid_json():
    assert parse_model_selection("not json") is None


def test_parse_model_selection_rejects_out_of_range_confidence():
    assert parse_model_selection('{"channel_id": "1", "confidence": 1.4, "reason": "x"}') is None


def test_parse_model_selection_rejects_missing_channel_id():
    assert parse_model_selection('{"confidence": 0.9, "reason": "x"}') is None


def test_resolve_target_selects_confident_known_channel():
    candidates = [{"id": "1"}, {"id": "2"}]
    assert resolve_target({"channel_id": "1", "confidence": 0.9, "reason": "r"}, candidates, "9", 0.85) == ("1", None)
    assert resolve_target({"channel_id": "1", "confidence": 0.85, "reason": "r"}, candidates, "9", 0.85) == ("1", None)


def test_resolve_target_falls_back_on_low_confidence_unknown_and_missing():
    candidates = [{"id": "1"}]
    assert resolve_target({"channel_id": "1", "confidence": 0.849, "reason": "r"}, candidates, "9", 0.85) == (
        "9", "low_confidence",
    )
    assert resolve_target({"channel_id": "7", "confidence": 0.99, "reason": "r"}, candidates, "9", 0.85) == (
        "9", "unknown_channel",
    )
    assert resolve_target(None, candidates, "9", 0.85) == ("9", "model_unavailable")


def test_build_message_suppresses_mentions_and_includes_link():
    message = build_message(
        "週次定例",
        "https://drive/file",
        {"start_time": "2026-07-03T10:00:00+09:00", "end_time": "2026-07-03T11:00:00+09:00"},
        None,
    )
    assert message["allowed_mentions"] == {"parse": []}
    assert "カボス議事録: 週次定例" in message["content"]
    assert "日時" in message["content"]
    assert "https://drive/file" in message["content"]
    assert len(message["content"]) <= 2000


def test_build_message_keeps_full_link_for_very_long_title():
    link = "https://drive.google.com/file/d/" + "l" * 120 + "/view?usp=drivesdk"
    message = build_message(
        "長い議題" * 800,
        link,
        {"start_time": "2026-07-03T10:00:00+09:00", "end_time": "2026-07-03T11:00:00+09:00"},
        "low_confidence",
    )
    assert len(message["content"]) <= 2000
    assert link in message["content"]
    assert message["content"].startswith("カボス議事録: ")
    assert "(既定チャンネルへ通知: low_confidence)" in message["content"]
    assert message["allowed_mentions"] == {"parse": []}


# ---------------------------------------------------------------------------
# Model router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_channel_with_model_sends_title_and_candidates(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    monkeypatch.setenv("KABOSU_DISCORD_ROUTER_MAX_COMPLETION_TOKENS", "640")
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {"message": {"content": '{"channel_id": "1", "confidence": 0.9, "reason": "定例"}'}}
                ]
            }

    class FakeClient:
        async def post(self, url, *, json, headers, timeout):
            captured.update({"url": url, "json": json})
            return FakeResponse()

    selection = await select_channel_with_model(
        "週次定例", "本文", [{"id": "1", "name": "general", "topic": "", "category": ""}], client=FakeClient()
    )

    assert selection == {"channel_id": "1", "confidence": 0.9, "reason": "定例"}
    user_prompt = captured["json"]["messages"][-1]["content"]
    assert "週次定例" in user_prompt
    assert '"id": "1"' in user_prompt
    assert captured["json"]["max_completion_tokens"] == 640


@pytest.mark.asyncio
async def test_select_channel_with_model_returns_none_on_timeout(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gk")

    class TimeoutClient:
        async def post(self, *args, **kwargs):
            raise httpx.TimeoutException("timeout")

    selection = await select_channel_with_model(
        "t", "c", [{"id": "1", "name": "general", "topic": "", "category": ""}], client=TimeoutClient()
    )
    assert selection is None


@pytest.mark.asyncio
async def test_select_channel_with_model_returns_none_on_invalid_json(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gk")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "選べません"}}]}

    class FakeClient:
        async def post(self, *args, **kwargs):
            return FakeResponse()

    selection = await select_channel_with_model(
        "t", "c", [{"id": "1", "name": "general", "topic": "", "category": ""}], client=FakeClient()
    )
    assert selection is None


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class FakeDiscord:
    def __init__(self, channels, failures=None):
        self.channels = channels
        self.failures = failures or {}
        self.list_calls = 0
        self.posts = []

    async def list_guild_channels(self, guild_id):
        self.list_calls += 1
        return self.channels

    async def create_message(self, channel_id, message):
        self.posts.append((channel_id, message))
        status = self.failures.get(channel_id)
        if status:
            raise DiscordHTTPError(status, "denied")
        return {"id": f"msg-{len(self.posts)}"}


class FakeClientFactory:
    def __init__(self):
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        return self

    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, *args):
        return False


def _configure_discord(monkeypatch):
    monkeypatch.setenv("KABOSU_DISCORD_BOT_TOKEN", "bot-token")
    monkeypatch.setenv("KABOSU_DISCORD_GUILD_ID", "guild-1")
    monkeypatch.setenv("KABOSU_DISCORD_DEFAULT_CHANNEL_ID", "999")
    monkeypatch.setattr(discord_notify.attributes, "flag_modified", MagicMock())


def _envelope(event_id="drive_export_hooks:drive_export.completed:42:abcdef"):
    return {
        "event_id": event_id,
        "event_type": "drive_export.completed",
        "data": {
            "meeting": {"id": 42, "platform": "google_meet", "native_meeting_id": "abc-defg-hij"},
            "calendar_event": {"title": "週次定例", "start_time": "2026-07-03T10:00:00+09:00"},
            "title": "週次定例",
            "drive_export": {"file_id": "f1", "web_view_link": "https://drive/file", "filename": "x.md"},
            "context_excerpt": "本文",
        },
    }


def _fake_db(meeting):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult([meeting] if meeting else []))
    db.commit = AsyncMock()
    return db


def _install_fakes(monkeypatch, fake_discord, selection):
    monkeypatch.setattr(discord_notify, "DiscordClient", lambda client, **kwargs: fake_discord)

    async def fake_select(title, context, candidates, *, client):
        return selection

    monkeypatch.setattr(discord_notify, "select_channel_with_model", fake_select)


@pytest.mark.asyncio
async def test_handle_posts_to_selected_channel(monkeypatch):
    _configure_discord(monkeypatch)
    meeting = SimpleNamespace(id=42, data={})
    db = _fake_db(meeting)
    fake = FakeDiscord(_channels())
    _install_fakes(monkeypatch, fake, {"channel_id": "1", "confidence": 0.92, "reason": "定例"})

    result = await handle_drive_export_completed(
        db, _envelope(), http_client_factory=FakeClientFactory()
    )

    assert result == {"status": "posted", "channel_id": "1", "fallback_reason": None}
    assert fake.list_calls == 1
    assert len(fake.posts) == 1
    channel_id, message = fake.posts[0]
    assert channel_id == "1"
    assert message["allowed_mentions"] == {"parse": []}
    assert "https://drive/file" in message["content"]
    notify = meeting.data["discord_notify"]
    assert notify["event_id"] == _envelope()["event_id"]
    assert notify["status"] == "posted"
    assert notify["selected_channel_id"] == "1"
    assert notify["confidence"] == 0.92
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_handle_falls_back_to_default_on_model_timeout(monkeypatch):
    _configure_discord(monkeypatch)
    meeting = SimpleNamespace(id=42, data={})
    db = _fake_db(meeting)
    fake = FakeDiscord(_channels())
    _install_fakes(monkeypatch, fake, None)

    result = await handle_drive_export_completed(
        db, _envelope(), http_client_factory=FakeClientFactory()
    )

    assert result["channel_id"] == "999"
    assert result["fallback_reason"] == "model_unavailable"
    assert [p[0] for p in fake.posts] == ["999"]


@pytest.mark.asyncio
async def test_handle_falls_back_to_default_on_unknown_channel(monkeypatch):
    _configure_discord(monkeypatch)
    meeting = SimpleNamespace(id=42, data={})
    db = _fake_db(meeting)
    fake = FakeDiscord(_channels())
    _install_fakes(monkeypatch, fake, {"channel_id": "777", "confidence": 0.99, "reason": "?"})

    result = await handle_drive_export_completed(
        db, _envelope(), http_client_factory=FakeClientFactory()
    )

    assert result["channel_id"] == "999"
    assert result["fallback_reason"] == "unknown_channel"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404])
async def test_handle_reposts_to_default_when_selected_channel_rejects(monkeypatch, status):
    _configure_discord(monkeypatch)
    meeting = SimpleNamespace(id=42, data={})
    db = _fake_db(meeting)
    fake = FakeDiscord(_channels(), failures={"1": status})
    _install_fakes(monkeypatch, fake, {"channel_id": "1", "confidence": 0.95, "reason": "定例"})

    result = await handle_drive_export_completed(
        db, _envelope(), http_client_factory=FakeClientFactory()
    )

    assert result["channel_id"] == "999"
    assert result["fallback_reason"] == f"selected_{status}"
    assert [p[0] for p in fake.posts] == ["1", "999"]
    assert meeting.data["discord_notify"]["fallback_reason"] == f"selected_{status}"


@pytest.mark.asyncio
async def test_handle_raises_when_default_channel_post_fails(monkeypatch):
    _configure_discord(monkeypatch)
    meeting = SimpleNamespace(id=42, data={})
    db = _fake_db(meeting)
    fake = FakeDiscord(_channels(), failures={"1": 403, "999": 500})
    _install_fakes(monkeypatch, fake, {"channel_id": "1", "confidence": 0.95, "reason": "定例"})

    with pytest.raises(DiscordDeliveryError):
        await handle_drive_export_completed(
            db, _envelope(), http_client_factory=FakeClientFactory()
        )

    assert "discord_notify" not in meeting.data
    assert db.commit.await_count == 0


@pytest.mark.asyncio
async def test_handle_skips_duplicate_event(monkeypatch):
    _configure_discord(monkeypatch)
    envelope = _envelope()
    meeting = SimpleNamespace(
        id=42,
        data={"discord_notify": {"event_id": envelope["event_id"], "status": "posted", "channel_id": "1"}},
    )
    db = _fake_db(meeting)
    fake = FakeDiscord(_channels())
    _install_fakes(monkeypatch, fake, {"channel_id": "1", "confidence": 0.95, "reason": "定例"})

    result = await handle_drive_export_completed(
        db, envelope, http_client_factory=FakeClientFactory()
    )

    assert result == {"status": "duplicate", "channel_id": "1"}
    assert fake.posts == []
    assert fake.list_calls == 0
    assert db.commit.await_count == 0


@pytest.mark.asyncio
async def test_handle_skips_when_discord_not_configured(monkeypatch):
    monkeypatch.delenv("KABOSU_DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("KABOSU_DISCORD_GUILD_ID", raising=False)
    monkeypatch.delenv("KABOSU_DISCORD_DEFAULT_CHANNEL_ID", raising=False)
    meeting = SimpleNamespace(id=42, data={})
    db = _fake_db(meeting)
    fake = FakeDiscord(_channels())
    _install_fakes(monkeypatch, fake, {"channel_id": "1", "confidence": 0.95, "reason": "定例"})
    factory = FakeClientFactory()

    result = await handle_drive_export_completed(db, _envelope(), http_client_factory=factory)

    assert result == {"status": "skipped", "reason": "discord_not_configured"}
    assert factory.calls == 0
    assert fake.list_calls == 0
    assert fake.posts == []
    assert db.execute.await_count == 0


@pytest.mark.asyncio
async def test_handle_skips_when_meeting_missing(monkeypatch):
    _configure_discord(monkeypatch)
    db = _fake_db(None)
    fake = FakeDiscord(_channels())
    _install_fakes(monkeypatch, fake, {"channel_id": "1", "confidence": 0.95, "reason": "定例"})

    result = await handle_drive_export_completed(
        db, _envelope(), http_client_factory=FakeClientFactory()
    )

    assert result == {"status": "skipped", "reason": "meeting_not_found"}
    assert fake.posts == []


# ---------------------------------------------------------------------------
# Handler: calendar title gate
# ---------------------------------------------------------------------------


class CountingModel:
    """select_channel_with_model の呼び出し回数と渡されたタイトルを記録する。"""

    def __init__(self, selection=None):
        self.selection = selection
        self.calls = 0
        self.titles = []

    async def __call__(self, title, context, candidates, *, client):
        self.calls += 1
        self.titles.append(title)
        return self.selection


def _install_counted_fakes(monkeypatch, fake_discord, selection=None):
    monkeypatch.setattr(discord_notify, "DiscordClient", lambda client, **kwargs: fake_discord)
    model = CountingModel(selection)
    monkeypatch.setattr(discord_notify, "select_channel_with_model", model)
    return model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "calendar_event",
    [
        None,
        {"start_time": "2026-07-03T10:00:00+09:00"},
        {"title": "  ", "start_time": "2026-07-03T10:00:00+09:00"},
        "週次定例",
    ],
    ids=["missing_key", "no_title", "blank_title", "not_a_dict"],
)
async def test_handle_skips_when_calendar_title_missing(monkeypatch, calendar_event):
    _configure_discord(monkeypatch)
    meeting = SimpleNamespace(id=42, data={})
    db = _fake_db(meeting)
    fake = FakeDiscord(_channels())
    model = _install_counted_fakes(
        monkeypatch, fake, {"channel_id": "1", "confidence": 0.95, "reason": "定例"}
    )
    factory = FakeClientFactory()
    envelope = _envelope()
    if calendar_event is None:
        envelope["data"].pop("calendar_event")
    else:
        envelope["data"]["calendar_event"] = calendar_event

    result = await handle_drive_export_completed(db, envelope, http_client_factory=factory)

    assert result == {"status": "skipped", "reason": "calendar_title_missing"}
    assert factory.calls == 0
    assert fake.list_calls == 0
    assert fake.posts == []
    assert model.calls == 0
    assert "discord_notify" not in meeting.data
    assert db.commit.await_count == 0


@pytest.mark.asyncio
async def test_handle_skips_when_calendar_title_mismatches(monkeypatch):
    _configure_discord(monkeypatch)
    meeting = SimpleNamespace(id=42, data={})
    db = _fake_db(meeting)
    fake = FakeDiscord(_channels())
    model = _install_counted_fakes(
        monkeypatch, fake, {"channel_id": "1", "confidence": 0.95, "reason": "定例"}
    )
    factory = FakeClientFactory()
    envelope = _envelope()
    envelope["data"]["calendar_event"] = {
        "title": "週次定例",
        "start_time": "2026-07-03T10:00:00+09:00",
    }
    envelope["data"]["title"] = "meeting-42"

    result = await handle_drive_export_completed(db, envelope, http_client_factory=factory)

    assert result == {"status": "skipped", "reason": "calendar_title_mismatch"}
    assert factory.calls == 0
    assert fake.list_calls == 0
    assert fake.posts == []
    assert model.calls == 0
    assert "discord_notify" not in meeting.data
    assert db.commit.await_count == 0


@pytest.mark.asyncio
async def test_handle_posts_when_calendar_title_matches_after_strip(monkeypatch):
    _configure_discord(monkeypatch)
    meeting = SimpleNamespace(id=42, data={})
    db = _fake_db(meeting)
    fake = FakeDiscord(_channels())
    model = _install_counted_fakes(
        monkeypatch, fake, {"channel_id": "1", "confidence": 0.92, "reason": "定例"}
    )
    envelope = _envelope()
    envelope["data"]["calendar_event"]["title"] = "  週次定例  "
    envelope["data"]["title"] = "週次定例 "

    result = await handle_drive_export_completed(
        db, envelope, http_client_factory=FakeClientFactory()
    )

    assert result == {"status": "posted", "channel_id": "1", "fallback_reason": None}
    assert model.titles == ["週次定例"]
    assert len(fake.posts) == 1
    channel_id, message = fake.posts[0]
    assert channel_id == "1"
    assert "カボス議事録: 週次定例\n" in message["content"]
    assert "カボス議事録:   週次定例" not in message["content"]
    assert meeting.data["discord_notify"]["status"] == "posted"
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_handle_title_gate_runs_after_existing_checks(monkeypatch):
    _configure_discord(monkeypatch)
    fake = FakeDiscord(_channels())
    model = _install_counted_fakes(
        monkeypatch, fake, {"channel_id": "1", "confidence": 0.95, "reason": "定例"}
    )
    envelope = _envelope()
    envelope["data"]["title"] = "meeting-42"  # calendar_event.title と不一致

    # meeting 無し + 不一致 → meeting_not_found(ゲートより前)
    missing_db = _fake_db(None)
    missing = await handle_drive_export_completed(
        missing_db, envelope, http_client_factory=FakeClientFactory()
    )
    assert missing == {"status": "skipped", "reason": "meeting_not_found"}
    assert missing_db.execute.await_count == 1

    # duplicate + 不一致 → duplicate(ゲートより前)
    duplicate_meeting = SimpleNamespace(
        id=42,
        data={
            "discord_notify": {
                "event_id": envelope["event_id"],
                "status": "posted",
                "channel_id": "1",
            }
        },
    )
    duplicate_db = _fake_db(duplicate_meeting)
    duplicate = await handle_drive_export_completed(
        duplicate_db, envelope, http_client_factory=FakeClientFactory()
    )
    assert duplicate == {"status": "duplicate", "channel_id": "1"}

    # env 未設定 + 不一致 → discord_not_configured かつ DB 取得 0 回
    monkeypatch.delenv("KABOSU_DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("KABOSU_DISCORD_GUILD_ID", raising=False)
    monkeypatch.delenv("KABOSU_DISCORD_DEFAULT_CHANNEL_ID", raising=False)
    unconfigured_db = _fake_db(SimpleNamespace(id=42, data={}))
    unconfigured = await handle_drive_export_completed(
        unconfigured_db, envelope, http_client_factory=FakeClientFactory()
    )
    assert unconfigured == {"status": "skipped", "reason": "discord_not_configured"}
    assert unconfigured_db.execute.await_count == 0

    assert fake.list_calls == 0
    assert fake.posts == []
    assert model.calls == 0


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def _route_client(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app import main as main_module
    from meeting_api.database import get_db

    async def override_get_db():
        yield AsyncMock()

    main_module.app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=main_module.app)
    return main_module, AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_route_returns_503_without_secret(monkeypatch):
    monkeypatch.delenv("KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET", raising=False)
    main_module, client = _route_client(monkeypatch)
    try:
        async with client as ac:
            resp = await ac.post("/internal/webhooks/drive-export-completed", content=b"{}")
        assert resp.status_code == 503
    finally:
        main_module.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_route_rejects_bad_signature_and_bearer_only(monkeypatch):
    monkeypatch.setenv("KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET", SECRET)
    main_module, client = _route_client(monkeypatch)
    body = json.dumps(_envelope()).encode()
    try:
        async with client as ac:
            tampered = await ac.post(
                "/internal/webhooks/drive-export-completed",
                content=body,
                headers={"X-Webhook-Signature": "sha256=deadbeef", "X-Webhook-Timestamp": str(int(time.time()))},
            )
            bearer_only = await ac.post(
                "/internal/webhooks/drive-export-completed",
                content=body,
                headers={"Authorization": f"Bearer {SECRET}"},
            )
        assert tampered.status_code == 401
        assert bearer_only.status_code == 401
    finally:
        main_module.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_route_rejects_other_event_types(monkeypatch):
    monkeypatch.setenv("KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET", SECRET)
    main_module, client = _route_client(monkeypatch)
    envelope = _envelope()
    envelope["event_type"] = "meeting.completed"
    body = json.dumps(envelope).encode()
    try:
        async with client as ac:
            resp = await ac.post(
                "/internal/webhooks/drive-export-completed", content=body, headers=_sign(body)
            )
        assert resp.status_code == 400
    finally:
        main_module.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_route_accepts_signed_event_and_returns_handler_result(monkeypatch):
    from meeting_api.webhook_delivery import build_headers

    monkeypatch.setenv("KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET", SECRET)
    main_module, client = _route_client(monkeypatch)
    envelope = _envelope()
    body = json.dumps(envelope).encode()
    # Sender-side headers prove both sides share one signing algorithm.
    headers = build_headers(SECRET, body)

    async def fake_handler(db, env, **kwargs):
        return {"status": "posted", "channel_id": "1", "fallback_reason": None}

    monkeypatch.setattr(main_module, "handle_drive_export_completed", fake_handler)
    try:
        async with client as ac:
            resp = await ac.post(
                "/internal/webhooks/drive-export-completed", content=body, headers=headers
            )
        assert resp.status_code == 200
        assert resp.json() == {"status": "posted", "channel_id": "1", "fallback_reason": None}
    finally:
        main_module.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_route_returns_502_when_discord_delivery_fails(monkeypatch):
    monkeypatch.setenv("KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET", SECRET)
    main_module, client = _route_client(monkeypatch)
    envelope = _envelope()
    body = json.dumps(envelope).encode()

    async def failing_handler(db, env, **kwargs):
        raise DiscordDeliveryError("default channel post failed")

    monkeypatch.setattr(main_module, "handle_drive_export_completed", failing_handler)
    try:
        async with client as ac:
            resp = await ac.post(
                "/internal/webhooks/drive-export-completed", content=body, headers=_sign(body)
            )
        assert resp.status_code == 502
    finally:
        main_module.app.dependency_overrides.clear()
