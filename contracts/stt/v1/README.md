# stt.v1 — speech to timestamped text

The proven contract: OpenAI-compatible audio API, live since v0.10. The seam
that ended the WhisperLive era — standard, swappable, testable with a curl.

- **Producer:** any stt-client (today: `vexa-bot` pipeline bricks)
- **Consumer:** `services/transcription-service` (or any OpenAI-compatible endpoint)
- **Standard:** OpenAI Audio API (`/v1/audio/transcriptions` shape). **Never fork it.**
- **Golden:** `examples/` holds a real recorded request/response pair from the
  live service. CI replay uses recorded responses (MANIFEST §4 trust contract
  rule 1) so pipeline oracles are deterministic.

Request: audio (wav/opus) + model + response_format=verbose_json.
Response: segments with start/end timestamps + text. See examples.

## Speaker diarization extension (additive, optional)

Each segment MAY additionally carry a `speaker` field: an **anonymous acoustic
cluster id as a string** ("1", "2", ...), present only when the backing STT
returns diarization (today: Soniox async `stt-async-*` models via
`services/transcription-service/soniox_adapter.py`, which folds Soniox
token-level speaker labels into segments). Cluster ids are stable within one
response, are NOT names, and are NOT stable across files/sessions.

Each segment MAY additionally carry a `token_count` field: an **integer count
of the STT tokens folded into that segment** (today: emitted by the same
Soniox adapter, alongside `speaker`). It is a pure additive signal for
downstream false-split guards (e.g. distinguishing a stable speaker cluster
from a single stray misclassified token) and is not a measure of duration or
confidence.

Backends without diarization (Whisper) omit both fields entirely — existing
`start/end/text` consumers are unaffected, and downstream falls back to DOM
speaker mapping. The OpenAI shape is never forked; these are pure additive
fields. Golden: `examples/golden-2-diarization.*` (Soniox token payload +
folded verbose_json response pair, replayed in
`services/transcription-service/tests/test_soniox_adapter.py`).
