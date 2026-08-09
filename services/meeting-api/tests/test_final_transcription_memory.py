"""ST-9 / ST-10 — final transcription audio path is disk-streamed.

The point of these tests is empirical, not documentary: the whole
run_deferred_transcription pipeline is exercised end to end with REAL httpx
multipart encoding (only the network transport is replaced, and it drains
`request.stream` chunk by chunk exactly like a socket would), so the peak
tracemalloc footprint measured here is a real measurement of how much of the
audio the API process holds at once. A regression that reads the audio back
into memory anywhere in the chain (storage download, ffmpeg output, duration
probe, multipart body) makes the 192MiB run's peak jump by ~192MiB and fails
AT-001, regardless of what the code claims to do.

Covers: AT-001 (mixed peak), AT-002 (lane peak), AT-003 (static guard),
AT-004/FP-006 (size-proportional ffmpeg timeout + clamps + env), AT-005
(timeout actually reaches subprocess.run), AT-006 (temp cleanup), AT-007
(no-conversion passthrough + unchanged conversion failure mapping).
"""

from __future__ import annotations

import gc
import inspect
import json
import os
import re
import shutil
import subprocess
import tempfile
import tracemalloc
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from meeting_api import final_transcription
from meeting_api.final_transcription import (
    _audio_duration_seconds,
    _call_transcription_service,
    _convert_audio_to_wav,
    _ffmpeg_timeout_seconds,
    run_deferred_transcription,
)
from meeting_api.schemas import MeetingStatus
from meeting_api.storage import LocalStorageClient

from .conftest import TEST_MEETING_ID, MockResult, make_meeting

