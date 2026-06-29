# Plan — Wake Assistant: Kabosu wake + Groq + Aivis

## Request

- Source: user attachment and follow-up
- Task: implement the Wake Orchestrator with Kabosu wake words, Groq `openai/gpt-oss-20b`, and Aivis Cloud voice model `18972473-ca36-4e06-a33a-5cc14adba0c4`.

## Proposed Change

1. Add `services/wake-orchestrator` as an external service.
2. Discover dashboard-created running bots through Vexa `/bots/status`.
3. Subscribe to Vexa `/ws` transcript events for discovered meetings.
4. Default wake words to Kabosu-facing terms, not Vexa-facing terms.
5. Generate short Japanese meeting replies through Groq.
6. Synthesize speech through Aivis Cloud as mp3.
7. Send Aivis audio to Vexa `/speak` as `audio_base64`.
8. Enable dashboard-created bots for voice-agent playback by default.
9. Add cooldown, echo suppression, retry, and bounded response cleanup.

## Non Goals

- UI wake-word configuration.
- Chunked Aivis streaming into Vexa.
- Modifying Vexa bot audio playback internals.
- Live meeting E2E without real Groq/Aivis credentials.

## S/M/L Decision

| Field | Value |
|---|---|
| size | M |
| reason | Adds a new runtime service plus dashboard creation defaults so Wake can follow dashboard-created bots. |
| human gate required | no |

## GitNexus Impact

- `websocket_multiplex`: LOW; no upstream dependents reported.
- `bot_speak`: LOW; no upstream dependents reported.
- `publishTranscript`: LOW; direct callers `initPerSpeakerPipeline` and `cleanupPerSpeakerPipeline`; no edits planned.
- `withPostMeetingAutoStop`: CRITICAL before edit; shared by join, pending meeting, and Zoom callback flows. Change limited to defaulting `voice_agent_enabled` true while preserving explicit false.
- `CreateBotRequest`: CRITICAL before edit; interface-only addition of optional `voice_agent_enabled`.
- Final `detect-changes`: medium; affected process is `ZoomCallbackContent -> WithPostMeetingAutoStop`.

## Verification

See `.pipeline/plans/wake-assistant/verification-contract.md`.
