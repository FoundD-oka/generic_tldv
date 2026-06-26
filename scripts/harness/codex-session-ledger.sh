#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/codex-session-ledger.sh [--append] <task-id> -- <codex exec args>

Examples:
  scripts/harness/codex-session-ledger.sh issue-12 -- --sandbox workspace-write "Fix the failing test"
USAGE
}

append=0
if [[ "${1:-}" == "--append" ]]; then
  append=1
  shift
fi

task_id="${1:-}"
if [[ -z "$task_id" ]]; then
  usage
  exit 2
fi
if [[ ! "$task_id" =~ ^[A-Za-z0-9._:-]+$ ]]; then
  echo "task id contains unsupported characters: $task_id" >&2
  exit 2
fi
shift

if [[ "${1:-}" == "--" ]]; then
  shift
fi

if [[ "$#" -eq 0 ]]; then
  usage
  exit 2
fi

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$git_root"
fi

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

event_count="$(python3 -c '
import json
import sys

path = sys.argv[1]
event_count = 0
with open(path, "r", encoding="utf-8") as handle:
    for line_number, line in enumerate(handle, 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            json.loads(stripped)
        except Exception as exc:
            print(f"invalid JSONL at {path}:{line_number}: {exc}", file=sys.stderr)
            sys.exit(1)
        event_count += 1

print(event_count)
' "$tmp_path")"

if [[ "$event_count" -eq 0 ]]; then
  rm -f "$tmp_path"
  echo "codex produced no JSONL events; no session ledger written" >&2
  exit 1
fi

if [[ "$append" -eq 1 && -e "$events_path" ]]; then
  cat "$tmp_path" >> "$events_path"
  rm -f "$tmp_path"
else
  mv "$tmp_path" "$events_path"
fi

completed_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
run_status="succeeded"
if [[ "$status" -ne 0 ]]; then
  run_status="failed"
fi

printf '{\n  "schema_version": "1.0",\n  "task_id": "%s",\n  "runtime_profile": "codex-cli",\n  "started_at": "%s",\n  "completed_at": "%s",\n  "status": "%s",\n  "exit_status": %s,\n  "event_count": %s,\n  "events": "%s"\n}\n' \
  "$task_id" "$started_at" "$completed_at" "$run_status" "$status" "$event_count" "$events_path" > "$session_meta_path"

echo "session ledger written: $events_path" >&2
exit "$status"
