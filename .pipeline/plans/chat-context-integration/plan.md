# Plan — Chat Context Integration

## Request

- Source: user attachment
- Task: Treat Google Meet chat as a first-class Kabosu input channel, not a side note.
- Policy update: typed chat triggers whenever it contains `カボス`, `かぼす`, or `kabosu`; do not restrict to sentence start or command grammar.

## Proposed Change

1. Add typed-chat wake detection that is stricter than voice ASR wake detection.
2. Store recent chat messages in the Wake Orchestrator context, including REST bootstrap messages.
3. Use REST chat bootstrap only as context; do not trigger assistant turns from historical messages.
4. Consume real-time `chat.received` events as assistant turns when they mention Kabosu.
5. Ignore bot/self chat, duplicate events, and short repeated same-text chat wakes.
6. Send chat-originated replies back through Vexa `/chat` by default.
7. Support chat-originated `口頭で` / `読み上げて` requests by replying in both chat and voice.
8. Add recent chat and shared URLs to voice-originated LLM context.
9. Allow voice-originated requests to explicitly route to chat when the user says to post or return in chat.
10. Redact obvious credentials before transcript/chat context is sent to the LLM.

## Non Goals

- Persistent database tables for chat messages and assistant turns.
- Rolling summary persistence.
- External URL body fetching.
- Full live meeting E2E with real Groq, Aivis, and Vexa credentials.

## S/M/L Decision

| Field | Value |
|---|---|
| size | M |
| reason | Adds a new first-class input route to an existing runtime service and changes LLM context construction. |
| human gate required | no |

## GitNexus Impact

- `WakeOrchestrator`: LOW; direct importer `services/wake-orchestrator/app/main.py`.
- `VexaClient`: LOW; direct importers `orchestrator.py` and `main.py`.
- `VexaTranscriptSubscriber`: LOW; direct importers `orchestrator.py` and `main.py`.
- `GroqClient.generate_reply`: LOW; no upstream dependents reported.
- `Settings`: LOW; direct importers `orchestrator.py`, `main.py`, and `clients.py`.
- Final `detect-changes`: medium because pre-existing non-wake changes in agent-api and dashboard are also present in the worktree.

## Verification

See `.pipeline/plans/chat-context-integration/verification-contract.md`.
