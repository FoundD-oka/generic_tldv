# Verification Contract — Issues #4-#19

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | Dashboard AI and agent meeting context redact obvious secrets before LLM use. | unit | dashboard/pytest tests |
| AT-002 | Final transcription uses Japanese by default and preserves explicit language override. | unit | `services/meeting-api/tests/test_final_transcription.py` |
| AT-003 | Final transcription does not replace speaker-labelled realtime rows with all-Unknown output unless forced. | unit | `services/meeting-api/tests/test_final_transcription.py` |
| AT-004 | Dashboard bot creation defaults bot name and language to Kabosu/Japanese across join, pending, and Zoom callback flows. | unit | dashboard vitest |
| AT-005 | API bot creation defaults `voiceAgentEnabled=true` while explicit false remains false. | unit | meeting-api pytest |
| AT-006 | Persona copies match the canonical Japanese source and wake prompt uses a platform label. | unit | agent-api, wake-orchestrator, dashboard tests |
| AT-007 | User-visible listed English/Vexa strings are translated or brand-backed. | inspection + unit | grep/test evidence |
| AT-008 | Wake compose profile no longer starts wake-stt/tts by default and env failures are clear. | config + unit | compose config, wake tests |
| AT-009 | Wake meeting resolution handles early events and ID aliases without duplicate reply bypasses. | unit | wake-orchestrator tests |
| AT-010 | Final transcript replacement publishes `transcript.finalized` and dashboard re-fetches transcript data. | unit | meeting-api/dashboard tests |
| AT-011 | Meeting deletion removes `chat_messages` and ambiguous duplicate deletion returns 409. | unit | meeting-api tests |
| AT-012 | WS docs/types prefer runtime `transcript` and only retain deprecated `transcript.mutable` compatibility. | grep + type/test | dashboard/docs evidence |
| AT-013 | Assistant-context endpoint returns redacted transcript/chat/url context for active and completed meetings. | unit/API | meeting-api/api-gateway/dashboard tests |
| AT-014 | Design document records Japanese-only single-brand policy and aligns with tests. | inspection + unit | docs/dashboard tests |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | Explicit bot creation choices are not overwritten. | unit | dashboard/meeting-api tests |
| FP-002 | Existing wake consumers still accept runtime `transcript` events. | unit | wake tests |
| FP-003 | Existing dashboard delete path with explicit `meeting_id` still succeeds. | unit | dashboard/meeting-api tests |
| FP-004 | Redaction does not mask speaker names or timestamps unnecessarily. | unit | redaction tests |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | Harness doctor/residency passes. | command | `.pipeline/evidence/issues-4-19/test-results.md` |
| NFT-002 | GitNexus `detect-changes` is run before completion. | command | `.pipeline/evidence/issues-4-19/test-results.md` |
| NFT-003 | Outcome judge passes. | command | `.pipeline/evidence/issues-4-19/test-results.md` |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- QA judgment required: yes
- issue closure required: no
