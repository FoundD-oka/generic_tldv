"""
Voiceprint embedding service (issue #27 Phase 4, Batch 1).

Wraps a local SpeechBrain ECAPA-TDNN speaker-embedding model behind a small
FastAPI service so meeting-api never has to load torch/speechbrain itself
and audio never leaves the deployment (PII policy: local processing only).

This service is intentionally dumb: it turns 16kHz mono WAV bytes into a
192-dim embedding vector and returns it. It does NOT persist audio or
embeddings, does NOT do matching/thresholding, and does NOT know about
speaker profiles, consent, or encryption — all of that lives in meeting-api
(services/meeting-api/meeting_api), which owns the voiceprints table.

PII rule (non-negotiable): never log embedding vector values or raw audio
bytes. Only log shapes/lengths/durations. See tests/test_no_pii_in_logs.py.

Structure mirrors services/transcription-service/main.py: startup model
load + health, env-driven config, semaphore-based backpressure.
"""
import os
import io
import json
import base64
import logging
import asyncio
import secrets
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn

# --------------------------------------------------------------------------
# Logging — set up before anything else so early startup logs are captured.
# --------------------------------------------------------------------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Config helpers (mirrors services/transcription-service/main.py style)
# --------------------------------------------------------------------------
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, None)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, None)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid int env %s=%r, using default %s", name, raw, default)
        return default


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
MODEL_NAME = "ecapa-tdnn"
EMBEDDING_MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
EMBEDDING_DIM = 192
MIN_AUDIO_DURATION_SECONDS = 1.0

VOICEPRINT_SERVICE_TOKEN = os.getenv("VOICEPRINT_SERVICE_TOKEN", "").strip()
ALLOW_UNAUTHENTICATED = _env_bool("ALLOW_UNAUTHENTICATED", False)
VOICEPRINT_MODEL_CACHE = os.getenv("VOICEPRINT_MODEL_CACHE", "/models")
VOICEPRINT_MAX_AUDIO_BYTES = _env_int("VOICEPRINT_MAX_AUDIO_BYTES", 20 * 1024 * 1024)  # 20MB
MAX_ACTIVE_REQUESTS = _env_int("MAX_ACTIVE_REQUESTS", 2)

_VEXA_ENV = os.getenv("VEXA_ENV", "development")
_PUBLIC_DOCS = _VEXA_ENV != "production"

app = FastAPI(
    title="Vexa Voiceprint Embedding Service",
    description="Local SpeechBrain ECAPA-TDNN speaker-embedding service (issue #27)",
    version="1.0.0",
    docs_url="/docs" if _PUBLIC_DOCS else None,
    redoc_url="/redoc" if _PUBLIC_DOCS else None,
    openapi_url="/openapi.json" if _PUBLIC_DOCS else None,
)

# Model state. `model_holder["model"]` is None until warmup completes;
# /health reports "loading" until then and /embed returns 503.
model_holder: Dict[str, Any] = {"model": None}

# Concurrency guard: at most MAX_ACTIVE_REQUESTS /embed calls run at once.
# Non-blocking — a request that would exceed the limit gets 429 immediately
# rather than queueing (mirrors transcription-service FAIL_FAST_WHEN_BUSY).
_active_requests = 0
_active_lock = asyncio.Lock()


async def _try_acquire_slot() -> bool:
    global _active_requests
    async with _active_lock:
        if _active_requests >= MAX_ACTIVE_REQUESTS:
            return False
        _active_requests += 1
        return True


async def _release_slot() -> None:
    global _active_requests
    async with _active_lock:
        _active_requests = max(0, _active_requests - 1)


# --------------------------------------------------------------------------
# Model loading / inference
#
# torch and speechbrain are imported lazily *inside* these two functions
# (never at module import time) so unit tests can monkeypatch both without
# ever pulling in those heavy dependencies or downloading model weights.
# --------------------------------------------------------------------------
def _load_model_sync():
    """Blocking model load. Runs in a thread executor from startup."""
    from speechbrain.inference.speaker import EncoderClassifier

    return EncoderClassifier.from_hparams(
        source=EMBEDDING_MODEL_SOURCE,
        savedir=VOICEPRINT_MODEL_CACHE,
        run_opts={"device": "cpu"},
    )


