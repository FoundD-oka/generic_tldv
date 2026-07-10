"""Soniox async STT adapter (stt.v1 contract, speaker-diarization extension).

Folds Soniox token-level async responses into OpenAI verbose_json-shaped
segments carrying an optional `speaker` field (anonymous acoustic cluster id,
"1","2",...). Backends without diarization simply omit `speaker`, so existing
start/end/text consumers are unaffected. Segments also carry an optional
`token_count` (number of tokens folded into the segment), used downstream as
a false-split guard signal; it is additive and never changes fold boundaries.

Contract: contracts/stt/v1 (see README "Speaker diarization extension").
Adapter manifest: .pipeline/adapters/soniox-stt.adapter.json
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

SONIOX_API_BASE = os.getenv("SONIOX_API_BASE", "https://api.soniox.com").rstrip("/")


def _api_key() -> str:
    return os.getenv("SONIOX_API_KEY", "").strip()
# Silence gap (seconds) that splits a same-speaker token run into two segments.
SONIOX_SEGMENT_MAX_GAP_S = float(os.getenv("SONIOX_SEGMENT_MAX_GAP_S", "1.0"))
SONIOX_POLL_INTERVAL_S = float(os.getenv("SONIOX_POLL_INTERVAL_S", "2.0"))
SONIOX_POLL_TIMEOUT_S = float(os.getenv("SONIOX_POLL_TIMEOUT_S", "600"))
SONIOX_HTTP_TIMEOUT_S = float(os.getenv("SONIOX_HTTP_TIMEOUT_S", "60"))


class SonioxError(Exception):
    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def is_soniox_model(model: Optional[str]) -> bool:
    """Route models like 'stt-async-v5' to the Soniox adapter."""
    return bool(model) and model.strip().lower().startswith("stt-async")


def _token_times(token: Dict[str, Any]) -> Optional[tuple[float, float]]:
    start_ms = token.get("start_ms")
    if start_ms is None:
        return None
    end_ms = token.get("end_ms")
    if end_ms is None:
        duration_ms = token.get("duration_ms") or 0
        end_ms = float(start_ms) + float(duration_ms)
    return float(start_ms) / 1000.0, float(end_ms) / 1000.0


def fold_tokens_to_segments(
    tokens: List[Dict[str, Any]],
    *,
    max_gap_s: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Fold Soniox token stream into verbose_json segments.

    Consecutive tokens with the same `speaker` become one segment; a speaker
    change or a silence gap larger than max_gap_s starts a new segment. The
    Soniox speaker number is kept as a string cluster id in `speaker`; tokens
    without a speaker yield segments without the field (backward compatible).
    Each segment also carries `token_count`, the number of tokens folded into
    it (an additive, optional field; see contracts/stt/v1/README.md).
    """
    gap = SONIOX_SEGMENT_MAX_GAP_S if max_gap_s is None else max_gap_s
    runs: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for token in tokens or []:
        text = str(token.get("text") or "")
        if not text:
            continue
        times = _token_times(token)
        if times is None:
            continue
        start, end = times
        raw_speaker = token.get("speaker")
        speaker = str(raw_speaker) if raw_speaker not in (None, "") else None

        if (
            current is None
            or speaker != current["speaker"]
            or (start - current["end"]) > gap
        ):
            if current is not None:
                runs.append(current)
            current = {
                "start": start,
                "end": end,
                "text": text,
                "speaker": speaker,
                "token_count": 1,
            }
        else:
            current["end"] = max(current["end"], end)
            current["text"] += text
            current["token_count"] += 1
    if current is not None:
        runs.append(current)

    segments: List[Dict[str, Any]] = []
    for idx, run in enumerate(runs):
        text = run["text"].strip()
        if not text:
            continue
        segment: Dict[str, Any] = {
            "id": idx,
            "seek": 0,
            "start": round(run["start"], 3),
            "end": round(run["end"], 3),
            "text": text,
            "tokens": [],
            "temperature": 0.0,
            "avg_logprob": 0.0,
            "compression_ratio": 1.0,
            "no_speech_prob": 0.0,
            "audio_start": round(run["start"], 3),
            "audio_end": round(run["end"], 3),
            "token_count": run["token_count"],
        }
        if run["speaker"] is not None:
            segment["speaker"] = run["speaker"]
        segments.append(segment)
    return segments


