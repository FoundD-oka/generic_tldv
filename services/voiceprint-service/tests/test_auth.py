from fastapi.testclient import TestClient

from conftest import make_wav_bytes


def test_embed_returns_503_when_token_unset_and_unauthenticated_not_allowed(load_app):
    main = load_app(VOICEPRINT_SERVICE_TOKEN="", ALLOW_UNAUTHENTICATED="false")
    main.model_holder["model"] = object()
    client = TestClient(main.app)

    resp = client.post(
        "/embed",
        json={"audio_base64": "irrelevant-because-auth-should-block-first"},
    )

    assert resp.status_code == 503


def test_embed_allows_unauthenticated_when_explicitly_enabled(load_app, monkeypatch):
    main = load_app(VOICEPRINT_SERVICE_TOKEN="", ALLOW_UNAUTHENTICATED="true")
    main.model_holder["model"] = object()
    monkeypatch.setattr(main, "_run_inference", lambda model, audio: [0.0] * main.EMBEDDING_DIM)
    client = TestClient(main.app)

    wav_bytes = make_wav_bytes(2.0)
    import base64

    resp = client.post("/embed", json={"audio_base64": base64.b64encode(wav_bytes).decode()})

    assert resp.status_code == 200


def test_embed_rejects_missing_bearer_token(load_app):
    main = load_app(VOICEPRINT_SERVICE_TOKEN="secret-token")
    main.model_holder["model"] = object()
    client = TestClient(main.app)

    resp = client.post("/embed", json={"audio_base64": "doesnt-matter"})

    assert resp.status_code == 401


def test_embed_rejects_wrong_bearer_token(load_app):
    main = load_app(VOICEPRINT_SERVICE_TOKEN="secret-token")
    main.model_holder["model"] = object()
    client = TestClient(main.app)

    resp = client.post(
        "/embed",
        json={"audio_base64": "doesnt-matter"},
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert resp.status_code == 401


def test_embed_accepts_correct_bearer_token(load_app, monkeypatch):
    main = load_app(VOICEPRINT_SERVICE_TOKEN="secret-token")
    main.model_holder["model"] = object()
    monkeypatch.setattr(main, "_run_inference", lambda model, audio: [0.0] * main.EMBEDDING_DIM)
    client = TestClient(main.app)

    wav_bytes = make_wav_bytes(2.0)
    import base64

    resp = client.post(
        "/embed",
        json={"audio_base64": base64.b64encode(wav_bytes).decode()},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert resp.status_code == 200
