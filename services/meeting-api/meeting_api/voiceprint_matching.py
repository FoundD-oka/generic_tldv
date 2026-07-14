"""Voiceprint matching (issue #27 Phase 4): cluster audio slicing + speaker
embedding lookup for auto-naming suggestions.

`run_voiceprint_matching_followup` is invoked by `final_transcription.py` as
a POST-COMMIT follow-up step to `run_deferred_transcription` — it must NEVER
raise, and a failure/timeout here must NEVER change the transcript's
success/failure state (plan §6, Codex critique FC-4/5/20). Every exit path
either writes a `speaker_suggestions` entry or a `skip` audit event; an
embedding for an unmatched/below-threshold cluster is discarded, never
persisted (PII policy §2 OPEN DECISION B, 案A).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import wave
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import attributes

from .models import Meeting, SpeakerProfile, Voiceprint, VoiceprintAuditLog
from .voiceprint_crypto import get_voiceprint_crypto
from .voiceprint_master_download_worker import (
    STATUS_DOWNLOAD_LENGTH,
    STATUS_EMPTY,
    STATUS_INVALID_CHUNK,
    STATUS_INVALID_REQUEST,
    STATUS_INVALID_SIZE,
    STATUS_OK,
    STATUS_RANGE_LENGTH,
    STATUS_STORAGE_ERROR,
    STATUS_TOO_LARGE,
)

logger = logging.getLogger("meeting_api.voiceprint_matching")

VOICEPRINT_SERVICE_URL = os.getenv("VOICEPRINT_SERVICE_URL", "").strip()
VOICEPRINT_SERVICE_TOKEN = os.getenv("VOICEPRINT_SERVICE_TOKEN", "").strip()
# option-matrix proposed initial range 0.75-0.80; 0.78 sits inside that
# range (critique NH-5 — this is NOT "the upper end of community norms").
VOICEPRINT_SUGGEST_THRESHOLD = float(os.getenv("VOICEPRINT_SUGGEST_THRESHOLD", "0.78"))
VOICEPRINT_RETENTION_MONTHS = int(os.getenv("VOICEPRINT_RETENTION_MONTHS", "24"))
VOICEPRINT_MATCH_TOTAL_BUDGET_S = float(os.getenv("VOICEPRINT_MATCH_TOTAL_BUDGET_S", "120"))
VOICEPRINT_EMBED_TIMEOUT_S = float(os.getenv("VOICEPRINT_EMBED_TIMEOUT_S", "15"))

VOICEPRINT_MIN_CLIP_SECONDS = float(os.getenv("VOICEPRINT_MIN_CLIP_SECONDS", "5"))
VOICEPRINT_MAX_CLIP_SECONDS = float(os.getenv("VOICEPRINT_MAX_CLIP_SECONDS", "30"))
VOICEPRINT_FFMPEG_TIMEOUT_SECONDS = float(os.getenv("VOICEPRINT_FFMPEG_TIMEOUT_SECONDS", "60"))
VOICEPRINT_MAX_DIRECT_AUDIO_BYTES = int(
    os.getenv("VOICEPRINT_MAX_DIRECT_AUDIO_BYTES", str(20 * 1024 * 1024))
)
VOICEPRINT_MAX_MASTER_BYTES = int(
    os.getenv("VOICEPRINT_MAX_MASTER_BYTES", str(400 * 1024 * 1024))
)
VOICEPRINT_MASTER_DOWNLOAD_CHUNK_BYTES = int(
    os.getenv("VOICEPRINT_MASTER_DOWNLOAD_CHUNK_BYTES", str(4 * 1024 * 1024))
)
VOICEPRINT_MASTER_DOWNLOAD_TIMEOUT_S = float(
    os.getenv("VOICEPRINT_MASTER_DOWNLOAD_TIMEOUT_S", "90")
)
VOICEPRINT_MAX_ACTIVE_MASTER_PIPELINES = max(
    1, int(os.getenv("VOICEPRINT_MAX_ACTIVE_MASTER_PIPELINES", "1"))
)
VOICEPRINT_MAX_ACTIVE_DIRECT_PIPELINES = max(
    1, int(os.getenv("VOICEPRINT_MAX_ACTIVE_DIRECT_PIPELINES", "1"))
)

# Master files can be hundreds of MiB and ffmpeg is CPU intensive.  Keep a
# dedicated gate around the complete download -> ffmpeg lifecycle so explicit
# previews and automatic matching cannot multiply both costs independently.
_VOICEPRINT_MASTER_PIPELINE_SEMAPHORE = asyncio.Semaphore(
    VOICEPRINT_MAX_ACTIVE_MASTER_PIPELINES
)
_VOICEPRINT_DIRECT_PIPELINE_SEMAPHORE = asyncio.Semaphore(
    VOICEPRINT_MAX_ACTIVE_DIRECT_PIPELINES
)
_VOICEPRINT_MASTER_CONTROL_MAX_BYTES = 64 * 1024

# Same shape as collector/endpoints.py's _LANE_SUB_CLUSTER_RE — kept as an
# independent copy on purpose (Codex critique FC-4): the matching hook runs
# inside final_transcription.py's in-memory `segments`, not the read-time
# merge path, and must compute the needs_review-equivalent condition itself
# rather than reaching into collector/endpoints.py (different layer,
# different data shape) or leaving the condition implicit.
_LANE_SUB_CLUSTER_RE = re.compile(r"^lane:[^:]+:.+$")
# Gemini adapterの会議ごと匿名話者ID。providerが返した名前は保存せず、
# `gemini_adapter.normalize_response` がこの形へ再マップする。
_GEMINI_CLUSTER_RE = re.compile(r"^g:[0-9a-f]{8}:s[1-9][0-9]*$")


def _is_unconfirmed_speaker(speaker: Any) -> bool:
    """空値とGemini/DOMの既定値`Unknown`を未確定名として扱う。"""
    if speaker is None:
        return True
    normalized = str(speaker).strip()
    return not normalized or normalized.casefold() == "unknown"


class VoiceprintServiceUnavailable(Exception):
    """The voiceprint-service /embed call could not be completed."""


class InsufficientAudioError(Exception):
    """The cluster's available speech is below VOICEPRINT_MIN_CLIP_SECONDS."""


