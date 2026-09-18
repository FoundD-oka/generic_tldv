"""Exercise actual media, persistence, playback routing and retry behavior."""

import copy
import json
from contextlib import asynccontextmanager
import subprocess
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi import HTTPException

from meeting_api import recording_finalizer as fin, recordings, sweeps
from meeting_api.storage import LocalStorageClient
from meeting_api.video_mux import mux_recording_video, needs_video_audio_mux
from .conftest import MockResult, make_meeting, make_session
from .test_recordings_concurrent_chunks import _StatefulMockDB, _make_upload_call
from .test_sweeps_unfinalized_recordings import FetchAllResult

BASE = "recordings/5/999/sess-abc123"


def ffmpeg(*args):
    return subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", *map(str, args)],
        check=True, capture_output=True, timeout=30,
    )


@pytest.fixture(params=[("webm", "libvpx-vp9", "webm"), ("mp4", "libx264", "wav")])
def media_pair(tmp_path, request):
    fmt, codec, audio_fmt = request.param
    video_path = tmp_path / f"video.{fmt}"
    audio_path = tmp_path / f"audio.{audio_fmt}"
    # Audio is deliberately shorter than video. A -shortest-only mux would
    # lose the final second of footage; apad must preserve all 3 seconds.
    ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10:d=3",
           "-c:v", codec, "-an", video_path)
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=700:duration=2:sample_rate=16000",
           "-c:a", "libopus" if audio_fmt == "webm" else "pcm_s16le", audio_path)
    if audio_fmt == "wav":
        # Zoom's PulseAudioCapture emits canonical 44-byte WAV headers;
        # ffmpeg adds a LIST chunk, which the chunk finalizer rightly rejects.
        with wave.open(str(audio_path), "rb") as source:
            params, frames = source.getparams(), source.readframes(source.getnframes())
        with wave.open(str(audio_path), "wb") as destination:
            destination.setparams(params)
            destination.writeframes(frames)
    storage = LocalStorageClient(base_dir=str(tmp_path / "storage"))
    video_key, audio_key = f"{BASE}/video/000000.{fmt}", f"{BASE}/audio/master.{audio_fmt}"
    storage.upload_file_path(video_key, str(video_path))
    storage.upload_file_path(audio_key, str(audio_path))
    video = {"id": 2, "type": "video", "format": fmt, "storage_path": video_key,
             "start_time_utc": "2026-09-18T01:00:00.000Z", "duration_seconds": 3}
    audio = {"id": 1, "type": "audio", "format": audio_fmt, "storage_path": audio_key,
             "start_time_utc": "2026-09-18T01:00:00.000Z", "finalized_by": "recording_finalizer.master"}
    return storage, video, audio


def decode_audio(storage, media, tmp_path):
    path = tmp_path / ("decoded." + media["format"])
    storage.download_file_to_path(media["storage_path"], str(path))
    result = ffmpeg("-i", path, "-map", "0:a:0", "-ar", "16000", "-ac", "1", "-f", "f32le", "-")
    samples = np.frombuffer(result.stdout, dtype="<f4")
    frames = ffmpeg("-i", path, "-map", "0:v:0", "-f", "framemd5", "-").stdout.decode()
    assert len([line for line in frames.splitlines() if line and not line.startswith("#")]) == 30
    return samples


def rms(samples, start, end):
    segment = samples[int(start * 16000):int(end * 16000)]
    assert len(segment)
    return float(np.sqrt(np.mean(segment ** 2)))


@pytest.mark.parametrize("delay", [0, 0.5, -0.5])
def test_real_mux_aligns_audio_preserves_frames_and_is_idempotent(media_pair, tmp_path, delay):
    storage, video, audio = media_pair
    audio["start_time_utc"] = (
        "2026-09-18T01:00:00.500Z" if delay > 0 else
        "2026-09-18T00:59:59.500Z" if delay < 0 else video["start_time_utc"]
    )
    original_video = storage.download_file(video["storage_path"])
    original_audio = storage.download_file(audio["storage_path"])
    result = mux_recording_video(storage, video, audio)
    samples = decode_audio(storage, result, tmp_path)
    assert rms(samples, max(delay, 0) + 0.15, max(delay, 0) + 0.3) > 0.05
    if delay > 0:
        assert rms(samples, 0.1, 0.3) < 0.001
    assert rms(samples, 2.7, 2.9) < 0.001
    if delay < 0:
        assert rms(samples, 1.7, 1.9) < 0.001  # early audio was trimmed
    assert storage.download_file(video["storage_path"]) == original_video
    assert storage.download_file(audio["storage_path"]) == original_audio
    assert result["is_final"] and result["video_audio_mux"]["version"] == 1
    assert not needs_video_audio_mux({"media_files": [audio, result]})
    with patch("meeting_api.video_mux.subprocess.run", side_effect=AssertionError("must not rebuild")):
        assert mux_recording_video(storage, result, audio) == result


