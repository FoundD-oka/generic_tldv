"""Hard-deadline and resource bounds for finalized-master voiceprint I/O."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile as stdlib_tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api import voiceprint_master_download_worker as worker
from meeting_api import voiceprint_matching as vm


def _source(*, backend: str = "local", key: str = "recordings/test/master.wav"):
    return SimpleNamespace(
        storage_backend=backend,
        storage_path=key,
        media_format="wav",
    )


def _put_local_master(root: Path, key: str, data: bytes) -> None:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _tracked_mkstemp(tmp_path, created_paths):
    real_mkstemp = stdlib_tempfile.mkstemp

    def _create(*, suffix):
        fd, path = real_mkstemp(suffix=suffix, dir=tmp_path)
        created_paths.append(path)
        return fd, path

    return _create


def _blocking_range_process_factory(marker: Path, processes: list[subprocess.Popen]):
    """Return a real child whose fake range request never returns.

    SIGTERM is ignored intentionally so production cleanup must exercise its
    terminate -> kill fallback rather than passing because a polite child
    happened to exit cooperatively.
    """
    child_code = r"""
import os
import signal
import sys
import time
from meeting_api.voiceprint_master_download_worker import download_master_to_fd

signal.signal(signal.SIGTERM, lambda *_args: None)
output_fd = int(sys.argv[1])
marker = sys.argv[2]

class PermanentlyBlockedStorage:
    def get_file_size(self, _path):
        return 8

    def download_file_range(self, _path, _start, _end):
        with open(marker, "wb"):
            pass
        while True:
            time.sleep(1)

