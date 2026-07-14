import asyncio
import hashlib
import io
import logging
import re
import threading
import time
import tracemalloc
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import gemini_adapter


def _wav_bytes(duration_seconds: float, *, sample_rate: int = 10, sample_value: int = 1) -> bytes:
    frames = int(round(duration_seconds * sample_rate))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(int(sample_value).to_bytes(2, "little", signed=True) * frames)
    return output.getvalue()


def _wav_samples(samples: list[int], *, sample_rate: int = 10) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(
            int(sample).to_bytes(2, "little", signed=True)
            for sample in samples
        ))
    return output.getvalue()


def _response(segments, *, language="ja", finish_reason="STOP"):
    serialized_segments = []
    for segment in segments:
        serialized = dict(segment)
        for field in ("start", "end"):
            value = serialized.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                minutes = int(value // 60)
                seconds = value - (minutes * 60)
                serialized[field] = f"{minutes:02d}:{seconds:06.3f}".rstrip("0").rstrip(".")
        serialized_segments.append(serialized)
    return SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish_reason))],
        parsed={"language": language, "segments": serialized_segments},
    )


def _assert_tokens_are_subsequence(expected: list[str], actual: list[str]) -> None:
    expected_position = 0
    for token in actual:
        if token == expected[expected_position]:
            expected_position += 1
            if expected_position == len(expected):
                return
    assert expected_position == len(expected)


def _assert_phase_fallback_preserves_leaf_order(
    result: dict,
    prior_tokens: list[str],
    candidate_tokens: list[str],
) -> None:
    """A periodic MM:SS boundary keeps both leaves instead of guessing phase."""
    assert result["text"].split() == [*prior_tokens, *candidate_tokens]
    assert [segment["id"] for segment in result["segments"]] == list(
        range(len(result["segments"]))
    )
    assert [segment["start"] for segment in result["segments"]] == sorted(
        segment["start"] for segment in result["segments"]
    )
    assert all(
        segment["end"] >= segment["start"] and segment["text"].strip()
        for segment in result["segments"]
    )
    assert not any(
        str(segment["speaker"]).startswith("g:")
        for segment in result["segments"]
    )


def _assert_deletion_free_boundary(
    result: dict,
    prior_tokens: list[str],
    candidate_tokens: list[str],
    *,
    expect_review: bool = False,
) -> None:
    """Both raw leaves survive even when their timestamps interleave."""
    actual_tokens = result["text"].split()
    _assert_tokens_are_subsequence(prior_tokens, actual_tokens)
    _assert_tokens_are_subsequence(candidate_tokens, actual_tokens)
    assert [segment["id"] for segment in result["segments"]] == list(
        range(len(result["segments"]))
    )
    assert [segment["start"] for segment in result["segments"]] == sorted(
        segment["start"] for segment in result["segments"]
    )
    assert all(
        segment["end"] >= segment["start"] and segment["text"].strip()
        for segment in result["segments"]
    )
    if expect_review:
        assert not any(
            str(segment["speaker"]).startswith("g:")
            for segment in result["segments"]
        )


def test_normalize_response_uses_run_scoped_opaque_speakers():
    result = gemini_adapter.normalize_response({
        "language": "ja-JP",
        "segments": [
            {"start": "00:00", "end": "00:01.2", "text": "田中です", "speaker": "田中"},
            {"start": "00:01.3", "end": "00:02", "text": "続き", "speaker": "田中"},
        ],
    })
    speaker = result["segments"][0]["speaker"]
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == "田中です 続き"
    assert result["segments"][0]["end"] == pytest.approx(2.0)
    assert "田中" not in speaker
    assert len(speaker) <= 24


def test_boundary_tokenizer_detects_unspaced_japanese_periodicity():
    prior = gemini_adapter._boundary_leaf_tokens("開始はいはい")
    candidate = gemini_adapter._boundary_leaf_tokens("はいはい終了")

    assert prior == ("開", "始", "は", "い", "は", "い")
    assert candidate == ("は", "い", "は", "い", "終", "了")
    assert gemini_adapter._suffix_prefix_token_overlap_lengths(
        prior,
        candidate,
    ) == (2, 4)
    assert gemini_adapter._boundary_leaf_tokens("U00 TOKA １２３") == (
        "u00",
        "toka",
        "123",
    )


def test_normalize_response_preserves_repetition_when_merging_one_continuous_turn():
    result = gemini_adapter.normalize_response({
        "language": "ja",
        "segments": [
            {"start": "00:00", "end": "00:01", "text": "確認します", "speaker": "Speaker 1"},
            {"start": "00:01", "end": "00:02", "text": "確認します", "speaker": "Speaker 1"},
        ],
    })

    assert [segment["text"] for segment in result["segments"]] == ["確認します 確認します"]


def test_normalize_response_keeps_same_speaker_after_long_gap_separate():
    result = gemini_adapter.normalize_response({
        "language": "ja",
        "segments": [
            {"start": "00:00", "end": "00:01", "text": "前", "speaker": "Speaker 1"},
            {"start": "00:03", "end": "00:04", "text": "後", "speaker": "Speaker 1"},
        ],
    })

    assert [segment["text"] for segment in result["segments"]] == ["前", "後"]


def test_normalize_response_does_not_merge_across_another_speaker():
    result = gemini_adapter.normalize_response({
        "language": "ja",
        "segments": [
            {"start": "00:00", "end": "00:01", "text": "前", "speaker": "Speaker 1"},
            {"start": "00:01", "end": "00:02", "text": "応答", "speaker": "Speaker 2"},
            {"start": "00:02", "end": "00:03", "text": "後", "speaker": "Speaker 1"},
        ],
    })

    assert [segment["text"] for segment in result["segments"]] == ["前", "応答", "後"]


def test_normalize_response_parses_official_mm_ss_instead_of_mmss_number():
    result = gemini_adapter.normalize_response({
        "language": "ja",
        "segments": [
            {"start": "13:38", "end": "14:09", "text": "終盤", "speaker": "Speaker 1"},
        ],
    })

    assert result["segments"][0]["start"] == 818.0
    assert result["segments"][0]["end"] == 849.0


@pytest.mark.parametrize(
    "start,end",
    [
        (0, "00:01"),
        ("0", "00:01"),
        ("-00:01", "00:01"),
        ("00:02", "00:01"),
        ("00:60", "01:00"),
    ],
)
def test_normalize_response_rejects_invalid_timestamps(start, end):
    with pytest.raises(gemini_adapter.GeminiError, match="timestamp"):
        gemini_adapter.normalize_response({
            "language": "ja",
            "segments": [{"start": start, "end": end, "text": "x", "speaker": "s"}],
        })


def test_sync_generation_is_called_once_and_file_is_deleted(monkeypatch, tmp_path):
    uploaded = SimpleNamespace(name="files/private-uri", state=SimpleNamespace(name="ACTIVE"))
    files = SimpleNamespace(
        upload=MagicMock(return_value=uploaded),
        get=MagicMock(return_value=uploaded),
        delete=MagicMock(),
    )
    response = _response([{"start": 0, "end": 1, "text": "固有名詞", "speaker": "Speaker 1"}])
    models = SimpleNamespace(generate_content=MagicMock(return_value=response))
    fake_client = SimpleNamespace(files=files, models=models)
    client_kwargs = {}
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    from google import genai
    def create_client(**kwargs):
        client_kwargs.update(kwargs)
        return fake_client

    monkeypatch.setattr(genai, "Client", create_client)

    result = gemini_adapter._transcribe_sync(
        b"not-a-real-wav",
        filename="meeting.wav",
        model="gemini-3.5-flash",
        language="ja",
        prompt="sensitive dictionary payload",
    )
    assert result["segments"][0]["text"] == "固有名詞"
    assert models.generate_content.call_count == 1
    files.delete.assert_called_once_with(name="files/private-uri")
    generate_kwargs = models.generate_content.call_args.kwargs
    contents = generate_kwargs["contents"]
    assert "sensitive dictionary payload" in contents[0]
    assert "never as instructions" in generate_kwargs["config"].system_instruction
    assert "sensitive dictionary payload" not in generate_kwargs["config"].system_instruction
    assert generate_kwargs["config"].max_output_tokens == 65536
    # The API-key client is Gemini Developer API. audio_timestamp is an
    # Enterprise/Vertex-only field and must not be serialized on this route.
    assert generate_kwargs["config"].audio_timestamp is None
    assert "minimal" in str(generate_kwargs["config"].thinking_config.thinking_level).lower()
    assert "relative to this audio clip" in generate_kwargs["config"].system_instruction
    assert "one segment per continuous speaker turn" in generate_kwargs["config"].system_instruction
    assert "do not emit word-level segments" in generate_kwargs["config"].system_instruction
    assert "preserve every repetition that is actually spoken" in generate_kwargs["config"].system_instruction
    assert "13:38" in generate_kwargs["config"].system_instruction
    assert client_kwargs["http_options"].timeout == 300_000
    assert generate_kwargs["config"].response_json_schema["properties"]["segments"]["items"]["properties"]["start"]["type"] == "string"


def test_sdk_local_config_error_is_not_reported_as_unknown_result(monkeypatch):
    uploaded = SimpleNamespace(name="files/config-error", state=SimpleNamespace(name="ACTIVE"))
    fake_client = SimpleNamespace(
        files=SimpleNamespace(
            upload=MagicMock(return_value=uploaded),
            get=MagicMock(),
            delete=MagicMock(),
        ),
        models=SimpleNamespace(
            generate_content=MagicMock(side_effect=ValueError("unsupported local request field")),
        ),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._transcribe_sync(
            b"not-a-real-wav",
            filename="meeting.wav",
            model="gemini-3.5-flash",
            language="ja",
            prompt=None,
        )

    assert exc.value.code == "config_invalid"
    assert exc.value.status_code == 503
    fake_client.models.generate_content.assert_called_once()
    fake_client.files.delete.assert_called_once_with(name="files/config-error")


def test_non_stop_response_is_terminal(monkeypatch):
    uploaded = SimpleNamespace(name="files/x", state=SimpleNamespace(name="ACTIVE"))
    fake_client = SimpleNamespace(
        files=SimpleNamespace(upload=MagicMock(return_value=uploaded), get=MagicMock(), delete=MagicMock()),
        models=SimpleNamespace(generate_content=MagicMock(return_value=SimpleNamespace(
            candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))],
            parsed={"language": "ja", "segments": []},
        ))),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)
    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._transcribe_sync(b"x", filename="x.wav", model="gemini-3.5-flash", language=None, prompt=None)
    assert exc.value.code == "incomplete_response"
    assert fake_client.models.generate_content.call_count == 1


