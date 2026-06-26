# Verification Contract — Issue #1 (GCS recordings)

Independent QA judges against THIS contract + evidence only.

## Acceptance criteria → checks

| # | Issue acceptance criterion | Verification |
|---|---|---|
| AC1 | New recordings stored in Cloud Storage | `STORAGE_BACKEND=gcs` routes to `GCSStorageClient`; unit test asserts factory dispatch + upload calls GCS blob upload. |
| AC2 | DB/meeting.data stores storage key + metadata, not expiring URLs | Test asserts media_files entry has `storage_path` (key, not URL), `storage_class_policy`, `delete_after`, `upload_status`; asserts no signed URL string persisted; `playback_url` stays a route. |
| AC3 | Lifecycle rule: 14d → Nearline exists | `deploy/gcs/lifecycle.json` contains SetStorageClass NEARLINE @ age 14; schema test / json assertion. |
| AC4 | Lifecycle rule: 60d → delete exists | `deploy/gcs/lifecycle.json` contains Delete @ age 60; json assertion. |
| AC5 | After successful upload, bot/meeting-api local temp deleted | Existing bot finally-delete preserved; test/inspection confirms cleanup path; meeting-api finalizer temp cleanup unchanged. |
| AC6 | Dashboard playback/download works as before | Per-file backend dispatch test: media_file with `storage_backend="minio"` is read via MinIO client even when default is gcs (migration safety). 206 path unchanged. |
| AC7 | Range request playback/seek not broken | GCS `download_file_range(p,0,0)` returns EXACTLY 1 byte (inclusive-end); `get_file_size` uses blob.size without downloading body. |
| AC8 | Failure not silent — traceable via log/status | Pack G.1 permanent-failure structured log present; signing-unavailable returns None + WARNING (not silent), playback falls back to `/raw` (200, not 500). |

## Test commands (evidence)

- `cd services/meeting-api && python -m pytest tests/ -k "storage or gcs or recording" -q`
- New: `tests/test_gcs_storage.py` — fake google.cloud.storage; assert range inclusive-end,
  get_file_size no-download, file_exists NotFound→False, presigned None-fallback,
  list_objects sorted, factory dispatch.
- New/extended: metadata enrichment fields + delete_after computation.
- New: per-file backend dispatch (mixed-backend read).
- `deploy/gcs/lifecycle.json` assertion (14d Nearline, 60d Delete).
- Bot: `cd services/vexa-bot/core && npm test` (or tsc typecheck) for cleanup/sweep.

## Non-regression

- MinIO default path unchanged (boto3 still default backend).
- GCS SDK import is lazy — non-GCS deploys don't import it.
- 206/Range, /raw fallback, /master route, mp3 path semantics unchanged.

## Gates required before PR readiness

residency (pass) · preflight · adapter-contract · hd-gate · qa-judge · (L) tribunal or
sidechain · doc-staleness · hash-bound approval.
