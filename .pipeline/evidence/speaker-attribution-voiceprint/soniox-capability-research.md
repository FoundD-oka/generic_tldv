# Soniox Capability Research — Blocker #1 (STT speaker labels) + Phase 4 (voiceprint)

Date: 2026-07-07. Source: Soniox official docs. Purpose: resolve Plan Relay blocker
"can Soniox return per-segment/token speaker labels?".

## Verdict: Blocker #1 RESOLVED — feasible

- Enable with `enable_speaker_diarization: true` on the **async** API (model `stt-async-v5`).
- Each **token** carries a `speaker` field, e.g. `{"text": "How", "speaker": "1"}`. Labels are **anonymous numbers** ("1","2",...), not names.
- **Up to 15 speakers** per session.
- **Async is materially better than realtime** for diarization: "significantly higher diarization accuracy because the model has access to the full audio context." Realtime has higher attribution error + temporary speaker switches. → deferred/post-meeting is the ideal place to use it.
- Segment grouping is **application-side**: "group tokens by speaker to create readable segments." Response is token-level.

## Adapter implication (confirms Phase-0 precondition is real but achievable)

- Soniox native response is **token-level with `speaker`**, NOT OpenAI `verbose_json` segments. Our `contracts/stt/v1` is OpenAI segments (start/end/text, no speaker).
- Adapter work = have `services/transcription-service` (or a Soniox adapter) call Soniox async with diarization on, then **fold tokens → segments carrying a cluster id** (extend verbose_json with a `speaker`/`cluster` field). This is exactly the Phase 1 / Phase 0 precondition; it is well-scoped, not a research risk.
- Within-file speaker-number consistency: not explicitly documented; expected stable within one file (validate with a fixture in P0/P1).

## Phase 4 (voiceprint) implication

- Soniox provides **no speaker enrollment / voiceprint / cross-session identification** — only within-file diarization clusters.
- Confirms the plan's decision: cross-meeting auto-naming needs a **separate speaker-embedding model/adapter** (SpeechBrain/ECAPA, pyannote, or a hosted speaker-ID). Soniox clusters feed that embedder per cluster; it is not a Soniox feature.

## Sources

- https://soniox.com/docs/stt/concepts/speaker-diarization
- https://soniox.com/docs/stt/async/async-transcription
- https://soniox.com/docs/stt/models
- https://soniox.com/docs/api-reference/stt/websocket-api
