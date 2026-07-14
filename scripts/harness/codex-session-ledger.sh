#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/codex-session-ledger.sh [--append] <task-id> -- <codex exec args>
  scripts/harness/codex-session-ledger.sh record <task-id> [--profile codex-app] [--status succeeded|failed] [--summary <text>]

Examples:
  scripts/harness/codex-session-ledger.sh issue-12 -- --sandbox workspace-write "Fix the failing test"
  scripts/harness/codex-session-ledger.sh record issue-12 --profile codex-app --status succeeded --summary "実装と検証が完了"

The record form writes a normalized ledger for work already performed by an
interactive runtime. It never starts another model or Codex CLI process.
USAGE
}

validate_task_id() {
  if [[ ! "$1" =~ ^[A-Za-z0-9._:-]+$ ]]; then
    echo "task id contains unsupported characters: $1" >&2
    exit 2
  fi
}

if [[ "${1:-}" == "record" ]]; then
  shift
  task_id="${1:-}"
  if [[ -z "$task_id" ]]; then usage; exit 2; fi
  validate_task_id "$task_id"
  shift

  runtime_profile="codex-app"
  run_status="succeeded"
  summary=""
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --profile) runtime_profile="${2:-}"; shift 2 ;;
      --status) run_status="${2:-}"; shift 2 ;;
      --summary) summary="${2:-}"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) echo "unknown record argument: $1" >&2; usage; exit 2 ;;
    esac
  done
  if [[ -z "$runtime_profile" ]]; then
    echo "--profile requires a value" >&2
    exit 2
  fi
  if [[ "$run_status" != "succeeded" && "$run_status" != "failed" ]]; then
    echo "--status must be succeeded or failed" >&2
    exit 2
  fi

  if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then cd "$git_root"; fi
  python3 - "$task_id" "$runtime_profile" "$run_status" "$summary" <<'PY'
import json
import os
import pathlib
import sys
import uuid
from datetime import datetime, timezone

task_id, runtime_profile, run_status, summary = sys.argv[1:5]
root = pathlib.Path.cwd()
session_dir = root / ".pipeline" / "sessions" / task_id
events_path = session_dir / "events.jsonl"
meta_path = session_dir / "session.json"
session_dir.mkdir(parents=True, exist_ok=True)

events = []
if events_path.exists():
    for line_number, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception as exc:
            raise SystemExit(f"invalid JSONL at {events_path}:{line_number}: {exc}")
        if not isinstance(event, dict):
            raise SystemExit(f"event must be an object at {events_path}:{line_number}")
        events.append(event)

now = datetime.now(timezone.utc).isoformat()
run_id = str(uuid.uuid4())
sequence = max(
    [len(events), *[
        event.get("sequence", 0)
        for event in events
        if isinstance(event.get("sequence"), int)
    ]]
)
new_events = []
if not any(event.get("type") == "thread.started" for event in events):
    sequence += 1
    new_events.append({
        "schema_version": "2.0",
        "type": "thread.started",
        "task_id": task_id,
        "runtime_profile": runtime_profile,
        "timestamp": now,
        "sequence": sequence,
        "run_id": run_id,
        "status": "started",
    })
sequence += 1
terminal_type = "turn.completed" if run_status == "succeeded" else "turn.failed"
terminal = {
    "schema_version": "2.0",
    "type": terminal_type,
    "task_id": task_id,
    "runtime_profile": runtime_profile,
    "timestamp": now,
    "sequence": sequence,
    "run_id": run_id,
    "status": run_status,
}
if summary:
    terminal["summary"] = summary
new_events.append(terminal)
events.extend(new_events)

tmp_path = events_path.with_suffix(".tmp")
tmp_path.write_text(
    "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
    encoding="utf-8",
)
os.replace(tmp_path, events_path)

timestamps = [
    event.get("timestamp") or event.get("at")
    for event in events
    if event.get("timestamp") or event.get("at")
]
meta = {
    "schema_version": "2.0",
    "task_id": task_id,
    "runtime_profile": runtime_profile,
    "started_at": timestamps[0] if timestamps else now,
    "completed_at": now,
    "status": run_status,
    "event_count": len(events),
    "events": str(events_path.relative_to(root)),
    "latest_run_id": run_id,
}
meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"session ledger written: {events_path.relative_to(root)}", file=sys.stderr)
PY
  [[ "$run_status" == "succeeded" ]]
  exit $?
fi

append=0
if [[ "${1:-}" == "--append" ]]; then append=1; shift; fi

task_id="${1:-}"
if [[ -z "$task_id" ]]; then usage; exit 2; fi
validate_task_id "$task_id"
shift
if [[ "${1:-}" == "--" ]]; then shift; fi
if [[ "$#" -eq 0 ]]; then usage; exit 2; fi

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then cd "$git_root"; fi

session_dir=".pipeline/sessions/$task_id"
events_path="$session_dir/events.jsonl"
session_meta_path="$session_dir/session.json"
tmp_path="$session_dir/events.tmp.$$"
mkdir -p "$session_dir"

if [[ -e "$events_path" && "$append" -eq 0 ]]; then
  echo "session ledger already exists: $events_path" >&2
  echo "rerun with --append to append another Codex run" >&2
  exit 1
fi

started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
status=0
codex exec --json "$@" | tee "$tmp_path" || status=$?
completed_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
run_status="succeeded"
if [[ "$status" -ne 0 ]]; then run_status="failed"; fi

python3 - "$task_id" "$started_at" "$completed_at" "$run_status" "$status" "$events_path" "$tmp_path" "$session_meta_path" "$append" <<'PY'
import json
import os
import pathlib
import sys
import uuid

task_id, started_at, completed_at, run_status, exit_status, events_raw, tmp_raw, meta_raw, append_raw = sys.argv[1:10]
events_path = pathlib.Path(events_raw)
tmp_path = pathlib.Path(tmp_raw)
meta_path = pathlib.Path(meta_raw)
append = append_raw == "1"

def read_events(path):
    events = []
    if not path.exists():
        return events
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception as exc:
            raise SystemExit(f"invalid JSONL at {path}:{line_number}: {exc}")
        if not isinstance(event, dict):
            raise SystemExit(f"event must be an object at {path}:{line_number}")
        events.append(event)
    return events

new_events = read_events(tmp_path)
if not new_events:
    tmp_path.unlink(missing_ok=True)
    raise SystemExit("codex produced no JSONL events; no session ledger written")
existing_events = read_events(events_path) if append else []
all_events = existing_events + new_events

previous = {}
if meta_path.exists():
    try:
        previous = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        previous = {}
runs = previous.get("runs") if isinstance(previous.get("runs"), list) else []
runs.append({
    "run_id": str(uuid.uuid4()),
    "started_at": started_at,
    "completed_at": completed_at,
    "status": run_status,
    "exit_status": int(exit_status),
    "event_count": len(new_events),
})

combined_tmp = events_path.with_suffix(".combined.tmp")
combined_tmp.write_text(
    "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in all_events),
    encoding="utf-8",
)
os.replace(combined_tmp, events_path)
tmp_path.unlink(missing_ok=True)

meta = {
    "schema_version": "1.1",
    "task_id": task_id,
    "runtime_profile": "codex-cli",
    "started_at": previous.get("started_at") or started_at,
    "completed_at": completed_at,
    "status": run_status,
    "exit_status": int(exit_status),
    "event_count": len(all_events),
    "events": str(events_path),
    "runs": runs,
}
meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

echo "session ledger written: $events_path" >&2
exit "$status"
