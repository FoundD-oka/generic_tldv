# Verification Contract: calendar-wakeword-toggle

- size: M
- external consultation required: no
- external consultation provider: claude-fable-cli

## Required Commands
- `cd services/dashboard && npm run lint -- src/components/join/join-modal.tsx src/lib/dashboard-copy.ts tests/test_export_and_bot_defaults.test.ts`
- `cd services/dashboard && npm test -- test_export_and_bot_defaults.test.ts`
- `cd services/dashboard && npx tsc --noEmit --pretty false`
- `python -m pytest services/calendar-service/tests/test_sync.py -q`（PYTHONPATH: calendar-service, meeting-api, libs/admin-models, testdeps）

## Evidence Rule
- Evidence Manifest must have no missing_evidence entries.
