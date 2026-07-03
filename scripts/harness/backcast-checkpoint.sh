#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/backcast-checkpoint.sh <task-id> \
    --goal <text> \
    --current <text> \
    --target <text> \
    --condition <id::condition::ok-line::command-id> \
    --ac <id::text::command-id> \
    --command <id::command> \
    --allowed <glob> \
    [--forbidden <glob>] \
    [--type implementation|discovery|agreement|risk_reduction|validation|midpoint] \
    [--approval-required] \
    [--max-files <n>]

Writes:
  .pipeline/plans/<task-id>/goal-contract.json
  .pipeline/plans/<task-id>/current-state.md
  .pipeline/plans/<task-id>/checkpoint-contract.json

Notes:
  --goal is the issue/task goal.
  --target is the next quality checkpoint, not the finished issue goal.
  --condition records the minimum OK line for one required condition.
  For KPI Backcast, convert each realistic KPI into one or more --condition
  values after drafting .pipeline/plans/<task-id>/kpi-backcast-roadmap.md.
  --ac is kept as a legacy alias for --condition with condition=text.
USAGE
}

task_id="${1:-}"
if [[ -z "$task_id" ]]; then
  usage
  exit 2
fi
shift

goal=""
current=""
target=""
checkpoint_type="implementation"
approval_required=0
max_files=12
acs=()
commands=()
allowed_paths=()
forbidden_paths=()

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --goal)
      goal="${2:-}"
      shift 2
      ;;
    --current)
      current="${2:-}"
      shift 2
      ;;
    --target)
      target="${2:-}"
      shift 2
      ;;
    --condition)
      acs+=("${2:-}")
      shift 2
      ;;
    --type)
      checkpoint_type="${2:-}"
      shift 2
      ;;
    --ac)
      acs+=("${2:-}")
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

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$git_root"
fi

# Bash 3 treats empty arrays as unbound under `set -u`; allow optional lists to
# be empty while preserving strict mode for the rest of the script.
set +u
python3 - "$task_id" "$goal" "$current" "$target" "$checkpoint_type" "$approval_required" "$max_files" \
  "${acs[@]}" -- "${commands[@]}" -- "${allowed_paths[@]}" -- "${forbidden_paths[@]}" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

task_id, goal, current, target, checkpoint_type, approval_required_raw, max_files_raw = sys.argv[1:8]
rest = sys.argv[8:]


def split_sections(items):
    sections = [[]]
    for item in items:
        if item == "--":
            sections.append([])
        else:
            sections[-1].append(item)
    while len(sections) < 4:
        sections.append([])
    return sections[:4]


acs_raw, commands_raw, allowed_paths, forbidden_paths = split_sections(rest)
approval_required = approval_required_raw == "1"

if not goal:
    print("--goal is required", file=sys.stderr)
    raise SystemExit(2)
if not current:
    print("--current is required", file=sys.stderr)
    raise SystemExit(2)
if not target:
    print("--target is required", file=sys.stderr)
    raise SystemExit(2)
if not acs_raw:
    print("at least one --condition or --ac is required", file=sys.stderr)
    raise SystemExit(2)
if not commands_raw:
    print("at least one --command is required", file=sys.stderr)
    raise SystemExit(2)
if not allowed_paths:
    print("at least one --allowed path is required", file=sys.stderr)
    raise SystemExit(2)
try:
    max_files = int(max_files_raw)
except Exception:
    print("--max-files must be an integer", file=sys.stderr)
    raise SystemExit(2)

root = pathlib.Path.cwd()
plans_dir = root / ".pipeline" / "plans" / task_id
plans_dir.mkdir(parents=True, exist_ok=True)

now = datetime.now(timezone.utc).isoformat()
goal_id = f"{task_id}-goal"
checkpoint_id = f"{task_id}-cp-001"

