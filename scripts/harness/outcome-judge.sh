#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/outcome-judge.sh <task-id> [outcome-card-path]

Default outcome card path:
  .pipeline/outcomes/<task-id>/outcome-card.json
USAGE
}

task_id="${1:-}"
if [[ -z "$task_id" ]]; then
  usage
  exit 2
fi

card_path="${2:-.pipeline/outcomes/$task_id/outcome-card.json}"

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$git_root"
fi

if [[ ! -f "$card_path" ]]; then
  echo "missing outcome card: $card_path" >&2
  exit 1
fi

python3 - "$task_id" "$card_path" <<'PY'
import json
import os
import sys

expected_task_id = sys.argv[1]
path = sys.argv[2]

with open(path, "r", encoding="utf-8") as handle:
    card = json.load(handle)

failures = []

if card.get("task_id") != expected_task_id:
    failures.append(f"task_id mismatch: expected {expected_task_id}, got {card.get('task_id')}")

size = card.get("size")
if size not in {"S", "M", "L"}:
    failures.append("size must be S, M, or L")

result = card.get("result") or {}
for key in ("at_pass", "fp_pass", "nft_pass", "hd_resolved"):
    if result.get(key) is not True:
        failures.append(f"result.{key} is not true")

blocking = result.get("blocking_findings")
if not isinstance(blocking, int) or blocking != 0:
    failures.append("result.blocking_findings must be 0")

evidence = card.get("evidence") or {}
verification = evidence.get("verification")
if not isinstance(verification, list) or not verification:
    failures.append("evidence.verification must contain at least one command or artifact")

ledger = evidence.get("session_ledger")
if size in {"M", "L"}:
    if not isinstance(ledger, str) or not ledger:
        failures.append("M/L tasks require evidence.session_ledger")
    elif not os.path.exists(ledger):
        failures.append(f"session ledger does not exist: {ledger}")
    else:
        event_types = []
        try:
            with open(ledger, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    event = json.loads(stripped)
                    event_types.append(event.get("type"))
        except Exception as exc:
            failures.append(f"session ledger is not valid JSONL: {ledger}: {exc}")
            event_types = []

        if not event_types:
            failures.append(f"session ledger has no events: {ledger}")
        if "thread.started" not in event_types:
            failures.append("session ledger missing thread.started event")
        if "turn.completed" not in event_types:
            failures.append("session ledger missing turn.completed event")
        if "turn.failed" in event_types or "error" in event_types:
            failures.append("session ledger contains failed/error event")

if size == "L":
    sidechain = evidence.get("sidechain_synthesis")
    if not isinstance(sidechain, str) or not sidechain:
        failures.append("L tasks require evidence.sidechain_synthesis")
    elif not os.path.exists(sidechain):
        failures.append(f"sidechain synthesis does not exist: {sidechain}")

if failures:
    for failure in failures:
        print(f"outcome fail: {failure}", file=sys.stderr)
    sys.exit(1)

print(f"outcome pass: {expected_task_id}")
PY
