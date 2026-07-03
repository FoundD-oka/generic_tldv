#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/backcast-state.sh <task-id> <next-state> [--reason <text>] [--actor <name>] [--allow-same]

Reads:
  .pipeline/plans/<task-id>/checkpoint-contract.json
  .pipeline/rules/backcast-state-machine.json

Writes:
  .pipeline/plans/<task-id>/checkpoint-contract.json
  .pipeline/gates/<task-id>/state-transition.json
  .pipeline/sessions/<task-id>/state-events.jsonl
USAGE
}

task_id="${1:-}"
next_state="${2:-}"
if [[ -z "$task_id" || -z "$next_state" ]]; then
  usage
  exit 2
fi
shift 2

reason=""
actor="${USER:-harness}"
allow_same=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --reason)
      reason="${2:-}"
      shift 2
      ;;
    --actor)
      actor="${2:-}"
      shift 2
      ;;
    --allow-same)
      allow_same=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$git_root"
fi

python3 - "$task_id" "$next_state" "$reason" "$actor" "$allow_same" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

task_id, next_state, reason, actor, allow_same_raw = sys.argv[1:6]
allow_same = allow_same_raw == "1"
root = pathlib.Path.cwd()
checkpoint_path = root / ".pipeline" / "plans" / task_id / "checkpoint-contract.json"
machine_path = root / ".pipeline" / "rules" / "backcast-state-machine.json"
gate_dir = root / ".pipeline" / "gates" / task_id
session_dir = root / ".pipeline" / "sessions" / task_id
transition_path = gate_dir / "state-transition.json"
events_path = session_dir / "state-events.jsonl"


def rel(path):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


if not checkpoint_path.exists():
    fail(f"missing checkpoint contract: {rel(checkpoint_path)}")
if not machine_path.exists():
    fail(f"missing state machine: {rel(machine_path)}")

checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
machine = json.loads(machine_path.read_text(encoding="utf-8"))
states = set(machine.get("states") or [])
transitions = machine.get("transitions") if isinstance(machine.get("transitions"), dict) else {}

current = checkpoint.get("state") or "checkpoint_approved"
if next_state not in states:
    fail(f"next state is not declared: {next_state}")
if current not in states:
    fail(f"current state is not declared: {current}")

allowed = set(transitions.get(current) or [])
if next_state == current and allow_same:
    allowed.add(next_state)
if next_state not in allowed:
    fail(f"illegal state transition: {current} -> {next_state}")

now = datetime.now(timezone.utc).isoformat()
event = {
    "schema_version": "1.0",
    "task_id": task_id,
    "checkpoint_id": checkpoint.get("checkpoint_id"),
    "from": current,
    "to": next_state,
    "reason": reason,
    "actor": actor,
    "timestamp": now,
}

checkpoint["state"] = next_state
checkpoint.setdefault("state_history", []).append(event)
checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

gate_dir.mkdir(parents=True, exist_ok=True)
session_dir.mkdir(parents=True, exist_ok=True)
transition_path.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
with events_path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(event, ensure_ascii=False) + "\n")

print(f"state {current} -> {next_state}")
print(f"wrote {rel(transition_path)}")
PY