def _run_inference(model: Any, audio_array: np.ndarray) -> List[float]:
    """Blocking inference call. Runs in a thread executor per request."""
    import torch

    waveform = torch.from_numpy(np.ascontiguousarray(audio_array, dtype=np.float32)).unsqueeze(0)
    with torch.no_grad():
        embedding_tensor = model.encode_batch(waveform)
    return embedding_tensor.detach().cpu().numpy().reshape(-1).tolist()


async def _load_model_and_warmup() -> None:
    """Background startup task: load the model, then run one warmup pass.

    Runs as a background task (not awaited by the startup event) so /health
    is reachable and reports status="loading" while this is in flight,
    instead of blocking the ASGI server from accepting connections.
    """
    loop = asyncio.get_event_loop()
    try:
        logger.info("Loading voiceprint model (cache_dir=%s)", VOICEPRINT_MODEL_CACHE)
        model = await loop.run_in_executor(None, _load_model_sync)
        # Warmup: run one inference pass on synthetic silence so the first
        # real request doesn't pay a cold JIT/thread-pool warmup cost.
        warmup_audio = np.zeros(int(MIN_AUDIO_DURATION_SECONDS * 16000), dtype=np.float32)
        await loop.run_in_executor(None, _run_inference, model, warmup_audio)
        model_holder["model"] = model
        logger.info("Voiceprint model ready (model=%s, dim=%d)", MODEL_NAME, EMBEDDING_DIM)
    except Exception:
        # Never re-raise: a failed load must not crash the process (it would
        # just restart-loop). /health keeps reporting "loading"/non-ready
        # forever; operators see the traceback in logs.
        logger.error("Voiceprint model failed to load", exc_info=True)


@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(_load_model_and_warmup())


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
async def verify_token(request: Request) -> bool:
    """Bearer-token auth for /embed.

    - VOICEPRINT_SERVICE_TOKEN set: a matching `Authorization: Bearer <token>`
      header is required (401 otherwise).
    - VOICEPRINT_SERVICE_TOKEN unset AND ALLOW_UNAUTHENTICATED=true: allowed.
    - VOICEPRINT_SERVICE_TOKEN unset AND ALLOW_UNAUTHENTICATED unset/false:
      503 — refuse to run an unauthenticated biometric endpoint by default.
    """
    token = VOICEPRINT_SERVICE_TOKEN
    if not token:
        if ALLOW_UNAUTHENTICATED:
            return True
        raise HTTPException(
            status_code=503,
            detail=(
                "voiceprint-service: VOICEPRINT_SERVICE_TOKEN is not configured "
                "and ALLOW_UNAUTHENTICATED is not enabled"
            ),
        )

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    provided = auth_header[len("Bearer "):].strip()
    # secrets.compare_digest instead of `!=`: a plain string comparison
    # short-circuits at the first mismatched byte, which is a timing side
    # channel on a bearer-token credential (tribunal BUG-008).
    if not secrets.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return True


# --------------------------------------------------------------------------
# /health
# --------------------------------------------------------------------------
@app.get("/health")
async def health_check():
    ready = model_holder["model"] is not None
    body = {"status": "healthy" if ready else "loading", "model": MODEL_NAME}
    if not ready:
        return JSONResponse(content=body, status_code=503)
    return body


# --------------------------------------------------------------------------
# /embed
# --------------------------------------------------------------------------
async def _read_bounded_body(request: Request, max_bytes: int) -> bytes:
    """Read the request body incrementally via the ASGI stream, aborting
    with 413 the moment the running total exceeds max_bytes.

    This is the real enforcement point for VOICEPRINT_MAX_AUDIO_BYTES: the
    Content-Length header (checked separately, see embed()) is a
    client-supplied, best-effort fast path that is absent under chunked
    transfer encoding. Without this streaming guard a misbehaving/compromised
    caller could make the service buffer an unbounded body in memory before
    any size check ran (tribunal BUG-006). We never hold more than
    max_bytes + (one chunk) bytes.
    """
    chunks: List[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"payload exceeds {max_bytes} byte limit",
            )
    return b"".join(chunks)