# ---------------------------------------------------------------------------
# Cluster -> audio source resolution (lane/mixed branch, ARC-3 in the plan)
# ---------------------------------------------------------------------------


def resolve_cluster_audio_source(
    cluster_id: str,
    *,
    mixed_source: Optional[Any],
    lane_sources: List[Any],
) -> Optional[Any]:
    """Pick the audio source (mixed master or a specific lane master) that
    `cluster_id`'s segments must be sliced from.

    - mixed cluster (no "lane:" prefix): the mixed recording master.
    - lane cluster ("lane:{key}" or "lane:{key}:{sub}"): the matching lane's
      own master, found by lane_key.
    """
    if cluster_id.startswith("lane:"):
        lane_key = cluster_id.split(":")[1]
        for lane in lane_sources:
            if getattr(lane, "lane_key", None) == lane_key:
                return lane
        return None
    return mixed_source


def cluster_local_time_ranges(
    cluster_id: str,
    segments: List[Dict[str, Any]],
    *,
    offset_seconds: float = 0.0,
) -> List[Tuple[float, float]]:
    """Return this cluster's segment (start, end) ranges in ITS OWN audio
    source's local time base.

    Segments carry MIXED-timeline start/end (final_transcription.py's
    `_shift_segment_times` already shifted lane segments there). A lane
    master file itself is on the lane's OWN local timeline, so recovering
    lane-local time means subtracting the lane's `start_offset_seconds`
    again (Codex critique FC-6). `offset_seconds=0.0` (the mixed-cluster
    case) is a no-op passthrough.
    """
    ranges: List[Tuple[float, float]] = []
    for seg in segments:
        if seg.get("speaker_cluster") != cluster_id:
            continue
        try:
            start = float(seg.get("start", 0)) - offset_seconds
            end = float(seg.get("end", 0)) - offset_seconds
        except (TypeError, ValueError):
            continue
        if end > start:
            ranges.append((max(0.0, start), max(0.0, end)))
    return ranges