command_specs = []
command_ids = set()
for raw in commands_raw:
    parts = raw.split("::", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        print(f"invalid --command value, expected id::command: {raw}", file=sys.stderr)
        raise SystemExit(2)
    command_id, command = parts
    command_ids.add(command_id)
    command_specs.append({"id": command_id, "command": command, "required": True})

acceptance_criteria = []
quality_conditions = []
for raw in acs_raw:
    parts = raw.split("::")
    if len(parts) == 3:
        ac_id, quality_line, command_id = parts
        condition = quality_line
    elif len(parts) == 4:
        ac_id, condition, quality_line, command_id = parts
    else:
        print(
            "invalid --condition/--ac value, expected "
            f"id::condition::ok-line::command-id or id::ok-line::command-id: {raw}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if not ac_id or not condition or not quality_line or not command_id:
        print(f"invalid empty field in --condition/--ac value: {raw}", file=sys.stderr)
        raise SystemExit(2)
    if command_id not in command_ids:
        print(f"quality condition {ac_id} references unknown command: {command_id}", file=sys.stderr)
        raise SystemExit(2)
    acceptance_criteria.append(
        {
            "id": ac_id,
            "text": quality_line,
            "condition": condition,
            "quality_line": quality_line,
            "verification": {
                "type": "command",
                "command_id": command_id,
            },
        }
    )
    quality_conditions.append(
        {
            "id": ac_id,
            "condition": condition,
            "ok_line": quality_line,
            "target_state": quality_line,
            "verification": {
                "type": "command",
                "command_id": command_id,
            },
            "progression": {
                "on_pass": "continue_to_next_condition_or_build",
                "on_fail": "fix_condition_or_split_scope",
            },
        }
    )

goal_contract = {
    "schema_version": "1.0",
    "task_id": task_id,
    "goal_id": goal_id,
    "source": {
        "type": "manual",
        "created_at": now,
    },
    "raw_request": [goal],
    "interpreted_goal": {
        "description": goal,
        "business_outcome": [],
    },
    "success_metrics": [
        {
            "id": "sm-001",
            "text": target,
        }
    ],
    "non_goals": [],
    "approval": {
        "state": "pending",
        "required_before_checkpoint_split": False,
        "approver_role": "client_owner",
    },
}

checkpoint = {
    "schema_version": "1.0",
    "task_id": task_id,
    "goal_id": goal_id,
    "checkpoint_id": checkpoint_id,
    "checkpoint_type": checkpoint_type,
    "state": "checkpoint_approved",
    "current_state_summary": [current],
    "target_state": {
        "description": target,
        "target_state_type": "quality_checkpoint",
        "relationship_to_goal": "checkpoint_is_a_quality_line_before_issue_goal",
    },
    "quality_checkpoint": {
        "purpose": (
            "Provide minimum state guarantees for required implementation "
            "conditions so agents can advance without overbuilding."
        ),
        "issue_goal": goal,
        "checkpoint_target": target,
        "not_the_issue_goal": True,
        "conditions_must_be_minimal": True,
    },
    "midpoint_score": {
        "value_distance_reduction": 4,
        "risk_reduction": 3,
        "uncertainty_reduction": 3,
        "agreement_clarity": 4,
        "verification_ease": 4,
        "total": 18,
        "rationale": "Generated by backcast-checkpoint.sh as a thin quality checkpoint.",
    },
    "quality_conditions": quality_conditions,
    "acceptance_criteria": acceptance_criteria,
    "progression_policy": {
        "pass_rule": "Every quality condition must pass before advancing.",
        "loop_guard": (
            "Do not expand design beyond the ok_line while satisfying a condition; "
            "split scope if the condition needs a larger design."
        ),
        "single_simple_feature_may_skip_checkpoint": True,
    },
    "non_goals": [
        {
            "id": "ng-001",
            "text": "Do not implement unrelated parking-lot work.",
        }
    ],
    "blast_radius": {
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
    },
    "execution_bounds": {
        "max_changed_files": max_files,
        "max_review_iterations": 2,
        "require_human_approval_before_build": False,
        "require_human_approval_before_pr": approval_required,
        "require_scope_check_before_pr": True,
        "require_evidence_manifest_before_pr": True,
        "require_clean_worktree_before_start": False,
    },
    "verification_commands": command_specs,
    "evidence_manifest_required": [
        "checkpoint_id",
        "base_sha",
        "head_sha",
        "branch_name",
        "command_results",
        "changed_files",
        "forbidden_path_check",
        "missing_evidence",
        "approval_decision",
    ],
    "parking_lot_policy": {
        "unrelated_findings": "record_only",
        "output_path": f".pipeline/reports/{task_id}-parking-lot.md",
    },
}

condition_lines = [
    f"- {item['id']}: {item['condition']} -> {item['ok_line']}"
    for item in quality_conditions
]

(plans_dir / "goal-contract.json").write_text(
    json.dumps(goal_contract, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
(plans_dir / "current-state.md").write_text(
    "\n".join(
        [
            f"# Current State Report: {goal_id}",
            "",
            "## Known Facts",
            f"- {current}",
            "",
            "## Issue Goal",
            f"- {goal}",
            "",
            "## Suggested Quality Checkpoint",
            f"- {target}",
            "",
            "## Quality Conditions",
            *condition_lines,
            "",
        ]
    ),
    encoding="utf-8",
)
(plans_dir / "checkpoint-contract.json").write_text(
    json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

print(f"wrote .pipeline/plans/{task_id}/goal-contract.json")
print(f"wrote .pipeline/plans/{task_id}/current-state.md")
print(f"wrote .pipeline/plans/{task_id}/checkpoint-contract.json")
PY
status=$?
set -u
exit "$status"