download_master_to_fd(
    PermanentlyBlockedStorage(), "opaque", output_fd,
    max_bytes=8, chunk_bytes=4,
)
"""

    def _start(_backend, _path, output_fd, *, max_bytes, chunk_bytes):
        del max_bytes, chunk_bytes
        process = subprocess.Popen(
            [sys.executable, "-c", child_code, str(output_fd), str(marker)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            pass_fds=(output_fd,),
            start_new_session=True,
        )
        processes.append(process)
        return process

    return _start


async def _wait_for_path(path: Path, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not path.exists():
        if loop.time() >= deadline:
            raise AssertionError("blocking range fake did not start")
        await asyncio.sleep(0.01)


def _assert_reaped(processes: list[subprocess.Popen]) -> None:
    assert processes
    for process in processes:
        assert process.poll() is not None
        assert process.returncode is not None


@pytest.mark.asyncio
async def test_cancel_kills_permanently_blocked_range_and_unlinks_without_ffmpeg(tmp_path):
    marker = tmp_path / "range-entered"
    processes: list[subprocess.Popen] = []
    created_paths: list[str] = []
    extractor = MagicMock(return_value=b"should-not-run")

    with patch(
        "meeting_api.voiceprint_matching._start_master_download_process",
        side_effect=_blocking_range_process_factory(marker, processes),
    ), patch(
        "meeting_api.voiceprint_matching.tempfile.mkstemp",
        side_effect=_tracked_mkstemp(tmp_path, created_paths),
    ), patch(
        "meeting_api.voiceprint_matching._extract_exact_clip", extractor,
    ), patch(
        "meeting_api.voiceprint_matching._VOICEPRINT_MASTER_PIPELINE_SEMAPHORE",
        asyncio.Semaphore(1),
    ), patch(
        "meeting_api.voiceprint_matching.VOICEPRINT_MASTER_DOWNLOAD_TIMEOUT_S", 5.0,
    ), patch(
        "meeting_api.voiceprint_matching.VOICEPRINT_MASTER_DOWNLOAD_CHUNK_BYTES", 4,
    ):
        task = asyncio.create_task(vm.extract_exact_clip_wav(_source(), [(0.0, 1.0)]))
        await _wait_for_path(marker)
        started = asyncio.get_running_loop().time()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2.0)
        elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 1.0
    _assert_reaped(processes)
    extractor.assert_not_called()
    assert created_paths and all(not os.path.exists(path) for path in created_paths)


@pytest.mark.asyncio
async def test_timeout_kills_permanently_blocked_range_with_bounded_completion(tmp_path):
    marker = tmp_path / "range-entered"
    processes: list[subprocess.Popen] = []
    created_paths: list[str] = []
    extractor = MagicMock(return_value=b"should-not-run")

    with patch(
        "meeting_api.voiceprint_matching._start_master_download_process",
        side_effect=_blocking_range_process_factory(marker, processes),
    ), patch(
        "meeting_api.voiceprint_matching.tempfile.mkstemp",
        side_effect=_tracked_mkstemp(tmp_path, created_paths),
    ), patch(
        "meeting_api.voiceprint_matching._extract_exact_clip", extractor,
    ), patch(
        "meeting_api.voiceprint_matching._VOICEPRINT_MASTER_PIPELINE_SEMAPHORE",
        asyncio.Semaphore(1),
    ), patch(
        "meeting_api.voiceprint_matching.VOICEPRINT_MASTER_DOWNLOAD_TIMEOUT_S", 0.35,
    ), patch(
        "meeting_api.voiceprint_matching.VOICEPRINT_MASTER_DOWNLOAD_CHUNK_BYTES", 4,
    ):
        started = asyncio.get_running_loop().time()
        with pytest.raises(asyncio.TimeoutError, match="timed out"):
            await asyncio.wait_for(
                vm.extract_exact_clip_wav(_source(), [(0.0, 1.0)]),
                timeout=2.0,
            )
        elapsed = asyncio.get_running_loop().time() - started

    assert marker.exists(), "deadline fired before the permanent range fake began"
    assert elapsed < 1.25
    _assert_reaped(processes)
    extractor.assert_not_called()
    assert created_paths and all(not os.path.exists(path) for path in created_paths)


def test_worker_rejects_oversized_master_before_any_range_body_io(tmp_path):
    range_download = MagicMock()
    storage = SimpleNamespace(
        get_file_size=MagicMock(return_value=9),
        download_file_range=range_download,
    )
    fd, path = stdlib_tempfile.mkstemp(dir=tmp_path)
    try:
        with pytest.raises(worker.MasterDownloadRejected) as exc_info:
            worker.download_master_to_fd(
                storage, "opaque", fd, max_bytes=8, chunk_bytes=4,
            )
    finally:
        os.close(fd)
        os.unlink(path)

    assert exc_info.value.status == worker.STATUS_TOO_LARGE
    range_download.assert_not_called()


@pytest.mark.parametrize("size", [0, -1])
def test_worker_rejects_empty_master_before_any_range_body_io(tmp_path, size):
    range_download = MagicMock()
    storage = SimpleNamespace(
        get_file_size=MagicMock(return_value=size),
        download_file_range=range_download,
    )
    fd, path = stdlib_tempfile.mkstemp(dir=tmp_path)
    try:
        with pytest.raises(worker.MasterDownloadRejected) as exc_info:
            worker.download_master_to_fd(
                storage, "opaque", fd, max_bytes=8, chunk_bytes=4,
            )
    finally:
        os.close(fd)
        os.unlink(path)

    assert exc_info.value.status == worker.STATUS_EMPTY
    range_download.assert_not_called()


def test_worker_downloads_byte_exact_bounded_ranges(tmp_path):
    data = b"0123456789"
    calls: list[tuple[int, int]] = []

    class Storage:
        def get_file_size(self, _storage_path):
            return len(data)

        def download_file_range(self, _storage_path, start, end):
            calls.append((start, end))
            return data[start:end + 1]

    fd, path = stdlib_tempfile.mkstemp(dir=tmp_path)
    try:
        worker.download_master_to_fd(
            Storage(), "opaque", fd, max_bytes=10, chunk_bytes=4,
        )
        os.lseek(fd, 0, os.SEEK_SET)
        assert os.read(fd, len(data) + 1) == data
    finally:
        os.close(fd)
        os.unlink(path)

    assert calls == [(0, 3), (4, 7), (8, 9)]


def test_worker_rejects_short_range_response(tmp_path):
    storage = SimpleNamespace(
        get_file_size=MagicMock(return_value=4),
        download_file_range=MagicMock(return_value=b"abc"),
    )
    fd, path = stdlib_tempfile.mkstemp(dir=tmp_path)
    try:
        with pytest.raises(worker.MasterDownloadRejected) as exc_info:
            worker.download_master_to_fd(
                storage, "opaque", fd, max_bytes=4, chunk_bytes=4,
            )
    finally:
        os.close(fd)
        os.unlink(path)

    assert exc_info.value.status == worker.STATUS_RANGE_LENGTH
    storage.download_file_range.assert_called_once_with("opaque", 0, 3)


@pytest.mark.asyncio
async def test_parent_local_path_downloads_and_unlinks_before_return(tmp_path):
    storage_root = tmp_path / "storage"
    key = "recordings/test/master.wav"
    data = b"0123456789"
    _put_local_master(storage_root, key, data)
    created_paths: list[str] = []

    def _extractor(path, ranges):
        assert Path(path).read_bytes() == data
        assert ranges == [(1.0, 3.0)]
        return b"normalized-wav"

    with patch.dict(os.environ, {"LOCAL_STORAGE_DIR": str(storage_root)}), patch(
        "meeting_api.voiceprint_matching.tempfile.mkstemp",
        side_effect=_tracked_mkstemp(tmp_path, created_paths),
    ), patch(
        "meeting_api.voiceprint_matching._extract_exact_clip", side_effect=_extractor,
    ), patch(
        "meeting_api.voiceprint_matching.VOICEPRINT_MASTER_DOWNLOAD_CHUNK_BYTES", 4,
    ), patch(
        "meeting_api.voiceprint_matching._VOICEPRINT_MASTER_PIPELINE_SEMAPHORE",
        asyncio.Semaphore(1),
    ):
        result = await vm.extract_exact_clip_wav(
            _source(key=key), [(1.0, 3.0)],
        )

    assert result == b"normalized-wav"
    assert created_paths and all(not os.path.exists(path) for path in created_paths)


@pytest.mark.asyncio
async def test_parent_maps_oversize_status_and_unlinks(tmp_path):
    storage_root = tmp_path / "storage"
    key = "recordings/test/master.wav"
    _put_local_master(storage_root, key, b"123456789")
    created_paths: list[str] = []
    extractor = MagicMock()

    with patch.dict(os.environ, {"LOCAL_STORAGE_DIR": str(storage_root)}), patch(
        "meeting_api.voiceprint_matching.tempfile.mkstemp",
        side_effect=_tracked_mkstemp(tmp_path, created_paths),
    ), patch(
        "meeting_api.voiceprint_matching._extract_exact_clip", extractor,
    ), patch(
        "meeting_api.voiceprint_matching.VOICEPRINT_MAX_MASTER_BYTES", 8,
    ), patch(
        "meeting_api.voiceprint_matching._VOICEPRINT_MASTER_PIPELINE_SEMAPHORE",
        asyncio.Semaphore(1),
    ):
        with pytest.raises(ValueError, match="exceeds"):
            await vm.extract_exact_clip_wav(_source(key=key), [(0.0, 1.0)])

    extractor.assert_not_called()
    assert created_paths and all(not os.path.exists(path) for path in created_paths)


@pytest.mark.asyncio
async def test_exact_and_automatic_paths_share_one_download_ffmpeg_gate(tmp_path):
    storage_root = tmp_path / "storage"
    key = "recordings/test/master.wav"
    _put_local_master(storage_root, key, b"abcd")
    created_paths: list[str] = []
    extractor_entered = threading.Event()
    extractor_release = threading.Event()
    lock = threading.Lock()
    counters = {"active": 0, "max_active": 0}

    def _extractor(path, ranges):
        assert Path(path).read_bytes() == b"abcd"
        with lock:
            counters["active"] += 1
            counters["max_active"] = max(counters["max_active"], counters["active"])
        extractor_entered.set()
        assert extractor_release.wait(3), "test did not release ffmpeg substitute"
        with lock:
            counters["active"] -= 1
        return b"normalized-wav"

    exact_extractor = MagicMock(side_effect=_extractor)
    automatic_extractor = MagicMock(side_effect=_extractor)
    embed = AsyncMock(return_value=[1.0, 0.0])
    with patch.dict(os.environ, {"LOCAL_STORAGE_DIR": str(storage_root)}), patch(
        "meeting_api.voiceprint_matching.tempfile.mkstemp",
        side_effect=_tracked_mkstemp(tmp_path, created_paths),
    ), patch(
        "meeting_api.voiceprint_matching._extract_exact_clip", exact_extractor,
    ), patch(
        "meeting_api.voiceprint_matching._extract_and_concat_clip", automatic_extractor,
    ), patch(
        "meeting_api.voiceprint_matching._embed_clip", new=embed,
    ), patch(
        "meeting_api.voiceprint_matching.VOICEPRINT_SERVICE_URL", "http://voiceprint-service",
    ), patch(
        "meeting_api.voiceprint_matching.VOICEPRINT_MASTER_DOWNLOAD_CHUNK_BYTES", 4,
    ), patch(
        "meeting_api.voiceprint_matching._VOICEPRINT_MASTER_PIPELINE_SEMAPHORE",
        asyncio.Semaphore(1),
    ):
        exact = asyncio.create_task(
            vm.extract_exact_clip_wav(_source(key=key), [(0.0, 10.0)])
        )
        assert await asyncio.to_thread(extractor_entered.wait, 3)
        automatic = asyncio.create_task(
            vm.embed_clip_from_ranges(_source(key=key), [(0.0, 10.0)])
        )
        await asyncio.sleep(0.1)
        automatic_extractor.assert_not_called()
        extractor_release.set()
        assert await exact == b"normalized-wav"
        assert await automatic == [1.0, 0.0]

    exact_extractor.assert_called_once()
    automatic_extractor.assert_called_once()
    assert counters["max_active"] == 1
    embed.assert_awaited_once_with(b"normalized-wav")
    assert created_paths and all(not os.path.exists(path) for path in created_paths)