def _needs_review_clusters(segments: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group clusters that still need a human-confirmed speaker name.

    Lane shared-mic sub-clusters and Gemini's meeting-scoped anonymous
    ``g:<salt>:sN`` clusters are eligible. Boundary-ambiguous ``x:<salt>:sN``
    clusters are deliberately excluded: their widened overlap envelope may
    contain both sides of a chunk boundary and is not safe voiceprint input.
    If even one segment in a cluster already has a confirmed name, the whole
    cluster is excluded so a partial or concurrent rename can never be
    overwritten by a new suggestion.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for seg in segments:
        cluster = seg.get("speaker_cluster")
        if not cluster or not (
            _LANE_SUB_CLUSTER_RE.match(cluster) or _GEMINI_CLUSTER_RE.match(cluster)
        ):
            continue
        grouped.setdefault(cluster, []).append(seg)
    return {
        cluster: cluster_segments
        for cluster, cluster_segments in grouped.items()
        if all(_is_unconfirmed_speaker(seg.get("speaker")) for seg in cluster_segments)
    }


# ---------------------------------------------------------------------------
# Clip selection + ffmpeg extraction
# ---------------------------------------------------------------------------


def _select_clip_ranges(
    ranges: List[Tuple[float, float]],
    *,
    min_seconds: float,
    max_seconds: float,
) -> Optional[List[Tuple[float, float]]]:
    """Pick the cluster's LONGEST segments (by duration) up to max_seconds
    total speech. Returns None when the total available speech across all
    segments is below min_seconds (plan §2: skip, leave "needs review")."""
    total_available = sum(max(0.0, end - start) for start, end in ranges)
    if total_available < min_seconds:
        return None

    by_duration = sorted(ranges, key=lambda r: (r[1] - r[0]), reverse=True)
    selected: List[Tuple[float, float]] = []
    accumulated = 0.0
    for start, end in by_duration:
        if accumulated >= max_seconds:
            break
        duration = end - start
        if accumulated + duration > max_seconds:
            end = start + (max_seconds - accumulated)
            duration = end - start
        if duration <= 0:
            continue
        selected.append((start, end))
        accumulated += duration
    if not selected:
        return None
    # Chronological order — a concat of out-of-order clips still decodes
    # fine, but keeping natural order makes debugging clips less confusing.
    selected.sort(key=lambda r: r[0])
    return selected


def _extract_and_concat_clip(src_path: str, ranges: List[Tuple[float, float]]) -> bytes:
    """ffmpeg-extract the given ranges from src_path and concat into one
    16kHz mono WAV. A separate implementation from final_transcription.py's
    `_convert_audio_to_wav` (single-file passthrough) because voiceprint
    slicing needs multi-range extraction + concat in one pass."""
    filter_parts = []
    concat_inputs = []
    for i, (start, end) in enumerate(ranges):
        duration = max(0.01, end - start)
        filter_parts.append(
            f"[0:a]atrim=start={start:.3f}:duration={duration:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
        concat_inputs.append(f"[a{i}]")
    filter_complex = (
        ";".join(filter_parts)
        + ";"
        + "".join(concat_inputs)
        + f"concat=n={len(ranges)}:v=0:a=1[out]"
    )

    dst_path = None
    try:
        fd, dst_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        result = subprocess.run(
            [
                "ffmpeg", "-i", src_path,
                "-filter_complex", filter_complex,
                "-map", "[out]",
                "-ar", "16000", "-ac", "1", "-f", "wav",
                dst_path, "-y",
            ],
            capture_output=True,
            timeout=VOICEPRINT_FFMPEG_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg clip extraction failed: {result.stderr.decode(errors='ignore')[:500]}"
            )
        with open(dst_path, "rb") as f:
            return f.read()
    finally:
        if dst_path:
            try:
                os.unlink(dst_path)
            except FileNotFoundError:
                pass


def _extract_exact_clip(src_path: str, ranges: List[Tuple[float, float]]) -> bytes:
    """Extract every supplied range without the cluster path's longest-first cap.

    Callers must validate range count, ordering, overlap, and total duration
    before reaching this helper.  Bit-exact ffmpeg flags keep the WAV bytes
    deterministic so a human-reviewed preview can be hash-bound to a later
    enrollment request without retaining a second raw-audio copy.
    """
    filter_parts = []
    concat_inputs = []
    for i, (start, end) in enumerate(ranges):
        duration = end - start
        filter_parts.append(
            f"[0:a]atrim=start={start:.6f}:duration={duration:.6f},"
            f"asetpts=PTS-STARTPTS[a{i}]"
        )
        concat_inputs.append(f"[a{i}]")
    filter_complex = (
        ";".join(filter_parts)
        + ";"
        + "".join(concat_inputs)
        + f"concat=n={len(ranges)}:v=0:a=1[out]"
    )

    dst_path = None
    try:
        fd, dst_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        result = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-i", src_path,
                "-filter_complex", filter_complex,
                "-map", "[out]",
                "-map_metadata", "-1",
                "-fflags", "+bitexact", "-flags:a", "+bitexact",
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                "-f", "wav", dst_path, "-y",
            ],
            capture_output=True,
            timeout=VOICEPRINT_FFMPEG_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg exact clip extraction failed: "
                f"{result.stderr.decode(errors='ignore')[:500]}"
            )
        with open(dst_path, "rb") as f:
            return f.read()
    finally:
        if dst_path:
            try:
                os.unlink(dst_path)
            except FileNotFoundError:
                pass


def _normalize_audio_to_wav(audio_bytes: bytes, media_format: str) -> bytes:
    """Normalize a user-reviewed recording to deterministic 16kHz mono WAV."""
    src_path = None
    dst_path = None
    try:
        src_fd, src_path = tempfile.mkstemp(suffix=f".{media_format}")
        os.close(src_fd)
        with open(src_path, "wb") as src_file:
            src_file.write(audio_bytes)

        dst_fd, dst_path = tempfile.mkstemp(suffix=".wav")
        os.close(dst_fd)
        result = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-i", src_path,
                "-vn", "-map_metadata", "-1",
                "-fflags", "+bitexact", "-flags:a", "+bitexact",
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                "-t", f"{VOICEPRINT_MAX_CLIP_SECONDS + 0.25:g}",
                "-f", "wav", dst_path, "-y",
            ],
            capture_output=True,
            timeout=VOICEPRINT_FFMPEG_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise ValueError(
                f"audio conversion failed: {result.stderr.decode(errors='ignore')[:500]}"
            )
        with open(dst_path, "rb") as dst_file:
            return dst_file.read()
    finally:
        for path in (src_path, dst_path):
            if path:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass


