# Verification Contract: calendar-meeting-title

- size: S
- external consultation required: no
- external consultation provider: claude-fable-cli

## Required Commands
- `PYTHONPATH=services/meeting-api:libs/admin-models /Users/bonginkan-3-gouki/project/generic_tldv/.pipeline/worktrees/recording-range-streaming/checkout/.venv/bin/python -m pytest services/meeting-api/tests/test_meetings.py -q`
- `cd services/dashboard && node_modules/.bin/vitest run tests/test_meeting_cards_ui.test.ts`
- `cd services/dashboard && node scripts/generate-release-version.js && node_modules/.bin/tsc --noEmit`
- `node /Users/bonginkan-3-gouki/project/generic_tldv/.gitnexus/run.cjs detect-changes --repo /Users/bonginkan-3-gouki/project/generic_tldv/.pipeline/worktrees/calendar-meeting-title/checkout --scope compare --base-ref main`

## Evidence Rule
- Evidence Manifest must have no missing_evidence entries.