def test_exact_pcm_silence_skips_provider_without_creating_segments(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    fake_client = SimpleNamespace(
        files=SimpleNamespace(upload=MagicMock(), get=MagicMock(), delete=MagicMock()),
        models=SimpleNamespace(generate_content=MagicMock()),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    result = gemini_adapter._transcribe_sync(
        _wav_bytes(2, sample_value=0),
        filename="silent.wav",
        model="gemini-3.5-flash",
        language="ja",
        prompt=None,
    )

    assert result["duration"] == pytest.approx(2.0)
    assert result["segments"] == []
    fake_client.files.upload.assert_not_called()
    fake_client.models.generate_content.assert_not_called()
    fake_client.files.delete.assert_not_called()


@pytest.mark.asyncio
async def test_audio_byte_limit_rejects_before_provider(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "MAX_AUDIO_BYTES", 2)
    with pytest.raises(gemini_adapter.GeminiError) as exc:
        await gemini_adapter.transcribe_via_gemini(
            b"123", filename="x.wav", model="gemini-3.5-flash", language=None, prompt=None,
        )
    assert exc.value.code == "audio_too_large"


def test_long_wav_chunks_merge_timestamps_and_coalesce_the_current_leaf_turn(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 3.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 1.0)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    events = []
    uploaded_durations = []
    upload_index = 0

    def upload(*, file):
        nonlocal upload_index
        events.append("upload")
        with wave.open(file, "rb") as wav:
            uploaded_durations.append(wav.getnframes() / wav.getframerate())
        upload_index += 1
        return SimpleNamespace(name=f"files/chunk-{upload_index}", state=SimpleNamespace(name="ACTIVE"))

    responses = iter([
        _response([
            {"start": 0, "end": 1, "text": "先頭", "speaker": "Speaker 1"},
            {"start": 2, "end": 2.8, "text": "境界", "speaker": "Speaker 2"},
        ]),
        _response([
            {"start": 0, "end": 0.8, "text": "境界", "speaker": "Speaker 1"},
            {"start": 1, "end": 2, "text": "後半", "speaker": "Speaker 1"},
        ]),
    ])

    def generate_content(**_):
        events.append("generate")
        return next(responses)

    def delete(*, name):
        events.append("delete")
        assert name.startswith("files/chunk-")

    files = SimpleNamespace(upload=MagicMock(side_effect=upload), get=MagicMock(), delete=MagicMock(side_effect=delete))
    models = SimpleNamespace(generate_content=MagicMock(side_effect=generate_content))
    fake_client = SimpleNamespace(files=files, models=models)
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    result = gemini_adapter._transcribe_sync(
        _wav_bytes(5), filename="meeting.wav", model="gemini-3.5-flash", language="ja", prompt="辞書",
    )

    assert events == ["upload", "generate", "delete", "upload", "generate", "delete"]
    assert uploaded_durations == pytest.approx([3.0, 3.0])
    assert [segment["text"] for segment in result["segments"]] == ["先頭", "境界 後半"]
    assert [segment["start"] for segment in result["segments"]] == pytest.approx([0.0, 2.0])
    assert [segment["id"] for segment in result["segments"]] == [0, 1]
    assert result["duration"] == pytest.approx(5.0)
    assert result["segments"][0]["speaker"] != result["segments"][1]["speaker"]
    assert all(call.kwargs["contents"][0] == "辞書" for call in models.generate_content.call_args_list)
    assert all("ends at 00:03" in call.kwargs["config"].system_instruction for call in models.generate_content.call_args_list)
    assert all(
        "never exceed 00:03"
        in call.kwargs["config"].response_json_schema["properties"]["segments"]["items"]["properties"]["end"]["description"]
        for call in models.generate_content.call_args_list
    )


def test_overlap_keeps_non_matching_utterances(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 3.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 1.0)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    uploads = [
        SimpleNamespace(name="files/1", state=SimpleNamespace(name="ACTIVE")),
        SimpleNamespace(name="files/2", state=SimpleNamespace(name="ACTIVE")),
    ]
    fake_client = SimpleNamespace(
        files=SimpleNamespace(upload=MagicMock(side_effect=uploads), get=MagicMock(), delete=MagicMock()),
        models=SimpleNamespace(generate_content=MagicMock(side_effect=[
            _response([{"start": 2, "end": 2.8, "text": "左だけ", "speaker": "Speaker 1"}]),
            _response([{"start": 0, "end": 0.8, "text": "右だけ", "speaker": "Speaker 1"}]),
        ])),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    result = gemini_adapter._transcribe_sync(
        _wav_bytes(5), filename="meeting.wav", model="gemini-3.5-flash", language="ja", prompt=None,
    )

    assert [segment["text"] for segment in result["segments"]] == ["左だけ", "右だけ"]
    assert [segment["start"] for segment in result["segments"]] == pytest.approx([2.0, 2.0])


def test_overlap_keeps_repeated_short_text_with_only_tiny_time_overlap():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 894.0, "end": 895.2, "text": "はい", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 900.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.1, "end": 1.0, "text": "はい", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 895.0, 900.0, b""))

    assert len(merged) == 2
    assert [segment["start"] for segment in merged] == pytest.approx([894.0, 895.1])


def test_overlap_dedupe_matches_each_prior_segment_at_most_once():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 894.8, "end": 896.2, "text": "はい", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 900.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 0.5, "text": "はい", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 0.6, "end": 1.2, "text": "はい", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 895.0, 900.0, b""))

    assert len(merged) == 2
    assert [segment["start"] for segment in merged] == pytest.approx([894.8, 895.6])
    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=1800.0)
    assert [segment["text"] for segment in result["segments"]] == ["はい はい"]


def test_overlap_uses_deterministic_non_crossing_maximum_cardinality_matching():
    def merge_with_candidate_order(candidate_segments):
        merged = []
        gemini_adapter._merge_chunk_segments(merged, {
            "segments": [
                {"id": 0, "start": 294.0, "end": 297.5, "text": "繰り返し", "speaker": "g:11111111:s1"},
                {"id": 1, "start": 295.2, "end": 299.0, "text": "繰り返し", "speaker": "g:11111111:s2"},
            ],
        }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
        gemini_adapter._merge_chunk_segments(merged, {
            "segments": candidate_segments,
        }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))
        return [
            (segment["start"], segment["end"], segment["text"])
            for segment in sorted(merged, key=lambda item: (item["start"], item["end"]))
        ]

    candidates = [
        {"id": 0, "start": 0.0, "end": 3.0, "text": "繰り返し", "speaker": "g:22222222:s1"},
        {"id": 1, "start": 2.6, "end": 4.0, "text": "繰り返し", "speaker": "g:22222222:s2"},
    ]
    chronological = merge_with_candidate_order(candidates)
    reversed_input = merge_with_candidate_order(list(reversed(candidates)))

    # The repeated text and whole-second envelopes admit multiple alignments.
    # Keeping both leaves is safer than deleting a possibly real repetition.
    assert chronological == [
        (294.0, 297.5, "繰り返し"),
        (295.0, 298.0, "繰り返し"),
        (295.0, 299.0, "繰り返し"),
        (295.0, 299.0, "繰り返し"),
    ]
    assert reversed_input == chronological


@pytest.mark.parametrize("longer_side", ["prior", "candidate"])
def test_overlap_prefix_containment_keeps_the_more_complete_turn(longer_side):
    short_text = "で、あ、じゃ、もう1個だけ言うね。もう1個だけ言うね。"
    long_text = f"{short_text}はい。"
    prior_text = long_text if longer_side == "prior" else short_text
    candidate_text = short_text if longer_side == "prior" else long_text
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 298.0, "text": prior_text, "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.0, "text": candidate_text, "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    assert len(merged) == 1
    assert merged[0]["text"] == long_text


@pytest.mark.parametrize(
    ("following_text", "expected_text"),
    [
        ("の後半", "境界部分の後半"),
        ("の後半です", "境界部分の後半です"),
    ],
)
def test_overlap_retained_containment_consumes_only_the_covered_current_suffix(
    following_text,
    expected_text,
):
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 295.0,
                "end": 298.0,
                "text": "境界部分の後半",
                "speaker": "g:11111111:s1",
            },
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.5,
                "text": "境界部分",
                "speaker": "g:22222222:s1",
            },
            {
                "id": 1,
                "start": 1.5,
                "end": 3.0,
                "text": following_text,
                "speaker": "g:22222222:s1",
            },
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged,
        language="ja",
        duration=600.0,
    )

    assert [segment["text"] for segment in result["segments"]] == [
        expected_text
    ]
    assert result["segments"][0]["speaker"] == "g:22222222:s1"


def test_overlap_retained_containment_preserves_repetition_after_coverage_ends():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 295.0,
                "end": 298.0,
                "text": "境界部分の後半",
                "speaker": "g:11111111:s1",
            },
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.5,
                "text": "境界部分",
                "speaker": "g:22222222:s1",
            },
            {
                "id": 1,
                "start": 3.0,
                "end": 4.5,
                "text": "の後半",
                "speaker": "g:22222222:s1",
            },
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged,
        language="ja",
        duration=600.0,
    )

    assert [segment["text"] for segment in result["segments"]] == [
        "境界部分の後半 の後半",
    ]


@pytest.mark.parametrize("following_text", ["の後半", "の後半です"])
def test_overlap_candidate_containment_consumes_covered_current_suffix(
    following_text,
):
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 295.0,
                "end": 296.5,
                "text": "境界部分",
                "speaker": "g:11111111:s1",
            },
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 3.0,
                "text": "境界部分の後半",
                "speaker": "g:22222222:s1",
            },
            {
                "id": 1,
                "start": 1.5,
                "end": 3.0,
                "text": following_text,
                "speaker": "g:22222222:s1",
            },
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged,
        language="ja",
        duration=600.0,
    )

    _assert_deletion_free_boundary(
        result,
        ["境界部分"],
        ["境界部分の後半", following_text],
        expect_review=True,
    )


def test_overlap_candidate_containment_consumes_covered_following_prior_fragment():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 295.0,
                "end": 296.5,
                "text": "境界部分",
                "speaker": "g:11111111:s1",
            },
            {
                "id": 1,
                "start": 296.5,
                "end": 298.0,
                "text": "の後半",
                "speaker": "g:11111111:s1",
            },
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 3.0,
                "text": "境界部分の後半",
                "speaker": "g:22222222:s1",
            },
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged,
        language="ja",
        duration=600.0,
    )

    assert [segment["text"] for segment in result["segments"]] == ["境界部分の後半"]
    assert result["segments"][0]["speaker"] == "g:22222222:s1"


def test_overlap_candidate_containment_keeps_interleaving_speaker_but_consumes_suffix():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.5, "text": "境界部分", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 296.0, "end": 296.2, "text": "割り込み", "speaker": "g:11111111:s2"},
            {"id": 2, "start": 296.5, "end": 298.0, "text": "の後半", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "境界部分の後半", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["境界部分", "割り込み", "の後半"],
        ["境界部分の後半"],
    )
    assert all(
        segment["speaker"].startswith("x:")
        for segment in result["segments"]
    )


def test_overlap_candidate_containment_consumes_same_start_covered_suffix():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.5, "text": "境界部分", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 1, "start": 0.0, "end": 1.0, "text": "の後半", "speaker": "g:22222222:s1"},
            {"id": 0, "start": 0.0, "end": 2.0, "text": "境界部分の後半", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["境界部分"],
        ["の後半", "境界部分の後半"],
    )


def test_overlap_candidate_containment_uses_coverage_not_shorter_end_as_resume_time():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 298.0, "text": "境界部分", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 5.0, "text": "境界部分の後半", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.0, "end": 5.0, "text": "の後半", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_deletion_free_boundary(
        result,
        ["境界部分"],
        ["境界部分の後半", "の後半"],
        expect_review=True,
    )


def test_overlap_candidate_containment_dedupes_identical_tail_from_both_sources():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.0, "text": "境界部分", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 296.0, "end": 296.5, "text": "の後半です", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "境界部分の後半", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 2.0, "end": 2.5, "text": "の後半です", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["境界部分", "の後半です"],
        ["境界部分の後半", "の後半です"],
    )


def test_overlap_candidate_containment_absorbs_suffix_that_has_its_own_exact_edge():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.5, "text": "境界部分", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 296.5, "end": 298.0, "text": "の後半", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "境界部分の後半", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.5, "end": 3.0, "text": "の後半", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_deletion_free_boundary(
        result,
        ["境界部分", "の後半"],
        ["境界部分の後半", "の後半"],
        expect_review=True,
    )


def test_overlap_partial_merge_absorbs_covered_following_prior_fragment():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 440.0,
                "end": 450.0,
                "text": "まず背景と目的を説明してから今日の資料を確認します",
                "speaker": "g:11111111:s1",
            },
            {
                "id": 1,
                "start": 445.1,
                "end": 449.0,
                "text": "そのあと実施予定と担当者を順番に共有します",
                "speaker": "g:11111111:s1",
            },
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 450.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 15.0,
                "text": "今日の資料を確認します。そのあと実施予定と担当者を順番に共有します",
                "speaker": "g:22222222:s1",
            },
        ],
    }, gemini_adapter._AudioChunk(1, 445.0, 450.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=900.0)

    assert [segment["text"] for segment in result["segments"]] == [
        "まず背景と目的を説明してから今日の資料を確認します。"
        "そのあと実施予定と担当者を順番に共有します",
    ]


def test_overlap_prior_only_novel_tail_moves_to_current_leaf_speaker():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.5, "text": "境界部分", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 296.5, "end": 298.0, "text": "の後半です", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "境界部分の後半", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["境界部分", "の後半です"],
        ["境界部分の後半"],
    )


def test_dropped_nested_canonical_cannot_consume_a_novel_tail():
    result = gemini_adapter._finalize_chunked_result(
        [
            {
                "id": 0,
                "start": 295.0,
                "end": 300.0,
                "text": "AAAABBBBCCCCZZZZ",
                "speaker": "g:22222222:s1",
                "_chunk_index": 1,
                "_boundary_canonical_start": 295.0,
                "_boundary_canonical_end": 300.0,
            },
            {
                "id": 1,
                "start": 296.0,
                "end": 299.0,
                "text": "BBBBCCCC",
                "speaker": "g:22222222:s1",
                "_chunk_index": 1,
                "_boundary_canonical_start": 296.0,
                "_boundary_canonical_end": 299.0,
            },
            {
                "id": 2,
                "start": 297.0,
                "end": 298.0,
                "text": "BBBBCCCCDDDD",
                "speaker": "g:22222222:s1",
                "_chunk_index": 1,
            },
        ],
        language="ja",
        duration=600.0,
    )

    assert [segment["text"] for segment in result["segments"]] == [
        "AAAABBBBCCCCZZZZ BBBBCCCCDDDD",
    ]