async def _download_master_to_tempfile(
    storage_backend: Optional[str], storage_path: str, media_format: str,
) -> str:
    """Download a finalized master in a killable, deadline-bound process.

    Storage SDK calls are synchronous and a blocked range request cannot be
    interrupted inside a Python thread.  The stat and all byte-range body I/O
    therefore run in a dedicated interpreter process.  Timeout/cancellation
    terminates (then kills if needed) and reaps that process *before* the temp
    path is unlinked or this coroutine returns, so no storage worker can keep
    writing after request cleanup.
    """
    if (
        isinstance(VOICEPRINT_MAX_MASTER_BYTES, bool)
        or not isinstance(VOICEPRINT_MAX_MASTER_BYTES, int)
        or VOICEPRINT_MAX_MASTER_BYTES <= 0
    ):
        raise ValueError("voiceprint master size limit must be positive")
    if (
        isinstance(VOICEPRINT_MASTER_DOWNLOAD_CHUNK_BYTES, bool)
        or not isinstance(VOICEPRINT_MASTER_DOWNLOAD_CHUNK_BYTES, int)
        or VOICEPRINT_MASTER_DOWNLOAD_CHUNK_BYTES <= 0
    ):
        raise ValueError("voiceprint master download chunk size must be positive")
    if (
        not math.isfinite(VOICEPRINT_MASTER_DOWNLOAD_TIMEOUT_S)
        or VOICEPRINT_MASTER_DOWNLOAD_TIMEOUT_S <= 0
    ):
        raise ValueError("voiceprint master download timeout must be positive")

    normalized_format = (media_format or "webm").strip().lower()
    suffix = f".{normalized_format}" if re.fullmatch(r"[a-z0-9]{1,12}", normalized_format) else ".media"
    fd, path = tempfile.mkstemp(suffix=suffix)
    process = None
    try:
        try:
            process = _start_master_download_process(
                storage_backend,
                storage_path,
                fd,
                max_bytes=VOICEPRINT_MAX_MASTER_BYTES,
                chunk_bytes=VOICEPRINT_MASTER_DOWNLOAD_CHUNK_BYTES,
            )
        except BaseException:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            raise
    finally:
        os.close(fd)

    try:
        await _wait_for_master_download_process(
            process, timeout=VOICEPRINT_MASTER_DOWNLOAD_TIMEOUT_S,
        )
        status = _read_master_download_status(process)
        _raise_for_master_download_status(status, process.returncode)
        return path
    except BaseException:
        # This branch includes asyncio.CancelledError.  Reaping is synchronous
        # on purpose: once cancellation is visible to the caller, neither a
        # child process nor an SDK I/O operation may remain alive.
        _stop_master_download_process(process)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise
    finally:
        _close_master_download_process_streams(process)


