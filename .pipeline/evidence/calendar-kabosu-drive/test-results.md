# Test Results — Calendar Kabosu Drive

## Passed

- `py_compile` for changed Python modules and OAuth helper: passed.
- `services/meeting-api` full suite (py3.11 venv): **343 passed, 18 skipped** (2026-07-07 rerun after Shared Drive + all-meetings change).
- `services/meeting-api/tests/test_drive_export.py services/meeting-api/tests/test_final_transcription.py`: 20 passed (was 16; +4 for Shared Drive/all-meetings behavior).
- `services/calendar-service/tests/test_sync.py`: 1 passed.
- `docker compose -f deploy/compose/docker-compose.yml config`: passed; base profile config generated.
- `docker compose -f deploy/compose/docker-compose.yml --profile calendar config`: passed; `calendar-service` and Kabosu env are present.
- `node .gitnexus/run.cjs detect-changes --repo generic_tldv`: passed, risk low, affected processes 0.
- `scripts/harness/validate-runtime-profile.sh`: passed.
- `git diff --check`: passed.
- `.claude/hooks/harness-residency.sh`: passed.
- `.claude/hooks/preflight.sh --full`: passed.
- `.claude/hooks/hd-gate.sh`: passed.
- `.claude/hooks/adapter-validate.sh`: passed.

## Change delta (2026-07-07)

- `drive_export.py`: added `supportsAllDrives=true` to the Drive multipart upload so Shared Drive parent folders no longer 404.
- `drive_export.py`: Drive export now covers every completed meeting by default; `KABOSU_DRIVE_EXPORT_CALENDAR_ONLY=true` restores calendar-origin-only behavior. Non-calendar meetings export with meeting-derived metadata (`calendar_event = {}`).
- Tests updated: `test_final_transcription.py` now asserts `drive_export` is queued for non-calendar meetings; `test_drive_export.py` adds all-meetings, calendar-only, Shared Drive params, and non-calendar run coverage.

## Notes

- Pytest was not installed in the bundled Python runtime, so test dependencies were installed into a py3.11 venv (scratchpad) and used through it. The repo was not modified for those dependencies.
- Compose emitted warnings for unset optional env such as `IMAGE_TAG`; config generation still exited successfully.
