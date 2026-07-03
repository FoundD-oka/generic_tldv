#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/backcast-approval.sh <task-id> <decision> [--approver <name>] [--role <role>] [--notes <text>]

Decisions:
  approved
  rejected
  request_changes
  needs_resplit
  scope_change_requested
  manual_override

Reads:
  .pipeline/evidence/<task-id>/evidence-manifest.json
  .pipeline/evidence/<task-id>/evidence-pack.md

Writes:
  .pipeline/approvals/<task-id>/approval-decision.json

When possible, also updates manifest.approval.state so the machine manifest and
approval decision stay aligned.
USAGE
}

task_id="${1:-}"
decision="${2:-}"
if [[ -z "$task_id" || -z "$decision" ]]; then
  usage
  exit 2
fi
shift 2

approver=""
role="client_owner"
notes=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --approver)
      approver="${2:-}"
      if [[ -z "$approver" ]]; then
        echo "--approver requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --role)
      role="${2:-}"
      if [[ -z "$role" ]]; then
        echo "--role requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --notes)
      notes="${2:-}"
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

python3 - "$task_id" "$decision" "$approver" "$role" "$notes" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

task_id, decision, approver_name, approver_role, notes = sys.argv[1:6]
allowed = {
    "approved",
    "rejected",
    "request_changes",
    "needs_resplit",
    "scope_change_requested",
    "manual_override",
}
if decision not in allowed:
    print(f"unsupported decision: {decision}", file=sys.stderr)
    raise SystemExit(2)

root = pathlib.Path.cwd()
manifest_path = root / ".pipeline" / "evidence" / task_id / "evidence-manifest.json"
pack_path = root / ".pipeline" / "evidence" / task_id / "evidence-pack.md"
approval_dir = root / ".pipeline" / "approvals" / task_id
approval_path = approval_dir / "approval-decision.json"


def rel(path):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


if not manifest_path.exists():
    print(f"missing evidence manifest: {rel(manifest_path)}", file=sys.stderr)
    raise SystemExit(1)
if not pack_path.exists():
    print(f"missing evidence pack: {rel(pack_path)}", file=sys.stderr)
    raise SystemExit(1)

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
checkpoint_id = manifest.get("checkpoint_id")
if not checkpoint_id:
    print("manifest missing checkpoint_id", file=sys.stderr)
    raise SystemExit(1)

approved_at = None
if decision in {"approved", "manual_override"}:
    approved_at = datetime.now(timezone.utc).isoformat()
    if not approver_name:
        print(f"{decision} requires --approver", file=sys.stderr)
        raise SystemExit(2)
if decision == "manual_override" and not notes:
    print("manual_override requires --notes", file=sys.stderr)
    raise SystemExit(2)

approval = {
    "approval_id": f"approval-{checkpoint_id}",
    "task_id": task_id,
    "checkpoint_id": checkpoint_id,
    "decision": decision,
    "approver": {
        "name": approver_name,
        "role": approver_role,
        "approved_at": approved_at,
    },
    "reviewed_evidence": {
        "manifest_path": rel(manifest_path),
        "pack_path": rel(pack_path),
    },
    "conditions": [],
    "notes": notes,
}

approval_dir.mkdir(parents=True, exist_ok=True)
approval_path.write_text(json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

manifest.setdefault("approval", {})
manifest["approval"]["state"] = decision
manifest["approval"]["decision_path"] = rel(approval_path)
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print(f"wrote {rel(approval_path)}")
PY

state_script="scripts/harness/backcast-state.sh"
if [[ -x "$state_script" ]]; then
  target_state="$decision"
  case "$decision" in
    request_changes)
      target_state="rejected"
      ;;
  esac
  "$state_script" "$task_id" awaiting_approval --allow-same --reason "approval decision recorded" >/dev/null 2>&1 || true
  "$state_script" "$task_id" "$target_state" --reason "approval decision: $decision" >/dev/null 2>&1 || true
fi
