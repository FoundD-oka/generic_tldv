"""Unit tests for the Soniox async STT adapter (stt.v1 diarization extension).

Covers: token→segment folding (speaker boundaries, silence gaps, cluster id
preservation), golden fixture replay, backward compatibility (no speaker),
and model routing.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from soniox_adapter import (  # noqa: E402
    build_verbose_json_response,
    fold_tokens_to_segments,
    is_soniox_model,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_DIR = REPO_ROOT / "contracts" / "stt" / "v1" / "examples"


def test_is_soniox_model_routes_async_models_only():
    assert is_soniox_model("stt-async-v5")
    assert is_soniox_model("STT-ASYNC-V6")
    assert not is_soniox_model("large-v3-turbo")
    assert not is_soniox_model("whisper-1")
    assert not is_soniox_model(None)
    assert not is_soniox_model("")


def test_fold_alternating_speakers_splits_on_speaker_boundary():
    tokens = [
        {"text": "A1", "start_ms": 0, "end_ms": 100, "speaker": "1"},
        {"text": "A2", "start_ms": 100, "end_ms": 200, "speaker": "1"},
        {"text": "B1", "start_ms": 200, "end_ms": 300, "speaker": "2"},
        {"text": "A3", "start_ms": 300, "end_ms": 400, "speaker": "1"},
    ]
    segments = fold_tokens_to_segments(tokens)
    assert [s["speaker"] for s in segments] == ["1", "2", "1"]
    assert [s["text"] for s in segments] == ["A1A2", "B1", "A3"]
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 0.2
    assert [s["id"] for s in segments] == [0, 1, 2]


def test_fold_splits_same_speaker_on_silence_gap():
    tokens = [
        {"text": "before", "start_ms": 0, "end_ms": 500, "speaker": "1"},
        {"text": "after", "start_ms": 3000, "end_ms": 3400, "speaker": "1"},
    ]
    segments = fold_tokens_to_segments(tokens, max_gap_s=1.0)
    assert len(segments) == 2
    assert all(s["speaker"] == "1" for s in segments)


def test_fold_numeric_speaker_becomes_string_cluster_id():
    tokens = [{"text": "x", "start_ms": 0, "end_ms": 100, "speaker": 3}]
    segments = fold_tokens_to_segments(tokens)
    assert segments[0]["speaker"] == "3"


def test_fold_without_speaker_omits_field_for_backward_compat():
    tokens = [
        {"text": "plain", "start_ms": 0, "end_ms": 100},
        {"text": " text", "start_ms": 100, "end_ms": 200},
    ]
    segments = fold_tokens_to_segments(tokens)
    assert len(segments) == 1
    assert "speaker" not in segments[0]
    assert {"start", "end", "text"} <= set(segments[0].keys())


def test_fold_uses_duration_ms_when_end_ms_missing():
    tokens = [{"text": "x", "start_ms": 1000, "duration_ms": 500, "speaker": "1"}]
    segments = fold_tokens_to_segments(tokens)
    assert segments[0]["start"] == 1.0
    assert segments[0]["end"] == 1.5


def test_fold_skips_empty_or_timeless_tokens():
    tokens = [
        {"text": "", "start_ms": 0, "end_ms": 100, "speaker": "1"},
        {"text": "no-time", "speaker": "1"},
        {"text": "ok", "start_ms": 0, "end_ms": 100, "speaker": "1"},
    ]
    segments = fold_tokens_to_segments(tokens)
    assert len(segments) == 1
    assert segments[0]["text"] == "ok"


def test_fold_empty_input():
    assert fold_tokens_to_segments([]) == []
    assert fold_tokens_to_segments(None) == []


def test_golden_fixture_replay_is_deterministic():
    tokens_payload = json.loads(
        (GOLDEN_DIR / "golden-2-diarization.soniox-tokens.json").read_text(encoding="utf-8")
    )
    expected = json.loads(
        (GOLDEN_DIR / "golden-2-diarization.response.json").read_text(encoding="utf-8")
    )
    actual = build_verbose_json_response(tokens_payload["tokens"], language="ja")
    assert actual == expected


def test_golden_fixture_keeps_cluster_ids_within_file():
    tokens_payload = json.loads(
        (GOLDEN_DIR / "golden-2-diarization.soniox-tokens.json").read_text(encoding="utf-8")
    )
    segments = fold_tokens_to_segments(tokens_payload["tokens"])
    # Speaker "1" appears in two non-adjacent segments with the SAME cluster id.
    speaker_1_segments = [s for s in segments if s["speaker"] == "1"]
    assert len(speaker_1_segments) == 2


def test_build_verbose_json_response_shape():
    response = build_verbose_json_response(
        [{"text": "hello", "start_ms": 0, "end_ms": 900, "speaker": "1"}],
        language="ja",
    )
    assert set(response.keys()) == {
        "text", "language", "language_probability", "duration", "segments",
    }
    assert response["language"] == "ja"
    assert response["duration"] == 0.9
    assert response["text"] == "hello"
