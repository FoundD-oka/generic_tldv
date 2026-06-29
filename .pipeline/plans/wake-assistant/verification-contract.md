# Verification Contract — Wake Assistant

## Acceptance Criteria

| # | Criterion | Verification |
|---|---|---|
| AC1 | Default wake words are Kabosu-facing, not Vexa-facing | Unit tests assert `カボス` fires and `ねえVexa` does not. |
| AC2 | Groq request uses `openai/gpt-oss-20b` and hidden reasoning | Mocked HTTP test asserts payload model and `reasoning_format`. |
| AC3 | Aivis request uses model UUID `18972473-ca36-4e06-a33a-5cc14adba0c4` and mp3 | Mocked HTTP test asserts payload. |
| AC4 | Vexa speech uses `audio_base64` and `format=mp3` | Mocked HTTP test asserts `/speak` payload. |
| AC5 | Wake flow produces one response for one Kabosu utterance | Orchestrator unit test verifies Groq -> Aivis -> Vexa call chain. |
| AC6 | Bot echo suppression utilities exist | Unit test covers normalized echo detection. |
| AC7 | Service can be configured through environment without a fixed meeting id | `WAKE_ORCHESTRATOR_CHECK_CONFIG=1` supports startup config validation with auto discovery. |
| AC8 | Wake can follow dashboard-created running bots | Vexa client unit test parses `/bots/status` into meeting refs. |
| AC9 | Dashboard-created bots can speak back | Dashboard unit test asserts `voice_agent_enabled=true` default and preserves explicit false. |

## Test Commands

- `cd services/wake-orchestrator && PYTHONPATH=. python -m unittest discover -s tests -q`
- `python -m compileall services/wake-orchestrator/app`
- `cd services/dashboard && npm test -- test_export_and_bot_defaults.test.ts`
- `cd services/dashboard && VEXA_API_URL=http://localhost:8056 npm run build`
- `node .gitnexus/run.cjs detect-changes -r generic_tldv`
