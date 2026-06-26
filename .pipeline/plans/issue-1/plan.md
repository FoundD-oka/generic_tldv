# Plan — Issue #1: GCS source-of-truth for recordings

/ 録画ファイルをGCSで14日Standard・60日Nearline保持にする

## Decisions (from user + Plan Relay)

- **Backend**: native `gcs` backend (本命案). `google-cloud-storage` SDK, ADC / Workload
  Identity. No HMAC key in the app. Signed URLs via V4 IAM `signBlob` (no key file).
- **Lifecycle infra**: no Terraform in repo → ship gcloud script + lifecycle JSON + docs
  under `deploy/gcs/`. Applying it to a real bucket is an **operator step**, not run here.
- **Size**: L (storage backend + read-path dispatch + bot cleanup + IaC + docs + tests).

## Plan Relay outcome

Context Scout → (Opus) Planner → adversarial Plan Critic → (Opus) Refiner.
Critic raised three load-bearing corrections, folded in below:

- **R1**: the bot already deletes temp files in a `finally` *regardless* of upload success
  (`index.ts` audio/video finally blocks). So this is NOT "ensure delete after success" —
  switching to failure-retention would add disk pressure with no reaper. Keep finally-delete;
  the "not silently lost" acceptance criterion is already met by the Pack G.1 permanent-failure
  structured log. Orphan sweep is a SIGKILL-before-finally safety net only, scoped tightly.
- **R3**: GCS `download_file_range` must honor the **inclusive** end contract (MinIO uses
  `Range: bytes=start-end`, inclusive; the 206 path builds inclusive `Content-Range`).
  Must also override `get_file_size` to use `blob.size` (reload) — NOT the base-class
  download-the-whole-object default, which the 206 path would hit on every request.
- **Migration**: flipping `STORAGE_BACKEND=gcs` orphans historical MinIO objects because the
  read path uses the *global* backend. media_files already persist per-file `storage_backend`.
  Add **per-file backend dispatch on read** so old MinIO recordings stay playable after cutover.

## In-PR scope

1. **`GCSStorageClient(StorageClient)`** in `services/meeting-api/meeting_api/storage.py`
   - Lazy `from google.cloud import storage` import in `__init__` (mirrors boto3 lazy import),
     so non-GCS deployments don't need the SDK.
   - Auth: ADC (`storage.Client()` picks up Workload Identity / ADC). Config: `GCS_BUCKET`,
     `GCS_PROJECT` (optional), `GCS_SIGNING_SERVICE_ACCOUNT` (optional, for signBlob).
   - Implement full interface: `upload_file`, `download_file`, **`get_file_size` (blob.size, no download)**,
     **`download_file_range` (inclusive end via `download_as_bytes(start,end)`)**,
     `upload_file_path` (stream from disk), `download_file_to_path` (stream to disk),
     `get_presigned_url` (V4, `service_account_email=` + IAM signer; on signer unavailable
     log a WARNING and return `None` → caller falls back to `/raw`, same as local backend),
     `delete_file`, `file_exists` (NotFound→False; other errors propagate so transient 503
     don't become silent 404s), `list_objects` / `list_objects_bounded` (sorted, bounded).
   - Wire `backend == "gcs"` into `create_storage_client()`.
   - Add `google-cloud-storage` to `requirements.txt` + `pyproject.toml`.

2. **Per-file backend dispatch (migration safety)** in `recordings.py`
   - Add a small cached resolver `get_storage_client_for(backend_name)` and use the media_file's
     persisted `storage_backend` on the read/download/delete paths so historical MinIO objects
     remain reachable when the default backend is `gcs`. Default to current backend when absent.

3. **Metadata enrichment** in `recordings.py` media_files entry (additive only)
   - Add `storage_class_policy = "standard_14d_nearline_until_60d"`,
     `delete_after = created_at + 60d` (ISO, advisory/auditable — lifecycle does the real delete),
     `upload_status = "uploaded"`, `content_type`.
   - Confirm no signed URL is persisted (already true: `playback_url` is a stable route, not a
     signed URL — keep it a route; don't regress).

4. **Size logging for cost** in `recording_finalizer.py`
   - Emit one structured line at master finalize with `{bytes, duration_seconds, media_type,
     bytes_per_hour}` for the WebM master (currently size only logged generically in
     `upload_file_path`). Enables the 1h≈?GB cost estimate from the issue.

5. **Bot local cleanup** (`vexa-bot/core/src/services/recording.ts`, `video-recording.ts`, `index.ts`)
   - Keep finally-delete (do not regress to retention). Verify the permanent-failure structured
     log (Pack G.1) covers "not silently lost".
   - Add an **orphan sweep** for stale `/tmp/recording_*` / `/tmp/video_recording_*` / `*_muxed.*`
     files older than a max-meeting-duration threshold, excluding active session UIDs — SIGKILL
     safety net only.

6. **Lifecycle IaC + docs** under `deploy/gcs/`
   - `lifecycle.json`: age 14 → SetStorageClass NEARLINE; age 60 → Delete.
   - `apply-lifecycle.sh`: `gcloud storage buckets update --lifecycle-file=...`.
   - `README.md`: bucket naming/region/permissions, Workload Identity + `signBlob` setup
     (`roles/iam.serviceAccountTokenCreator` on the runtime SA, enable `iamcredentials` API),
     Nearline 30-day-minimum note, chunk-vs-master cost note, cost estimate table.

7. **Config wiring**: `.env.example`, docker-compose, helm env passthrough for `GCS_*`.

8. **Tests** (see verification contract) + **docs update** via doc-update skill.

## Out of scope / non-goals (per issue + critic)

- Creating/applying the real GCS bucket + lifecycle (operator step; documented).
- Deleting chunk objects post-finalization — breaks `recover_recordings_jsonb_from_storage`
  sweep recovery; lifecycle 60d-delete reclaims them. Documented as a follow-up + cost note.
- Google Drive as source of truth; long-lived signed URLs; >60d archival; Coldline/Archive.
