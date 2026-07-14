"""Cancellation and concurrency boundaries for direct voiceprint audio."""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from meeting_api import voiceprint_matching as vm


@pytest.mark.asyncio
async def test_direct_audio_cancel_waits_for_worker_cleanup(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    created_paths: list[str] = []

    def blocking_normalize(_audio_bytes: bytes, _media_format: str) -> bytes:
        fd, path = tempfile.mkstemp(suffix=".webm", dir=tmp_path)
        os.close(fd)
        created_paths.append(path)
        try:
            entered.set()
            assert release.wait(2), "test did not release direct audio worker"
            return b"normalized-wav"
        finally:
            os.unlink(path)

    with patch(
        "meeting_api.voiceprint_matching._normalize_audio_to_wav",
        side_effect=blocking_normalize,
    ), patch(
        "meeting_api.voiceprint_matching._VOICEPRINT_DIRECT_PIPELINE_SEMAPHORE",
        asyncio.Semaphore(1),
    ):
        task = asyncio.create_task(vm.normalize_direct_audio_to_wav(b"raw", "webm"))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done(), "cancel returned while direct ffmpeg worker was active"
        assert created_paths and os.path.exists(created_paths[0])
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert created_paths and all(not os.path.exists(path) for path in created_paths)


@pytest.mark.asyncio
async def test_direct_audio_cancel_drains_real_normalizer_and_removes_both_temp_files(
    tmp_path,
):
    entered = threading.Event()
    release = threading.Event()
    created_paths: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def tracked_mkstemp(*, suffix):
        fd, path = real_mkstemp(suffix=suffix, dir=tmp_path)
        created_paths.append(path)
        return fd, path

    def blocking_subprocess(args, **_kwargs):
        destination = args[-2]
        entered.set()
        assert release.wait(2), "test did not release ffmpeg substitute"
        with open(destination, "wb") as output:
            output.write(b"normalized-wav")
        return SimpleNamespace(returncode=0, stderr=b"")

    with patch(
        "meeting_api.voiceprint_matching.tempfile.mkstemp",
        side_effect=tracked_mkstemp,
    ), patch(
        "meeting_api.voiceprint_matching.subprocess.run",
        side_effect=blocking_subprocess,
    ), patch(
        "meeting_api.voiceprint_matching._VOICEPRINT_DIRECT_PIPELINE_SEMAPHORE",
        asyncio.Semaphore(1),
    ):
        task = asyncio.create_task(vm.normalize_direct_audio_to_wav(b"raw", "webm"))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()
        assert len(created_paths) == 2
        assert all(os.path.exists(path) for path in created_paths)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert all(not os.path.exists(path) for path in created_paths)


@pytest.mark.asyncio
async def test_direct_audio_normalization_respects_dedicated_concurrency_gate():
    first_entered = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    counters = {"active": 0, "max_active": 0, "calls": 0}

    def blocking_normalize(_audio_bytes: bytes, _media_format: str) -> bytes:
        with lock:
            counters["calls"] += 1
            counters["active"] += 1
            counters["max_active"] = max(counters["max_active"], counters["active"])
        first_entered.set()
        assert release.wait(2), "test did not release direct audio worker"
        with lock:
            counters["active"] -= 1
        return b"normalized-wav"

    with patch(
        "meeting_api.voiceprint_matching._normalize_audio_to_wav",
        side_effect=blocking_normalize,
    ), patch(
        "meeting_api.voiceprint_matching._VOICEPRINT_DIRECT_PIPELINE_SEMAPHORE",
        asyncio.Semaphore(1),
    ):
        first = asyncio.create_task(vm.normalize_direct_audio_to_wav(b"one", "webm"))
        assert await asyncio.to_thread(first_entered.wait, 1)
        second = asyncio.create_task(vm.normalize_direct_audio_to_wav(b"two", "webm"))
        await asyncio.sleep(0.05)
        assert counters["calls"] == 1
        release.set()
        assert await first == b"normalized-wav"
        assert await second == b"normalized-wav"

    assert counters == {"active": 0, "max_active": 1, "calls": 2}


@pytest.mark.asyncio
async def test_cancel_while_waiting_for_direct_gate_never_starts_worker():
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    lock = threading.Lock()

    def blocking_normalize(_audio_bytes: bytes, _media_format: str) -> bytes:
        nonlocal calls
        with lock:
            calls += 1
        entered.set()
        assert release.wait(2), "test did not release direct audio worker"
        return b"normalized-wav"

    with patch(
        "meeting_api.voiceprint_matching._normalize_audio_to_wav",
        side_effect=blocking_normalize,
    ), patch(
        "meeting_api.voiceprint_matching._VOICEPRINT_DIRECT_PIPELINE_SEMAPHORE",
        asyncio.Semaphore(1),
    ):
        first = asyncio.create_task(vm.normalize_direct_audio_to_wav(b"one", "webm"))
        assert await asyncio.to_thread(entered.wait, 1)
        waiting = asyncio.create_task(vm.normalize_direct_audio_to_wav(b"two", "webm"))
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert calls == 1
        release.set()
        assert await first == b"normalized-wav"