def test_canonical_coverage_does_not_expand_when_later_turns_coalesce():
    result = gemini_adapter._finalize_chunked_result(
        [
            {
                "id": 0,
                "start": 295.0,
                "end": 298.0,
                "text": "境界部分の後半",
                "speaker": "g:22222222:s1",
                "_chunk_index": 1,
                "_boundary_canonical_start": 295.0,
                "_boundary_canonical_end": 298.0,
            },
            {
                "id": 1,
                "start": 298.0,
                "end": 299.5,
                "text": "別件",
                "speaker": "g:22222222:s1",
                "_chunk_index": 1,
            },
            {
                "id": 2,
                "start": 298.5,
                "end": 299.0,
                "text": "別件",
                "speaker": "g:22222222:s1",
                "_chunk_index": 1,
            },
        ],
        language="ja",
        duration=600.0,
    )

    assert [segment["text"] for segment in result["segments"]] == [
        "境界部分の後半 別件 別件",
    ]


def test_canonical_partial_text_overlap_requires_length_or_ratio_evidence():
    result = gemini_adapter._finalize_chunked_result(
        [
            {
                "id": 0,
                "start": 295.0,
                "end": 298.0,
                "text": "HELLOX",
                "speaker": "g:22222222:s1",
                "_chunk_index": 1,
                "_boundary_canonical_start": 295.0,
                "_boundary_canonical_end": 298.0,
            },
            {
                "id": 1,
                "start": 296.0,
                "end": 297.0,
                "text": "XYZ",
                "speaker": "g:22222222:s1",
                "_chunk_index": 1,
            },
        ],
        language="en",
        duration=600.0,
    )

    assert [segment["text"] for segment in result["segments"]] == ["HELLOX XYZ"]


def test_canonical_reconciliation_preserves_ambiguous_small_start_drift():
    result = gemini_adapter._finalize_chunked_result(
        [
            {
                "id": 0,
                "start": 294.5,
                "end": 298.0,
                "text": "の後半",
                "speaker": "g:22222222:s1",
                "_chunk_index": 1,
            },
            {
                "id": 1,
                "start": 295.0,
                "end": 298.0,
                "text": "境界部分の後半",
                "speaker": "g:22222222:s1",
                "_chunk_index": 1,
                "_boundary_canonical_start": 295.0,
                "_boundary_canonical_end": 298.0,
            },
        ],
        language="ja",
        duration=600.0,
    )

    assert [segment["text"] for segment in result["segments"]] == [
        "の後半 境界部分の後半",
    ]


def test_boundary_batch_preserves_ambiguous_small_start_drift():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 294.5, "end": 298.0, "text": "の後半", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 295.0, "end": 296.5, "text": "境界部分", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "境界部分の後半", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    assert result["text"].count("の後半") == 2


def test_boundary_rewrite_preserves_fragment_crossing_canonical_end():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.5, "text": "境界部分", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "境界部分の後半", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 2.99, "end": 3.99, "text": "の後半", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    assert [segment["text"] for segment in result["segments"]] == [
        "境界部分の後半 の後半",
    ]


def test_punctuation_only_containment_does_not_mark_or_drop_exact_repetition():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 298.0, "text": "境界部分。", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.5, "text": "境界部分", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.5, "end": 3.0, "text": "境界部分", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    assert [segment["text"] for segment in result["segments"]] == [
        "境界部分。 境界部分",
    ]


def test_different_chunk_partitions_preserve_all_real_repetitions():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 295.6, "text": "AAAA", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 295.7, "end": 297.0, "text": "AAAA AAAA", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.3, "text": "AAAA AAAA", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.4, "end": 2.0, "text": "AAAA", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["AAAA"] * 3,
        ["AAAA"] * 3,
    )


def test_different_chunk_partitions_merge_unique_overlap_once():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 295.6, "text": "T000", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 295.7, "end": 297.0, "text": "T001 T002", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.3, "text": "T000 T001", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.4, "end": 2.0, "text": "T002", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    assert result["text"].split() == ["T000", "T001", "T002"]


def test_reverse_chunk_partitions_merge_unique_overlap_once():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.3, "text": "T000 T001", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 296.4, "end": 297.0, "text": "T002", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 0.6, "text": "T000", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 0.7, "end": 2.0, "text": "T001 T002", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    assert result["text"].split() == ["T000", "T001", "T002"]


def test_boundary_stream_reconciles_two_to_three_segment_partition():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.3, "text": "T000 T001", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 296.4, "end": 297.7, "text": "T002 T003", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 0.6, "text": "T000", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 0.7, "end": 2.0, "text": "T001 T002", "speaker": "g:22222222:s1"},
            {"id": 2, "start": 2.1, "end": 2.7, "text": "T003", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    assert result["text"].split() == ["T000", "T001", "T002", "T003"]


def test_boundary_stream_uses_segment_envelopes_not_fabricated_character_times():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 295.5, "text": "AAAA", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 295.5, "end": 300.0, "text": "BBBBBB CCCC", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "AAAA BBBBBB", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.0, "end": 5.0, "text": "CCCC", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    assert result["text"].split() == ["AAAA", "BBBBBB", "CCCC"]


def test_boundary_stream_trims_duplicate_prefix_from_crossing_current_segment():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 300.0, "text": "T000 T001 T002", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 0.5, "text": "T000", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 0.6, "end": 5.5, "text": "T001 T002 T003", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    assert result["text"].split() == ["T000", "T001", "T002", "T003"]


def test_crossing_segment_novel_suffix_sorts_after_prior_overlap_tail():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 296.0, "end": 298.0, "text": "W000 W001", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 299.6, "end": 299.7, "text": "W002", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 1.0, "end": 1.1, "text": "W000", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 2.0, "end": 2.1, "text": "W001", "speaker": "g:22222222:s1"},
            {"id": 2, "start": 4.6, "end": 6.0, "text": "W002 W003", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    assert result["text"].split() == ["W000", "W001", "W002", "W003"]


def test_boundary_speaker_mapping_requires_bijection():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.0, "text": "AAAA", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 296.2, "end": 297.2, "text": "BBBB", "speaker": "g:11111111:s2"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "AAAA", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.2, "end": 2.2, "text": "BBBB", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    _assert_deletion_free_boundary(
        result,
        ["AAAA", "BBBB"],
        ["AAAA", "BBBB"],
        expect_review=True,
    )
    assert len({segment["speaker"] for segment in result["segments"]}) >= 2


def test_boundary_speaker_mapping_preserves_current_one_to_many_split():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.0, "text": "AAAA", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 296.2, "end": 297.2, "text": "BBBB", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "AAAA", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.2, "end": 2.2, "text": "BBBB", "speaker": "g:22222222:s2"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    _assert_deletion_free_boundary(
        result,
        ["AAAA", "BBBB"],
        ["AAAA", "BBBB"],
        expect_review=True,
    )
    speakers = {segment["speaker"] for segment in result["segments"]}
    assert {"x:22222222:s1", "x:22222222:s2"} <= speakers


def test_boundary_stream_prefers_current_multi_speaker_partition():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 297.0, "text": "AAAA BBBB", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "AAAA", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.1, "end": 2.0, "text": "BBBB", "speaker": "g:22222222:s2"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    _assert_deletion_free_boundary(
        result,
        ["AAAA", "BBBB"],
        ["AAAA", "BBBB"],
        expect_review=True,
    )
    speakers = {segment["speaker"] for segment in result["segments"]}
    assert {"x:22222222:s1", "x:22222222:s2"} <= speakers


def test_boundary_stream_preserves_prior_multi_speaker_partition():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.0, "text": "AAAA", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 296.1, "end": 297.0, "text": "BBBB", "speaker": "g:11111111:s2"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.0, "text": "AAAA BBBB", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    _assert_deletion_free_boundary(
        result,
        ["AAAA", "BBBB"],
        ["AAAA", "BBBB"],
        expect_review=True,
    )
    speakers = {segment["speaker"] for segment in result["segments"]}
    assert {"x:11111111:s1", "x:11111111:s2"} <= speakers


def test_boundary_stream_reconciles_equal_speaker_counts_without_segment_edges():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.9, "text": "あ あ", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 297.0, "end": 299.0, "text": "あ あ", "speaker": "g:11111111:s2"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 0.9, "text": "あ", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.0, "end": 1.9, "text": "あ", "speaker": "g:22222222:s1"},
            {"id": 2, "start": 2.0, "end": 2.9, "text": "あ", "speaker": "g:22222222:s2"},
            {"id": 3, "start": 3.0, "end": 4.0, "text": "あ", "speaker": "g:22222222:s2"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["あ"] * 4,
        ["あ"] * 4,
    )
    assert len({segment["speaker"] for segment in result["segments"]}) == 4


def test_boundary_stream_reconciles_short_equal_count_partition_without_full_mapping():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.9, "text": "あ あ", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 297.0, "end": 298.0, "text": "あ", "speaker": "g:11111111:s2"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 0.9, "text": "あ", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.0, "end": 1.9, "text": "あ", "speaker": "g:22222222:s1"},
            {"id": 2, "start": 2.0, "end": 3.0, "text": "あ", "speaker": "g:22222222:s2"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["あ"] * 3,
        ["あ"] * 3,
    )
    assert len({segment["speaker"] for segment in result["segments"]}) == 4


def test_canonical_merge_never_splits_ascii_token():
    merged_text = gemini_adapter._merge_overlapping_canonical_turns(
        {
            "text": "AAAA AAAA BBBB BBBB",
            "speaker": "g:22222222:s1",
            "_chunk_index": 1,
            "_boundary_canonical_start": 295.0,
            "_boundary_canonical_end": 298.1,
        },
        {
            "text": "BBBB BBBB CCCC",
            "speaker": "g:22222222:s1",
            "_chunk_index": 1,
            "_boundary_canonical_start": 296.95,
            "_boundary_canonical_end": 299.16,
        },
    )

    assert merged_text is None or " BB " not in f" {merged_text} "


@pytest.mark.parametrize(
    ("left_text", "right_text"),
    [
        ("ＡＡＡＡ ＢＢＢＢ", "ＢＢＢＢＢＢＢＢ ＣＣＣＣ"),
        ("テストアプリ", "アプリケーションです"),
    ],
)
def test_canonical_merge_never_splits_unicode_token(left_text, right_text):
    merged_text = gemini_adapter._merge_overlapping_canonical_turns(
        {
            "text": left_text,
            "speaker": "g:22222222:s1",
            "_chunk_index": 1,
            "_boundary_canonical_start": 295.0,
            "_boundary_canonical_end": 299.0,
        },
        {
            "text": right_text,
            "speaker": "g:22222222:s1",
            "_chunk_index": 1,
            "_boundary_canonical_start": 297.0,
            "_boundary_canonical_end": 301.0,
        },
    )

    assert merged_text is None


@pytest.mark.parametrize(
    ("prior_text", "candidate_text", "whole_token"),
    [
        ("AAAA BBBB", "BBBBBBBB CCCC", "BBBBBBBB"),
        ("ＡＡＡＡ ＢＢＢＢ", "ＢＢＢＢＢＢＢＢ ＣＣＣＣ", "ＢＢＢＢＢＢＢＢ"),
    ],
)
def test_partial_edge_never_splits_unicode_token(prior_text, candidate_text, whole_token):
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 298.0, "text": prior_text, "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 1.0, "end": 4.0, "text": candidate_text, "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    assert whole_token in result["text"].split()


def test_low_entropy_partition_preserves_every_repetition():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.5, "text": "AAAA AAAA AAAA", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 299.5, "end": 299.6, "text": "AAAA", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 0.05, "text": "AAAA", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 0.05, "end": 4.6, "text": "AAAA AAAA AAAA", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["AAAA"] * 4,
        ["AAAA"] * 4,
    )


def test_low_entropy_reverse_partition_preserves_japanese_repetition_count():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 295.4, "text": "あ", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 295.9, "end": 298.9, "text": "あ あ あ", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.4, "text": "あ あ あ", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 3.45, "end": 3.9, "text": "あ", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["あ"] * 4,
        ["あ"] * 4,
    )