def test_failed_mux_does_not_publish_and_retries(media_pair):
    storage, video, audio = media_pair
    original = copy.deepcopy(video)
    # Existing silent master must not short-circuit the new mux.
    storage.upload_file(f"{BASE}/video/master.{video['format']}", b"old silent master")
    with patch("meeting_api.video_mux.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 1800)):
        with pytest.raises(subprocess.TimeoutExpired):
            mux_recording_video(storage, video, audio)
    assert video == original
    assert not storage.file_exists(f"{BASE}/video/master.av.{video['format']}")
    assert needs_video_audio_mux({"media_files": [video, audio]})
    assert mux_recording_video(storage, video, audio)["video_audio_mux"]


@pytest.mark.parametrize("missing", ["video", "audio"])
def test_partial_capture_timestamp_does_not_publish_unsynchronized_video(media_pair, missing):
    storage, video, audio = media_pair
    (video if missing == "video" else audio).pop("start_time_utc")
    with pytest.raises(ValueError, match="Both capture timestamps are required"):
        mux_recording_video(storage, video, audio)
    assert not storage.file_exists(f"{BASE}/video/master.av.{video['format']}")
    assert needs_video_audio_mux({"media_files": [video, audio]})


def test_legacy_media_without_timestamps_warns_and_still_has_sound(media_pair, tmp_path, caplog):
    storage, video, audio = media_pair
    video.pop("start_time_utc")
    audio.pop("start_time_utc")
    result = mux_recording_video(storage, video, audio)
    assert rms(decode_audio(storage, result, tmp_path), 0.2, 0.4) > 0.05
    assert "Capture timestamps unavailable" in caplog.text


@pytest.mark.asyncio
async def test_finalizer_publishes_mux_for_playback_and_download(media_pair):
    storage, video, audio = media_pair
    # Exercise the real chunk -> audio master -> mux chain, not only an
    # already-finalized fixture. Real uploads can arrive in either order.
    master_key = audio["storage_path"]
    audio["storage_path"] = master_key.replace("master.", "000000.")
    storage.upload_file(audio["storage_path"], storage.download_file(master_key))
    storage.delete_file(master_key)
    audio.pop("finalized_by")
    # Put video first to verify finalization is independent of upload order.
    rec = {"id": 999, "session_uid": "sess-abc123", "status": "completed", "media_files": [video, audio]}
    meeting = make_meeting(data={"recordings": [rec]})
    db = AsyncMock()
    db.execute.return_value = MockResult([meeting])
    with patch.object(fin, "create_storage_client", return_value=storage), \
         patch("sqlalchemy.orm.attributes.flag_modified"):
        await fin.finalize_recording_master(meeting.id, db)
    saved = meeting.data["recordings"][0]
    assert saved["playback_url"]["video"] == "/recordings/999/master?type=video"
    assert saved["playback_url"]["audio"] == "/recordings/999/master?type=audio"
    muxed = saved["media_files"][0]
    assert muxed["source_video_path"] == video["storage_path"]
    assert muxed["storage_path"].endswith("master.av." + video["format"])
    with patch.object(recordings, "_find_meeting_data_recording", new=AsyncMock(return_value=(meeting, saved))), \
         patch.object(recordings, "get_storage_client_for", return_value=storage):
        # The actual canonical route delegates to the actual download route.
        response = await recordings.get_recording_master(999, "video", (None, MagicMock(id=5)), db)
    assert "master.av." in response["url"]
    assert response["content_type"] == f"video/{video['format']}"
    assert response["media_file_id"] == 2
    assert response["raw_url"] == "/recordings/999/media/2/raw"


@pytest.mark.asyncio
async def test_finalizer_failure_leaves_metadata_unpublished(media_pair):
    storage, video, audio = media_pair
    original = {"recordings": [{"id": 999, "media_files": [video, audio]}]}
    meeting = make_meeting(data=copy.deepcopy(original))
    db = AsyncMock()
    db.execute.return_value = MockResult([meeting])
    with patch.object(fin, "create_storage_client", return_value=storage), \
         patch.object(fin, "mux_recording_video", side_effect=RuntimeError("failed")):
        with pytest.raises(RuntimeError):
            await fin.finalize_recording_master(meeting.id, db)
    assert meeting.data == original
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_preserves_capture_start_and_mux_metadata():
    meeting, session = make_meeting(data={}), make_session()
    db = _StatefulMockDB(session=session, meeting=meeting)
    with patch.object(recordings, "get_storage_client", return_value=MagicMock()), \
         patch.object(recordings.attributes, "flag_modified"):
        for kind in ("audio", "video"):
            args = _make_upload_call(kind)
            args["metadata"] = json.dumps({"start_time_utc": "2026-09-18T01:00:00Z"})
            await recordings.internal_upload_recording(db=db, **args)
        rec = meeting.data["recordings"][0]
        video = next(m for m in rec["media_files"] if m["type"] == "video")
        video.update(storage_path=f"{BASE}/video/master.av.webm", video_audio_mux={"version": 1},
                     source_video_path=f"{BASE}/video/000000.webm")
        args = _make_upload_call("video")
        args.update(metadata=None, chunk_seq=1, is_final=False)
        await recordings.internal_upload_recording(db=db, **args)
    saved = meeting.data["recordings"][0]["media_files"]
    assert all(m["start_time_utc"] == "2026-09-18T01:00:00Z" for m in saved)
    saved_video = next(m for m in saved if m["type"] == "video")
    assert saved_video["storage_path"].endswith("/master.av.webm")
    assert saved_video["source_video_path"].endswith("/000000.webm")
    assert saved_video["video_audio_mux"] == {"version": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", ["invalid", 123, {}])
async def test_upload_rejects_invalid_capture_timestamp(timestamp):
    args = _make_upload_call("audio")
    args["metadata"] = json.dumps({"start_time_utc": timestamp})
    with pytest.raises(HTTPException) as err:
        await recordings.internal_upload_recording(db=AsyncMock(), **args)
    assert err.value.status_code == 422


@pytest.mark.asyncio
async def test_sweep_retries_unmuxed_video_even_when_audio_is_playable():
    rec = {"id": 999, "session_uid": "sess-abc123", "source": "bot", "status": "completed",
           "playback_url": {"audio": "/recordings/999/master?type=audio"},
           "media_files": [{"type": "audio", "finalized_by": "recording_finalizer.master"},
                           {"type": "video", "finalized_by": "recording_finalizer.master"}]}
    meeting = make_meeting(data={"recordings": [rec]})
    db = AsyncMock()
    db.execute.side_effect = [FetchAllResult([(meeting.id,)]), MockResult([meeting]), MockResult([make_session()])]

    @asynccontextmanager
    async def factory():
        yield db

    with patch("meeting_api.recording_finalizer.finalize_recording_master", new=AsyncMock()) as finalize:
        assert await sweeps._sweep_unfinalized_recordings(factory) == 1
    finalize.assert_awaited_once_with(meeting.id, db)


def test_audio_only_and_lanes_do_not_request_video_mux():
    assert not needs_video_audio_mux({"media_files": [{"type": "audio"}, {"type": "lane-123"}]})
    assert not needs_video_audio_mux({"media_files": [{"type": "video"}]})


@pytest.mark.asyncio
async def test_requested_audio_must_arrive_before_video_is_published():
    meeting = make_meeting(data={
        "capture_modes": ["audio", "video"],
        "recordings": [{"id": 999, "media_files": [{"type": "video"}]}],
    })
    db = AsyncMock()
    db.execute.return_value = MockResult([meeting])
    with patch.object(fin, "create_storage_client", return_value=MagicMock()):
        with pytest.raises(ValueError, match="waiting for its mixed audio"):
            await fin.finalize_recording_master(meeting.id, db)
    db.commit.assert_not_awaited()
    assert "playback_url" not in meeting.data["recordings"][0]


@pytest.mark.parametrize("timestamp", ["2026-09-18T01:00:10Z", "2026-09-18T00:59:50Z"])
def test_nonoverlapping_capture_fails_instead_of_publishing_silence(media_pair, timestamp):
    storage, video, audio = media_pair
    audio["start_time_utc"] = timestamp
    with pytest.raises(ValueError, match="do not overlap"):
        mux_recording_video(storage, video, audio)
    assert not storage.file_exists(f"{BASE}/video/master.av.{video['format']}")


def test_audio_object_without_an_audio_stream_is_rejected(media_pair):
    storage, video, audio = media_pair
    storage.upload_file(audio["storage_path"], storage.download_file(video["storage_path"]))
    with pytest.raises(ValueError, match="stream is absent"):
        mux_recording_video(storage, video, audio)
    assert not storage.file_exists(f"{BASE}/video/master.av.{video['format']}")
