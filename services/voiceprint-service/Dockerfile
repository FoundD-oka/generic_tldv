# Voiceprint embedding service (issue #27 Phase 4, Batch 1).
# Build context is the repo root (see deploy/compose/docker-compose.yml),
# mirroring services/tts-service/Dockerfile.
FROM python:3.11-slim

# libsndfile1: required by soundfile for WAV decoding.
# curl: used by the Docker HEALTHCHECK below to probe /health.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/voiceprint-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/voiceprint-service/main.py .

# Model cache — downloaded at BUILD time so a cold container doesn't need a
# network round-trip to HuggingFace on first request. VOICEPRINT_MODEL_CACHE
# (default /models) must match this path. If a runtime volume is mounted
# over /models empty (e.g. a fresh named volume before Docker's initial
# content copy runs, or an operator-provided empty bind mount), the
# EncoderClassifier.from_hparams() call in main.py falls back to downloading
# at runtime — same code path, no special handling needed.
ENV VOICEPRINT_MODEL_CACHE=/models
RUN mkdir -p /models && \
    python -c "\
from speechbrain.inference.speaker import EncoderClassifier; \
EncoderClassifier.from_hparams(source='speechbrain/spkrec-ecapa-voxceleb', savedir='/models', run_opts={'device': 'cpu'})"

# Non-root (mirrors services/tts-service/Dockerfile). Named-volume mounts
# over /models inherit this ownership from the image on first use.
RUN useradd -r -s /bin/false appuser \
    && chown -R appuser /app /models
USER appuser

# Generous start_period: cold CPU torch/speechbrain init can take well over
# the transcription-service default (60s) even with the model pre-cached.
HEALTHCHECK --interval=20s --timeout=10s --start-period=120s --retries=6 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
