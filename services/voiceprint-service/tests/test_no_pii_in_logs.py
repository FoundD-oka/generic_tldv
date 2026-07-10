"""PII rule: /embed must never log the embedding vector or raw audio bytes.

We drive a real request through the app with a *mocked* logger (capturing
every call made to it) and a fake model that returns a fingerprintable
embedding, then assert none of the captured log calls contain the
embedding values, the audio bytes, or their base64 encoding.
"""
import base64
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from conftest import make_wav_bytes


def _flatten_log_calls(mock_logger: MagicMock) -> str:
    """Render every argument passed to any logger.<level>() call as text."""
    chunks = []
    for method_name in ("debug", "info", "warning", "error", "critical", "exception"):
        method = getattr(mock_logger, method_name)
        for call in method.call_args_list:
            args, kwargs = call
            chunks.append(" ".join(str(a) for a in args))
            chunks.append(" ".join(f"{k}={v}" for k, v in kwargs.items()))
    return "\n".join(chunks)


def test_embed_never_logs_embedding_values_or_audio_bytes(load_app, monkeypatch):
    main = load_app(ALLOW_UNAUTHENTICATED="true")
    main.model_holder["model"] = object()

    # A distinctive embedding — if this ever leaks into a log line verbatim,
    # the fingerprint below will catch it.
    fingerprinted_embedding = [123456.789 + i for i in range(main.EMBEDDING_DIM)]
    monkeypatch.setattr(main, "_run_inference", lambda model, audio: fingerprinted_embedding)

    mock_logger = MagicMock()
    monkeypatch.setattr(main, "logger", mock_logger)

    client = TestClient(main.app)
    wav_bytes = make_wav_bytes(2.0)
    audio_b64 = base64.b64encode(wav_bytes).decode()

    resp = client.post("/embed", json={"audio_base64": audio_b64})

    assert resp.status_code == 200
    assert resp.json()["embedding"] == fingerprinted_embedding  # sanity: it really flowed through

    logged_text = _flatten_log_calls(mock_logger)

    # Embedding values must not appear.
    assert "123456.789" not in logged_text
    for value in fingerprinted_embedding[:5]:
        assert str(value) not in logged_text

    # Raw audio bytes / base64 payload must not appear.
    assert audio_b64 not in logged_text
    assert audio_b64[:40] not in logged_text
