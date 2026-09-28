"""Regression tests: Whisper decoding must not block the API event loop.

faster-whisper's ``model.transcribe`` returns a lazy segments generator and the
decoder runs while that generator is iterated. These tests replace the model
with a fake whose generator blocks on a thread event, so they reproduce the
original stall locally without network access or a real model.
"""
import asyncio
import io
import os
import sys
import threading
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
import soundfile as sf

SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, SERVICE_ROOT)

import main  # noqa: E402

GENERATOR_WAIT_TIMEOUT_S = 5.0


def _wav_bytes(seconds: float = 0.5, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(seconds * sample_rate), dtype=np.float32), sample_rate, format="WAV")
    return buf.getvalue()


def _segment(start, end, text, words=None):
    return SimpleNamespace(
        start=start,
        end=end,
        text=text,
        avg_logprob=-0.2,
        compression_ratio=1.1,
        no_speech_prob=0.01,
        words=words,
    )


class FakeModel:
    """Mimics WhisperModel.transcribe: returns (lazy generator, info)."""

    def __init__(self, segments, *, block=False, fail_after=None):
        self.segments = segments
        self.block = block
        self.fail_after = fail_after
        self.started = threading.Event()
        self.release = threading.Event()
        self.transcribe_thread = None
        self.iter_threads = []
        self.released_by_timeout = False
        self.calls = 0

    def transcribe(self, audio, **kwargs):
        self.calls += 1
        self.transcribe_thread = threading.get_ident()
        self.last_kwargs = kwargs

        def gen():
            self.iter_threads.append(threading.get_ident())
            self.started.set()
            if self.block and not self.release.wait(GENERATOR_WAIT_TIMEOUT_S):
                self.released_by_timeout = True
            for idx, seg in enumerate(self.segments):
                if self.fail_after is not None and idx >= self.fail_after:
                    raise RuntimeError("decoder exploded mid-iteration")
                self.iter_threads.append(threading.get_ident())
                yield seg

        return gen(), SimpleNamespace(language="ja", language_probability=0.97)


def _slot_state(semaphore):
    return (
        semaphore._value,
        main.active_realtime_requests,
        main.active_deferred_requests,
        main.waiting_requests,
    )


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setattr(main, "API_TOKEN", "")
    monkeypatch.setattr(main, "USE_TEMPERATURE_FALLBACK", False)
    semaphore = main.transcription_semaphore
    before = _slot_state(semaphore)
    yield
    assert _slot_state(semaphore) == before, "transcription slots/counters leaked"


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")


def _post(client, **data):
    form = {"model": "whisper-1", "response_format": "verbose_json", **data}
    return client.post(
        "/v1/audio/transcriptions",
        files={"file": ("chunk.wav", _wav_bytes(), "audio/wav")},
        data=form,
    )


@pytest.mark.asyncio
async def test_generator_consumed_off_loop_and_health_responds_during_decode(monkeypatch, fake_env):
    fake = FakeModel([_segment(0.0, 0.4, " はい"), _segment(0.4, 0.9, " 三時です")], block=True)
    monkeypatch.setattr(main, "model", fake)
    loop_thread = threading.get_ident()

    async with _client() as client:
        post_task = asyncio.create_task(_post(client, language="ja"))
        # Wait (without blocking the loop) until the fake decoder is running.
        assert await asyncio.to_thread(fake.started.wait, GENERATOR_WAIT_TIMEOUT_S)

        # Other event-loop work must progress while decoding is in progress.
        ticks = 0
        for _ in range(5):
            await asyncio.sleep(0)
            ticks += 1
        health = await asyncio.wait_for(client.get("/health"), timeout=2.0)
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"
        assert ticks == 5
        # The decode is still blocked, so /health was served concurrently.
        assert not fake.release.is_set()
        assert not post_task.done()

        fake.release.set()
        resp = await asyncio.wait_for(post_task, timeout=GENERATOR_WAIT_TIMEOUT_S)

    assert not fake.released_by_timeout, "event loop was blocked by generator iteration"
    assert resp.status_code == 200
    assert fake.iter_threads, "generator was never iterated"
    assert all(tid != loop_thread for tid in fake.iter_threads)
    assert fake.transcribe_thread != loop_thread

    body = resp.json()
    assert set(body) == {"text", "language", "language_probability", "duration", "segments"}
    assert body["text"] == "はい 三時です"
    assert body["language"] == "ja"
    assert body["language_probability"] == pytest.approx(0.97)
    assert body["duration"] == pytest.approx(0.9)
    assert [s["id"] for s in body["segments"]] == [0, 1]
    first = body["segments"][0]
    assert first["text"] == " はい"
    assert first["temperature"] == 0.0
    assert first["audio_start"] == 0.0 and first["audio_end"] == 0.4
    assert "words" not in first