def _start_master_download_process(
    storage_backend: Optional[str],
    storage_path: str,
    output_fd: int,
    *,
    max_bytes: int,
    chunk_bytes: int,
) -> subprocess.Popen:
    """Start the isolated worker without putting object paths in argv."""
    try:
        request = json.dumps(
            {
                "storage_backend": storage_backend,
                "storage_path": storage_path,
                "output_fd": output_fd,
                "max_bytes": max_bytes,
                "chunk_bytes": chunk_bytes,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("voiceprint master download configuration is invalid") from exc
    if len(request) > _VOICEPRINT_MASTER_CONTROL_MAX_BYTES:
        raise ValueError("voiceprint master download configuration is invalid")
    process = subprocess.Popen(
        [sys.executable, "-m", "meeting_api.voiceprint_master_download_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        pass_fds=(output_fd,),
        start_new_session=True,
    )
    try:
        if process.stdin is None:
            raise RuntimeError("voiceprint master worker control pipe unavailable")
        process.stdin.write(request)
        process.stdin.close()
    except BaseException as exc:
        _stop_master_download_process(process)
        _close_master_download_process_streams(process)
        raise RuntimeError("voiceprint master worker could not be started") from exc
    return process


async def _wait_for_master_download_process(
    process: subprocess.Popen, *, timeout: float,
) -> None:
    """Wait asynchronously for a worker while enforcing a hard deadline."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while process.poll() is None:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError("voiceprint master download timed out")
        await asyncio.sleep(min(0.02, remaining))


def _stop_master_download_process(process: subprocess.Popen) -> None:
    """Terminate/kill and reap a worker before returning to its caller."""
    if process.poll() is not None:
        process.wait()
        return
    try:
        process.terminate()
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        # A storage SDK network read is interruptible by SIGKILL.  Reap the
        # process so the caller never observes a zombie or live I/O worker.
        process.wait()


def _read_master_download_status(process: subprocess.Popen) -> str:
    if process.stdout is None:
        return ""
    raw = process.stdout.read(64)
    if not isinstance(raw, bytes):
        return ""
    try:
        return raw.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError:
        return ""


def _raise_for_master_download_status(status: str, returncode: Optional[int]) -> None:
    """Map fixed worker codes to sanitized parent-side exceptions."""
    if status == STATUS_OK and returncode == 0:
        return
    if status == STATUS_INVALID_SIZE:
        raise ValueError("voiceprint master size is invalid")
    if status == STATUS_EMPTY:
        raise ValueError("voiceprint master is empty")
    if status == STATUS_TOO_LARGE:
        raise ValueError(
            f"voiceprint master exceeds the {VOICEPRINT_MAX_MASTER_BYTES} byte limit"
        )
    if status in {STATUS_INVALID_REQUEST, STATUS_INVALID_CHUNK}:
        raise ValueError("voiceprint master download configuration is invalid")
    if status == STATUS_RANGE_LENGTH:
        raise IOError("voiceprint master range length mismatch")
    if status == STATUS_DOWNLOAD_LENGTH:
        raise IOError("voiceprint master download length mismatch")
    if status == STATUS_STORAGE_ERROR:
        raise RuntimeError("voiceprint master storage download failed")
    raise RuntimeError("voiceprint master download worker failed")


def _close_master_download_process_streams(process: subprocess.Popen) -> None:
    for stream in (process.stdin, process.stdout):
        if stream is not None and not stream.closed:
            stream.close()


async def _run_blocking_audio_operation(operation: Any, *args: Any) -> bytes:
    """Run bounded ffmpeg work without releasing its gate prematurely."""
    current = asyncio.current_task()
    if current is not None and current.cancelling():
        raise asyncio.CancelledError
    worker = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # subprocess.run has its own bounded timeout but cannot be interrupted
        # safely from this thread.  Keep the source path and semaphore alive
        # until it exits instead of unlinking under an active ffmpeg process.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if worker.done():
            try:
                worker.result()
            except BaseException:
                pass
        raise


async def _run_blocking_clip_extractor(
    extractor: Any, src_path: str, ranges: List[Tuple[float, float]],
) -> bytes:
    return await _run_blocking_audio_operation(extractor, src_path, ranges)


async def _extract_clip_from_master(
    source: Any,
    ranges: List[Tuple[float, float]],
    extractor: Any,
) -> bytes:
    """Serialize the bounded master download and its ffmpeg lifecycle."""
    async with _VOICEPRINT_MASTER_PIPELINE_SEMAPHORE:
        src_path = await _download_master_to_tempfile(
            getattr(source, "storage_backend", None),
            source.storage_path,
            source.media_format,
        )
        try:
            return await _run_blocking_clip_extractor(extractor, src_path, ranges)
        finally:
            try:
                os.unlink(src_path)
            except FileNotFoundError:
                pass


async def extract_exact_clip_wav(
    source: Any, ranges: List[Tuple[float, float]],
) -> bytes:
    """Download a finalized master and return exactly the requested ranges.

    Unlike :func:`embed_clip_from_ranges`, this helper never chooses, reorders,
    or truncates ranges.  It is reserved for explicit human-reviewed audio
    selection; validation belongs at the API boundary before this call.
    """
    if not ranges:
        raise ValueError("at least one audio range is required")
    return await _extract_clip_from_master(source, ranges, _extract_exact_clip)


async def normalize_direct_audio_to_wav(audio_bytes: bytes, media_format: str) -> bytes:
    """Normalize a bounded direct recording without retaining the raw input."""
    if not audio_bytes:
        raise ValueError("audio is empty")
    if len(audio_bytes) > VOICEPRINT_MAX_DIRECT_AUDIO_BYTES:
        raise ValueError(
            f"audio exceeds the {VOICEPRINT_MAX_DIRECT_AUDIO_BYTES} byte limit"
        )
    async with _VOICEPRINT_DIRECT_PIPELINE_SEMAPHORE:
        return await _run_blocking_audio_operation(
            _normalize_audio_to_wav,
            audio_bytes,
            media_format,
        )


def wav_duration_seconds(wav_bytes: bytes) -> float:
    """Return WAV duration while also checking the normalized wire format."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            frames = wav_file.getnframes()
    except (EOFError, wave.Error) as exc:
        raise ValueError("invalid WAV audio") from exc
    if frame_rate != 16000 or channels != 1 or sample_width != 2:
        raise ValueError("voiceprint WAV must be 16kHz mono PCM16")
    if frames <= 0:
        raise ValueError("audio is empty")
    return frames / float(frame_rate)


async def _embed_clip(wav_bytes: bytes) -> List[float]:
    headers = {}
    if VOICEPRINT_SERVICE_TOKEN:
        headers["Authorization"] = f"Bearer {VOICEPRINT_SERVICE_TOKEN}"
    payload = {"audio_base64": base64.b64encode(wav_bytes).decode("ascii")}
    async with httpx.AsyncClient(timeout=VOICEPRINT_EMBED_TIMEOUT_S) as client:
        response = await client.post(
            f"{VOICEPRINT_SERVICE_URL.rstrip('/')}/embed",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
    embedding = data.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise VoiceprintServiceUnavailable("voiceprint-service /embed response missing 'embedding'")
    return [float(x) for x in embedding]


async def embed_wav_bytes(wav_bytes: bytes) -> List[float]:
    """Embed an already-normalized WAV for explicit enrollment."""
    if not VOICEPRINT_SERVICE_URL:
        raise VoiceprintServiceUnavailable("VOICEPRINT_SERVICE_URL not configured")
    try:
        return await _embed_clip(wav_bytes)
    except httpx.HTTPError as exc:
        raise VoiceprintServiceUnavailable(
            f"voiceprint-service /embed failed: {exc}"
        ) from exc


async def embed_clip_from_ranges(source: Any, ranges: List[Tuple[float, float]]) -> List[float]:
    """Slice, convert, and embed one anonymous cluster for suggestion matching.

    Explicit enrollment uses only the separately reviewed selected-audio or
    pre-recorded paths; Vexa/Gemini cluster membership is never an enrollment
    source.
    """
    selected = _select_clip_ranges(
        ranges, min_seconds=VOICEPRINT_MIN_CLIP_SECONDS, max_seconds=VOICEPRINT_MAX_CLIP_SECONDS,
    )
    if not selected:
        raise InsufficientAudioError(
            f"cluster audio below the {VOICEPRINT_MIN_CLIP_SECONDS}s minimum for voiceprint matching"
        )
    if not VOICEPRINT_SERVICE_URL:
        raise VoiceprintServiceUnavailable("VOICEPRINT_SERVICE_URL not configured")

    clip_wav = await _extract_clip_from_master(
        source, selected, _extract_and_concat_clip,
    )

    try:
        return await _embed_clip(clip_wav)
    except httpx.HTTPError as exc:
        raise VoiceprintServiceUnavailable(f"voiceprint-service /embed failed: {exc}") from exc


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    import numpy as np

    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


async def _load_user_voiceprints(
    db: AsyncSession, user_id: int, crypto,
) -> List[Tuple[int, str, List[float]]]:
    """Return (profile_id, display_name, embedding) for every decryptable
    voiceprint belonging to user_id. A row that fails to decrypt (corrupt,
    or encrypted under a rotated-out key) is skipped, never raised — one bad
    row must not abort matching for every cluster in the meeting."""
    rows = (await db.execute(
        select(Voiceprint, SpeakerProfile.display_name)
        .join(SpeakerProfile, SpeakerProfile.id == Voiceprint.profile_id)
        .where(Voiceprint.user_id == user_id)
    )).all()
    out: List[Tuple[int, str, List[float]]] = []
    for vp, display_name in rows:
        try:
            embedding = crypto.decrypt_embedding(vp.embedding_encrypted, dim=vp.embedding_dim)
        except Exception:
            logger.warning("voiceprint %s failed to decrypt — skipping", vp.id)
            continue
        out.append((vp.profile_id, display_name, embedding))
    return out


# ---------------------------------------------------------------------------
# Post-commit follow-up entry point
# ---------------------------------------------------------------------------


async def run_voiceprint_matching_followup(
    meeting: Meeting,
    db: AsyncSession,
    *,
    segments: List[Dict[str, Any]],
    mixed_source: Optional[Any],
    lane_sources: List[Any],
    mode: str,
) -> None:
    """Post-commit follow-up for run_deferred_transcription.

    MUST NEVER raise. Every failure path (missing key, service down, ffmpeg
    error, budget exceeded) degrades to a `skip` audit event; the caller's
    transcript success/failure state is never touched (plan §6, critique
    FC-4/5/20).
    """
    meeting_id = meeting.id
    user_id = meeting.user_id
    try:
        await asyncio.wait_for(
            _run_matching(
                meeting, db,
                segments=segments, mixed_source=mixed_source,
                lane_sources=lane_sources, mode=mode,
            ),
            timeout=VOICEPRINT_MATCH_TOTAL_BUDGET_S,
        )
    except asyncio.TimeoutError:
        logger.warning("voiceprint matching budget exceeded for meeting %s", meeting_id)
        await _record_skip(db, user_id=user_id, meeting_id=meeting_id, reason="budget_exceeded")
    except Exception as exc:
        logger.warning(
            "voiceprint matching failed for meeting %s: %s", meeting_id, str(exc)[:200],
        )
        await _record_skip(db, user_id=user_id, meeting_id=meeting_id, reason="matching_error")


async def _record_skip(db: AsyncSession, *, user_id: int, meeting_id: int, reason: str) -> None:
    try:
        await db.rollback()
        db.add(VoiceprintAuditLog(
            user_id=user_id, event="skip", meeting_id=meeting_id,
            detail={"reason": reason},
        ))
        await db.commit()
    except Exception:
        logger.exception(
            "failed to record voiceprint skip audit (reason=%s) for meeting %s",
            reason, meeting_id,
        )


async def _merge_speaker_suggestions_into_fresh_row(
    db: AsyncSession, meeting_id: int, new_entries: Dict[str, Any], *, replace: bool = False,
) -> None:
    """Re-SELECT the meeting row with a row lock IMMEDIATELY before writing
    speaker_suggestions, and merge ONLY that key into the freshly-read data
    dict — never write back a dict captured earlier in `_run_matching`.

    `_run_matching` can hold the same `meeting` ORM object / db session for
    up to VOICEPRINT_MATCH_TOTAL_BUDGET_S (default 120s) of network + ffmpeg work.
    database.py's ``expire_on_commit=False`` means ``meeting.data`` is never
    refreshed after a commit, and because the session already has this row
    in its identity map, an ordinary re-SELECT would return the SAME
    (still-stale) Python object without touching already-loaded columns —
    hence ``populate_existing=True`` to force the freshly queried row's
    values onto it. Without this, a concurrent PATCH (rename, suggestion
    accept/reject) committed by a different session while this run was in
    flight would be silently discarded by a full-dict overwrite (BUG-002).

    BUG-002 follow-up (Fable F1): even with the fresh re-SELECT above, the
    original fix still wrote the ENTIRE ``speaker_suggestions`` dict from a
    value built at `_run_matching`'s run-start — a snapshot plus this run's
    new entries. A concurrent reject (DELETE) or confirm (rename, which also
    pops the entry) committed against a DIFFERENT cluster_id during the
    matching window (up to 120s) would land in the DB, only to be resurrected
    as "suggested" by this wholesale overwrite once the run finally
    committed. The fix is entry-level: unless ``replace`` is requested (the
    intentional mode="replace" stale-clear, which runs in its own commit
    BEFORE matching starts), the value written is the FRESH row's *current*
    speaker_suggestions (respecting anything popped/edited concurrently)
    merged with ONLY ``new_entries`` — the cluster_id -> suggestion entries
    this run itself produced. An entry that came from a stale snapshot but
    was never touched by this run is never rewritten.
    """
    stmt = (
        select(Meeting)
        .where(Meeting.id == meeting_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    fresh = (await db.execute(stmt)).scalar_one()
    fresh_data = dict(fresh.data or {}) if isinstance(fresh.data, dict) else {}
    if replace:
        merged = dict(new_entries)
    else:
        current = fresh_data.get("speaker_suggestions")
        current = dict(current) if isinstance(current, dict) else {}
        merged = {**current, **new_entries}
    fresh_data["speaker_suggestions"] = merged
    fresh.data = fresh_data
    attributes.flag_modified(fresh, "data")


async def _run_matching(
    meeting: Meeting,
    db: AsyncSession,
    *,
    segments: List[Dict[str, Any]],
    mixed_source: Optional[Any],
    lane_sources: List[Any],
    mode: str,
) -> None:
    meeting_id = meeting.id
    user_id = meeting.user_id

    data = dict(meeting.data or {}) if isinstance(meeting.data, dict) else {}
    stale_suggestions = data.get("speaker_suggestions") or {}
    # Entries this run itself produces (cluster_id -> suggestion). This is
    # deliberately NOT seeded from `stale_suggestions` above — that snapshot
    # is only used to decide (heuristically) whether a mode="replace" clear
    # is worth attempting below. Seeding the accumulator from it would
    # resurrect a concurrently-rejected/confirmed entry at the final
    # entry-level merge (Fable F1 / BUG-002 follow-up): see
    # `_merge_speaker_suggestions_into_fresh_row`.
    new_entries: Dict[str, Any] = {}

    if mode == "replace" and stale_suggestions:
        # Stale-clear BEFORE writing this run's results, in its own commit
        # (plan §6): a crash mid-loop below then leaves "no suggestions"
        # rather than a suggestion from a prior, now-discarded run. This
        # runs even when the new run finds nothing to match (below) — an
        # old suggestion for a cluster that no longer needs review must not
        # survive the replace. Re-SELECT under a fresh lock (BUG-002) so
        # this doesn't clobber a concurrent PATCH made since `meeting` was
        # first loaded — this is an intentional wholesale wipe (replace=True),
        # not the entry-level merge used by the final write below; any
        # concurrent reject/confirm landing AFTER this clear is still
        # respected because the final write re-reads the row fresh again.
        await _merge_speaker_suggestions_into_fresh_row(db, meeting_id, {}, replace=True)
        await db.commit()

    # Nothing to match — return WITHOUT any audit noise. This is the common
    # case (most meetings have no unnamed lane/Gemini clusters), so
    # checking crypto/service availability only after confirming there is
    # real work avoids writing a `skip` audit row for every single meeting.
    grouped = _needs_review_clusters(segments)
    if not grouped:
        return

    crypto = get_voiceprint_crypto()
    if not crypto.is_enabled():
        db.add(VoiceprintAuditLog(
            user_id=user_id, event="skip", meeting_id=meeting_id,
            detail={"reason": "encryption_disabled"},
        ))
        await db.commit()
        return

    if not VOICEPRINT_SERVICE_URL:
        db.add(VoiceprintAuditLog(
            user_id=user_id, event="skip", meeting_id=meeting_id,
            detail={"reason": "service_not_configured"},
        ))
        await db.commit()
        return

    voiceprints = await _load_user_voiceprints(db, user_id, crypto)
    if not voiceprints:
        db.add(VoiceprintAuditLog(
            user_id=user_id, event="skip", meeting_id=meeting_id,
            detail={"reason": "no_enrolled_voiceprints", "cluster_count": len(grouped)},
        ))
        await db.commit()
        return

    completed_at = datetime.utcnow().isoformat()
    changed = False

    for cluster_id, cluster_segments in grouped.items():
        source = resolve_cluster_audio_source(
            cluster_id, mixed_source=mixed_source, lane_sources=lane_sources,
        )
        if source is None:
            db.add(VoiceprintAuditLog(
                user_id=user_id, event="skip", meeting_id=meeting_id,
                detail={"reason": "no_audio_source", "cluster_id": cluster_id},
            ))
            continue

        offset = getattr(source, "start_offset_seconds", 0.0)
        ranges = cluster_local_time_ranges(cluster_id, cluster_segments, offset_seconds=offset)

        try:
            embedding = await embed_clip_from_ranges(source, ranges)
        except InsufficientAudioError:
            db.add(VoiceprintAuditLog(
                user_id=user_id, event="skip", meeting_id=meeting_id,
                detail={"reason": "insufficient_audio", "cluster_id": cluster_id},
            ))
            continue
        except Exception as exc:
            logger.warning(
                "voiceprint slice/embed failed for meeting %s cluster %s: %s",
                meeting_id, cluster_id, str(exc)[:200],
            )
            db.add(VoiceprintAuditLog(
                user_id=user_id, event="skip", meeting_id=meeting_id,
                detail={"reason": "embed_failed", "cluster_id": cluster_id},
            ))
            continue

        scored = [
            (profile_id, display_name, _cosine_similarity(embedding, vp_embedding))
            for profile_id, display_name, vp_embedding in voiceprints
        ]
        # BUG-011: NaN/inf similarity scores (a corrupted/degenerate stored
        # embedding, or a NaN slipping through _embed_clip's
        # `[float(x) for x in embedding]` on a malformed service response)
        # must never win max()'s left-to-right fold — NaN comparisons are
        # always False, so an order-dependent NaN entry could silently beat
        # a legitimately higher real score. Filter first; if EVERY score for
        # this cluster is non-finite, treat it as embed_failed (an audited
        # skip) rather than silently dropping the cluster or letting max()
        # raise ValueError on an empty sequence.
        finite_scored = [t for t in scored if math.isfinite(t[2])]
        if not finite_scored:
            logger.warning(
                "voiceprint similarity scoring produced only non-finite "
                "scores for meeting %s cluster %s", meeting_id, cluster_id,
            )
            db.add(VoiceprintAuditLog(
                user_id=user_id, event="skip", meeting_id=meeting_id,
                detail={"reason": "embed_failed", "cluster_id": cluster_id},
            ))
            continue
        best_profile_id, best_name, best_score = max(finite_scored, key=lambda t: t[2])
        clip_seconds = sum(end - start for start, end in ranges)

        # FMR/FRR research log: SCORES only, never the embedding vector
        # (PII policy §6, plan §2 AC — this is the 5/15/30s clip-length
        # comparison basis for a future auto-rollout decision).
        db.add(VoiceprintAuditLog(
            user_id=user_id, event="match_attempt", meeting_id=meeting_id,
            subject_profile_id=best_profile_id,
            detail={
                "cluster_id": cluster_id,
                "clip_seconds": round(clip_seconds, 2),
                "top_similarity": round(best_score, 4),
                "scores": [round(s, 4) for (_pid, _name, s) in scored],
                "threshold": VOICEPRINT_SUGGEST_THRESHOLD,
            },
        ))

        if best_score >= VOICEPRINT_SUGGEST_THRESHOLD:
            new_entries[cluster_id] = {
                "candidate_display_name": best_name,
                "profile_id": best_profile_id,
                "similarity": round(best_score, 4),
                "status": "suggested",
                "run_completed_at": completed_at,
            }
            changed = True
            db.add(VoiceprintAuditLog(
                user_id=user_id, event="suggest", meeting_id=meeting_id,
                subject_profile_id=best_profile_id,
                detail={"cluster_id": cluster_id, "similarity": round(best_score, 4)},
            ))
        # else: below threshold — the embedding is discarded here (goes out
        # of scope, never persisted). PII policy §2 OPEN DECISION B, 案A.

    if changed:
        # Re-SELECT + entry-level merge of ONLY this run's new_entries
        # (BUG-002 / Fable F1) — `meeting.data` here would be whatever this
        # long-held object last saw, possibly minutes stale relative to a
        # concurrent PATCH, and a wholesale key overwrite from a run-start
        # snapshot would resurrect anything popped (rejected/confirmed)
        # concurrently during the up-to-120s matching window.
        await _merge_speaker_suggestions_into_fresh_row(db, meeting_id, new_entries)

    await db.commit()
