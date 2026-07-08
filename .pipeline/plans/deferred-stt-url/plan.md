# Plan — Deferred-only STT endpoint override (S fast-path)

## Request

User approved "案A" (2026-07-08): run the repo transcription-service locally
for the Soniox deferred route while realtime STT stays on the host
whisper-server (port 8091).

## Problem

`TRANSCRIPTION_SERVICE_URL` is shared by BOTH the realtime bot pipeline
(`meetings.py:1109` → bot config, runtime-api env) and the deferred
post-meeting transcription (`final_transcription._call_transcription_service`).
Pointing it at the containerized CPU faster-whisper service would degrade
realtime latency/quality. A deferred-only override is needed.

## Change (S: 1 product file + compose/env plumbing)

- `final_transcription.py`: resolve the deferred endpoint as
  `DEFERRED_TRANSCRIPTION_SERVICE_URL || TRANSCRIPTION_SERVICE_URL` (same for
  `DEFERRED_TRANSCRIPTION_SERVICE_TOKEN || TRANSCRIPTION_SERVICE_TOKEN`) via a
  small `_deferred_transcription_endpoint()` helper.
- `deploy/compose/docker-compose.yml`: pass both new vars to meeting-api.
- `deploy/env-example`: document.
- Unit test for the helper (override wins, fallback works).

## S criteria check

≤2 product files (final_transcription.py + compose), no new deps, no
schema/auth/payment/PII, unambiguous, one sentence: "deferred transcription
can use a different STT endpoint than realtime."

## Verification

- `pytest services/meeting-api/tests/test_final_transcription.py tests/test_speaker_clusters.py` green
- `python -m compileall services/meeting-api/meeting_api`
- deploy smoke: deferred hits the new worker (checked via worker logs)