async def _extract_audio_bytes(request: Request, max_bytes: int) -> bytes:
    """Pull raw audio bytes out of either a multipart file or a JSON body.

    Reads the whole body through `_read_bounded_body` first so the size cap
    is enforced while streaming, not after the fact. The bounded bytes are
    then cached on `request._body`, which Starlette's `Request.stream()`
    honours (it yields the cached body instead of re-reading the — already
    fully consumed — ASGI receive channel), so `request.form()` below reuses
    the already-capped buffer instead of triggering a second, unbounded
    read of the connection.

    Never logs the extracted bytes or any decoded content.
    """
    content_type = request.headers.get("content-type", "")

    raw_body = await _read_bounded_body(request, max_bytes)
    request._body = raw_body  # noqa: SLF001 - starlette-documented cache hook (Request.stream()/.body())

    if content_type.startswith("multipart/form-data"):
        form = await request.form(max_part_size=max_bytes + 1)
        file_field = form.get("file") or form.get("audio")
        if file_field is None or not hasattr(file_field, "read"):
            raise HTTPException(
                status_code=400,
                detail="multipart body must include an audio file field named 'file'",
            )
        return await file_field.read()

    if not raw_body:
        raise HTTPException(status_code=400, detail="empty request body")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="unsupported content type; send multipart/form-data or JSON {audio_base64}",
        )
    audio_b64 = payload.get("audio_base64") if isinstance(payload, dict) else None
    if not audio_b64:
        raise HTTPException(status_code=400, detail="JSON body must include 'audio_base64'")
    try:
        return base64.b64decode(audio_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="'audio_base64' is not valid base64")


def _decode_and_validate_audio(audio_bytes: bytes) -> Tuple[np.ndarray, int, float]:
    try:
        audio_array, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
    except Exception:
        # Do not include the exception text verbatim — soundfile errors can
        # occasionally echo header bytes back; keep the detail generic.
        raise HTTPException(
            status_code=422,
            detail="could not decode audio; expected 16kHz mono WAV",
        )

    if audio_array.ndim > 1:
        audio_array = audio_array.mean(axis=1)
    audio_array = np.ascontiguousarray(audio_array, dtype=np.float32)

    duration_seconds = float(len(audio_array)) / float(sample_rate) if sample_rate else 0.0
    if duration_seconds < MIN_AUDIO_DURATION_SECONDS:
        raise HTTPException(
            status_code=422,
            detail=f"audio duration {duration_seconds:.2f}s is below the {MIN_AUDIO_DURATION_SECONDS}s minimum",
        )
    return audio_array, sample_rate, duration_seconds


@app.post("/embed")
async def embed(request: Request, _: bool = Depends(verify_token)):
    # Fast-path rejection for oversized payloads using Content-Length, before
    # we read the body at all.
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > VOICEPRINT_MAX_AUDIO_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"payload exceeds {VOICEPRINT_MAX_AUDIO_BYTES} byte limit",
                )
        except ValueError:
            pass

    model = model_holder["model"]
    if model is None:
        raise HTTPException(status_code=503, detail="voiceprint model is still loading")

    # _extract_audio_bytes enforces VOICEPRINT_MAX_AUDIO_BYTES while reading
    # the stream (see _read_bounded_body) — the check below is a redundant
    # belt-and-suspenders guard on the decoded/extracted payload, which can
    # only ever be <= the already-capped raw body.
    audio_bytes = await _extract_audio_bytes(request, VOICEPRINT_MAX_AUDIO_BYTES)
    if len(audio_bytes) > VOICEPRINT_MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"payload exceeds {VOICEPRINT_MAX_AUDIO_BYTES} byte limit",
        )

    acquired = await _try_acquire_slot()
    if not acquired:
        raise HTTPException(
            status_code=429,
            detail="voiceprint-service is busy, retry later",
            headers={"Retry-After": "1"},
        )
    try:
        audio_array, _sample_rate, duration_seconds = _decode_and_validate_audio(audio_bytes)
        loop = asyncio.get_event_loop()
        embedding = await loop.run_in_executor(None, _run_inference, model, audio_array)
        # PII rule: log only shape/duration metadata, never the vector or audio.
        logger.info(
            "embed request completed: duration=%.2fs dim=%d",
            duration_seconds,
            len(embedding),
        )
        return {
            "embedding": embedding,
            "duration_seconds": duration_seconds,
            "model": MODEL_NAME,
        }
    finally:
        await _release_slot()


@app.get("/")
async def root():
    return {
        "service": "Vexa Voiceprint Embedding Service",
        "model": MODEL_NAME,
        "status": "ready" if model_holder["model"] is not None else "initializing",
        "endpoints": {"embed": "/embed", "health": "/health"},
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, log_level="info")
