import asyncio
import base64

import pytest
from fastapi.testclient import TestClient

from conftest import make_wav_bytes


def _authed_client(main):
    client = TestClient(main.app)
    return client


class _MockStreamRequest:
    """Fakes just enough of `Request` for `_read_bounded_body`: a `.stream()`
    async generator. Used to assert the reader stops pulling chunks the
    moment the running total crosses the cap, instead of buffering the
    whole body first (tribunal BUG-006)."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.chunks_consumed = 0

    async def stream(self):
        for chunk in self._chunks:
            self.chunks_consumed += 1
            yield chunk


def test_embed_happy_path_returns_192_floats(load_app, monkeypatch):
    main = load_app(ALLOW_UNAUTHENTICATED="true")
    main.model_holder["model"] = object()
    fake_embedding = [float(i) / 1000.0 for i in range(main.EMBEDDING_DIM)]
    monkeypatch.setattr(main, "_run_inference", lambda model, audio: fake_embedding)
    client = _authed_client(main)

    wav_bytes = make_wav_bytes(2.0)
    resp = client.post("/embed", json={"audio_base64": base64.b64encode(wav_bytes).decode()})

    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "ecapa-tdnn"
    assert isinstance(body["embedding"], list)
    assert len(body["embedding"]) == 192
    assert all(isinstance(v, float) for v in body["embedding"])
    assert body["duration_seconds"] >= 1.9  # ~2s input, allow float slop


def test_embed_happy_path_via_multipart(load_app, monkeypatch):
    main = load_app(ALLOW_UNAUTHENTICATED="true")
    main.model_holder["model"] = object()
    fake_embedding = [0.5] * main.EMBEDDING_DIM
    monkeypatch.setattr(main, "_run_inference", lambda model, audio: fake_embedding)
    client = _authed_client(main)

    wav_bytes = make_wav_bytes(2.0)
    resp = client.post(
        "/embed",
        files={"file": ("clip.wav", wav_bytes, "audio/wav")},
    )

    assert resp.status_code == 200
    assert len(resp.json()["embedding"]) == 192


def test_embed_rejects_payload_over_max_bytes(load_app):
    main = load_app(ALLOW_UNAUTHENTICATED="true", VOICEPRINT_MAX_AUDIO_BYTES="500")
    main.model_holder["model"] = object()
    client = _authed_client(main)

    # ~2s of 16kHz PCM16 mono is far bigger than the 500-byte cap configured above.
    wav_bytes = make_wav_bytes(2.0)
    resp = client.post("/embed", json={"audio_base64": base64.b64encode(wav_bytes).decode()})

    assert resp.status_code == 413


def test_read_bounded_body_aborts_without_consuming_whole_stream(load_app):
    """Unit-level check on the streaming guard itself: once the running
    total crosses the cap, _read_bounded_body must raise 413 immediately
    and must NOT have pulled every chunk from the stream — i.e. no
    unbounded buffering, regardless of how many/how large the remaining
    chunks are."""
    main = load_app(ALLOW_UNAUTHENTICATED="true", VOICEPRINT_MAX_AUDIO_BYTES="1000")

    # 4 chunks of 600 bytes = 2400 bytes total, well over the 1000-byte cap.
    # Only the first two chunks (1200 bytes) are needed to cross it.
    chunks = [b"a" * 600, b"b" * 600, b"c" * 600, b"d" * 600]
    mock_request = _MockStreamRequest(chunks)

    async def _run():
        with pytest.raises(main.HTTPException) as exc_info:
            await main._read_bounded_body(mock_request, main.VOICEPRINT_MAX_AUDIO_BYTES)
        assert exc_info.value.status_code == 413

    asyncio.run(_run())
    assert mock_request.chunks_consumed == 2


def test_embed_rejects_oversized_json_body_with_no_content_length_header(load_app):
    """Integration-level check: send a body via a generator (httpx omits
    Content-Length for streamed/generator content) so the fast-path
    Content-Length check in embed() cannot fire, and only the streaming
    guard in _read_bounded_body can catch the oversized payload."""
    main = load_app(ALLOW_UNAUTHENTICATED="true", VOICEPRINT_MAX_AUDIO_BYTES="1000")
    main.model_holder["model"] = object()
    client = _authed_client(main)

    def body_chunks():
        for _ in range(5):
            yield b"a" * 1000  # 5000 bytes total, far over the 1000-byte cap

    resp = client.post(
        "/embed",
        content=body_chunks(),
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 413


def test_embed_rejects_oversized_multipart_body_with_no_content_length_header(load_app):
    """Same as above but for the multipart path: the file field content
    alone stays under the cap check that used to run only after
    request.form() fully buffered it, but the *raw* multipart body
    (boundaries + headers + file bytes) here is what must trip the
    streaming guard before any buffering happens."""
    main = load_app(ALLOW_UNAUTHENTICATED="true", VOICEPRINT_MAX_AUDIO_BYTES="1000")
    main.model_holder["model"] = object()
    client = _authed_client(main)

    boundary = "testboundary"
    file_bytes = b"x" * 5000  # far over the 1000-byte cap
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="clip.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()

    def body_chunks():
        # Split into small chunks so the reader must accumulate across
        # multiple stream reads, mirroring a real chunked upload.
        step = 512
        for i in range(0, len(body), step):
            yield body[i : i + step]

    resp = client.post(
        "/embed",
        content=body_chunks(),
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )

    assert resp.status_code == 413


def test_embed_rejects_audio_shorter_than_one_second(load_app, monkeypatch):
    main = load_app(ALLOW_UNAUTHENTICATED="true")
    main.model_holder["model"] = object()
    monkeypatch.setattr(main, "_run_inference", lambda model, audio: [0.0] * main.EMBEDDING_DIM)
    client = _authed_client(main)

    wav_bytes = make_wav_bytes(0.5)
    resp = client.post("/embed", json={"audio_base64": base64.b64encode(wav_bytes).decode()})

    assert resp.status_code == 422


def test_embed_returns_503_when_model_not_loaded(load_app):
    main = load_app(ALLOW_UNAUTHENTICATED="true")
    main.model_holder["model"] = None
    client = _authed_client(main)

    wav_bytes = make_wav_bytes(2.0)
    resp = client.post("/embed", json={"audio_base64": base64.b64encode(wav_bytes).decode()})

    assert resp.status_code == 503


def test_embed_returns_429_when_concurrency_limit_exceeded(load_app, monkeypatch):
    main = load_app(ALLOW_UNAUTHENTICATED="true", MAX_ACTIVE_REQUESTS="1")
    main.model_holder["model"] = object()
    monkeypatch.setattr(main, "_run_inference", lambda model, audio: [0.0] * main.EMBEDDING_DIM)
    # Simulate one request already in flight.
    main._active_requests = 1
    client = _authed_client(main)

    wav_bytes = make_wav_bytes(2.0)
    resp = client.post("/embed", json={"audio_base64": base64.b64encode(wav_bytes).decode()})

    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_embed_slot_is_released_after_request_completes(load_app, monkeypatch):
    main = load_app(ALLOW_UNAUTHENTICATED="true", MAX_ACTIVE_REQUESTS="1")
    main.model_holder["model"] = object()
    monkeypatch.setattr(main, "_run_inference", lambda model, audio: [0.0] * main.EMBEDDING_DIM)
    client = _authed_client(main)

    wav_bytes = make_wav_bytes(2.0)
    resp1 = client.post("/embed", json={"audio_base64": base64.b64encode(wav_bytes).decode()})
    assert resp1.status_code == 200

    # The slot from resp1 must have been released; a second request should
    # not be starved by the first one under the same MAX_ACTIVE_REQUESTS=1 cap.
    resp2 = client.post("/embed", json={"audio_base64": base64.b64encode(wav_bytes).decode()})
    assert resp2.status_code == 200
