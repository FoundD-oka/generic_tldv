"""Tests for bot spawn retry — _spawn_via_runtime_api transient failure handling.

Retries only failures that provably did not create a container (connect errors,
500/502/503/504). Response-loss failures (ReadTimeout) and deterministic
failures (400, 429) are never resent, so no meeting ever gets a second bot.

All backoff sleeps are patched out; these tests must not sleep for real.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from meeting_api.meetings import _spawn_via_runtime_api
from meeting_api.retry import with_retry

SPAWN_ARGS = dict(
    profile="vexa-bot",
    config={"platform": "google_meet"},
    user_id=1,
    callback_url="http://meeting-api/callbacks/bot",
    metadata={"meeting_id": 42},
)

CREATED_BODY = {"container_id": "abc123", "name": "vexa-bot-42-deadbeef"}


def _resp(status_code, json_data=None, text=""):
    """Build a mock httpx.Response with the fields _spawn_via_runtime_api reads."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


def _client(side_effect):
    client = MagicMock()
    client.post = AsyncMock(side_effect=side_effect)
    return client


@pytest.fixture
def no_sleep():
    """Patch the backoff sleep so retries are instant and observable."""
    with patch("meeting_api.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        yield mock_sleep


class TestSpawnRetryTransient:
    """Failures that are safe to resend are retried."""

    @pytest.mark.asyncio
    async def test_connect_error_twice_then_created(self, no_sleep):
        """AT-001: two ConnectErrors then 201 → dict returned, post called 3 times."""
        client = _client([
            httpx.ConnectError("connection refused"),
            httpx.ConnectError("connection refused"),
            _resp(201, CREATED_BODY),
        ])

        with patch("meeting_api.meetings._get_httpx_client", return_value=client):
            result = await _spawn_via_runtime_api(**SPAWN_ARGS)

        assert result == CREATED_BODY
        assert client.post.await_count == 3

    @pytest.mark.asyncio
    async def test_http_500_then_created(self, no_sleep):
        """AT-002: 500 then 201 → dict returned, post called twice."""
        client = _client([
            _resp(500, text="internal error"),
            _resp(201, CREATED_BODY),
        ])

        with patch("meeting_api.meetings._get_httpx_client", return_value=client):
            result = await _spawn_via_runtime_api(**SPAWN_ARGS)

        assert result == CREATED_BODY
        assert client.post.await_count == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [502, 503, 504])
    async def test_gateway_statuses_then_created(self, no_sleep, status_code):
        """AT-003: 502/503/504 then 201 → retried and dict returned."""
        client = _client([
            _resp(status_code, text="gateway failure"),
            _resp(201, CREATED_BODY),
        ])

        with patch("meeting_api.meetings._get_httpx_client", return_value=client):
            result = await _spawn_via_runtime_api(**SPAWN_ARGS)

        assert result == CREATED_BODY
        assert client.post.await_count == 2


class TestSpawnRetryExhaustion:
    """Retries are bounded and never leak exceptions to callers."""

    @pytest.mark.asyncio
    async def test_connect_error_exhausts_retries(self, no_sleep):
        """AT-004: 4 consecutive ConnectErrors → None, exactly 4 posts, no raise."""
        client = _client([httpx.ConnectError("connection refused")] * 4)

        with patch("meeting_api.meetings._get_httpx_client", return_value=client):
            result = await _spawn_via_runtime_api(**SPAWN_ARGS)

        assert result is None
        assert client.post.await_count == 4

    @pytest.mark.asyncio
    async def test_backoff_series_is_exponential_with_jitter(self, no_sleep):
        """AT-008: sleep(i) ∈ [1.0*2^i, 1.0*2^i + 0.5] and ≤ 10.0 for i = 0,1,2."""
        client = _client([httpx.ConnectError("connection refused")] * 4)

        with patch("meeting_api.meetings._get_httpx_client", return_value=client):
            await _spawn_via_runtime_api(**SPAWN_ARGS)

        delays = [call.args[0] for call in no_sleep.await_args_list]
        assert len(delays) == 3
        for i, delay in enumerate(delays):
            expected = 1.0 * (2 ** i)
            assert expected <= delay <= expected + 0.5
            assert delay <= 10.0


class TestSpawnNoRetry:
    """Failures that may have created a container, or are deterministic, are not resent."""

    @pytest.mark.asyncio
    async def test_429_raises_immediately(self, no_sleep):
        """AT-005/FP-004: 429 → HTTPException(429) at once, one post, no sleep."""
        client = _client([_resp(429, {"detail": "Concurrency limit reached"})])

        with patch("meeting_api.meetings._get_httpx_client", return_value=client):
            with pytest.raises(HTTPException) as exc_info:
                await _spawn_via_runtime_api(**SPAWN_ARGS)

        assert exc_info.value.status_code == 429
        assert client.post.await_count == 1
        assert no_sleep.await_count == 0

    @pytest.mark.asyncio
    async def test_read_timeout_is_not_retried(self, no_sleep):
        """AT-006: ReadTimeout → None after a single post (double-spawn guard)."""
        client = _client([httpx.ReadTimeout("response lost")])

        with patch("meeting_api.meetings._get_httpx_client", return_value=client):
            result = await _spawn_via_runtime_api(**SPAWN_ARGS)

        assert result is None
        assert client.post.await_count == 1
        assert no_sleep.await_count == 0

    @pytest.mark.asyncio
    async def test_400_is_not_retried(self, no_sleep):
        """AT-007: 400 → None after a single post (deterministic failure)."""
        client = _client([_resp(400, text="bad request")])

        with patch("meeting_api.meetings._get_httpx_client", return_value=client):
            result = await _spawn_via_runtime_api(**SPAWN_ARGS)

        assert result is None
        assert client.post.await_count == 1
        assert no_sleep.await_count == 0


class TestWithRetryIsRetryableArg:
    """AT-009: the new is_retryable argument is optional and overrides the default."""

    @pytest.mark.asyncio
    async def test_default_predicate_used_when_arg_omitted(self, no_sleep):
        """Omitting is_retryable keeps the existing behaviour."""
        transient = AsyncMock(side_effect=[httpx.ConnectError("boom"), "ok"])
        assert await with_retry(transient) == "ok"
        assert transient.await_count == 2

        deterministic = AsyncMock(side_effect=ValueError("nope"))
        with pytest.raises(ValueError):
            await with_retry(deterministic)
        assert deterministic.await_count == 1

    @pytest.mark.asyncio
    async def test_custom_predicate_overrides_default(self, no_sleep):
        """A custom predicate decides both retry and no-retry cases."""
        custom_retry = AsyncMock(side_effect=[ValueError("nope"), "ok"])
        result = await with_retry(custom_retry, is_retryable=lambda e: isinstance(e, ValueError))
        assert result == "ok"
        assert custom_retry.await_count == 2

        custom_stop = AsyncMock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(httpx.ConnectError):
            await with_retry(custom_stop, is_retryable=lambda e: False)
        assert custom_stop.await_count == 1
