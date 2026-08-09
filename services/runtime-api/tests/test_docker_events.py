"""Docker event stream: bounded read timeout and `since` replay (ST-23).

Unit-level only — a fake session is injected into DockerBackend, so no Docker
daemon, no unix socket and no network access is required.
"""

import asyncio
import importlib
import json
import logging

import pytest
import requests
import urllib3

from runtime_api import config
from runtime_api.backends import docker as docker_backend
from runtime_api.backends.docker import MANAGED_LABEL, DockerBackend


# --- Fakes -----------------------------------------------------------------


def _raise(exc):
    """Raise `exc` — mock side effects must raise, not return, the exception."""
    raise exc


class FakeResponse:
    """Streaming response: yields `lines`, then optionally raises `error`."""

    def __init__(self, lines=None, error=None):
        self._lines = list(lines or [])
        self._error = error

    def iter_lines(self):
        yield from self._lines
        if self._error is not None:
            _raise(self._error)


class FakeSession:
    """Records every GET; returns queued responses or raises queued errors."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, stream=False, timeout=None):
        self.calls.append({
            "url": url,
            "params": params,
            "stream": stream,
            "timeout": timeout,
        })
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            _raise(item)
        return item


def _backend(responses):
    backend = DockerBackend()
    session = FakeSession(responses)
    backend._session = session
    backend._socket_url = "http+unix://%2Ftmp%2Ffake-docker.sock"
    return backend, session


def _die_line(name="bot-1", exit_code="0", time_nano=1786281088293639700):
    return json.dumps({
        "status": "die",
        "timeNano": time_nano,
        "Actor": {"Attributes": {"name": name, "exitCode": exit_code}},
    }).encode()


def _stream_read_timeout():
    """The exception a read timeout raises while streaming the body.

    Measured against requests 2.34.2 / urllib3 2.7.0 (see RF-201).
    """
    return requests.exceptions.ConnectionError(
        urllib3.exceptions.ReadTimeoutError(None, "/events", "Read timed out.")
    )


# --- AT-201: bounded timeout ------------------------------------------------


def test_stream_events_uses_bounded_timeout():
    """The /events GET carries (connect, read) timeouts — never timeout=None."""
    backend, session = _backend([FakeResponse()])

    backend._stream_events(None, None)

    assert session.calls[0]["timeout"] == (5, config.DOCKER_EVENTS_READ_TIMEOUT)
    assert session.calls[0]["timeout"][1] is not None
    assert session.calls[0]["stream"] is True


def test_events_read_timeout_default_and_env_override(monkeypatch):
    """Default read timeout is 300s and DOCKER_EVENTS_READ_TIMEOUT overrides it."""
    monkeypatch.delenv("DOCKER_EVENTS_READ_TIMEOUT", raising=False)
    reloaded = importlib.reload(config)
    try:
        assert reloaded.DOCKER_EVENTS_READ_TIMEOUT == 300.0
        monkeypatch.setenv("DOCKER_EVENTS_READ_TIMEOUT", "42.5")
        reloaded = importlib.reload(config)
        assert reloaded.DOCKER_EVENTS_READ_TIMEOUT == 42.5
    finally:
        monkeypatch.delenv("DOCKER_EVENTS_READ_TIMEOUT", raising=False)
        importlib.reload(config)


# --- AT-202: quiet, exception-free return on read timeout -------------------


def test_stream_events_returns_on_streaming_read_timeout(caplog):
    """ReadTimeoutError-wrapped ConnectionError: return quietly, debug only."""
    backend, _ = _backend([FakeResponse(error=_stream_read_timeout())])

    with caplog.at_level(logging.DEBUG, logger="runtime_api.backends.docker"):
        backend._stream_events(None, None)

    assert [r.levelno for r in caplog.records] == [logging.DEBUG]
    assert "read timeout" in caplog.records[0].getMessage()
    assert caplog.records[0].exc_info is None


def test_stream_events_returns_on_requests_timeout(caplog):
    """requests.exceptions.Timeout (header-phase read timeout) is also normal."""
    backend, _ = _backend([requests.exceptions.Timeout("Read timed out.")])

    with caplog.at_level(logging.DEBUG, logger="runtime_api.backends.docker"):
        backend._stream_events(None, None)

    assert [r.levelno for r in caplog.records] == [logging.DEBUG]


def test_stream_events_reraises_non_timeout_errors():
    """A real connection failure still propagates to the reconnect loop."""
    backend, _ = _backend([requests.exceptions.ConnectionError("connection refused")])

    with pytest.raises(requests.exceptions.ConnectionError):
        backend._stream_events(None, None)


# --- AT-203: since replay ---------------------------------------------------


def test_first_connection_has_no_since():
    """The first connection does not replay history (reconcile_state's job)."""
    backend, session = _backend([FakeResponse()])

    backend._stream_events(None, None)

    assert "since" not in session.calls[0]["params"]


def test_reconnect_since_uses_last_observed_event_nano(monkeypatch):
    """After observing an event, `since` is that event's timeNano, ns-precise."""
    monkeypatch.setattr(docker_backend.time, "time_ns", lambda: 1786281000000000000)
    backend, session = _backend([
        FakeResponse(lines=[_die_line(time_nano=1786281088293639700)],
                     error=_stream_read_timeout()),
        FakeResponse(),
    ])
    loop = asyncio.new_event_loop()
    try:
        backend._stream_events(_noop_on_exit, loop)
        backend._stream_events(None, loop)
    finally:
        loop.close()

    assert session.calls[1]["params"]["since"] == "1786281088.293639700"


def test_reconnect_since_falls_back_to_connection_start(monkeypatch):
    """With no event observed, `since` comes from the previous connection start."""
    monkeypatch.setattr(docker_backend.time, "time_ns", lambda: 1786281088293639700)
    backend, session = _backend([
        FakeResponse(error=_stream_read_timeout()),
        FakeResponse(),
    ])

    backend._stream_events(None, None)
    backend._stream_events(None, None)

    assert session.calls[1]["params"]["since"] == "1786281088.293639700"


def test_since_keeps_nine_digit_nanoseconds(monkeypatch):
    """Sub-microsecond nanoseconds are zero-padded to 9 digits, not truncated."""
    monkeypatch.setattr(docker_backend.time, "time_ns", lambda: 1786281088000000042)
    backend, session = _backend([FakeResponse(error=_stream_read_timeout()), FakeResponse()])

    backend._stream_events(None, None)
    backend._stream_events(None, None)

    assert session.calls[1]["params"]["since"] == "1786281088.000000042"


# --- AT-204: unchanged die semantics ---------------------------------------


async def _noop_on_exit(name, exit_code):
    return None


@pytest.mark.asyncio
async def test_die_event_dispatches_on_exit():
    """die events still dispatch on_exit(name, exit_code) with unchanged filters."""
    received = []

    async def on_exit(name, exit_code):
        received.append((name, exit_code))

    backend, session = _backend([FakeResponse(lines=[_die_line("bot-7", "137")])])
    loop = asyncio.get_running_loop()

    await loop.run_in_executor(None, backend._stream_events, on_exit, loop)
    await asyncio.sleep(0.05)

    assert received == [("bot-7", 137)]
    assert json.loads(session.calls[0]["params"]["filters"]) == {
        "type": ["container"],
        "event": ["die"],
        "label": [f"{MANAGED_LABEL}=true"],
    }


@pytest.mark.asyncio
async def test_event_loop_warns_and_backs_off_on_failure(monkeypatch, caplog):
    """Non-timeout failures keep the warning + 2s backoff reconnect path."""
    backend, _ = _backend([requests.exceptions.ConnectionError("connection refused")])
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.DEBUG, logger="runtime_api.backends.docker"), \
            pytest.raises(asyncio.CancelledError):
        await backend._event_loop(_noop_on_exit)

    assert sleeps == [2]
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert caplog.records[0].exc_info is not None


# --- AT-205: TRANSCRIPTION_SERVICE_URL startup warning ----------------------


def test_warn_missing_bot_env_warns_once(monkeypatch, caplog):
    from runtime_api.main import warn_missing_bot_env

    monkeypatch.delenv("TRANSCRIPTION_SERVICE_URL", raising=False)
    with caplog.at_level(logging.WARNING, logger="runtime_api"):
        warn_missing_bot_env()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "TRANSCRIPTION_SERVICE_URL" in warnings[0].getMessage()


def test_warn_missing_bot_env_silent_when_set(monkeypatch, caplog):
    from runtime_api.main import warn_missing_bot_env

    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://transcription:8000")
    with caplog.at_level(logging.WARNING, logger="runtime_api"):
        warn_missing_bot_env()

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
