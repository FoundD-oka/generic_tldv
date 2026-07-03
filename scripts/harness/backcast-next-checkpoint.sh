#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/backcast-next-checkpoint.sh <source-task-id> \
    --next-task <task-id> \
    --target <text> \
    --condition <id::condition::ok-line::command-id> \
    --command <id::command> \
    --allowed <glob> \
    [--forbidden <glob>] \
    [--type implementation|discovery|agreement|risk_reduction|validation|midpoint] \
    [--approval-required] \
    [--max-files <n>]

Reads:
  .pipeline/current/latest.json or .pipeline/current/<goal-id>.json
  .pipeline/plans/<source-task-id>/goal-contract.json

Writes the next checkpoint draft through backcast-checkpoint.sh and marks the
Current record with the draft path.
USAGE
}

source_task="${1:-}"
if [[ -z "$source_task" ]]; then
  usage
  exit 2
fi
shift

next_task=""
target=""
checkpoint_type="implementation"
approval_required=0
max_files=""
conditions=()
commands=()
allowed_paths=()
forbidden_paths=()

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --next-task)
      next_task="${2:-}"
      shift 2
      ;;
    --target)
      target="${2:-}"
      shift 2
      ;;
    --condition)
      conditions+=("${2:-}")
      shift 2
      ;;
    --command)
      commands+=("${2:-}")
      shift 2
      ;;
    --allowed)
      allowed_paths+=("${2:-}")
      shift 2
      ;;
    --forbidden)
      forbidden_paths+=("${2:-}")
      shift 2
      ;;
    --type)
      checkpoint_type="${2:-}"
      shift 2
      ;;
    --approval-required)
      approval_required=1
      shift
      ;;
    --max-files)
      max_files="${2:-}"
      shift 2
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

if [[ -z "$next_task" || -z "$target" ]]; then
  echo "--next-task and --target are required" >&2
  usage
  exit 2
fi
if [[ "${#conditions[@]}" -eq 0 || "${#commands[@]}" -eq 0 || "${#allowed_paths[@]}" -eq 0 ]]; then
  echo "at least one --condition, --command, and --allowed are required" >&2
  usage
  exit 2
fi

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$git_root"
fi

readarray_support=0
if (eval 'tmp=(); readarray -t tmp < /dev/null') >/dev/null 2>&1; then
  readarray_support=1
fi

current_json="$(python3 - "$source_task" <<'PY'
import json
import pathlib
import sys

source_task = sys.argv[1]
root = pathlib.Path.cwd()
latest = root / ".pipeline" / "current" / "latest.json"
if not latest.exists():
    raise SystemExit(f"missing current record: {latest.relative_to(root)}")
current = json.loads(latest.read_text(encoding="utf-8"))
if current.get("current_task_id") != source_task:
    raise SystemExit(
        "latest current record does not match source task: "
        f"{current.get('current_task_id')} != {source_task}"
    )
goal_id = current.get("goal_id") or f"{source_task}-goal"
goal_path = root / ".pipeline" / "plans" / source_task / "goal-contract.json"
goal = {}
if goal_path.exists():
    goal = json.loads(goal_path.read_text(encoding="utf-8"))
goal_text = ""
interpreted = goal.get("interpreted_goal") if isinstance(goal.get("interpreted_goal"), dict) else {}
if interpreted.get("description"):
    goal_text = interpreted["description"]
elif isinstance(goal.get("raw_request"), list) and goal["raw_request"]:
    goal_text = str(goal["raw_request"][0])
elif current.get("summary"):
    goal_text = current["summary"]
print(json.dumps({
    "goal_id": goal_id,
    "goal": goal_text or f"continue goal {goal_id}",
    "current": current.get("summary", ""),
}, ensure_ascii=False))
PY
)"

goal_text="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["goal"])' "$current_json")"
current_text="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["current"])' "$current_json")"

args=(
  "$next_task"
  --goal "$goal_text"
  --current "$current_text"
  --target "$target"
  --type "$checkpoint_type"
)
for condition in "${conditions[@]}"; do
  args+=(--condition "$condition")
done
for command in "${commands[@]}"; do
  args+=(--command "$command")
done
for allowed in "${allowed_paths[@]}"; do
  args+=(--allowed "$allowed")
done
# Bash 3 treats empty arrays as unbound under `set -u`.
set +u
for forbidden in "${forbidden_paths[@]}"; do
  args+=(--forbidden "$forbidden")
done
set -u
if [[ "$approval_required" -eq 1 ]]; then
  args+=(--approval-required)
fi
if [[ -n "$max_files" ]]; then
  args+=(--max-files "$max_files")
fi

scripts/harness/backcast-checkpoint.sh "${args[@]}"

python3 - "$source_task" "$next_task" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

source_task, next_task = sys.argv[1:3]
root = pathlib.Path.cwd()
latest = root / ".pipeline" / "current" / "latest.json"
current = json.loads(latest.read_text(encoding="utf-8"))
next_path = root / ".pipeline" / "plans" / next_task / "checkpoint-contract.json"
current.setdefault("next_checkpoint", {})
current["next_checkpoint"].update({
    "status": "drafted",
    "task_id": next_task,
    "path": str(next_path.relative_to(root)),
    "drafted_at": datetime.now(timezone.utc).isoformat(),
})
goal_id = current.get("goal_id") or f"{source_task}-goal"
goal_current = root / ".pipeline" / "current" / f"{goal_id}.json"
latest.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
goal_current.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"next checkpoint draft: {next_path.relative_to(root)}")
PY

state_script="scripts/harness/backcast-state.sh"
if [[ -x "$state_script" ]]; then
  "$state_script" "$source_task" checkpoint_drafted --reason "next checkpoint drafted: $next_task" >/dev/null 2>&1 || true
fi
