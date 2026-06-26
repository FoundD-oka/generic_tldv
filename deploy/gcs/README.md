# GCS recordings backend (issue #1)

Run meeting recordings on Google Cloud Storage as the source of truth, with a
lifecycle that moves objects to Nearline at 14 days and deletes them at 60 days.

- 0–14 days: **Standard**
- 15–60 days: **Nearline**
- 60 days: **deleted** (automatic)

The app stores a stable storage key + metadata in `meeting.data` — never a
long-lived signed URL. Short-TTL V4 signed URLs are minted on demand at
playback/download, and fall back to the authenticated `/raw` proxy if signing
is unavailable.

## 1. Create the bucket

Choose a region close to where the bot/meeting-api run (egress + latency). A
single-region bucket is fine for this workload.

```bash
gcloud storage buckets create gs://<recordings-bucket> \
  --project=<project> \
  --location=<region> \
  --default-storage-class=STANDARD \
  --uniform-bucket-level-access
```

Naming: pick a stable, project-scoped name, e.g. `vexa-recordings-<env>`.
Keep recordings in their **own** bucket so the lifecycle policy below can't
touch anything else.

## 2. Apply the lifecycle policy

```bash
GCS_BUCKET=<recordings-bucket> ./apply-lifecycle.sh
# or: ./apply-lifecycle.sh <recordings-bucket>
```

This applies [`lifecycle.json`](./lifecycle.json):

- `SetStorageClass NEARLINE` at `age >= 14` (only objects still `STANDARD`)
- `Delete` at `age >= 60`

Nearline has a **30-day minimum storage duration**. Moving to Nearline at day 14
and deleting at day 60 leaves a 46-day Nearline window, so there is no
early-deletion fee. Do **not** push the Nearline transition later (e.g. day 45)
— that risks early-deletion charges and is out of scope (non-goal).

## 3. Auth — no HMAC key in the app (本命案)

The app uses Application Default Credentials (ADC) / Workload Identity. No HMAC
or service-account key file is mounted.

Grant the runtime service account access to the bucket:

```bash
gcloud storage buckets add-iam-policy-binding gs://<recordings-bucket> \
  --member="serviceAccount:<runtime-sa>@<project>.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

### Signed URLs via IAM signBlob

To mint V4 signed URLs without a key file, the runtime SA must be able to sign
on its own behalf:

```bash
# Allow the SA to sign blobs as itself
gcloud iam service-accounts add-iam-policy-binding \
  <runtime-sa>@<project>.iam.gserviceaccount.com \
  --member="serviceAccount:<runtime-sa>@<project>.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"

# Enable the IAM Credentials API (provides signBlob)
gcloud services enable iamcredentials.googleapis.com --project=<project>
```

If signing is **not** configured, `get_presigned_url` logs a warning and returns
`None`; playback then falls back to the authenticated `/raw` proxy through
meeting-api (works, but streams through the service rather than direct-to-GCS).
This is logged, never silent.

## 4. App configuration

Set on meeting-api (and any service that reads recordings):

```bash
STORAGE_BACKEND=gcs
GCS_BUCKET=<recordings-bucket>
GCS_PROJECT=<project>                 # optional; inferred from ADC if omitted
GCS_SIGNING_SERVICE_ACCOUNT=<runtime-sa>@<project>.iam.gserviceaccount.com  # optional; derived from ADC if omitted
```

Migration note: existing recordings written under MinIO/S3 keep their per-file
`storage_backend` in `meeting.data`, and the read path dispatches on that value
— so historical recordings stay playable after the cutover to `gcs`.

## 5. Cost model

Issue sizing: 8 meetings/day × 60 days = up to 480 meetings retained.

| 1h recording size | ~Retained (480 mtgs) |
|---|---|
| 1 GB/h | ~480 GB |
| 2 GB/h | ~960 GB |

Measure the real per-hour size from the finalizer log line
`[FINALIZER] master uploaded (cost): ... size_bytes=… duration_seconds=… bytes_per_hour=…`
and update this table from production data.

**Chunk-vs-master note:** during a meeting the bot uploads per-chunk objects,
and the finalizer writes a single `master.*` next to them. Both live under the
same `recordings/.../` prefix and share this lifecycle, so chunks roughly double
the stored bytes until the 60-day delete reclaims them. Deleting chunks
immediately after master assembly would halve storage, but the recovery sweep
(`recover_recordings_jsonb_from_storage`) depends on chunk presence — so chunk
cleanup is a deliberate **follow-up**, not part of this change.
