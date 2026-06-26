#!/usr/bin/env bash
#
# Issue #1 — apply the recordings-bucket lifecycle policy (14d Standard ->
# Nearline, delete at 60d) to a GCS bucket.
#
# This is an OPERATOR step, run from a workstation/CI with gcloud auth — it is
# NOT executed by the application. Idempotent: re-running re-applies the same
# policy.
#
# Usage:
#   GCS_BUCKET=my-recordings-bucket ./apply-lifecycle.sh
#   ./apply-lifecycle.sh my-recordings-bucket
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIFECYCLE_FILE="${LIFECYCLE_FILE:-$SCRIPT_DIR/lifecycle.json}"
BUCKET="${1:-${GCS_BUCKET:-}}"

if [[ -z "$BUCKET" ]]; then
  echo "ERROR: bucket name required. Set GCS_BUCKET or pass as arg 1." >&2
  exit 2
fi
if [[ ! -f "$LIFECYCLE_FILE" ]]; then
  echo "ERROR: lifecycle file not found: $LIFECYCLE_FILE" >&2
  exit 2
fi
if ! command -v gcloud >/dev/null 2>&1; then
  echo "ERROR: gcloud CLI not found on PATH." >&2
  exit 2
fi

echo "Applying lifecycle policy from $LIFECYCLE_FILE to gs://$BUCKET ..."
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file="$LIFECYCLE_FILE"

echo "Done. Current lifecycle:"
gcloud storage buckets describe "gs://$BUCKET" --format="value(lifecycle)"