@pytest.mark.asyncio
async def test_word_timestamps_are_preserved(monkeypatch, fake_env):
    words = [SimpleNamespace(word="はい", start=0.0, end=0.3, probability=0.9)]
    fake = FakeModel([_segment(0.0, 0.3, "はい", words=words)])
    monkeypatch.setattr(main, "model", fake)

    async with _client() as client:
        resp = await _post(client, timestamp_granularities="word")

    assert resp.status_code == 200
    assert fake.last_kwargs["word_timestamps"] is True
    assert resp.json()["segments"][0]["words"] == [
        {"word": "はい", "start": 0.0, "end": 0.3, "probability": 0.9}
    ]


@pytest.mark.asyncio
async def test_generator_error_returns_500_and_releases_slot(monkeypatch, fake_env):
    failing = FakeModel([_segment(0.0, 0.5, "あ"), _segment(0.5, 1.0, "い")], fail_after=1)
    monkeypatch.setattr(main, "model", failing)
    loop_thread = threading.get_ident()

    async with _client() as client:
        resp = await _post(client)
        assert resp.status_code == 500
        assert "decoder exploded mid-iteration" in resp.json()["detail"]
        assert all(tid != loop_thread for tid in failing.iter_threads)
        # Slot and counters released: fail-fast admission accepts the next request.
        assert not main.transcription_semaphore.locked()
        assert main.active_realtime_requests == 0

        ok = FakeModel([_segment(0.0, 0.5, "はい")])
        monkeypatch.setattr(main, "model", ok)
        resp2 = await _post(client)

    assert resp2.status_code == 200
    assert resp2.json()["text"] == "はい"


@pytest.mark.asyncio
async def test_fail_fast_busy_while_generator_decodes(monkeypatch, fake_env):
    """Concurrency limit still applies while decoding runs in the executor."""
    monkeypatch.setattr(main, "FAIL_FAST_WHEN_BUSY", True)
    monkeypatch.setattr(main, "transcription_semaphore", asyncio.Semaphore(1))
    fake = FakeModel([_segment(0.0, 0.5, "はい")], block=True)
    monkeypatch.setattr(main, "model", fake)

    async with _client() as client:
        first = asyncio.create_task(_post(client))
        assert await asyncio.to_thread(fake.started.wait, GENERATOR_WAIT_TIMEOUT_S)
        second = await asyncio.wait_for(_post(client), timeout=2.0)
        assert second.status_code == 503
        assert second.headers.get("Retry-After")
        fake.release.set()
        first_resp = await asyncio.wait_for(first, timeout=GENERATOR_WAIT_TIMEOUT_S)

    assert first_resp.status_code == 200
    assert fake.calls == 1
    assert main.transcription_semaphore._value == 1


@pytest.mark.asyncio
async def test_auth_still_enforced_before_decoding(monkeypatch, fake_env):
    monkeypatch.setattr(main, "API_TOKEN", "secret-token")
    fake = FakeModel([_segment(0.0, 0.5, "はい")])
    monkeypatch.setattr(main, "model", fake)

    async with _client() as client:
        denied = await _post(client)
        allowed = await client.post(
            "/v1/audio/transcriptions",
            files={"file": ("chunk.wav", _wav_bytes(), "audio/wav")},
            data={"model": "whisper-1"},
            headers={"X-API-Key": "secret-token"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_temperature_fallback_consumes_each_attempt_off_loop(monkeypatch, fake_env):
    """Quality gate still rejects low-confidence attempts and retries with the next temperature."""
    monkeypatch.setattr(main, "USE_TEMPERATURE_FALLBACK", True)
    loop_thread = threading.get_ident()
    bad = SimpleNamespace(
        start=0.0, end=0.5, text="ご視聴ありがとうございました", avg_logprob=-2.0,
        compression_ratio=1.0, no_speech_prob=0.01, words=None,
    )
    good = _segment(0.0, 0.5, "はい")
    threads = []

    class FallbackModel:
        calls = []

        def transcribe(self, audio, **kwargs):
            self.calls.append(kwargs["temperature"])
            seg = bad if len(self.calls) == 1 else good

            def gen():
                threads.append(threading.get_ident())
                yield seg

            return gen(), SimpleNamespace(language="ja", language_probability=0.9)

    fm = FallbackModel()
    monkeypatch.setattr(main, "model", fm)

    async with _client() as client:
        resp = await _post(client)

    assert resp.status_code == 200
    assert fm.calls == [0.0, 0.2]
    body = resp.json()
    assert body["text"] == "はい"
    assert body["segments"][0]["temperature"] == 0.2
    assert len(threads) == 2 and all(tid != loop_thread for tid in threads)