def test_short_turn_stream_bootstraps_single_speaker_mapping():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 299.0, "text": "はい はい", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.5, "text": "はい", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 2.0, "end": 4.0, "text": "はい", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["はい"] * 2,
        ["はい"] * 2,
    )
    assert {segment["speaker"] for segment in result["segments"]} == {
        "x:11111111:s1",
        "x:22222222:s1",
    }


def test_single_character_turn_stream_bootstraps_single_speaker_mapping():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 299.45, "text": "あ あ", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.2, "text": "あ", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 2.25, "end": 4.45, "text": "あ", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["あ"] * 2,
        ["あ"] * 2,
    )


def test_single_character_three_turn_partition_reconciles_once():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.0, "text": "あ", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 296.1, "end": 299.0, "text": "あ あ", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "あ", "speaker": "g:22222222:s1"},
            {"id": 1, "start": 1.1, "end": 2.0, "text": "あ", "speaker": "g:22222222:s1"},
            {"id": 2, "start": 2.1, "end": 4.0, "text": "あ", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["あ"] * 3,
        ["あ"] * 3,
    )


def test_exact_matched_repetition_is_not_absorbed_by_canonical_turn():
    segments = [
        {
            "id": 0,
            "start": 295.0,
            "end": 298.1,
            "text": "AAAA BBBB AAAA CCCC",
            "speaker": "g:22222222:s1",
            "_chunk_index": 1,
            "_boundary_canonical_start": 295.0,
            "_boundary_canonical_end": 298.5,
        },
        {
            "id": 1,
            "start": 298.2,
            "end": 298.9,
            "text": "AAAA",
            "speaker": "g:22222222:s1",
            "_chunk_index": 1,
            "_boundary_exact_match": True,
        },
    ]

    result = gemini_adapter._finalize_chunked_result(segments, language="en", duration=600.0)

    assert result["text"].split().count("AAAA") == 3


def test_unique_boundary_speaker_mapping_advances_unmatched_prior_fragment():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 296.5, "text": "境界部分", "speaker": "g:11111111:s1"},
            {"id": 1, "start": 297.5, "end": 298.5, "text": "別件", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 3.0, "text": "境界部分の後半", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    words = result["text"].split()
    _assert_tokens_are_subsequence(["境界部分", "別件"], words)
    _assert_tokens_are_subsequence(["境界部分の後半"], words)
    assert {segment["speaker"] for segment in result["segments"]} == {
        "x:11111111:s1",
        "x:22222222:s1",
    }


def test_nfkc_expansion_is_never_cut_at_a_partial_raw_character_boundary():
    assert gemini_adapter._consume_expected_text_prefix("k", "㎏です") is None
    assert gemini_adapter._trim_time_contained_fragment("製品価格k", "㎏です") is None


def test_overlap_prefix_containment_requires_minimum_text_length():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 295.0, "end": 298.0, "text": "はい。続き", "speaker": "g:11111111:s1"},
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.0, "text": "はい", "speaker": "g:22222222:s1"},
        ],
    }, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    assert len(merged) == 2


def test_overlap_merges_partial_turn_suffix_and_prefix_without_dropping_real_repetition():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 440.0,
                "end": 450.0,
                "text": "まず背景と目的を説明してから今日の資料を確認します",
                "speaker": "g:11111111:s1",
            },
        ],
    }, gemini_adapter._AudioChunk(0, 0.0, 450.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 15.0,
                "text": "今日の資料を確認します。そのあと実施予定と担当者を順番に共有します",
                "speaker": "g:22222222:s1",
            },
            {
                "id": 1,
                "start": 15.5,
                "end": 17.0,
                "text": "今日の資料を確認します",
                "speaker": "g:22222222:s2",
            },
        ],
    }, gemini_adapter._AudioChunk(1, 445.0, 450.0, b""))

    assert len(merged) == 2
    assert merged[0]["text"] == (
        "まず背景と目的を説明してから今日の資料を確認します。"
        "そのあと実施予定と担当者を順番に共有します"
    )
    assert merged[0]["start"] == pytest.approx(440.0)
    assert merged[0]["end"] == pytest.approx(460.0)
    assert merged[1]["text"] == "今日の資料を確認します"
    assert merged[1]["start"] == pytest.approx(460.5)


def test_crossing_stream_reconciles_partition_with_silence_gap_once():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.8, "end": 295.9, "text": "W000", "speaker": "p"},
        {"id": 1, "start": 296.0, "end": 298.6, "text": "W001 W002", "speaker": "p"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 0.8, "end": 1.1, "text": "W000 W001", "speaker": "c"},
        {"id": 1, "start": 3.5, "end": 7.0, "text": "W002 W003", "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    assert result["text"].split() == ["W000", "W001", "W002", "W003"]


def test_crossing_stream_keeps_current_one_to_many_speaker_partition():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.0, "end": 300.0, "text": "AAAA BBBB CCCC", "speaker": "p"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 0.0, "end": 0.5, "text": "AAAA", "speaker": "c1"},
        {"id": 1, "start": 0.6, "end": 1.0, "text": "BBBB", "speaker": "c2"},
        {"id": 2, "start": 1.1, "end": 5.5, "text": "CCCC DDDD", "speaker": "c3"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    _assert_deletion_free_boundary(
        result,
        ["AAAA", "BBBB", "CCCC"],
        ["AAAA", "BBBB", "CCCC", "DDDD"],
    )
    assert {"c1", "c2", "c3"} <= {
        segment["speaker"] for segment in result["segments"]
    }


def test_crossing_segment_rounding_past_boundary_does_not_duplicate_text():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 297.2, "end": 299.0, "text": "U0XX U1XX U2XX", "speaker": "p"},
        {"id": 1, "start": 299.2, "end": 299.9, "text": "U3XX", "speaker": "p"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 2.2, "end": 3.4, "text": "U0XX U1XX", "speaker": "c"},
        {"id": 1, "start": 3.6, "end": 4.1, "text": "U2XX", "speaker": "c"},
        {"id": 2, "start": 4.2, "end": 5.01, "text": "U3XX", "speaker": "c"},
        {"id": 3, "start": 5.2, "end": 5.7, "text": "U4XX", "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    assert result["text"].split() == ["U0XX", "U1XX", "U2XX", "U3XX", "U4XX"]


def test_equal_speaker_counts_with_shifted_turn_boundary_do_not_duplicate():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.0, "end": 297.0, "text": "AAAA BBBB", "speaker": "p1"},
        {"id": 1, "start": 297.1, "end": 298.0, "text": "CCCC", "speaker": "p2"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "AAAA", "speaker": "c1"},
        {"id": 1, "start": 1.1, "end": 3.0, "text": "BBBB CCCC", "speaker": "c2"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["AAAA", "BBBB", "CCCC"],
        ["AAAA", "BBBB", "CCCC"],
    )


def test_phase_ambiguous_boundary_falls_back_without_interleaving_leaf_order():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 297.7142857,
            "text": "START OKAY YES OKAY",
            "speaker": "g:11111111:s1",
        },
        {
            "id": 1,
            "start": 297.8571429,
            "end": 299.8571429,
            "text": "YES OKAY YES",
            "speaker": "g:11111111:s1",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 2.5,
            "end": 6.34,
            "text": "OKAY YES OKAY YES END",
            "speaker": "g:22222222:s1",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged,
        language="en",
        duration=600.0,
    )

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["START", "OKAY", "YES", "OKAY", "YES", "OKAY", "YES"],
        ["OKAY", "YES", "OKAY", "YES", "END"],
    )
    assert {segment["speaker"] for segment in result["segments"]} == {
        "x:11111111:s1",
        "x:22222222:s1",
    }


def test_atomic_stream_order_preflight_falls_back_to_both_leaves(caplog):
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 296.0,
            "end": 298.0,
            "text": "U00",
            "speaker": "g:11111111:s1",
        },
        {
            "id": 1,
            "start": 297.0,
            "end": 298.0,
            "text": "U01 U02 U03 U04",
            "speaker": "g:11111111:s2",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    with caplog.at_level(logging.INFO, logger=gemini_adapter.__name__):
        gemini_adapter._merge_chunk_segments(merged, {"segments": [
            {
                "id": 0,
                "start": 0.5,
                "end": 3.5,
                "text": "U03 U04",
                "speaker": "g:22222222:s1",
            },
            {
                "id": 1,
                "start": 1.0,
                "end": 2.0,
                "text": "U05",
                "speaker": "g:22222222:s2",
            },
        ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged,
        language="en",
        duration=600.0,
    )

    assert "gemini_chunk_boundary_fallback" in caplog.text
    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["U00", "U01", "U02", "U03", "U04"],
        ["U03", "U04", "U05"],
    )


def test_phase_fallback_marks_each_unproven_speaker_namespace_as_x():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 294.0,
            "end": 297.0,
            "text": "START PA PB",
            "speaker": "g:11111111:s1",
        },
        {
            "id": 1,
            "start": 296.0,
            "end": 297.0,
            "text": "PA PB",
            "speaker": "g:11111111:s2",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 2.0,
            "text": "PA PB",
            "speaker": "g:22222222:s2",
        },
        {
            "id": 1,
            "start": 1.0,
            "end": 4.0,
            "text": "PA PB END",
            "speaker": "g:22222222:s1",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged,
        language="en",
        duration=600.0,
    )

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["START", "PA", "PB", "PA", "PB"],
        ["PA", "PB", "PA", "PB", "END"],
    )
    assert {segment["speaker"] for segment in result["segments"]} == {
        "x:11111111:s1",
        "x:11111111:s2",
        "x:22222222:s1",
        "x:22222222:s2",
    }


def test_zero_full_leaf_overlap_does_not_reorder_a_later_exact_edge():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 297.0,
            "text": "UNIQUE",
            "speaker": "g:11111111:s1",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 1.0,
            "text": "PRE",
            "speaker": "g:22222222:s1",
        },
        {
            "id": 1,
            "start": 1.0,
            "end": 4.0,
            "text": "UNIQUE",
            "speaker": "g:22222222:s1",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged,
        language="en",
        duration=600.0,
    )

    assert result["text"].split() == ["UNIQUE", "PRE", "UNIQUE"]
    _assert_tokens_are_subsequence(["PRE", "UNIQUE"], result["text"].split())


def test_zero_overlap_subsequence_is_not_an_atomic_deletion_certificate(monkeypatch):
    monkeypatch.setattr(
        gemini_adapter,
        "_plan_exact_boundary_stream_consumption",
        lambda *args, **kwargs: (set(), set(), set(), {}, {}, False),
    )
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 296.0,
            "text": "A",
            "speaker": "g:11111111:s1",
        },
        {
            "id": 1,
            "start": 296.0,
            "end": 297.0,
            "text": "B",
            "speaker": "g:11111111:s1",
        },
        {
            "id": 2,
            "start": 297.0,
            "end": 298.0,
            "text": "C",
            "speaker": "g:11111111:s1",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 1.0,
            "text": "A",
            "speaker": "g:22222222:s1",
        },
        {
            "id": 1,
            "start": 2.0,
            "end": 3.0,
            "text": "C",
            "speaker": "g:22222222:s1",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged,
        language="en",
        duration=600.0,
    )
    tokens = result["text"].split()

    assert tokens.count("A") == 2
    assert tokens.count("B") == 1
    assert tokens.count("C") == 2
    _assert_tokens_are_subsequence(["A", "B", "C"], tokens)
    _assert_tokens_are_subsequence(["A", "C"], tokens)


