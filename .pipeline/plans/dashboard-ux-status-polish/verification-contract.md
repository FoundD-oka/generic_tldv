# Verification Contract: dashboard-ux-status-polish

- size: M
- external consultation required: no
- external consultation provider: claude-fable-cli

## Required Commands
- `cd services/dashboard && npm test -- tests/test_transcription_dictionary_ui.test.ts tests/test_dashboard_brand.test.ts tests/test_voiceprint_recording_ui.test.ts tests/test_meeting_status_display.test.ts tests/test_transcript_reprocess_ui.test.ts tests/test_single_flight_polling.test.ts tests/test_meetings_store_refresh_race.test.ts`
- `cd services/dashboard && npm test`
- `cd services/dashboard && VEXA_API_URL=http://localhost:8056 npm run build`
- `PYTHONPATH=services/meeting-api:libs/admin-models /Users/bonginkan-3-gouki/project/generic_tldv/.pipeline/worktrees/recording-range-streaming/checkout/.venv/bin/python -m pytest services/meeting-api/tests/test_meetings.py -q`
- `node /Users/bonginkan-3-gouki/project/generic_tldv/.gitnexus/run.cjs detect-changes --repo /Users/bonginkan-3-gouki/project/generic_tldv/.pipeline/worktrees/dashboard-ux-status-polish/checkout --scope compare --base-ref main`

## Evidence Rule
- Evidence Manifest must have no missing_evidence entries.