MIB = 1024 * 1024
BASE = "recordings/5/1001/sess-1"
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _write_silence_wav(path: str, size_mib: int) -> int:
    """Write a 16k mono 16-bit silent WAV of roughly `size_mib` MiB.

    Written in chunks so building the fixture never allocates the whole file
    in the test process either.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    chunk = b"\x00" * (4 * MIB)
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(SAMPLE_RATE)
        for _ in range(size_mib // 4):
            wav_file.writeframes(chunk)
    del chunk
    gc.collect()
    return os.path.getsize(path)


class DrainTransport(httpx.AsyncBaseTransport):
    """Consumes the request body the way a socket would: chunk by chunk,
    never materializing it. Counts the bytes so a test can prove the audio
    really was sent (and not silently skipped)."""

    def __init__(self, *, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self.received_bytes = 0
        self.payload = payload if payload is not None else {
            "language": "ja",
            "segments": [{"start": 0.0, "end": 1.0, "text": "テスト"}],
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async for chunk in request.stream:
            self.received_bytes += len(chunk)
        return httpx.Response(
            self.status_code,
            content=json.dumps(self.payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            request=request,
        )


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    """Point every tempfile.* default at a directory we can inspect (AT-006)."""
    root = tmp_path / "tmpdir"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


@pytest.fixture
def storage_root(tmp_path, monkeypatch):
    """LocalStorageClient rooted in tmp_path, with the streaming
    download_file_to_path that MinIO/GCS implement natively (the local
    backend inherits the read-all fallback, which is a development-only
    path — AT-003 pins the production call site instead)."""
    root = tmp_path / "storage"
    root.mkdir()
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(root))
    monkeypatch.setenv("LOCAL_STORAGE_FSYNC", "false")

    def _streaming_download(self, key, dest_file_path):
        shutil.copyfile(self._full_path(key), dest_file_path)
        return dest_file_path

    monkeypatch.setattr(LocalStorageClient, "download_file_to_path", _streaming_download)
    return root


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _install_transport(monkeypatch, transport: DrainTransport) -> None:
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: _REAL_ASYNC_CLIENT(transport=transport, **kwargs),
    )
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://tx.invalid/v1/audio/transcriptions")
    monkeypatch.delenv("DEFERRED_TRANSCRIPTION_SERVICE_URL", raising=False)


def _mixed_meeting(media_files=None):
    return make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "transcribe_enabled": True,
            "recording_enabled": True,
            "speaker_events": [],
            "recordings": [{
                "id": 1001,
                "session_uid": "sess-1",
                "status": "completed",
                "media_files": media_files or [{
                    "id": 2001, "type": "audio", "format": "wav",
                    "storage_backend": "local",
                    "storage_path": f"{BASE}/audio/master.wav",
                    "finalized_by": "recording_finalizer.master",
                }],
            }],
        },
    )


def _db_for(meeting):
    db = AsyncMock()

    async def execute(statement, *args, **kwargs):
        sql = str(statement)
        if "FROM meetings" in sql:
            return MockResult([meeting])
        if "count(transcriptions.id)" in sql:
            return MockResult(scalar_value=0)
        return MockResult()

    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


def _patched_side_effects():
    """Everything outside the audio path that would need a real DB/Redis."""
    return [
        patch("meeting_api.final_transcription.attributes.flag_modified", MagicMock()),
        patch("meeting_api.final_transcription._clear_live_transcript_cache", AsyncMock(return_value=True)),
        patch("meeting_api.final_transcription._publish_transcript_finalized", AsyncMock()),
        patch("meeting_api.final_transcription.queue_drive_export_if_needed", MagicMock()),
        patch("meeting_api.voiceprint_matching.run_voiceprint_matching_followup", AsyncMock()),
    ]


async def _run_and_measure(meeting) -> tuple[int, object]:
    """Run the whole deferred pipeline and return (tracemalloc peak, result)."""
    db = _db_for(meeting)
    patches = _patched_side_effects()
    for p in patches:
        p.start()
    gc.collect()
    tracemalloc.start()
    tracemalloc.reset_peak()
    try:
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        for p in patches:
            p.stop()
    return peak, result


# ---------------------------------------------------------------------------
# AT-001 — mixed master peak memory does not scale with audio length
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mixed_master_peak_memory_does_not_scale_with_audio_length(
    tmp_path, monkeypatch, storage_root, temp_root, capsys
):
    peaks: dict[int, int] = {}
    sent: dict[int, int] = {}

    for size_mib in (48, 192):
        wav_path = str(storage_root / BASE / "audio" / "master.wav")
        wav_size = _write_silence_wav(wav_path, size_mib)
        transport = DrainTransport()
        _install_transport(monkeypatch, transport)

        peak, result = await _run_and_measure(_mixed_meeting())

        assert result.segment_count == 1
        assert transport.received_bytes >= wav_size, (
            "the audio must actually have been streamed to the provider"
        )
        peaks[size_mib] = peak
        sent[size_mib] = transport.received_bytes
        os.remove(wav_path)

    with capsys.disabled():
        print(
            "\n[AT-001] mixed peak: "
            f"48MiB run={peaks[48] / MIB:.2f}MiB (sent {sent[48] / MIB:.1f}MiB), "
            f"192MiB run={peaks[192] / MIB:.2f}MiB (sent {sent[192] / MIB:.1f}MiB), "
            f"delta={(peaks[192] - peaks[48]) / MIB:.2f}MiB"
        )

    assert peaks[192] <= 64 * MIB, (
        f"peak for a 192MiB recording was {peaks[192] / MIB:.1f}MiB — the audio "
        "is being buffered in memory again"
    )
    assert peaks[192] - peaks[48] <= 16 * MIB, (
        f"peak grew {(peaks[192] - peaks[48]) / MIB:.1f}MiB for 4x the audio — "
        "memory is proportional to audio length"
    )


# ---------------------------------------------------------------------------
# AT-002 — lane path peak memory does not scale with the lane set size
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lane_peak_memory_does_not_scale_with_lane_total(
    tmp_path, monkeypatch, storage_root, temp_root, capsys
):
    lane_keys = ["aaaaaaaaaa", "bbbbbbbbbb", "cccccccccc"]
    media_files = [{
        "id": 2001, "type": "audio", "format": "wav",
        "storage_backend": "local",
        "storage_path": f"{BASE}/audio/master.wav",
        "finalized_by": "recording_finalizer.master",
    }]
    _write_silence_wav(str(storage_root / BASE / "audio" / "master.wav"), 4)
    total_bytes = 0
    for idx, key in enumerate(lane_keys):
        lane_path = f"{BASE}/lane-{key}/master.wav"
        total_bytes += _write_silence_wav(str(storage_root / lane_path), 64)
        media_files.append({
            "id": 2100 + idx, "type": f"lane-{key}", "format": "wav",
            "storage_backend": "local",
            "storage_path": lane_path,
            "finalized_by": "recording_finalizer.master",
            "lane": {"lane_id": f"t{idx}", "lane_label": f"参加者{idx}",
                     "lane_id_source": "participant-id"},
        })

    transport = DrainTransport()
    _install_transport(monkeypatch, transport)

    peak, result = await _run_and_measure(_mixed_meeting(media_files))

    with capsys.disabled():
        print(
            f"\n[AT-002] lane peak: {peak / MIB:.2f}MiB for "
            f"{total_bytes / MIB:.0f}MiB across {len(lane_keys)} lanes "
            f"(sent {transport.received_bytes / MIB:.1f}MiB)"
        )

    assert result.segment_count == len(lane_keys), "every lane must be transcribed"
    assert transport.received_bytes >= total_bytes
    assert peak <= 64 * MIB, (
        f"peak for {total_bytes / MIB:.0f}MiB of lane audio was {peak / MIB:.1f}MiB"
    )


# ---------------------------------------------------------------------------
# AT-003 — static guard against re-introducing whole-file reads
# ---------------------------------------------------------------------------

def test_module_never_calls_read_all_storage_api():
    source = inspect.getsource(final_transcription)
    calls = re.findall(r"download_file\w*", source)
    assert calls, "sanity: the module must still download the recording somehow"
    assert set(calls) == {"download_file_to_path"}, (
        f"read-all storage API resurfaced: {sorted(set(calls))}"
    )


def test_audio_path_helpers_do_not_buffer_audio_in_memory():
    for func in (_audio_duration_seconds, _convert_audio_to_wav, _call_transcription_service):
        assert "BytesIO(" not in inspect.getsource(func), (
            f"{func.__name__} buffers the audio in memory"
        )


# ---------------------------------------------------------------------------
# AT-004 / FP-006 — size-proportional ffmpeg timeout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size_mib,expected", [
    (0, 120.0),        # empty/tiny input → floor
    (1, 120.0),        # 4s proportional → floor still wins
    (30, 120.0),       # 120s proportional → exactly the floor
    (84, 336.0),       # linear region (3h @ 64kbps)
    (450, 1800.0),     # exactly the cap
    (4096, 1800.0),    # far beyond the cap → clamped
])
def test_ffmpeg_timeout_is_size_proportional_with_clamps(size_mib, expected, monkeypatch):
    for name in (
        "DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS",
        "DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_MAX_SECONDS",
        "DEFERRED_TRANSCRIPTION_FFMPEG_SECONDS_PER_MIB",
    ):
        monkeypatch.delenv(name, raising=False)
    assert _ffmpeg_timeout_seconds(size_mib * MIB) == pytest.approx(expected)


def test_ffmpeg_timeout_env_overrides(monkeypatch):
    # FP-006 — the pre-existing env keeps working, now as the floor: an
    # operator who raised it never gets a shorter timeout than before.
    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS", "600")
    assert _ffmpeg_timeout_seconds(1 * MIB) == pytest.approx(600.0)
    assert _ffmpeg_timeout_seconds(84 * MIB) == pytest.approx(600.0)

    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_FFMPEG_SECONDS_PER_MIB", "10")
    assert _ffmpeg_timeout_seconds(84 * MIB) == pytest.approx(840.0)

    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_MAX_SECONDS", "700")
    assert _ffmpeg_timeout_seconds(84 * MIB) == pytest.approx(700.0)


def test_ffmpeg_timeout_floor_wins_over_misconfigured_cap(monkeypatch):
    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS", "900")
    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_MAX_SECONDS", "60")
    assert _ffmpeg_timeout_seconds(1 * MIB) == pytest.approx(900.0)
    assert _ffmpeg_timeout_seconds(4096 * MIB) == pytest.approx(900.0)


# ---------------------------------------------------------------------------
# AT-005 — the computed timeout actually reaches subprocess.run
# ---------------------------------------------------------------------------

def test_convert_passes_computed_timeout_to_subprocess(tmp_path, monkeypatch):
    src = tmp_path / "master.webm"
    src.write_bytes(b"\x00" * (2 * MIB))
    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_FFMPEG_SECONDS_PER_MIB", "100")
    monkeypatch.delenv("DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_MAX_SECONDS", raising=False)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = kwargs.get("timeout")
        open(cmd[cmd.index("-f") + 2], "wb").close()
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dst_path, fmt = _convert_audio_to_wav(str(src), "webm")

    assert fmt == "wav"
    assert captured["timeout"] == pytest.approx(_ffmpeg_timeout_seconds(2 * MIB))
    assert captured["timeout"] == pytest.approx(200.0), "2MiB x 100s/MiB, above the 120s floor"
    assert not src.exists(), "the source encoding is released once converted"
    assert os.path.exists(dst_path)


# ---------------------------------------------------------------------------
# AT-006 — no temp files survive any exit path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_temp_dir_is_removed_on_success_failure_and_lane_fallback(
    tmp_path, monkeypatch, storage_root, temp_root
):
    wav_path = str(storage_root / BASE / "audio" / "master.wav")
    _write_silence_wav(wav_path, 4)

    # 1) success
    _install_transport(monkeypatch, DrainTransport())
    await _run_and_measure(_mixed_meeting())
    assert os.listdir(temp_root) == [], "temp dir leaked after a successful run"

    # 2) provider failure (HTTP 500)
    _install_transport(monkeypatch, DrainTransport(status_code=500, payload={"detail": "boom"}))
    with pytest.raises(HTTPException):
        await _run_and_measure(_mixed_meeting())
    assert os.listdir(temp_root) == [], "temp dir leaked after a provider failure"

    # 3) lane path fails (missing lane object) → mixed-master fallback
    media_files = [
        {
            "id": 2001, "type": "audio", "format": "wav",
            "storage_backend": "local",
            "storage_path": f"{BASE}/audio/master.wav",
            "finalized_by": "recording_finalizer.master",
        },
        {
            "id": 2002, "type": "lane-aaaaaaaaaa", "format": "wav",
            "storage_backend": "local",
            "storage_path": f"{BASE}/lane-aaaaaaaaaa/master.wav",
            "finalized_by": "recording_finalizer.master",
            "lane": {"lane_id": "t1", "lane_label": "山森",
                     "lane_id_source": "participant-id"},
        },
    ]
    _install_transport(monkeypatch, DrainTransport())
    meeting = _mixed_meeting(media_files)
    _peak, result = await _run_and_measure(meeting)
    assert "lane" in (meeting.data["final_transcription"]["lane_fallback_reason"] or "")
    assert result.segment_count == 1
    assert os.listdir(temp_root) == [], "temp dir leaked after a lane fallback"


# ---------------------------------------------------------------------------
# AT-007 — conversion semantics unchanged
# ---------------------------------------------------------------------------

def test_wav_input_is_passed_through_without_conversion(tmp_path, monkeypatch):
    src = tmp_path / "master.wav"
    src.write_bytes(b"\x00" * 1024)

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("ffmpeg must not run for an already-wav input")

    monkeypatch.setattr(subprocess, "run", explode)
    assert _convert_audio_to_wav(str(src), "wav") == (str(src), "wav")
    assert src.exists(), "a passthrough input must not be deleted"


def test_conversion_failure_and_timeout_keep_their_http_mapping(tmp_path, monkeypatch):
    src = tmp_path / "master.webm"
    src.write_bytes(b"\x00" * 1024)

    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"bad input"),
    )
    with pytest.raises(HTTPException) as failed:
        _convert_audio_to_wav(str(src), "webm")
    assert failed.value.status_code == 500
    assert failed.value.detail == "Audio conversion failed"

    def timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(HTTPException) as timed_out:
        _convert_audio_to_wav(str(src), "webm")
    assert timed_out.value.status_code == 500
    assert timed_out.value.detail == "Audio conversion timed out"
    assert src.exists(), "a failed conversion must not consume the source"