def test_unique_atomic_edge_also_runs_the_boundary_postcondition(monkeypatch):
    calls = 0
    original_postcondition = gemini_adapter._boundary_atomic_plan_is_preserved

    def record_postcondition(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_postcondition(*args, **kwargs)

    monkeypatch.setattr(
        gemini_adapter,
        "_plan_exact_boundary_stream_consumption",
        lambda *args, **kwargs: (set(), set(), set(), {}, {}, False),
    )
    monkeypatch.setattr(
        gemini_adapter,
        "_boundary_atomic_plan_is_preserved",
        record_postcondition,
    )
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 298.0,
            "text": "UNIQUE",
            "speaker": "g:11111111:s1",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 3.0,
            "text": "UNIQUE",
            "speaker": "g:22222222:s1",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    assert calls == 1
    assert gemini_adapter._finalize_chunked_result(
        merged,
        language="en",
        duration=600.0,
    )["text"] == "UNIQUE"


def test_atomic_postcondition_rejects_missing_speaker_hidden_by_extra_label():
    merged = [
        {
            "id": 0,
            "start": 295.0,
            "end": 296.0,
            "text": "A",
            "speaker": "c1",
            "_chunk_index": 1,
            "_source_chunk_index": 1,
            "_source_speaker": "c1",
        },
        {
            "id": 1,
            "start": 296.0,
            "end": 297.0,
            "text": "B",
            "speaker": "c1",
            "_chunk_index": 1,
            "_source_chunk_index": 1,
            "_source_speaker": "c2",
        },
        {
            "id": 2,
            "start": 297.0,
            "end": 298.0,
            "text": "C",
            "speaker": "c3",
            "_chunk_index": 1,
            "_source_chunk_index": 1,
            "_source_speaker": "c3",
        },
    ]

    assert not gemini_adapter._boundary_atomic_plan_is_preserved(
        merged,
        chunk=gemini_adapter._AudioChunk(1, 295.0, 300.0, b""),
        prior_indices=set(),
        expected_tokens=("a", "b", "c"),
        speaker_mapping={"p1": "c1", "p2": "c2"},
        boundary_prior_speakers={"p1", "p2"},
        boundary_candidate_speakers={"c1", "c2"},
    )


def test_atomic_postcondition_rejects_collapse_hidden_by_unrelated_origin():
    merged = [
        {
            "id": 0,
            "start": 295.0,
            "end": 296.0,
            "text": "A",
            "speaker": "c1",
            "_chunk_index": 1,
            "_source_chunk_index": 1,
            "_source_speaker": "c1",
        },
        {
            "id": 1,
            "start": 296.0,
            "end": 297.0,
            "text": "B",
            "speaker": "c1",
            "_chunk_index": 1,
            "_source_chunk_index": 1,
            "_source_speaker": "c2",
        },
        {
            "id": 2,
            "start": 297.0,
            "end": 298.0,
            "text": "C",
            "speaker": "c2",
            "_chunk_index": 1,
            "_source_chunk_index": 1,
            "_source_speaker": "c3",
        },
    ]

    assert not gemini_adapter._boundary_atomic_plan_is_preserved(
        merged,
        chunk=gemini_adapter._AudioChunk(1, 295.0, 300.0, b""),
        prior_indices=set(),
        expected_tokens=("a", "b", "c"),
        speaker_mapping={"p1": "c1", "p2": "c2"},
        boundary_prior_speakers={"p1", "p2"},
        boundary_candidate_speakers={"c1", "c2"},
    )


def test_raw_leaf_repetition_is_not_removed_before_boundary_certificate():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 297.0,
            "text": "LEFT TOKA",
            "speaker": "g:11111111:s1",
        },
        {
            "id": 1,
            "start": 296.0,
            "end": 298.0,
            "text": "TOKA RIGHT",
            "speaker": "g:11111111:s1",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 3.0,
            "text": "LEFT TOKA RIGHT",
            "speaker": "g:22222222:s1",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged,
        language="en",
        duration=600.0,
    )

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["LEFT", "TOKA", "TOKA", "RIGHT"],
        ["LEFT", "TOKA", "RIGHT"],
    )


def test_three_hour_chunk_boundary_workload_stays_local_and_bounded(monkeypatch):
    original_finalize = gemini_adapter._finalize_chunked_result
    finalize_sizes: list[int] = []

    def measured_finalize(segments, *, language, duration):
        finalize_sizes.append(len(segments))
        return original_finalize(
            segments,
            language=language,
            duration=duration,
        )

    monkeypatch.setattr(
        gemini_adapter,
        "_finalize_chunked_result",
        measured_finalize,
    )
    merged = []
    tracemalloc.start()
    started = time.perf_counter()
    try:
        for chunk_index in range(37):
            segments = [
                {
                    "id": segment_index,
                    "start": segment_index * 3.0,
                    "end": min(300.0, segment_index * 3.0 + 2.8),
                    "text": "PA PB",
                    "speaker": (
                        f"g:{chunk_index:08x}:s{(segment_index % 4) + 1}"
                    ),
                }
                for segment_index in range(100)
            ]
            gemini_adapter._merge_chunk_segments(
                merged,
                {"segments": segments},
                gemini_adapter._AudioChunk(
                    chunk_index,
                    chunk_index * 295.0,
                    300.0,
                    b"",
                ),
            )
        elapsed = time.perf_counter() - started
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(merged) == 3_700
    assert elapsed < 15.0
    assert peak_bytes < 32 * 1024 * 1024
    assert finalize_sizes
    assert max(finalize_sizes) <= 201


def test_periodic_shift_reconciles_only_one_fundamental_unit():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.0, "end": 300.0, "text": "OKAY YES OKAY YES", "speaker": "p"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 3.0, "end": 6.0, "text": "OKAY YES OKAY YES", "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    # Segment-level timestamps cannot identify which periodic phase overlaps.
    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["OKAY", "YES"] * 2,
        ["OKAY", "YES"] * 2,
    )


@pytest.mark.parametrize(
    ("repeat_count", "prior_start", "prior_end", "current_start", "current_end", "expected_count"),
    [
        (5, 295.0, 300.0, 1.0, 6.0, 6),
        (3, 294.0, 300.0, 1.0, 7.0, 4),
    ],
)
def test_periodic_equal_rate_shift_uses_segment_period_anchor(
    repeat_count,
    prior_start,
    prior_end,
    current_start,
    current_end,
    expected_count,
):
    motif = "OKAY YES"
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": prior_start,
            "end": prior_end,
            "text": " ".join([motif] * repeat_count),
            "speaker": "p",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": current_start,
            "end": current_end,
            "text": " ".join([motif] * repeat_count),
            "speaker": "c",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["OKAY", "YES"] * repeat_count,
        ["OKAY", "YES"] * repeat_count,
    )


def test_shifted_nonperiodic_self_border_does_not_consume_the_whole_window():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.0, "end": 300.0, "text": "ONE TWO ONE", "speaker": "p"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 1.0, "end": 6.0, "text": "ONE TWO ONE", "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["ONE", "TWO", "ONE"],
        ["ONE", "TWO", "ONE"],
    )


def test_partial_handoff_survives_segment_partition_time_ratio_shift():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.05, "end": 295.99, "text": "W000 W001", "speaker": "p"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 0.59, "end": 1.53, "text": "W001 W002", "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    assert result["text"].split() == ["W000", "W001", "W002"]


def test_stream_partition_can_end_inside_a_normal_candidate_segment():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.05, "end": 295.99, "text": "W000 W001", "speaker": "p"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 0.05, "end": 0.45, "text": "W000", "speaker": "c"},
        {"id": 1, "start": 0.59, "end": 1.53, "text": "W001 W002", "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    assert result["text"].split() == ["W000", "W001", "W002"]


def test_crossing_partition_rewrites_prior_prefix_and_keeps_current_speakers():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 294.0,
            "end": 300.0,
            "text": "W00 W01 W02 W03 W04 W05",
            "speaker": "p",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 0.0, "end": 1.0, "text": "W01", "speaker": "c2"},
        {
            "id": 1,
            "start": 1.0,
            "end": 6.0,
            "text": "W02 W03 W04 W05 W06",
            "speaker": "c1",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_deletion_free_boundary(
        result,
        ["W00", "W01", "W02", "W03", "W04", "W05"],
        ["W01", "W02", "W03", "W04", "W05", "W06"],
    )
    assert {"c1", "c2"} <= {
        segment["speaker"] for segment in result["segments"]
    }


def test_unique_speaker_mapping_propagates_through_prior_history():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 10.0, "end": 11.0, "text": "BEFORE", "speaker": "pA"},
        {
            "id": 1,
            "start": 295.0,
            "end": 300.0,
            "text": "SHARED WORDS",
            "speaker": "pA",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 5.0,
            "text": "SHARED WORDS",
            "speaker": "cA",
        },
        {"id": 1, "start": 5.0, "end": 7.0, "text": "AFTER", "speaker": "cA"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    assert result["text"].split() == ["BEFORE", "SHARED", "WORDS", "AFTER"]
    assert {segment["speaker"] for segment in result["segments"]} == {"cA"}


def test_non_crossing_matcher_only_compares_boundary_segments(monkeypatch):
    calls = []

    def record_edge(prior, candidate, **kwargs):
        calls.append((prior["id"], candidate["id"]))
        return None

    monkeypatch.setattr(gemini_adapter, "_build_overlap_edge", record_edge)
    priors = [
        (index, {
            "id": index,
            "start": float(index),
            "end": float(index) + 0.5,
            "text": f"P{index}",
            "speaker": "p",
        })
        for index in range(300)
    ]
    candidates = [
        {
            "id": index,
            "start": 295.0 + float(index),
            "end": 295.5 + float(index),
            "text": f"C{index}",
            "speaker": "c",
            "_boundary_start": 295.0,
            "_boundary_end": 300.0,
        }
        for index in range(300)
    ]

    assert gemini_adapter._select_non_crossing_overlap_matches(
        priors, candidates
    ) == tuple()
    assert calls == [
        (295, 0),
        (296, 1),
        (297, 2),
        (298, 3),
        (299, 4),
    ]


def test_partial_handoff_keeps_repetition_for_epsilon_time_overlap():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.05, "end": 295.99, "text": "THIS VERY", "speaker": "p"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 0.95, "end": 1.89, "text": "VERY GOOD", "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    assert result["text"].split() == ["THIS", "VERY", "VERY", "GOOD"]


def test_self_border_with_content_on_both_sides_never_full_consumes():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 294.8,
            "end": 300.0,
            "text": "ONE TWO ONE TWO ONE",
            "speaker": "p",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 5.2,
            "text": "ONE TWO ONE TWO ONE",
            "speaker": "c",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    # Segment-level timestamps cannot prove which proper border is shared.
    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["ONE", "TWO", "ONE", "TWO", "ONE"],
        ["ONE", "TWO", "ONE", "TWO", "ONE"],
    )


@pytest.mark.parametrize(
    ("prior_text", "candidate_text", "expected"),
    [
        (
            "START OKAY YES OKAY YES",
            "OKAY YES OKAY YES END",
            ["START", "OKAY", "YES", "OKAY", "YES", "OKAY", "YES", "END"],
        ),
        (
            "OKAY YES OKAY YES",
            "OKAY YES OKAY YES OKAY YES",
            ["OKAY", "YES", "OKAY", "YES", "OKAY", "YES", "OKAY", "YES"],
        ),
    ],
)
def test_periodic_partial_and_containment_keep_adjacent_real_repetitions(
    prior_text,
    candidate_text,
    expected,
):
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.0, "end": 300.0, "text": prior_text, "speaker": "p"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 2.5, "end": 6.5, "text": candidate_text, "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="en", duration=600.0)

    _assert_phase_fallback_preserves_leaf_order(
        result,
        prior_text.split(),
        candidate_text.split(),
    )


def test_periodic_partition_with_rounded_gap_never_deletes_or_reorders_speech():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.0, "end": 295.8, "text": "START", "speaker": "p"},
        {
            "id": 1,
            "start": 296.0,
            "end": 299.8,
            "text": "OKAY YES OKAY YES",
            "speaker": "p",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 2.5,
            "end": 4.74,
            "text": "OKAY YES OKAY",
            "speaker": "c",
        },
        {"id": 1, "start": 4.9, "end": 6.34, "text": "YES END", "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )
    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["START", "OKAY", "YES", "OKAY", "YES"],
        ["OKAY", "YES", "OKAY", "YES", "END"],
    )


def test_periodic_safe_border_caps_later_endpoint_matching():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 297.8,
            "text": "START OKAY YES",
            "speaker": "p",
        },
        {
            "id": 1,
            "start": 298.0,
            "end": 299.8,
            "text": "OKAY YES",
            "speaker": "p",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 2.5, "end": 3.14, "text": "OKAY", "speaker": "c"},
        {"id": 1, "start": 3.3, "end": 3.94, "text": "YES", "speaker": "c"},
        {
            "id": 2,
            "start": 4.1,
            "end": 6.34,
            "text": "OKAY YES END",
            "speaker": "c",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["START", "OKAY", "YES", "OKAY", "YES"],
        ["OKAY", "YES", "OKAY", "YES", "END"],
    )


def test_partial_periodic_same_envelope_uses_shortest_safe_border():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 297.0,
            "text": "START PA PB PA PB",
            "speaker": "pA",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 2.0,
            "text": "PA PB PA PB END",
            "speaker": "cA",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["START", "PA", "PB", "PA", "PB"],
        ["PA", "PB", "PA", "PB", "END"],
    )


