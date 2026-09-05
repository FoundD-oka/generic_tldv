"""R03 contracts for opt-in recording response streaming."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import anyio
import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import main
from main import app, forward_request
from media_streaming import ClosingStreamingResponse


class FakeByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), *, tail_event=None, error=None):
        self.chunks = list(chunks)
        self.tail_event = tail_event
        self.error = error
        self.close_count = 0
        self.generated = 0

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            if index and self.tail_event is not None:
                await self.tail_event.wait()
            self.generated += 1
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.close_count += 1


def gateway_request(path="/recordings/42/media/7/raw", headers=None, query=b"", include_api_key=True):
    raw_headers = [(b"x-api-key", b"test-key")] if include_api_key else []
    for name, value in (headers or {}).items():
        raw_headers.append((name.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": raw_headers,
        "client": ("127.0.0.1", 1),
        "server": ("gateway.test", 80),
    }
    body_sent = False

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await anyio.sleep_forever()

    return Request(scope, receive)


async def idle_receive():
    await anyio.sleep_forever()


@asynccontextmanager
async def forwarded(stream, *, status=200, headers=None, request=None, timeout=None):
    def backend(upstream_request):
        return httpx.Response(
            status,
            headers=headers,
            stream=stream,
            request=upstream_request,
        )

    user = {"user_id": 5, "scopes": ["bot", "tx", "browser"], "max_concurrent": 1}
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as client:
        with patch("main._resolve_token", AsyncMock(return_value=user)):
            response = await forward_request(
                client,
                "GET",
                "http://meeting-api/recordings/42/media/7/raw",
                request or gateway_request(),
                timeout=timeout,
                stream_response=True,
            )
        yield response


@pytest.mark.asyncio
async def test_R03_first_chunk_does_not_wait_for_tail():
    tail = asyncio.Event()
    stream = FakeByteStream([b"abc", b"def"], tail_event=tail)
    messages = []
    first = asyncio.Event()

    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            first.set()

    async with forwarded(stream) as response:
        task = asyncio.create_task(response(gateway_request().scope, idle_receive, send))
        await asyncio.wait_for(first.wait(), 1)
        assert b"".join(m.get("body", b"") for m in messages) == b"abc"
        assert not task.done()
        tail.set()
        await asyncio.wait_for(task, 1)

    assert b"".join(m.get("body", b"") for m in messages) == b"abcdef"
    assert stream.close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "chunks", "expected"),
    [
        (
            {
                "Content-Type": "audio/webm",
                "Content-Range": "bytes 2-4/10",
                "Content-Length": "3",
                "Accept-Ranges": "bytes",
                "Content-Disposition": 'inline; filename="clip.webm"',
                "Connection": "keep-alive, x-private",
                "X-Private": "drop-me",
            },
            [b"abc"],
            b"abc",
        ),
        (
            {"Content-Type": "application/octet-stream", "Content-Encoding": "gzip", "Content-Length": "6"},
            [b"\x1f\x8braw"],
            b"\x1f\x8braw",
        ),
    ],
)
async def test_R03_range_and_raw_encoding_are_preserved(headers, chunks, expected):
    messages = []

    async def send(message):
        messages.append(message)

    async with forwarded(FakeByteStream(chunks), status=206, headers=headers) as response:
        await response(gateway_request().scope, idle_receive, send)

    start = next(m for m in messages if m["type"] == "http.response.start")
    sent_headers = {k.decode(): v.decode() for k, v in start["headers"]}
    assert b"".join(m.get("body", b"") for m in messages) == expected
    assert sent_headers["content-length"] == headers["Content-Length"]
    assert "connection" not in sent_headers
    assert "keep-alive" not in sent_headers
    assert "x-private" not in sent_headers
    if "Content-Range" in headers:
        assert sent_headers["content-range"] == "bytes 2-4/10"
        assert sent_headers["accept-ranges"] == "bytes"
        assert sent_headers["content-disposition"] == 'inline; filename="clip.webm"'
    else:
        assert sent_headers["content-encoding"] == "gzip"


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_path", ["normal", "read_before", "read_after", "disconnect_before", "disconnect_after", "send_error", "cancel"])
async def test_R03_closes_upstream_on_all_exit_paths(exit_path):
    tail = asyncio.Event() if exit_path == "cancel" else None
    chunks = [] if exit_path in {"read_before", "disconnect_before"} else [b"abc"]
    if exit_path == "cancel":
        chunks = [b"abc", b"tail"]
    error = OSError("read failed") if exit_path in {"read_before", "read_after"} else None
    stream = FakeByteStream(chunks, tail_event=tail, error=error)
    upstream_request = httpx.Request("GET", "http://meeting-api/raw")
    upstream = httpx.Response(200, stream=stream, request=upstream_request)
    response = ClosingStreamingResponse(upstream)
    first = asyncio.Event()
    disconnect = asyncio.Event()

    async def receive():
        if exit_path == "disconnect_before":
            return {"type": "http.disconnect"}
        if exit_path == "disconnect_after":
            await disconnect.wait()
            return {"type": "http.disconnect"}
        await anyio.sleep_forever()

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            first.set()
            disconnect.set()
            if exit_path == "send_error":
                raise OSError("downstream failed")

    task = asyncio.create_task(response(gateway_request().scope, receive, send))
    if exit_path == "cancel":
        await asyncio.wait_for(first.wait(), 1)
        task.cancel()
    if exit_path in {"read_before", "read_after", "send_error", "cancel"}:
        with pytest.raises((OSError, asyncio.CancelledError, BaseExceptionGroup)):
            await asyncio.wait_for(task, 1)
    else:
        await asyncio.wait_for(task, 1)
    assert task.done()
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_R03_streaming_does_not_prefetch_tail():
    class LargeStream(FakeByteStream):
        async def __aiter__(self):
            for _ in range(4096):
                self.generated += 1
                yield b"x" * (64 * 1024)

    stream = LargeStream()

    async def stop_after_first(message):
        if message["type"] == "http.response.body" and message.get("body"):
            raise OSError("downstream stopped")

    async with forwarded(stream) as response:
        with pytest.raises((OSError, BaseExceptionGroup)):
            await response(gateway_request().scope, idle_receive, stop_after_first)
    assert stream.generated == 1
    assert stream.close_count == 1


class TrackingClient(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_calls = []
        self.send_stream_values = []

    async def request(self, method, url, **kwargs):
        self.request_calls.append((method, str(url), kwargs))
        return await super().request(method, url, **kwargs)

    async def send(self, request, *, stream=False, auth=httpx.USE_CLIENT_DEFAULT, follow_redirects=httpx.USE_CLIENT_DEFAULT):
        self.send_stream_values.append((request, stream))
        return await super().send(request, stream=stream, auth=auth, follow_redirects=follow_redirects)


@pytest.mark.asyncio
async def test_R03_only_binary_routes_opt_in():
    def backend(request):
        media = "/raw" in request.url.path or "/mp3" in request.url.path
        return httpx.Response(200, content=b"media" if media else b"{}", request=request)

    user = {"user_id": 5, "scopes": ["bot", "tx", "browser"], "max_concurrent": 1}
    old_client = getattr(app.state, "http_client", None)
    async with TrackingClient(transport=httpx.MockTransport(backend), timeout=30.0) as client:
        app.state.http_client = client
        try:
            with patch("main._resolve_token", AsyncMock(return_value=user)):
                responses = [
                    await main.download_media_raw_proxy(42, 7, gateway_request()),
                    await main.download_media_mp3_proxy(42, 7, gateway_request()),
                    await main.download_recording_master_mp3_proxy(42, gateway_request()),
                ]
                for response in responses:
                    await response.upstream.aclose()
                await main.get_recording_master_proxy(42, gateway_request())
                await main.list_recordings_proxy(gateway_request(path="/recordings"))
        finally:
            app.state.http_client = old_client

    assert len(client.request_calls) == 2
    assert [stream for _, stream in client.send_stream_values] == [True, True, True, False, False]
    binary_requests = [request for request, stream in client.send_stream_values if stream]
    assert binary_requests[0].extensions["timeout"]["read"] == 30.0
    assert binary_requests[1].extensions["timeout"]["read"] == 180.0
    assert binary_requests[2].extensions["timeout"]["read"] == 180.0


@pytest.mark.asyncio
async def test_R03_auth_and_error_contracts_stay_closed():
    calls = []

    def backend(request):
        calls.append(request)
        if request.url.path.endswith("/missing"):
            return httpx.Response(404, content=b"missing", request=request)
        return httpx.Response(
            416,
            content=b'{"detail":"range"}',
            headers={"Content-Range": "bytes */10", "Accept-Ranges": "bytes"},
            request=request,
        )

    async with TrackingClient(transport=httpx.MockTransport(backend)) as client:
        missing_key = await forward_request(
            client, "GET", "http://meeting-api/raw", gateway_request(include_api_key=False), stream_response=True
        )
        assert missing_key.status_code == 401
        assert client.send_stream_values == []

        denied_user = {"user_id": 5, "scopes": ["unrelated"]}
        with patch("main._resolve_token", AsyncMock(return_value=denied_user)):
            denied = await forward_request(
                client, "GET", "http://meeting-api/raw", gateway_request(path="/bots"), stream_response=True
            )
        assert denied.status_code == 403
        assert client.send_stream_values == []

        allowed = {"user_id": 5, "scopes": ["tx"]}
        spoofed = gateway_request(headers={"x-user-id": "999", "range": "bytes=99-100"})
        with patch("main._resolve_token", AsyncMock(return_value=allowed)):
            ranged = await forward_request(
                client, "GET", "http://meeting-api/raw", spoofed, stream_response=True
            )
        assert ranged.status_code == 416
        assert ranged.headers["content-range"] == "bytes */10"
        assert calls[-1].headers["x-user-id"] == "5"
        assert calls[-1].headers["range"] == "bytes=99-100"
        await ranged.upstream.aclose()

        with patch("main._resolve_token", AsyncMock(return_value=allowed)):
            missing = await forward_request(
                client,
                "GET",
                "http://meeting-api/missing",
                gateway_request(),
                stream_response=True,
            )
        assert missing.status_code == 404
        await missing.upstream.aclose()

    async def connect_error(_request):
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(connect_error)) as client:
        with patch("main._resolve_token", AsyncMock(return_value={"user_id": 5, "scopes": ["tx"]})):
            with pytest.raises(HTTPException) as exc:
                await forward_request(client, "GET", "http://meeting-api/raw", gateway_request(), stream_response=True)
    assert exc.value.status_code == 503
