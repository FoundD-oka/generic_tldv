"""Build a downloadable video with the separately persisted mixed audio."""

from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import subprocess
import tempfile

logger = logging.getLogger(__name__)


def needs_video_audio_mux(recording: dict) -> bool:
    media = recording.get("media_files") or []
    if not any(m.get("type") == "audio" for m in media if isinstance(m, dict)):
        return False
    return any(
        m.get("type") == "video" and not m.get("video_audio_mux")
        for m in media if isinstance(m, dict)
    )


def _audio_delay_seconds(video: dict, audio: dict) -> float:
    def parse(value: str) -> datetime:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)

    video_start, audio_start = video.get("start_time_utc"), audio.get("start_time_utc")
    if not video_start or not audio_start:
        # Legacy recordings did not persist capture timestamps. Do not infer
        # them from upload time (video arrives only after the meeting ends).
        logger.warning("[VIDEO MUX] Capture timestamps unavailable; aligning legacy media at zero")
        return 0.0
    return (parse(audio_start) - parse(video_start)).total_seconds()


def _media_duration(path: str, stream: str) -> float:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", stream,
        "-show_entries", "stream=duration:stream_tags=DURATION:format=duration",
        "-of", "json", path,
    ], check=True, capture_output=True, timeout=60)
    metadata = json.loads(result.stdout)
    streams = metadata.get("streams") or []
    if not streams:
        raise ValueError(f"Required {stream} stream is absent")
    value = streams[0].get("duration")
    if not value or value == "N/A":
        value = (streams[0].get("tags") or {}).get("DURATION")
    if not value or value == "N/A":
        value = (metadata.get("format") or {}).get("duration")
    # WebM commonly stores HH:MM:SS.fraction in the stream DURATION tag.
    duration = 0.0
    for part in str(value).split(":"):
        duration = duration * 60 + float(part)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Invalid {stream} duration: {value!r}")
    return duration


def mux_recording_video(storage, video: dict, audio: dict) -> dict:
    """Return updated metadata only after both streams have been validated.

    Originals stay intact. All large objects travel through files, and all
    failures propagate so the finalizer/sweep can retry without publishing a
    silent video as a successful audio/video master.
    """
    fmt = str(video.get("format") or "").lower()
    if fmt not in {"webm", "mp4", "mkv"}:
        raise ValueError(f"Unsupported video format: {fmt!r}")
    if audio.get("finalized_by") != "recording_finalizer.master":
        raise ValueError("Mixed audio master is not ready for video mux")
    source = video.get("source_video_path") or video["storage_path"]
    audio_source = audio["storage_path"]
    marker = {
        "version": 1,
        "audio_storage_path": audio_source,
        "audio_start_time_utc": audio.get("start_time_utc"),
        "video_start_time_utc": video.get("start_time_utc"),
    }
    output_key = source.rsplit("/", 1)[0] + f"/master.av.{fmt}"
    if video.get("video_audio_mux") == marker and storage.file_exists(output_key):
        return video

    delay = _audio_delay_seconds(video, audio)
    filters = ["asetpts=PTS-STARTPTS"]
    if delay < 0:
        filters = [f"atrim=start={-delay:.6f}", "asetpts=PTS-STARTPTS"]
    elif delay > 0:
        filters.append(f"adelay={round(delay * 1000)}:all=1")
    content_type = {"webm": "video/webm", "mp4": "video/mp4", "mkv": "video/x-matroska"}[fmt]
    with tempfile.TemporaryDirectory(prefix="vexa-video-mux-") as directory:
        video_path = str(Path(directory) / f"video.{fmt}")
        audio_path = str(Path(directory) / "audio")
        output_path = str(Path(directory) / f"output.{fmt}")
        storage.download_file_to_path(source, video_path)
        storage.download_file_to_path(audio_source, audio_path)
        duration = _media_duration(video_path, "v:0")
        audio_duration = _media_duration(audio_path, "a:0")
        if delay >= duration or audio_duration + delay <= 0:
            raise ValueError("Audio and video capture timelines do not overlap")
        # Bound padding explicitly: -shortest with stream-copy can run forever
        # on some ffmpeg versions. Media duration is authoritative, not the
        # upload's elapsed wall time (which includes shutdown/network delays).
        filters.append(f"apad=whole_dur={duration:.6f}")
        args = [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-i", video_path, "-i", audio_path,
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            "-af", ",".join(filters),
            "-c:a", "libopus" if fmt == "webm" else "aac",
            "-t", f"{duration:.6f}",
            *(["-movflags", "+faststart"] if fmt == "mp4" else []),
            output_path,
        ]
        subprocess.run(args, check=True, capture_output=True, timeout=1800)
        size = Path(output_path).stat().st_size
        if size == 0:
            raise ValueError("Video mux produced an empty file")
        # Required maps fail if either output stream is absent or undecodable.
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-i", output_path,
            "-map", "0:v:0", "-map", "0:a:0", "-t", "0.1", "-f", "null", "-",
        ], check=True, capture_output=True, timeout=60)
        storage.upload_file_path(output_key, output_path, content_type=content_type)

    return {
        **video,
        "source_video_path": source,
        "storage_path": output_key,
        "video_audio_mux": marker,
        "file_size_bytes": size,
        "duration_seconds": duration,
        "content_type": content_type,
        "finalized_by": "recording_finalizer.master",
        "finalized_at": datetime.now(timezone.utc).isoformat(),
        "is_final": True,
    }