def test_repeated_contained_fragment_is_not_deleted_by_coarse_timestamp():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 296.0,
            "text": "AAAA AAAA",
            "speaker": "p0",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 3.0,
            "text": "AAAA AAAA AAAA",
            "speaker": "c",
        },
        {"id": 1, "start": 2.0, "end": 3.0, "text": "AAAA", "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["AAAA"] * 2,
        ["AAAA"] * 4,
    )


def test_coarse_containment_keeps_prior_only_predecessor_order():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 296.0, "end": 297.0, "text": "U00", "speaker": "pB"},
        {
            "id": 1,
            "start": 296.0,
            "end": 298.0,
            "text": "U01 U02",
            "speaker": "pA",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 3.0,
            "text": "U01 U02 U03 U04 U05",
            "speaker": "cA",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_deletion_free_boundary(
        result,
        ["U00", "U01", "U02"],
        ["U01", "U02", "U03", "U04", "U05"],
    )


def test_consumed_current_prefix_anchors_following_candidate_after_prior_suffix():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 297.0,
            "text": "U00 U01",
            "speaker": "pA",
        },
        {"id": 1, "start": 297.0, "end": 298.0, "text": "U02", "speaker": "pA"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 2.0,
            "text": "U01 U02",
            "speaker": "cA",
        },
        {"id": 1, "start": 1.0, "end": 3.0, "text": "U03", "speaker": "cB"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["U00", "U01", "U02"],
        ["U01", "U02", "U03"],
    )


def test_equal_time_candidates_keep_provider_order_at_periodic_boundary():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 297.0,
            "text": "START PA PB PA PB",
            "speaker": "pA",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 1.0, "end": 4.0, "text": "PA PB PA", "speaker": "cA"},
        {"id": 1, "start": 2.0, "end": 4.0, "text": "PB", "speaker": "cA"},
        {"id": 2, "start": 2.0, "end": 4.0, "text": "END", "speaker": "cA"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["START", "PA", "PB", "PA", "PB"],
        ["PA", "PB", "PA", "PB", "END"],
    )


def test_interior_containment_does_not_delete_adjacent_periodic_turn():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 297.4,
            "text": "START TOKA TOKB TOKC TOKA",
            "speaker": "p",
        },
        {
            "id": 1,
            "start": 297.5,
            "end": 299.9,
            "text": "TOKB TOKC TOKA TOKB TOKC",
            "speaker": "p",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 2.5, "end": 2.957142857142857, "text": "TOKA", "speaker": "c"},
        {
            "id": 1,
            "start": 3.071428571428571,
            "end": 4.671428571428572,
            "text": "TOKB TOKC TOKA",
            "speaker": "c",
        },
        {"id": 2, "start": 4.785714285714286, "end": 5.242857142857143, "text": "TOKB", "speaker": "c"},
        {"id": 3, "start": 5.357142857142857, "end": 6.385714285714286, "text": "TOKC END", "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_tokens_are_subsequence([
        "START",
        "TOKA", "TOKB", "TOKC",
        "TOKA", "TOKB", "TOKC",
        "TOKA", "TOKB", "TOKC",
        "TOKA", "TOKB", "TOKC",
        "END",
    ], result["text"].split())


def test_periodic_prefix_crossing_boundary_is_not_absorbed_by_prior_turn():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 296.9,
            "text": "START TOKA TOKB TOKC",
            "speaker": "p",
        },
        {
            "id": 1,
            "start": 297.0,
            "end": 298.4,
            "text": "TOKA TOKB TOKC",
            "speaker": "p",
        },
        {
            "id": 2,
            "start": 298.5,
            "end": 299.9,
            "text": "TOKA TOKB TOKC",
            "speaker": "p",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 2.5,
            "end": 4.1,
            "text": "TOKA TOKB TOKC",
            "speaker": "c",
        },
        {
            "id": 1,
            "start": 4.214285714285714,
            "end": 5.242857142857143,
            "text": "TOKA TOKB",
            "speaker": "c",
        },
        {
            "id": 2,
            "start": 5.357142857142857,
            "end": 6.385714285714286,
            "text": "TOKC END",
            "speaker": "c",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_tokens_are_subsequence([
        "START",
        "TOKA", "TOKB", "TOKC",
        "TOKA", "TOKB", "TOKC",
        "TOKA", "TOKB", "TOKC",
        "TOKA", "TOKB", "TOKC",
        "END",
    ], result["text"].split())


def test_containment_chain_does_not_consume_next_periodic_prefix():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 293.0,
            "end": 295.0,
            "text": "START PX",
            "speaker": "p",
        },
        {
            "id": 1,
            "start": 295.0,
            "end": 297.0,
            "text": "PY PZ",
            "speaker": "p",
        },
        {
            "id": 2,
            "start": 296.0,
            "end": 298.0,
            "text": "PX PY PZ",
            "speaker": "p",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 2.0,
            "text": "PX PY PZ",
            "speaker": "c",
        },
        {
            "id": 1,
            "start": 1.0,
            "end": 3.0,
            "text": "PX PY",
            "speaker": "c",
        },
        {
            "id": 2,
            "start": 3.0,
            "end": 5.0,
            "text": "PZ END",
            "speaker": "c",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_tokens_are_subsequence(
        "START PX PY PZ PX PY PZ PX PY PZ END".split(),
        result["text"].split(),
    )


def test_equal_time_canonical_segments_keep_provider_order_and_text():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 293.0,
            "end": 295.0,
            "text": "START",
            "speaker": "pA",
        },
        {
            "id": 1,
            "start": 294.0,
            "end": 295.0,
            "text": "PX",
            "speaker": "pA",
        },
        {
            "id": 2,
            "start": 295.0,
            "end": 298.0,
            "text": "PY PZ PX PY PZ",
            "speaker": "pA",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 2.0,
            "end": 3.0,
            "text": "PX",
            "speaker": "cA",
        },
        {
            "id": 1,
            "start": 2.0,
            "end": 3.0,
            "text": "PY PZ PX",
            "speaker": "cA",
        },
        {
            "id": 2,
            "start": 4.0,
            "end": 5.0,
            "text": "PY PZ",
            "speaker": "cA",
        },
        {
            "id": 3,
            "start": 5.0,
            "end": 6.0,
            "text": "END",
            "speaker": "cA",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["START", "PX", "PY", "PZ", "PX", "PY", "PZ"],
        ["PX", "PY", "PZ", "PX", "PY", "PZ", "END"],
    )


def test_prior_split_periodic_alignment_keeps_text_and_richer_speakers():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 293.0, "end": 295.0, "text": "START PX", "speaker": "pA"},
        {"id": 1, "start": 295.0, "end": 297.0, "text": "PY PZ", "speaker": "pA"},
        {"id": 2, "start": 296.0, "end": 298.0, "text": "PX PY", "speaker": "pA"},
        {"id": 3, "start": 297.0, "end": 298.0, "text": "PZ", "speaker": "pA"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "PX PY PZ", "speaker": "cA"},
        {"id": 1, "start": 1.0, "end": 4.0, "text": "PX PY PZ", "speaker": "cB"},
        {"id": 2, "start": 3.0, "end": 5.0, "text": "END", "speaker": "cB"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_tokens_are_subsequence(
        "START PX PY PZ PX PY PZ PX PY PZ END".split(),
        result["text"].split(),
    )
    assert {"cA", "cB"}.issubset({
        segment["speaker"] for segment in result["segments"]
    })


def test_atomic_retained_prior_is_not_reused_by_segment_matching():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 295.0,
            "end": 297.8,
            "text": "START OKAY YES",
            "speaker": "p1",
        },
        {
            "id": 1,
            "start": 298.0,
            "end": 299.8,
            "text": "OKAY YES",
            "speaker": "p2",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 2.7, "end": 3.3, "text": "OKAY", "speaker": "c"},
        {"id": 1, "start": 3.5, "end": 4.0, "text": "YES", "speaker": "c"},
        {
            "id": 2,
            "start": 4.1,
            "end": 6.34,
            "text": "OKAY YES END",
            "speaker": "c",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["START", "OKAY", "YES", "OKAY", "YES"],
        ["OKAY", "YES", "OKAY", "YES", "END"],
    )


def test_equal_rounded_start_preserves_reconciled_stream_order():
    result = gemini_adapter._finalize_chunked_result(
        [
            {
                "id": 0,
                "start": 295.0,
                "end": 296.0,
                "text": "W000",
                "speaker": "p",
                "_chunk_index": 0,
            },
            {
                "id": 1,
                "start": 295.0,
                "end": 297.0,
                "text": "W001 W002",
                "speaker": "c",
                "_chunk_index": 1,
                "_boundary_canonical_start": 295.0,
                "_boundary_canonical_end": 297.0,
            },
        ],
        language="en",
        duration=600.0,
    )

    assert result["text"].split() == ["W000", "W001", "W002"]


def test_shifted_periodic_prefix_containment_requires_start_alignment():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 289.0, "end": 290.0, "text": "START", "speaker": "p"},
        {
            "id": 1,
            "start": 290.0,
            "end": 300.0,
            "text": "OKAY YES OKAY YES",
            "speaker": "p",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 11.0,
            "text": "OKAY YES OKAY YES END",
            "speaker": "c",
        },
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    _assert_phase_fallback_preserves_leaf_order(
        result,
        ["START", "OKAY", "YES", "OKAY", "YES"],
        ["OKAY", "YES", "OKAY", "YES", "END"],
    )


def test_shifted_equal_length_periodic_stream_keeps_current_speaker_partition():
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.05, "end": 296.05, "text": "A B", "speaker": "p"},
        {
            "id": 1,
            "start": 296.1,
            "end": 298.15,
            "text": "A B A B",
            "speaker": "p",
        },
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 2.21, "end": 3.0, "text": "A B", "speaker": "c0"},
        {"id": 1, "start": 3.0, "end": 4.0, "text": "A B A", "speaker": "c1"},
        {"id": 2, "start": 4.0, "end": 5.5, "text": "B", "speaker": "c2"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(
        merged, language="en", duration=600.0
    )

    assert result["text"].split().count("A") >= 3
    assert {"c0", "c1", "c2"}.issubset({
        segment["speaker"] for segment in result["segments"]
    })


@pytest.mark.parametrize(
    ("prior_text", "candidate_text", "expected"),
    [
        ("Visit example", "example.com now", "Visit example.com now"),
        ("version MODEL1", "MODEL1.2 released", "version MODEL1.2 released"),
        ("内容をお申し込", "お申し込みます", "内容をお申し込みます"),
        ("Use CPLUS+", "CPLUS++ guide", "Use CPLUS++ guide"),
        ("Question WHAT?", "WHAT?? really", "Question WHAT?? really"),
        ("Dots MODEL.", "MODEL..next", "Dots MODEL..next"),
    ],
)
def test_partial_merge_preserves_token_punctuation_and_japanese_okurigana(
    prior_text,
    candidate_text,
    expected,
):
    merged = []
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 295.0, "end": 298.0, "text": prior_text, "speaker": "p"},
    ]}, gemini_adapter._AudioChunk(0, 0.0, 300.0, b""))
    gemini_adapter._merge_chunk_segments(merged, {"segments": [
        {"id": 0, "start": 1.0, "end": 4.0, "text": candidate_text, "speaker": "c"},
    ]}, gemini_adapter._AudioChunk(1, 295.0, 300.0, b""))

    result = gemini_adapter._finalize_chunked_result(merged, language="ja", duration=600.0)

    assert result["text"] == expected


def test_normalized_units_handle_decomposed_graphemes_without_global_cache():
    assert gemini_adapter._dedupe_units("カ\u3099ラス") == gemini_adapter._dedupe_units("ガラス")
    assert gemini_adapter._consume_expected_text_prefix("カ", "カ\u3099ラス") is None
    assert not hasattr(gemini_adapter._normalized_alnum_map, "cache_info")


def test_partial_turn_uses_configured_boundary_region_for_long_segments():
    prior = {
        "start": 440.0,
        "end": 450.0,
        "text": "まず背景と目的を説明してから今日の資料を確認します",
    }
    candidate = {
        "start": 445.0,
        "end": 460.0,
        "text": "今日の資料を確認します。そのあと実施予定と担当者を順番に共有します",
    }
    partial = gemini_adapter._partial_suffix_prefix_overlap(prior["text"], candidate["text"])

    assert partial is not None
    _, overlap_length, full_text_ratio = partial
    assert gemini_adapter._overlap_ratio(prior, candidate) == pytest.approx(0.5)
    assert gemini_adapter._boundary_overlap_ratio(prior, candidate) == pytest.approx(1.0)
    assert full_text_ratio < gemini_adapter.CHUNK_PARTIAL_DEDUPE_MIN_TEXT_RATIO
    assert (
        gemini_adapter._partial_overlap_window_text_ratio(prior, candidate, overlap_length)
        >= gemini_adapter.CHUNK_PARTIAL_DEDUPE_MIN_TEXT_RATIO
    )


