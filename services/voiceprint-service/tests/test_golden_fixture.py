"""Golden fixture determinism check (referenced from
.pipeline/adapters/voiceprint-embedder.adapter.json evidence).

This does NOT exercise the real ECAPA-TDNN model (no download in CI/tests).
It fixes the model's output to the fixture's vector and asserts that the
/embed plumbing — decode, dispatch to inference, shape the response — is
byte-for-byte deterministic for a given input and mocked model output.
"""
import base64
import json
import os

from fastapi.testclient import TestClient

from conftest import make_wav_bytes

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "golden_embed.json")


def _load_fixture():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_golden_fixture_is_well_formed():
    fixture = _load_fixture()
    assert fixture["expected_output"]["embedding_dim"] == 192
    assert len(fixture["expected_output"]["embedding"]) == 192
    assert fixture["expected_output"]["model"] == "ecapa-tdnn"


def test_embed_reproduces_golden_fixture_under_mocked_model(load_app, monkeypatch):
    fixture = _load_fixture()
    expected = fixture["expected_output"]["embedding"]

    main = load_app(ALLOW_UNAUTHENTICATED="true")
    main.model_holder["model"] = object()
    monkeypatch.setattr(main, "_run_inference", lambda model, audio: expected)
    client = TestClient(main.app)

    wav_bytes = make_wav_bytes(fixture["input"]["duration_seconds"], fixture["input"]["sample_rate"])
    resp = client.post("/embed", json={"audio_base64": base64.b64encode(wav_bytes).decode()})

    assert resp.status_code == 200
    body = resp.json()
    assert body["embedding"] == expected
    assert body["model"] == fixture["expected_output"]["model"]
    assert len(body["embedding"]) == fixture["expected_output"]["embedding_dim"]