def build_verbose_json_response(
    tokens: List[Dict[str, Any]],
    *,
    language: Optional[str],
    max_gap_s: Optional[float] = None,
) -> Dict[str, Any]:
    segments = fold_tokens_to_segments(tokens, max_gap_s=max_gap_s)
    full_text = " ".join(seg["text"] for seg in segments).strip()
    duration = segments[-1]["end"] if segments else 0.0
    return {
        "text": full_text,
        "language": language or "unknown",
        "language_probability": 1.0,
        "duration": duration,
        "segments": segments,
    }


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}"}


async def _poll_transcription(client: httpx.AsyncClient, transcription_id: str) -> None:
    deadline = asyncio.get_event_loop().time() + SONIOX_POLL_TIMEOUT_S
    while True:
        resp = await client.get(
            f"{SONIOX_API_BASE}/v1/transcriptions/{transcription_id}",
            headers=_headers(),
        )
        if resp.status_code >= 400:
            raise SonioxError(f"Soniox status check failed: {resp.status_code} {resp.text[:300]}")
        payload = resp.json()
        status = str(payload.get("status") or "").lower()
        if status == "completed":
            return
        if status == "error":
            raise SonioxError(f"Soniox transcription failed: {payload.get('error_message')}")
        if asyncio.get_event_loop().time() >= deadline:
            raise SonioxError("Soniox transcription timed out", status_code=504)
        await asyncio.sleep(SONIOX_POLL_INTERVAL_S)


async def transcribe_via_soniox(
    audio_bytes: bytes,
    *,
    filename: str,
    model: str,
    language: Optional[str],
) -> Dict[str, Any]:
    """Run Soniox async transcription with speaker diarization enabled.

    Flow: upload file -> create transcription -> poll -> fetch transcript
    tokens -> fold into verbose_json segments with `speaker` cluster ids.
    """
    if not _api_key():
        raise SonioxError("SONIOX_API_KEY is not configured", status_code=503)

    async with httpx.AsyncClient(timeout=SONIOX_HTTP_TIMEOUT_S) as client:
        upload = await client.post(
            f"{SONIOX_API_BASE}/v1/files",
            headers=_headers(),
            files={"file": (filename, audio_bytes, "application/octet-stream")},
        )
        if upload.status_code >= 400:
            raise SonioxError(f"Soniox file upload failed: {upload.status_code} {upload.text[:300]}")
        file_id = upload.json().get("id")
        if not file_id:
            raise SonioxError("Soniox file upload returned no id")

        request_body: Dict[str, Any] = {
            "file_id": file_id,
            "model": model,
            "enable_speaker_diarization": True,
        }
        if language:
            request_body["language_hints"] = [language]
        created = await client.post(
            f"{SONIOX_API_BASE}/v1/transcriptions",
            headers=_headers(),
            json=request_body,
        )
        if created.status_code >= 400:
            raise SonioxError(f"Soniox transcription create failed: {created.status_code} {created.text[:300]}")
        transcription_id = created.json().get("id")
        if not transcription_id:
            raise SonioxError("Soniox transcription create returned no id")

        try:
            await _poll_transcription(client, transcription_id)
            transcript = await client.get(
                f"{SONIOX_API_BASE}/v1/transcriptions/{transcription_id}/transcript",
                headers=_headers(),
            )
            if transcript.status_code >= 400:
                raise SonioxError(
                    f"Soniox transcript fetch failed: {transcript.status_code} {transcript.text[:300]}"
                )
            tokens = transcript.json().get("tokens") or []
        finally:
            # Best-effort cleanup; Soniox bills stored files.
            for cleanup_url in (
                f"{SONIOX_API_BASE}/v1/transcriptions/{transcription_id}",
                f"{SONIOX_API_BASE}/v1/files/{file_id}",
            ):
                try:
                    await client.delete(cleanup_url, headers=_headers())
                except Exception:
                    logger.warning("Soniox cleanup failed for %s", cleanup_url)

    return build_verbose_json_response(tokens, language=language)