def test_76_minute_synthetic_wav_uses_sixteen_sequential_chunks(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 300.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 5.0)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    uploads = [SimpleNamespace(name=f"files/{index}", state=SimpleNamespace(name="ACTIVE")) for index in range(16)]
    responses = [
        _response([{"start": 0, "end": 1, "text": f"chunk-{index}", "speaker": "Speaker 1"}])
        for index in range(16)
    ]
    fake_client = SimpleNamespace(
        files=SimpleNamespace(upload=MagicMock(side_effect=uploads), get=MagicMock(), delete=MagicMock()),
        models=SimpleNamespace(generate_content=MagicMock(side_effect=responses)),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    result = gemini_adapter._transcribe_sync(
        _wav_bytes(76 * 60, sample_rate=1),
        filename="meeting.wav",
        model="gemini-3.5-flash",
        language="ja",
        prompt=None,
    )

    assert fake_client.models.generate_content.call_count == 16
    assert fake_client.files.upload.call_count == 16
    assert fake_client.files.delete.call_count == 16
    assert [segment["start"] for segment in result["segments"]] == pytest.approx(
        [
            0.0, 295.0, 590.0, 885.0, 1180.0, 1475.0, 1770.0, 2065.0,
            2360.0, 2655.0, 2950.0, 3245.0, 3540.0, 3835.0, 4130.0, 4425.0,
        ]
    )
    assert len({segment["speaker"] for segment in result["segments"]}) == 16
    assert all(
        re.fullmatch(r"[gx]:[0-9a-f]{8}:s1", segment["speaker"])
        for segment in result["segments"]
    )
    assert any(segment["speaker"].startswith("x:") for segment in result["segments"])
    assert result["duration"] == pytest.approx(4560.0)
    assert "ends at 05:00" in fake_client.models.generate_content.call_args_list[0].kwargs["config"].system_instruction
    assert "ends at 02:15" in fake_client.models.generate_content.call_args_list[-1].kwargs["config"].system_instruction


def test_chunk_failure_is_atomic_and_stops_before_next_chunk(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 3.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 1.0)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    uploads = [
        SimpleNamespace(name="files/1", state=SimpleNamespace(name="ACTIVE")),
        SimpleNamespace(name="files/2", state=SimpleNamespace(name="ACTIVE")),
    ]
    fake_client = SimpleNamespace(
        files=SimpleNamespace(upload=MagicMock(side_effect=uploads), get=MagicMock(), delete=MagicMock()),
        models=SimpleNamespace(generate_content=MagicMock(side_effect=[
            _response([{"start": 0, "end": 1, "text": "partial", "speaker": "Speaker 1"}]),
            _response([], finish_reason="MAX_TOKENS"),
        ])),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._transcribe_sync(
            _wav_bytes(7), filename="meeting.wav", model="gemini-3.5-flash", language="ja", prompt=None,
        )

    assert exc.value.code == "incomplete_response"
    assert fake_client.models.generate_content.call_count == 2
    assert fake_client.files.upload.call_count == 2
    assert fake_client.files.delete.call_count == 2


def test_max_tokens_parent_is_replaced_by_two_successful_children(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 8.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 1.0)
    monkeypatch.setattr(gemini_adapter, "MIN_CHUNK_DURATION_SECONDS", 3.0)
    monkeypatch.setattr(gemini_adapter, "MAX_CHUNK_SPLIT_DEPTH", 3)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    events = []
    uploaded_durations = []
    uploaded_digests = []
    upload_index = 0

    def upload(*, file):
        nonlocal upload_index
        events.append("upload")
        with open(file, "rb") as source:
            payload = source.read()
        uploaded_digests.append(hashlib.sha256(payload).hexdigest())
        with wave.open(io.BytesIO(payload), "rb") as wav:
            uploaded_durations.append(wav.getnframes() / wav.getframerate())
        upload_index += 1
        return SimpleNamespace(name=f"files/{upload_index}", state=SimpleNamespace(name="ACTIVE"))

    responses = iter([
        _response(
            [{"start": 0, "end": 1, "text": "親の部分結果", "speaker": "Speaker 1"}],
            finish_reason="MAX_TOKENS",
        ),
        _response([{"start": 1, "end": 2, "text": "左", "speaker": "Speaker 1"}]),
        _response([{"start": 1, "end": 2, "text": "右", "speaker": "Speaker 1"}]),
    ])

    def generate_content(**_):
        events.append("generate")
        return next(responses)

    def delete(**_):
        events.append("delete")

    fake_client = SimpleNamespace(
        files=SimpleNamespace(
            upload=MagicMock(side_effect=upload),
            get=MagicMock(),
            delete=MagicMock(side_effect=delete),
        ),
        models=SimpleNamespace(generate_content=MagicMock(side_effect=generate_content)),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    result = gemini_adapter._transcribe_sync(
        _wav_bytes(8),
        filename="meeting.wav",
        model="gemini-3.5-flash",
        language="ja",
        prompt=None,
    )

    assert events == ["upload", "generate", "delete"] * 3
    assert uploaded_durations == pytest.approx([8.0, 4.5, 4.5])
    assert uploaded_digests[1] == uploaded_digests[2]
    assert fake_client.models.generate_content.call_count == 3
    assert [segment["text"] for segment in result["segments"]] == ["左", "右"]
    assert [segment["start"] for segment in result["segments"]] == pytest.approx([1.0, 4.5])
    assert result["segments"][0]["speaker"] != result["segments"][1]["speaker"]
    assert result["duration"] == pytest.approx(8.0)
    assert "親の部分結果" not in result["text"]
    configs = [call.kwargs["config"] for call in fake_client.models.generate_content.call_args_list]
    assert "ends at 00:08" in configs[0].system_instruction
    assert all("ends at 00:05" in config.system_instruction for config in configs[1:])


def test_same_logical_clip_identity_is_rejected_before_second_provider_call(monkeypatch):
    transcribe = MagicMock(return_value={
        "language": "ja",
        "segments": [
            {"id": 0, "start": 0.0, "end": 1.0, "text": "一回だけ", "speaker": "g:12345678:s1"},
        ],
    })
    monkeypatch.setattr(gemini_adapter, "_transcribe_chunk_sync", transcribe)
    chunk = gemini_adapter._AudioChunk(
        0,
        0.0,
        2.0,
        _wav_bytes(2),
        source_start_frame=0,
        source_end_frame=20,
        split_path=(1,),
    )
    processed = set()
    merged = []
    next_index = [0]
    kwargs = dict(
        filename="meeting.part-1.wav",
        model="gemini-3.5-flash",
        language="ja",
        prompt=None,
        root_chunk_index=0,
        total_chunks=1,
        chunk_label="1.1",
        depth=1,
        next_merge_index=next_index,
        merged=merged,
        processed_clip_identities=processed,
    )

    gemini_adapter._transcribe_adaptive_chunk_sync(SimpleNamespace(), SimpleNamespace(), chunk, **kwargs)
    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._transcribe_adaptive_chunk_sync(SimpleNamespace(), SimpleNamespace(), chunk, **kwargs)

    assert exc.value.code == "clip_identity_reused"
    assert transcribe.call_count == 1


def test_adaptive_children_and_next_root_keep_boundary_dedupe(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 8.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 1.0)
    monkeypatch.setattr(gemini_adapter, "MIN_CHUNK_DURATION_SECONDS", 3.0)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    uploads = [
        SimpleNamespace(name=f"files/{index}", state=SimpleNamespace(name="ACTIVE"))
        for index in range(4)
    ]
    fake_client = SimpleNamespace(
        files=SimpleNamespace(upload=MagicMock(side_effect=uploads), get=MagicMock(), delete=MagicMock()),
        models=SimpleNamespace(generate_content=MagicMock(side_effect=[
            _response([], finish_reason="MAX_TOKENS"),
            _response([{"start": 3.6, "end": 4.5, "text": "子境界", "speaker": "Speaker 1"}]),
            _response([
                {"start": 0.1, "end": 1.0, "text": "子境界", "speaker": "Speaker 1"},
                {"start": 3.5, "end": 4.5, "text": "親境界", "speaker": "Speaker 1"},
            ]),
            _response([
                {"start": 0, "end": 1, "text": "親境界", "speaker": "Speaker 1"},
                {"start": 1, "end": 2, "text": "続き", "speaker": "Speaker 1"},
            ]),
        ])),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    result = gemini_adapter._transcribe_sync(
        _wav_bytes(15),
        filename="meeting.wav",
        model="gemini-3.5-flash",
        language="ja",
        prompt=None,
    )

    assert fake_client.models.generate_content.call_count == 4
    assert [segment["text"] for segment in result["segments"]] == ["子境界", "親境界 続き"]
    assert [segment["start"] for segment in result["segments"]] == pytest.approx([3.6, 7.0])


@pytest.mark.parametrize(
    "finish_reason",
    ["RECITATION", "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "OTHER", "MISSING"],
)
def test_policy_and_unknown_finish_reasons_are_never_split(monkeypatch, finish_reason):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 8.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 1.0)
    monkeypatch.setattr(gemini_adapter, "MIN_CHUNK_DURATION_SECONDS", 3.0)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    uploaded = SimpleNamespace(name="files/1", state=SimpleNamespace(name="ACTIVE"))
    fake_client = SimpleNamespace(
        files=SimpleNamespace(upload=MagicMock(return_value=uploaded), get=MagicMock(), delete=MagicMock()),
        models=SimpleNamespace(generate_content=MagicMock(return_value=_response([], finish_reason=finish_reason))),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._transcribe_sync(
            _wav_bytes(8),
            filename="meeting.wav",
            model="gemini-3.5-flash",
            language="ja",
            prompt=None,
        )

    assert exc.value.code == "incomplete_response"
    assert fake_client.models.generate_content.call_count == 1
    assert fake_client.files.delete.call_count == 1


def test_split_requires_both_children_to_meet_minimum(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 10.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 1.0)
    monkeypatch.setattr(gemini_adapter, "MIN_CHUNK_DURATION_SECONDS", 3.0)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    uploaded = SimpleNamespace(name="files/1", state=SimpleNamespace(name="ACTIVE"))
    fake_client = SimpleNamespace(
        files=SimpleNamespace(upload=MagicMock(return_value=uploaded), get=MagicMock(), delete=MagicMock()),
        models=SimpleNamespace(generate_content=MagicMock(return_value=_response([], finish_reason="MAX_TOKENS"))),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._transcribe_sync(
            _wav_bytes(4.9),
            filename="meeting.wav",
            model="gemini-3.5-flash",
            language="ja",
            prompt=None,
        )

    assert exc.value.code == "incomplete_response"
    assert fake_client.models.generate_content.call_count == 1


def test_minimum_child_failure_stops_before_sibling(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 8.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 1.0)
    monkeypatch.setattr(gemini_adapter, "MIN_CHUNK_DURATION_SECONDS", 3.0)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    uploads = [
        SimpleNamespace(name="files/parent", state=SimpleNamespace(name="ACTIVE")),
        SimpleNamespace(name="files/left", state=SimpleNamespace(name="ACTIVE")),
    ]
    fake_client = SimpleNamespace(
        files=SimpleNamespace(upload=MagicMock(side_effect=uploads), get=MagicMock(), delete=MagicMock()),
        models=SimpleNamespace(generate_content=MagicMock(side_effect=[
            _response([], finish_reason="MAX_TOKENS"),
            _response([], finish_reason="MAX_TOKENS"),
        ])),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._transcribe_sync(
            _wav_bytes(8),
            filename="meeting.wav",
            model="gemini-3.5-flash",
            language="ja",
            prompt=None,
        )

    assert exc.value.code == "incomplete_response"
    assert fake_client.models.generate_content.call_count == 2
    assert fake_client.files.upload.call_count == 2
    assert fake_client.files.delete.call_count == 2


def test_exact_silent_adaptive_child_is_not_sent_to_provider(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 8.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 1.0)
    monkeypatch.setattr(gemini_adapter, "MIN_CHUNK_DURATION_SECONDS", 3.0)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    uploads = [
        SimpleNamespace(name="files/parent", state=SimpleNamespace(name="ACTIVE")),
        SimpleNamespace(name="files/right", state=SimpleNamespace(name="ACTIVE")),
    ]
    fake_client = SimpleNamespace(
        files=SimpleNamespace(upload=MagicMock(side_effect=uploads), get=MagicMock(), delete=MagicMock()),
        models=SimpleNamespace(generate_content=MagicMock(side_effect=[
            _response([], finish_reason="MAX_TOKENS"),
            _response([{"start": 1, "end": 2, "text": "発話", "speaker": "Speaker 1"}]),
        ])),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)
    audio = _wav_samples(([0] * 45) + ([1] * 35), sample_rate=10)

    result = gemini_adapter._transcribe_sync(
        audio,
        filename="meeting.wav",
        model="gemini-3.5-flash",
        language="ja",
        prompt=None,
    )

    assert fake_client.models.generate_content.call_count == 2
    assert fake_client.files.upload.call_count == 2
    assert fake_client.files.delete.call_count == 2
    assert [segment["text"] for segment in result["segments"]] == ["発話"]
    assert result["segments"][0]["start"] == pytest.approx(4.5)


def test_stop_event_after_generate_prevents_next_chunk(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 3.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 1.0)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    stop_event = threading.Event()

    def generate_content(**_):
        stop_event.set()
        return _response([{"start": 0, "end": 1, "text": "partial", "speaker": "Speaker 1"}])

    fake_client = SimpleNamespace(
        files=SimpleNamespace(
            upload=MagicMock(return_value=SimpleNamespace(name="files/1", state=SimpleNamespace(name="ACTIVE"))),
            get=MagicMock(),
            delete=MagicMock(),
        ),
        models=SimpleNamespace(generate_content=MagicMock(side_effect=generate_content)),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._transcribe_sync(
            _wav_bytes(7),
            filename="meeting.wav",
            model="gemini-3.5-flash",
            language="ja",
            prompt=None,
            stop_event=stop_event,
            deadline_monotonic=float("inf"),
        )

    assert exc.value.code == "unknown_manual_reconcile"
    assert fake_client.models.generate_content.call_count == 1
    assert fake_client.files.upload.call_count == 1
    assert fake_client.files.delete.call_count == 1


def test_stop_event_ends_file_processing_poll_before_get_or_generate(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    stop_event = threading.Event()
    uploaded = SimpleNamespace(name="files/processing", state=SimpleNamespace(name="PROCESSING"))

    def upload(**_):
        stop_event.set()
        return uploaded

    fake_client = SimpleNamespace(
        files=SimpleNamespace(
            upload=MagicMock(side_effect=upload),
            get=MagicMock(return_value=uploaded),
            delete=MagicMock(),
        ),
        models=SimpleNamespace(generate_content=MagicMock()),
    )
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda **_: fake_client)

    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._transcribe_sync(
            b"not-a-real-wav",
            filename="meeting.wav",
            model="gemini-3.5-flash",
            language="ja",
            prompt=None,
            stop_event=stop_event,
            deadline_monotonic=float("inf"),
        )

    assert exc.value.code == "unknown_manual_reconcile"
    fake_client.files.get.assert_not_called()
    fake_client.models.generate_content.assert_not_called()
    fake_client.files.delete.assert_called_once_with(name="files/processing")


def test_chunk_local_timestamp_one_second_beyond_audio_is_clamped():
    chunk = gemini_adapter._AudioChunk(index=0, offset_seconds=10.0, duration_seconds=3.0, audio=b"")
    merged = []

    gemini_adapter._merge_chunk_segments(merged, {
        "segments": [{"id": 0, "start": 2.0, "end": 4.0, "text": "x", "speaker": "g:12345678:s1"}],
    }, chunk)

    assert len(merged) == 1
    assert merged[0]["start"] == pytest.approx(12.0)
    assert merged[0]["end"] == pytest.approx(13.0)


def test_chunk_local_timestamp_more_than_one_second_beyond_audio_is_rejected():
    chunk = gemini_adapter._AudioChunk(index=0, offset_seconds=0.0, duration_seconds=3.0, audio=b"")
    merged = []
    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._merge_chunk_segments(merged, {
            "segments": [{"id": 0, "start": 0.0, "end": 4.1, "text": "x", "speaker": "g:12345678:s1"}],
        }, chunk)
    assert exc.value.code == "schema_invalid"
    assert merged == []


def test_chunk_local_start_beyond_audio_is_rejected_without_clamping():
    chunk = gemini_adapter._AudioChunk(index=0, offset_seconds=0.0, duration_seconds=3.0, audio=b"")
    merged = []
    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._merge_chunk_segments(merged, {
            "segments": [{"id": 0, "start": 4.0, "end": 4.0, "text": "x", "speaker": "g:12345678:s1"}],
        }, chunk)
    assert exc.value.code == "schema_invalid"
    assert merged == []


def test_chunk_end_clamp_cannot_create_a_zero_length_segment():
    chunk = gemini_adapter._AudioChunk(index=0, offset_seconds=0.0, duration_seconds=3.0, audio=b"")
    merged = []
    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._merge_chunk_segments(merged, {
            "segments": [{"id": 0, "start": 3.0, "end": 4.0, "text": "x", "speaker": "g:12345678:s1"}],
        }, chunk)
    assert exc.value.code == "schema_invalid"
    assert merged == []


def test_invalid_chunk_overlap_is_rejected_before_provider(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "CHUNK_DURATION_SECONDS", 3.0)
    monkeypatch.setattr(gemini_adapter, "CHUNK_OVERLAP_SECONDS", 3.0)
    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._planned_chunk_count(10.0)
    assert exc.value.code == "config_invalid"


def test_invalid_http_timeout_is_rejected_before_provider(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "HTTP_TIMEOUT_SECONDS", 0)
    with pytest.raises(gemini_adapter.GeminiError) as exc:
        gemini_adapter._planned_chunk_count(10.0)
    assert exc.value.code == "config_invalid"


@pytest.mark.asyncio
async def test_timeout_keeps_slot_until_sync_worker_finishes(monkeypatch):
    release_worker = threading.Event()
    entered_worker = threading.Event()
    monkeypatch.setattr(gemini_adapter, "_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(gemini_adapter, "OPERATION_TIMEOUT_SECONDS", 0.02)

    def delayed_transcribe(*_, **__):
        entered_worker.set()
        release_worker.wait(timeout=2)
        return {"text": "ok", "language": "ja", "duration": 0, "segments": []}

    monkeypatch.setattr(gemini_adapter, "_transcribe_sync", delayed_transcribe)

    with pytest.raises(gemini_adapter.GeminiError) as exc:
        await gemini_adapter.transcribe_via_gemini(
            b"x", filename="x.wav", model="gemini-3.5-flash", language=None, prompt=None,
        )
    assert exc.value.code == "unknown_manual_reconcile"
    assert entered_worker.is_set()

    second = asyncio.create_task(gemini_adapter.transcribe_via_gemini(
        b"y", filename="y.wav", model="gemini-3.5-flash", language=None, prompt=None,
    ))
    with pytest.raises(gemini_adapter.GeminiError) as second_exc:
        await second
    assert second_exc.value.code == "admission_timeout"

    release_worker.set()
    await asyncio.sleep(0.03)
    result = await asyncio.wait_for(gemini_adapter.transcribe_via_gemini(
        b"z", filename="z.wav", model="gemini-3.5-flash", language=None, prompt=None,
    ), timeout=1)
    assert result["text"] == "ok"


@pytest.mark.asyncio
async def test_slot_wait_is_bounded_without_reading_body_or_releasing_foreign_permit(monkeypatch):
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()
    monkeypatch.setattr(gemini_adapter, "_semaphore", semaphore)
    monkeypatch.setattr(gemini_adapter, "OPERATION_TIMEOUT_SECONDS", 0.02)
    reads = 0

    class Upload:
        async def read(self, _size):
            nonlocal reads
            reads += 1
            return b"x"

    with pytest.raises(gemini_adapter.GeminiError) as exc:
        await gemini_adapter.transcribe_upload_via_gemini(
            Upload(), filename="blocked.wav", model="gemini-3.5-flash", language=None, prompt=None,
        )

    assert exc.value.code == "admission_timeout"
    assert reads == 0
    assert semaphore.locked()
    semaphore.release()


@pytest.mark.asyncio
async def test_body_read_is_bounded_and_releases_its_own_slot_before_worker(monkeypatch):
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(gemini_adapter, "_semaphore", semaphore)
    monkeypatch.setattr(gemini_adapter, "OPERATION_TIMEOUT_SECONDS", 0.02)
    transcribe = MagicMock()
    monkeypatch.setattr(gemini_adapter, "_transcribe_sync", transcribe)
    read_started = asyncio.Event()

    class Upload:
        async def read(self, _size):
            read_started.set()
            await asyncio.Event().wait()

    with pytest.raises(gemini_adapter.GeminiError) as exc:
        await gemini_adapter.transcribe_upload_via_gemini(
            Upload(), filename="slow.wav", model="gemini-3.5-flash", language=None, prompt=None,
        )

    assert read_started.is_set()
    assert exc.value.code == "admission_timeout"
    assert transcribe.call_count == 0
    assert not semaphore.locked()


@pytest.mark.asyncio
async def test_body_wait_is_subtracted_from_sync_worker_deadline(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(gemini_adapter, "OPERATION_TIMEOUT_SECONDS", 1.0)
    captured = {}

    def transcribe(*_, **kwargs):
        captured["deadline"] = kwargs["deadline_monotonic"]
        captured["entered"] = time.monotonic()
        return {"text": "ok", "language": "ja", "duration": 0, "segments": []}

    monkeypatch.setattr(gemini_adapter, "_transcribe_sync", transcribe)

    async def load_audio():
        await asyncio.sleep(0.05)
        return b"x"

    started = time.monotonic()
    result = await gemini_adapter._transcribe_with_loader(
        load_audio,
        filename="x.wav",
        model="gemini-3.5-flash",
        language=None,
        prompt=None,
    )

    assert result["text"] == "ok"
    assert captured["deadline"] - started <= 1.01
    assert captured["deadline"] - captured["entered"] < 0.98


@pytest.mark.asyncio
async def test_worker_wait_recomputes_remaining_budget_after_pre_worker_delay(monkeypatch):
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(gemini_adapter, "_semaphore", semaphore)
    monkeypatch.setattr(gemini_adapter, "OPERATION_TIMEOUT_SECONDS", 0.1)

    def delayed_log(*_args, **_kwargs):
        time.sleep(0.07)

    def delayed_transcribe(*_, **__):
        time.sleep(0.06)
        return {"text": "late", "language": "ja", "duration": 0, "segments": []}

    monkeypatch.setattr(gemini_adapter.logger, "info", delayed_log)
    monkeypatch.setattr(gemini_adapter, "_transcribe_sync", delayed_transcribe)

    with pytest.raises(gemini_adapter.GeminiError) as exc:
        await gemini_adapter.transcribe_via_gemini(
            b"x", filename="x.wav", model="gemini-3.5-flash", language=None, prompt=None,
        )

    assert exc.value.code == "unknown_manual_reconcile"
    await asyncio.sleep(0.07)
    assert not semaphore.locked()


@pytest.mark.asyncio
async def test_upload_body_is_read_only_after_slot_is_acquired(monkeypatch):
    first_read_started = asyncio.Event()
    allow_first_read = asyncio.Event()
    second_read_started = asyncio.Event()
    monkeypatch.setattr(gemini_adapter, "_semaphore", asyncio.Semaphore(1))
    monkeypatch.setattr(gemini_adapter, "_transcribe_sync", lambda *_, **__: {
        "text": "ok", "language": "ja", "duration": 0, "segments": [],
    })

    class Upload:
        def __init__(self, first):
            self.first = first

        async def read(self, _size):
            if self.first:
                first_read_started.set()
                await allow_first_read.wait()
            else:
                second_read_started.set()
            return b"x"

    first = asyncio.create_task(gemini_adapter.transcribe_upload_via_gemini(
        Upload(True), filename="1.wav", model="gemini-3.5-flash", language=None, prompt=None,
    ))
    await first_read_started.wait()
    second = asyncio.create_task(gemini_adapter.transcribe_upload_via_gemini(
        Upload(False), filename="2.wav", model="gemini-3.5-flash", language=None, prompt=None,
    ))
    await asyncio.sleep(0.02)
    assert not second_read_started.is_set()

    allow_first_read.set()
    await asyncio.wait_for(first, timeout=1)
    await asyncio.wait_for(second, timeout=1)
    assert second_read_started.is_set()
