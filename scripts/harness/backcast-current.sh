#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/backcast-current.sh update <task-id> [--summary <text>] [--actor <name>]

Reads:
  .pipeline/plans/<task-id>/checkpoint-contract.json
  .pipeline/evidence/<task-id>/evidence-manifest.json
  .pipeline/approvals/<task-id>/approval-decision.json

Writes:
  .pipeline/current/<goal-id>.json
  .pipeline/current/latest.json
  .pipeline/sessions/<task-id>/current-events.jsonl

Only approved or manual_override checkpoints can become Current.
USAGE
}

cmd="${1:-}"
task_id="${2:-}"
if [[ "$cmd" != "update" || -z "$task_id" ]]; then
  usage
  exit 2
fi
shift 2

summary=""
actor="${USER:-harness}"
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --summary)
      summary="${2:-}"
      shift 2
      ;;
    --actor)
      actor="${2:-}"
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

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$git_root"
fi

python3 - "$task_id" "$summary" "$actor" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

task_id, summary, actor = sys.argv[1:4]
root = pathlib.Path.cwd()
plans_dir = root / ".pipeline" / "plans" / task_id
evidence_dir = root / ".pipeline" / "evidence" / task_id
approval_dir = root / ".pipeline" / "approvals" / task_id
current_dir = root / ".pipeline" / "current"
session_dir = root / ".pipeline" / "sessions" / task_id

checkpoint_path = plans_dir / "checkpoint-contract.json"
manifest_path = evidence_dir / "evidence-manifest.json"
approval_path = approval_dir / "approval-decision.json"


def rel(path):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def load_json(path, label):
    if not path.exists():
        fail(f"missing {label}: {rel(path)}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"{label} is not valid JSON: {rel(path)}: {exc}")


checkpoint = load_json(checkpoint_path, "checkpoint contract")
manifest = load_json(manifest_path, "evidence manifest")
approval = load_json(approval_path, "approval decision")

decision = approval.get("decision")
if decision not in {"approved", "manual_override"}:
    fail(f"approval decision is not current-eligible: {decision}")
if checkpoint.get("task_id") != task_id:
    fail(f"checkpoint task_id mismatch: expected {task_id}, got {checkpoint.get('task_id')}")
if manifest.get("task_id") != task_id:
    fail(f"manifest task_id mismatch: expected {task_id}, got {manifest.get('task_id')}")
if approval.get("task_id") != task_id:
    fail(f"approval task_id mismatch: expected {task_id}, got {approval.get('task_id')}")

checkpoint_id = checkpoint.get("checkpoint_id")
goal_id = checkpoint.get("goal_id") or f"{task_id}-goal"
if manifest.get("checkpoint_id") != checkpoint_id:
    fail(
        "manifest checkpoint_id mismatch: "
        f"expected {checkpoint_id}, got {manifest.get('checkpoint_id')}"
    )
if approval.get("checkpoint_id") != checkpoint_id:
    fail(
        "approval checkpoint_id mismatch: "
        f"expected {checkpoint_id}, got {approval.get('checkpoint_id')}"
    )

repo = manifest.get("repo") if isinstance(manifest.get("repo"), dict) else {}
target_state = checkpoint.get("target_state") if isinstance(checkpoint.get("target_state"), dict) else {}
quality_conditions = [
    item for item in manifest.get("quality_conditions", [])
    if isinstance(item, dict)
]
now = datetime.now(timezone.utc).isoformat()
summary = summary or target_state.get("description") or f"checkpoint {checkpoint_id} approved"

current = {
    "schema_version": "1.0",
    "goal_id": goal_id,
    "current_task_id": task_id,
    "current_checkpoint_id": checkpoint_id,
    "summary": summary,
    "updated_at": now,
    "updated_by": actor,
    "repo": {
        "head_sha": repo.get("head_sha", ""),
        "branch_name": repo.get("branch_name", ""),
        "worktree_path": repo.get("worktree_path", ""),
    },
    "source": {
        "checkpoint_contract": rel(checkpoint_path),
        "evidence_manifest": rel(manifest_path),
        "approval_decision": rel(approval_path),
        "decision": decision,
    },
    "satisfied_quality_conditions": quality_conditions,
    "next_checkpoint": {
        "status": "not_drafted",
        "path": "",
    },
}

current_dir.mkdir(parents=True, exist_ok=True)
session_dir.mkdir(parents=True, exist_ok=True)
goal_current_path = current_dir / f"{goal_id}.json"
latest_path = current_dir / "latest.json"
event_path = session_dir / "current-events.jsonl"
goal_current_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
latest_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
with event_path.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({
        "schema_version": "1.0",
        "event": "current_updated",
        "task_id": task_id,
        "goal_id": goal_id,
        "checkpoint_id": checkpoint_id,
        "summary": summary,
        "timestamp": now,
        "actor": actor,
        "current_path": rel(goal_current_path),
    }, ensure_ascii=False) + "\n")

print(f"wrote {rel(goal_current_path)}")
print(f"wrote {rel(latest_path)}")
PY

state_script="scripts/harness/backcast-state.sh"
if [[ -x "$state_script" ]]; then
  "$state_script" "$task_id" approved --allow-same --reason "current update source checkpoint" >/dev/null 2>&1 || true
fi
