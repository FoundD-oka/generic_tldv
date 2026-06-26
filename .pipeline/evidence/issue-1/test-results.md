# Test evidence — issue #1 (GCS recordings backend)

Captured 2026-06-26.

## Environment

meeting-api requires Python >=3.11 (repo pyproject). Local default is 3.9.6
(cannot install the package). Tests run in a venv built from
`~/.local/bin/python3.11` (Python 3.11.15), mirroring CI
(`.github/workflows/test-meeting-api.yml`): `pip install -e libs/admin-models/
-e services/meeting-api/ pytest pytest-asyncio httpx`.

## meeting-api unit suite

Command:
```
python -m pytest services/meeting-api/tests/ -q \
  --ignore=services/meeting-api/tests/test_integration_live.py \
  --ignore=services/meeting-api/tests/collector
```
Result:
```
245 passed, 10 skipped, 17 warnings in 11.52s
```
Baseline before this change was 239 passed; +6 new (issue #1 tests). A mid-work
run surfaced 4 regressions in the raw/mp3 Range tests caused by the per-file
backend dispatch; fixed by reusing the default singleton when the file's backend
equals the current default (only a DIFFERENT backend gets a dedicated client).
Re-run is fully green.

New tests:
- `tests/test_gcs_storage.py` — fake google-cloud-storage client; asserts
  inclusive-end `download_file_range` (0,0)->1 byte, `get_file_size` does not
  download the body, `file_exists` NotFound->False, V4 presign success + None
  fallback (logged), `list_objects` sorted + bounded truncation, factory dispatch.
- `tests/test_recording_gcs_metadata.py` — `_compute_delete_after` (+60d / bad
  input), `RECORDING_STORAGE_CLASS_POLICY`, per-file backend dispatch (non-default
  -> dedicated cached client; default -> singleton; missing -> default; init
  failure -> default), lifecycle.json AC3 (Nearline@14) + AC4 (Delete@60).

## vexa-bot orphan sweep

Command:
```
npx tsx services/vexa-bot/core/src/services/recording-sweep.test.ts
```
Result:
```
7 passed, 0 failed
```
Covers: all 3 orphan filename shapes removed when old; fresh files kept; non-
recording files ignored; current-session files excluded; missing tmp dir handled
without throw. (tsx also transpiles the module, confirming it typechecks.)

## Acceptance-criteria coverage

- AC1 factory dispatch to gcs — test_factory_dispatches_gcs.
- AC2 storage key + metadata, no signed URL persisted — metadata fields + plan.
- AC3/AC4 lifecycle 14d Nearline / 60d Delete — lifecycle.json assertions.
- AC5 local temp deleted after upload — bot finally-delete preserved + sweep test.
- AC6 playback works post-migration — per-file dispatch tests (minio file read
  while default=gcs).
- AC7 Range not broken — inclusive-end + no-download size tests.
- AC8 failure not silent — presign None+WARNING fallback test; Pack G.1 log intact.
  QA review (qa-judgment.json) flagged that for the gcs backend a null presigned
  URL was returned to the client (consumer-dependent). Hardened: download_media_file
  now falls back to the /raw proxy path itself (matching the local backend), so the
  endpoint never returns a null url. New test: TestDownloadRecordingMediaDownloadFallback
  ::test_null_presigned_url_falls_back_to_raw.
