# Verification Contract — Calendar Kabosu Drive

## Acceptance Criteria

| # | Criterion | Verification |
|---|---|---|
| AC1 | Dedicated account mode exists without deleting legacy per-user calendar mode | Unit test/import check and code review of `sync_loop` branch. |
| AC2 | Calendar-created bot payload uses Kabosu defaults | `services/calendar-service/tests/test_sync.py` asserts bot name, language, voice agent, native meeting id. |
| AC3 | Calendar-created meeting receives durable origin metadata | `services/calendar-service/tests/test_sync.py` asserts `meeting.data.calendar_event`. |
| AC4 | Final transcription success queues Drive export only for calendar-origin meetings | `services/meeting-api/tests/test_final_transcription.py`. |
| AC5 | `skipped_no_speaker_events` fallback queues Drive export | `services/meeting-api/tests/test_final_transcription.py`. |
| AC6 | Drive exporter produces Markdown with meeting metadata and speaker-labeled transcript | `services/meeting-api/tests/test_drive_export.py`. |
| AC7 | Drive export sweep retries queued jobs | `services/meeting-api/tests/test_drive_export.py`. |
| AC8 | Compose exposes calendar service behind an explicit profile and passes required env | `docker compose -f deploy/compose/docker-compose.yml config`. |
| AC9 | GitNexus changed-symbol detection is reviewed before completion | `node .gitnexus/run.cjs detect-changes --repo generic_tldv`. |

## Test Commands

- `/Users/bonginkan-3-gouki/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m py_compile services/calendar-service/app/sync.py services/calendar-service/app/main.py services/meeting-api/meeting_api/drive_export.py services/meeting-api/meeting_api/final_transcription.py services/meeting-api/meeting_api/sweeps.py scripts/google-oauth-refresh-token.py`
- `PYTHONPATH=/tmp/generic_tldv_testdeps:services/meeting-api:libs/admin-models /Users/bonginkan-3-gouki/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest -q services/meeting-api/tests/test_drive_export.py services/meeting-api/tests/test_final_transcription.py`
- `PYTHONPATH=/tmp/generic_tldv_testdeps:services/calendar-service:services/meeting-api:libs/admin-models /Users/bonginkan-3-gouki/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m pytest -q services/calendar-service/tests/test_sync.py`
- `docker compose -f deploy/compose/docker-compose.yml config`
- `node .gitnexus/run.cjs detect-changes --repo generic_tldv`
