#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/backcast-evidence-pack.sh <task-id>

Reads:
  .pipeline/plans/<task-id>/checkpoint-contract.json
  .pipeline/evidence/<task-id>/evidence-manifest.json

Writes:
  .pipeline/evidence/<task-id>/evidence-pack.md
USAGE
}

task_id="${1:-}"
if [[ -z "$task_id" ]]; then
  usage
  exit 2
fi

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$git_root"
fi

python3 - "$task_id" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

task_id = sys.argv[1]
root = pathlib.Path.cwd()
checkpoint_path = root / ".pipeline" / "plans" / task_id / "checkpoint-contract.json"
manifest_path = root / ".pipeline" / "evidence" / task_id / "evidence-manifest.json"
pack_path = root / ".pipeline" / "evidence" / task_id / "evidence-pack.md"


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def rel(path):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def load(path, label):
    if not path.exists():
        fail(f"missing {label}: {rel(path)}")
    return json.loads(path.read_text(encoding="utf-8"))


def status_mark(status):
    return "pass" if status in {"passed", "not_applicable"} else "fail"


checkpoint = load(checkpoint_path, "checkpoint contract")
manifest = load(manifest_path, "evidence manifest")

target_state = checkpoint.get("target_state") if isinstance(checkpoint.get("target_state"), dict) else {}
scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
repo = manifest.get("repo") if isinstance(manifest.get("repo"), dict) else {}
approval = manifest.get("approval") if isinstance(manifest.get("approval"), dict) else {}

lines = [
    f"# Evidence Pack: {manifest.get('checkpoint_id') or task_id}",
    "",
    "## Target State",
    "",
    target_state.get("description", ""),
    "",
    "## Summary",
    "",
    "This pack summarizes the machine-readable Evidence Manifest for checkpoint review.",
    "",
    "## Quality Conditions",
    "",
]

condition_by_id = {
    str(condition.get("id", "")): condition
    for condition in checkpoint.get("quality_conditions", [])
    if isinstance(condition, dict)
}
quality_conditions = manifest.get("quality_conditions", []) or []
if quality_conditions:
    for condition in quality_conditions:
        if not isinstance(condition, dict):
            continue
        condition_id = str(condition.get("id", ""))
        source = condition_by_id.get(condition_id, {})
        label = condition.get("condition") or source.get("condition", "")
        ok_line = condition.get("ok_line") or source.get("ok_line", "")
        lines.append(
            f"- [{status_mark(condition.get('status'))}] {condition_id}: "
            f"{label} -> {ok_line} ({condition.get('status')})"
        )
        evidence = condition.get("evidence") or []
        if evidence:
            lines.append(f"  Evidence: {', '.join(str(item) for item in evidence)}")
else:
    lines.append("- none")

lines.extend(["", "## Acceptance Criteria Status", ""])

ac_by_id = {str(ac.get("id", "")): ac for ac in checkpoint.get("acceptance_criteria", []) if isinstance(ac, dict)}
for ac in manifest.get("acceptance_criteria", []):
    if not isinstance(ac, dict):
        continue
    ac_id = str(ac.get("id", ""))
    text = ac_by_id.get(ac_id, {}).get("text", "")
    lines.append(f"- [{status_mark(ac.get('status'))}] {ac_id}: {text} ({ac.get('status')})")
    evidence = ac.get("evidence") or []
    if evidence:
        lines.append(f"  Evidence: {', '.join(str(item) for item in evidence)}")

lines.extend(
    [
        "",
        "## Evidence Manifest",
        "",
        f"- Manifest: {rel(manifest_path)}",
        f"- Base SHA: {repo.get('base_sha', '')}",
        f"- Head SHA: {repo.get('head_sha', '')}",
        f"- Branch: {repo.get('branch_name', '')}",
        f"- Worktree: {repo.get('worktree_path', '')}",
        "",
        "## Verification Commands",
        "",
        "| Command | Required | Exit Code | Log |",
        "|---|---:|---:|---|",
    ]
)

for command in manifest.get("commands", []):
    if not isinstance(command, dict):
        continue
    lines.append(
        f"| `{command.get('id', '')}` | {str(command.get('required')).lower()} | "
        f"{command.get('exit_code', '')} | `{command.get('log_path', '')}` |"
    )

lines.extend(["", "## Artifacts", "", "| Artifact | Exists | Path |", "|---|---:|---|"])
for artifact in manifest.get("artifacts", []):
    if not isinstance(artifact, dict):
        continue
    lines.append(
        f"| `{artifact.get('id', '')}` | {str(artifact.get('exists')).lower()} | "
        f"`{artifact.get('path', '')}` |"
    )

lines.extend(
    [
        "",
        "## Scope Result",
        "",
        f"- Changed files: {scope.get('changed_file_count', len(scope.get('changed_files', []) or []))}",
        f"- Forbidden paths changed: {len(scope.get('forbidden_paths_changed', []) or [])}",
        f"- Outside allowed paths: {len(scope.get('allowed_paths_outside_with_justification', []) or [])}",
        "",
        "## Missing Evidence",
        "",
    ]
)

missing = manifest.get("missing_evidence", []) or []
if missing:
    lines.extend(f"- {item}" for item in missing)
else:
    lines.append("- none")

lines.extend(
    [
        "",
        "## Decision Needed",
        "",
        f"- Approval state: {approval.get('state', 'pending')}",
        f"- Approval record: {approval.get('decision_path', '')}",
        "- approve / request changes / split smaller / change scope",
        "",
        "## Generated",
        "",
        datetime.now(timezone.utc).isoformat(),
        "",
    ]
)

pack_path.parent.mkdir(parents=True, exist_ok=True)
pack_path.write_text("\n".join(lines), encoding="utf-8")
print(f"wrote {rel(pack_path)}")
PY
