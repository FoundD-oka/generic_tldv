#!/usr/bin/env bash
set -euo pipefail

# Persist the current harness task ID to .pipeline/current/task-id.
# All hooks read this file as the HARNESS_TASK_ID fallback so you do not
# need to prefix every command with HARNESS_TASK_ID=<id>.
#
# Usage:
#   scripts/harness/task-set.sh <task-id>   # set current task
#   scripts/harness/task-set.sh             # show current task

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
TID_FILE="${ROOT}/.pipeline/current/task-id"

if [ "${1:-}" = "" ]; then
  if [ -f "$TID_FILE" ]; then
    echo "[harness] current task: $(cat "$TID_FILE")"
  else
    echo "[harness] no current task set"
  fi
  exit 0
fi

TASK_ID="${1}"
mkdir -p "${ROOT}/.pipeline/current"
printf '%s' "$TASK_ID" > "$TID_FILE"
echo "[harness] task set to: $TASK_ID"
