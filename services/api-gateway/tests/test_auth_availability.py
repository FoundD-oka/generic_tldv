"""R09 contracts for distinguishing invalid tokens from auth outages."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import main
from main import app, auth_me, forward_request, _resolve_token


IDENTITY = {
    "user_id": 7,
    "email": "person@example.com",
    "scopes": ["bot"],
    "max_concurrent": 2,
}


def request(headers=None, path="/auth/me"):
    raw_headers = [
        (name.lower().encode(), value.encode())
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 1),
        "server": ("gateway.test", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def response(status, payload=None, *, invalid_json=False):
    result = MagicMock()
    result.status_code = status
    if invalid_json:
        result.json.side_effect = ValueError("malformed response")
    else:
        result.json.return_value = payload
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_status"),
    [
        ("valid", 200),
        ("invalid", 401),
        ("forbidden", 503),
        ("limited", 503),
        ("server_error", 503),
        ("invalid_json", 503),
        ("invalid_identity", 503),
        ("connect_error", 503),
    ],
)
async def test_R09_auth_me_distinguishes_invalid_and_unavailable(case, expected_status):
    app.state.redis = None
    client = AsyncMock()
    if case == "valid":
        client.post.return_value = response(200, IDENTITY)
    elif case == "invalid":
        client.post.return_value = response(401)
    elif case == "forbidden":
        client.post.return_value = response(403)
    elif case == "limited":
        client.post.return_value = response(429)
    elif case == "server_error":
        client.post.return_value = response(503)
    elif case == "invalid_json":
        client.post.return_value = response(200, invalid_json=True)
    elif case == "invalid_identity":
        client.post.return_value = response(200, {"user_id": 7, "scopes": "bot"})
    else:
        client.post.side_effect = httpx.ConnectError(
            "admin unavailable",
            request=httpx.Request("POST", "http://admin-api/internal/validate"),
        )
    app.state.http_client = client

    if expected_status == 200:
        result = await auth_me(request({"x-api-key": "vxa_test"}))
        assert result == IDENTITY
    else:
        with pytest.raises(HTTPException) as error:
            await auth_me(request({"x-api-key": "vxa_test"}))
        assert error.value.status_code == expected_status
        assert error.value.detail == (
            "Invalid API key"
            if expected_status == 401
            else "Authentication service unavailable"
        )


@pytest.mark.asyncio
async def test_R09_slow_cache_falls_back_within_budget():
    never_get = asyncio.Event()
    never_set = asyncio.Event()
    redis = AsyncMock()

    async def slow_get(_key):
        await never_get.wait()

    async def slow_set(*_args, **_kwargs):
        await never_set.wait()

    redis.get.side_effect = slow_get
    redis.set.side_effect = slow_set
    app.state.redis = redis
    client = AsyncMock()
    client.post.return_value = response(200, IDENTITY)
    app.state.http_client = client

    started = time.monotonic()
    assert await auth_me(request({"x-api-key": "vxa_slow_cache"})) == IDENTITY
    elapsed = time.monotonic() - started
    assert 2.0 <= elapsed < 4.0
    client.post.assert_awaited_once()
    assert redis.set.await_args.kwargs["ex"] == 60

    # A malformed cached value is never treated as an identity during outage.
    redis.get.side_effect = None
    redis.get.return_value = json.dumps({"email": "stale@example.com"})
    client.post.reset_mock()
    client.post.side_effect = httpx.ConnectError(
        "admin unavailable",
        request=httpx.Request("POST", "http://admin-api/internal/validate"),
    )
    with pytest.raises(HTTPException) as error:
        await auth_me(request({"x-api-key": "vxa_bad_cache"}))
    assert error.value.status_code == 503
    client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_R09_auth_me_has_eight_second_ceiling():
    waiting = AsyncMock(side_effect=lambda *_args, **_kwargs: asyncio.Event().wait())

    async def deadline(awaitable, timeout):
        assert timeout == 8.0
        awaitable.close()
        raise asyncio.TimeoutError

    with patch("main._resolve_token", waiting), patch("main.asyncio.wait_for", deadline):
        with pytest.raises(HTTPException) as error:
            await auth_me(request({"x-api-key": "vxa_waiting"}))

    assert error.value.status_code == 503
    assert error.value.detail == "Authentication service unavailable"
    waiting.assert_called_once_with(
        app.state.http_client,
        "vxa_waiting",
        report_unavailable=True,
    )


@pytest.mark.asyncio
async def test_R09_default_resolver_and_scope_checks_are_unchanged(monkeypatch):
    app.state.redis = None
    unavailable = AsyncMock(
        side_effect=httpx.ConnectError(
            "admin unavailable",
            request=httpx.Request("POST", "http://admin-api/internal/validate"),
        )
    )
    client = AsyncMock()
    client.post = unavailable
    assert await _resolve_token(client, "vxa_default") is None

    # Missing tokens fail before validation or forwarding.
    client.reset_mock()
    missing = await forward_request(
        client,
        "GET",
        "http://meeting-api:8000/bots",
        request(path="/bots"),
    )
    assert missing.status_code == 401
    client.post.assert_not_awaited()
    client.request.assert_not_awaited()

    # Default validation still injects identity, enforces scopes, and caches for 60s.
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-test-secret")
    redis = AsyncMock()
    redis.get.return_value = None
    app.state.redis = redis
    client = AsyncMock()
    client.post.return_value = response(200, IDENTITY)
    denied = await forward_request(
        client,
        "GET",
        "http://meeting-api:8000/transcripts",
        request({"x-api-key": "vxa_bot_only"}, path="/transcripts"),
    )
    assert denied.status_code == 403
    client.request.assert_not_awaited()
    assert client.post.await_args.kwargs["headers"] == {
        "X-Internal-Secret": "internal-test-secret"
    }
    assert client.post.await_args.kwargs["timeout"] == 5.0
    assert redis.set.await_args.kwargs["ex"] == 60
