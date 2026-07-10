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
import logging
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import attributes

from .models import Meeting, SpeakerProfile, Voiceprint, VoiceprintAuditLog
from .storage import create_storage_client
from .voiceprint_crypto import get_voiceprint_crypto

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

# Same shape as collector/endpoints.py's _LANE_SUB_CLUSTER_RE — kept as an
# independent copy on purpose (Codex critique FC-4): the matching hook runs
# inside final_transcription.py's in-memory `segments`, not the read-time
# merge path, and must compute the needs_review-equivalent condition itself
# rather than reaching into collector/endpoints.py (different layer,
# different data shape) or leaving the condition implicit.
_LANE_SUB_CLUSTER_RE = re.compile(r"^lane:[^:]+:.+$")


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
    """Group segments by cluster for clusters in the needs_review-equivalent
    state: a lane sub-cluster ("lane:{key}:{sub}") with no confirmed speaker
    name. Mirrors collector/endpoints.py's `_derive_speaker_mapping_status`
    condition (see module docstring for why this is a deliberate duplicate,
    not a shared import)."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for seg in segments:
        cluster = seg.get("speaker_cluster")
        if not cluster or not _LANE_SUB_CLUSTER_RE.match(cluster):
            continue
        speaker = seg.get("speaker")
        if speaker and str(speaker).strip():
            continue
        grouped.setdefault(cluster, []).append(seg)
    return grouped


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


async def _download_master_to_tempfile(
    storage_backend: Optional[str], storage_path: str, media_format: str,
) -> str:
    storage = create_storage_client(storage_backend)
    suffix = f".{(media_format or 'webm').lower()}"
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        await asyncio.to_thread(storage.download_file_to_path, storage_path, path)
    except Exception:
        # BUG-007: mkstemp already created `path` on disk. If the download
        # itself raises (network/credential/missing-object error — all
        # realistic against production recordings storage), this function
        # never returns a path, so embed_clip_from_ranges's own
        # `finally: os.unlink(src_path)` never runs (it never gets a
        # src_path value) and the empty temp file leaks for the container's
        # lifetime. Unlink it here before re-raising.
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise
    return path


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


async def embed_clip_from_ranges(source: Any, ranges: List[Tuple[float, float]]) -> List[float]:
    """Slice, convert, and embed one cluster's audio. Shared by the
    post-commit matching follow-up below and the explicit
    `POST /voiceprints/enroll-from-cluster` endpoint (voiceprints.py) so both
    paths apply the identical min/max clip policy."""
    selected = _select_clip_ranges(
        ranges, min_seconds=VOICEPRINT_MIN_CLIP_SECONDS, max_seconds=VOICEPRINT_MAX_CLIP_SECONDS,
    )
    if not selected:
        raise InsufficientAudioError(
            f"cluster audio below the {VOICEPRINT_MIN_CLIP_SECONDS}s minimum for voiceprint matching"
        )
    if not VOICEPRINT_SERVICE_URL:
        raise VoiceprintServiceUnavailable("VOICEPRINT_SERVICE_URL not configured")

    src_path = await _download_master_to_tempfile(
        getattr(source, "storage_backend", None),
        source.storage_path,
        source.media_format,
    )
    try:
        clip_wav = await asyncio.to_thread(_extract_and_concat_clip, src_path, selected)
    finally:
        try:
            os.unlink(src_path)
        except FileNotFoundError:
            pass

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
    db: AsyncSession, meeting_id: int, suggestions: Dict[str, Any],
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
    """
    stmt = (
        select(Meeting)
        .where(Meeting.id == meeting_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    fresh = (await db.execute(stmt)).scalar_one()
    fresh_data = dict(fresh.data or {}) if isinstance(fresh.data, dict) else {}
    fresh_data["speaker_suggestions"] = suggestions
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
    suggestions: Dict[str, Any] = dict(data.get("speaker_suggestions") or {})

    if mode == "replace" and suggestions:
        # Stale-clear BEFORE writing this run's results, in its own commit
        # (plan §6): a crash mid-loop below then leaves "no suggestions"
        # rather than a suggestion from a prior, now-discarded run. This
        # runs even when the new run finds nothing to match (below) — an
        # old suggestion for a cluster that no longer needs review must not
        # survive the replace. Re-SELECT + merge (BUG-002) so this doesn't
        # clobber a concurrent PATCH made since `meeting` was first loaded.
        await _merge_speaker_suggestions_into_fresh_row(db, meeting_id, {})
        await db.commit()
        suggestions = {}

    # Nothing to match — return WITHOUT any audit noise. This is the common
    # case (most meetings have no lane shared-mic sub-clusters at all), so
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
            suggestions[cluster_id] = {
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
        # Re-SELECT + merge only the speaker_suggestions key (BUG-002) —
        # `meeting.data` here would be whatever this long-held object last
        # saw, possibly minutes stale relative to a concurrent PATCH.
        await _merge_speaker_suggestions_into_fresh_row(db, meeting_id, suggestions)

    await db.commit()
