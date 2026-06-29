# Verification Contract — Chat Context Integration

## Acceptance Criteria

| # | Criterion | Verification |
|---|---|---|
| AC1 | Typed chat wake triggers on `カボス`, `かぼす`, and `kabosu` anywhere in the text. | Unit tests cover middle-of-message and `@kabosu` mentions. |
| AC2 | Typed chat wake does not use ASR-only fuzzy variants. | Unit tests assert `かもしれない` and `かばす` do not trigger chat wake. |
| AC3 | Bot/self chat messages do not create assistant turns. | Orchestrator unit test covers sender `カボス` and duplicate event handling. |
| AC4 | Duplicate chat events create at most one turn. | Orchestrator unit test sends the same `chat.received` twice. |
| AC5 | REST chat bootstrap is context-only and does not trigger old Kabosu messages. | Orchestrator unit test bootstraps an old Kabosu chat and verifies only the later voice wake answers. |
| AC6 | Chat-originated Kabosu requests reply through Vexa `/chat` by default. | Orchestrator and Vexa client unit tests assert `/chat` send behavior. |
| AC7 | Chat-originated requests can ask for voice too. | Orchestrator unit test covers `口頭で言って` producing chat plus voice output. |
| AC8 | Voice-originated requests include recent chat and URLs as tagged context. | Orchestrator unit test covers `チャットに貼った資料` while keeping voice output mode. |
| AC9 | Voice-originated requests can explicitly route output to chat. | Orchestrator unit test covers `チャットに貼って`. |
| AC10 | Obvious credentials are redacted before context leaves the service. | Text utility unit test covers bearer and API-key style strings. |

## Test Commands

- `cd services/wake-orchestrator && PYTHONPATH=.:/tmp/generic_tldv_wake_deps python3 -m unittest discover -s tests -q`
- `python3 -m compileall services/wake-orchestrator/app`
- `cd services/wake-orchestrator && WAKE_ORCHESTRATOR_CHECK_CONFIG=1 VEXA_API_KEY=vexa-key GROQ_API_KEY=groq-key AIVIS_API_KEY=aivis-key PYTHONPATH=.:/tmp/generic_tldv_wake_deps python3 -m app.main`
- `node .gitnexus/run.cjs detect-changes --repo generic_tldv`
