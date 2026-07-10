import base64

from fastapi.testclient import TestClient

from conftest import make_wav_bytes


def _authed_client(main):
    client = TestClient(main.app)
    return client


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
