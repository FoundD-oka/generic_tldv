#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/backcast-manifest.sh <task-id> [--base <sha-or-ref>] [--no-run]

Reads:
  .pipeline/plans/<task-id>/checkpoint-contract.json

Writes:
  .pipeline/evidence/<task-id>/evidence-manifest.json
  .pipeline/evidence/<task-id>/logs/<command-id>.log

By default, required verification_commands are executed and their exit codes are
recorded. Use --no-run to collect git/scope/artifact state without running
commands.
USAGE
}

task_id="${1:-}"
if [[ -z "$task_id" ]]; then
  usage
  exit 2
fi
shift

base_ref=""
run_commands=1
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base)
      base_ref="${2:-}"
      if [[ -z "$base_ref" ]]; then
        echo "--base requires a value" >&2
        exit 2
      fi
      shift 2
      ;;
    --no-run)
      run_commands=0
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

python3 - "$task_id" "$base_ref" "$run_commands" <<'PY'
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

task_id = sys.argv[1]
base_ref = sys.argv[2]
run_commands = sys.argv[3] == "1"

root = pathlib.Path.cwd()
checkpoint_path = root / ".pipeline" / "plans" / task_id / "checkpoint-contract.json"
evidence_dir = root / ".pipeline" / "evidence" / task_id
logs_dir = evidence_dir / "logs"
artifacts_dir = evidence_dir / "artifacts"
manifest_path = evidence_dir / "evidence-manifest.json"
approval_path = root / ".pipeline" / "approvals" / task_id / "approval-decision.json"


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def git(args, check=False):
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        fail((proc.stderr or proc.stdout or "").strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip(), proc.returncode


def rel(path):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def normalize_id(value):
    return "".join(ch if ch.isalnum() or ch in "._:-" else "-" for ch in str(value))


def command_for_shell(command):
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command)


if not checkpoint_path.exists():
    fail(f"missing checkpoint contract: {rel(checkpoint_path)}")

checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
if checkpoint.get("task_id") != task_id:
    fail(f"checkpoint task_id mismatch: expected {task_id}, got {checkpoint.get('task_id')}")

evidence_dir.mkdir(parents=True, exist_ok=True)
logs_dir.mkdir(parents=True, exist_ok=True)
artifacts_dir.mkdir(parents=True, exist_ok=True)

head_sha, _ = git(["rev-parse", "HEAD"], check=True)
branch_name, _ = git(["rev-parse", "--abbrev-ref", "HEAD"])
if not base_ref:
    upstream, code = git(["merge-base", "HEAD", "origin/main"])
    base_ref = upstream if code == 0 and upstream else "HEAD"
base_sha, _ = git(["rev-parse", base_ref], check=True)
base_branch = base_ref if base_ref != base_sha else ""

committed_changed, _ = git(["diff", "--name-only", f"{base_sha}..HEAD", "--", ".", ":(exclude).pipeline"])
dirty_changed, _ = git(["diff", "--name-only", "HEAD", "--", ".", ":(exclude).pipeline"])
changed_files = sorted(
    {line for line in (committed_changed + "\n" + dirty_changed).splitlines() if line.strip()}
)

blast_radius = checkpoint.get("blast_radius") if isinstance(checkpoint.get("blast_radius"), dict) else {}
forbidden_paths = [str(item) for item in blast_radius.get("forbidden_paths", [])]
allowed_paths = [str(item) for item in blast_radius.get("allowed_paths", [])]


def path_matches(path, patterns):
    import fnmatch

    normalized = path.replace(os.sep, "/")
    for pattern in patterns:
        candidate = pattern.replace(os.sep, "/")
        if fnmatch.fnmatch(normalized, candidate):
            return True
        if candidate.endswith("/**") and normalized.startswith(candidate[:-3]):
            return True
    return False


forbidden_changed = [path for path in changed_files if path_matches(path, forbidden_paths)]
outside_allowed = []
if allowed_paths:
    outside_allowed = [
        {"path": path, "justification": ""}
        for path in changed_files
        if not path_matches(path, allowed_paths)
    ]

command_results = []
command_by_id = {}
for command_spec in checkpoint.get("verification_commands", []):
    if not isinstance(command_spec, dict):
        continue
    command_id = normalize_id(command_spec.get("id", "command"))
    command = command_for_shell(command_spec.get("command", ""))
    required = bool(command_spec.get("required"))
    log_path = logs_dir / f"{command_id}.log"
    exit_code = None
    started_at = datetime.now(timezone.utc).isoformat()
    finished_at = None
    if run_commands:
        with log_path.open("w", encoding="utf-8") as log:
            log.write(f"$ {command}\n")
            proc = subprocess.run(
                command,
                cwd=root,
                shell=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            exit_code = proc.returncode
        finished_at = datetime.now(timezone.utc).isoformat()
    result = {
        "id": command_id,
        "command": command,
        "required": required,
        "exit_code": exit_code if exit_code is not None else 127,
        "started_at": started_at,
        "finished_at": finished_at,
        "log_path": rel(log_path),
    }
    command_results.append(result)
    command_by_id[command_id] = result

artifact_specs = {}
for ac in checkpoint.get("acceptance_criteria", []):
    if not isinstance(ac, dict):
        continue
    verification = ac.get("verification") if isinstance(ac.get("verification"), dict) else {}
    for artifact_id in verification.get("required_artifacts", []) or []:
        artifact_specs.setdefault(str(artifact_id), set()).add(str(ac.get("id", "")))

artifacts = []
for artifact_id, required_by in sorted(artifact_specs.items()):
    artifact_path = artifacts_dir / artifact_id
    artifacts.append(
        {
            "id": artifact_id,
            "type": "artifact",
            "path": rel(artifact_path),
            "required_by": sorted(required_by),
            "exists": artifact_path.exists(),
        }
    )

missing_evidence = []
ac_statuses = []
artifact_by_id = {item["id"]: item for item in artifacts}
for ac in checkpoint.get("acceptance_criteria", []):
    if not isinstance(ac, dict):
        continue
    ac_id = str(ac.get("id", ""))
    verification = ac.get("verification") if isinstance(ac.get("verification"), dict) else {}
    command_id = verification.get("command_id")
    required_artifacts = verification.get("required_artifacts", []) or []
    evidence_refs = []
    status = "passed"
    if command_id:
        command_id = normalize_id(command_id)
        evidence_refs.append(f"command:{command_id}")
        command_result = command_by_id.get(command_id)
        if command_result is None:
            status = "missing"
            missing_evidence.append(f"{ac_id}: missing command result {command_id}")
        elif command_result.get("exit_code") != 0:
            status = "failed"
            missing_evidence.append(f"{ac_id}: command {command_id} exit_code={command_result.get('exit_code')}")
    for artifact_id in required_artifacts:
        artifact_id = str(artifact_id)
        evidence_refs.append(f"artifact:{artifact_id}")
        artifact = artifact_by_id.get(artifact_id)
        if artifact is None or not artifact.get("exists"):
            status = "missing" if status == "passed" else status
            missing_evidence.append(f"{ac_id}: missing artifact {artifact_id}")
    ac_statuses.append({"id": ac_id, "status": status, "evidence": evidence_refs})

ac_status_by_id = {item["id"]: item for item in ac_statuses}
quality_statuses = []
for condition in checkpoint.get("quality_conditions", []):
    if not isinstance(condition, dict):
        continue
    condition_id = str(condition.get("id", ""))
    ac_status = ac_status_by_id.get(condition_id)
    if ac_status is not None:
        quality_statuses.append(
            {
                "id": condition_id,
                "condition": condition.get("condition", ""),
                "ok_line": condition.get("ok_line", ""),
                "target_state": condition.get("target_state", ""),
                "status": ac_status.get("status", "missing"),
                "evidence": ac_status.get("evidence", []),
            }
        )
        continue

    verification = condition.get("verification") if isinstance(condition.get("verification"), dict) else {}
    command_id = verification.get("command_id")
    required_artifacts = verification.get("required_artifacts", []) or []
    evidence_refs = []
    status = "passed"
    if command_id:
        command_id = normalize_id(command_id)
        evidence_refs.append(f"command:{command_id}")
        command_result = command_by_id.get(command_id)
        if command_result is None:
            status = "missing"
            missing_evidence.append(f"{condition_id}: missing quality condition command result {command_id}")
        elif command_result.get("exit_code") != 0:
            status = "failed"
            missing_evidence.append(
                f"{condition_id}: quality condition command {command_id} "
                f"exit_code={command_result.get('exit_code')}"
            )
    for artifact_id in required_artifacts:
        artifact_id = str(artifact_id)
        evidence_refs.append(f"artifact:{artifact_id}")
        artifact = artifact_by_id.get(artifact_id)
        if artifact is None or not artifact.get("exists"):
            status = "missing" if status == "passed" else status
            missing_evidence.append(f"{condition_id}: missing quality condition artifact {artifact_id}")
    quality_statuses.append(
        {
            "id": condition_id,
            "condition": condition.get("condition", ""),
            "ok_line": condition.get("ok_line", ""),
            "target_state": condition.get("target_state", ""),
            "status": status,
            "evidence": evidence_refs,
        }
    )

for result in command_results:
    if result.get("required") and result.get("exit_code") != 0:
        message = f"required command {result.get('id')} exit_code={result.get('exit_code')}"
        if message not in missing_evidence:
            missing_evidence.append(message)

manifest = {
    "manifest_version": "1.0",
    "task_id": task_id,
    "goal_id": checkpoint.get("goal_id", ""),
    "checkpoint_id": checkpoint.get("checkpoint_id"),
    "repo": {
        "base_branch": base_branch,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "branch_name": branch_name,
        "worktree_path": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    },
    "commands": command_results,
    "artifacts": artifacts,
    "scope": {
        "changed_files": changed_files,
        "forbidden_paths_changed": forbidden_changed,
        "allowed_paths_outside_with_justification": outside_allowed,
        "changed_file_count": len(changed_files),
    },
    "quality_conditions": quality_statuses,
    "acceptance_criteria": ac_statuses,
    "missing_evidence": missing_evidence,
    "approval": {
        "state": "pending",
        "decision_path": rel(approval_path),
    },
}

manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {rel(manifest_path)}")
if missing_evidence or forbidden_changed:
    print("manifest has blocking evidence gaps; run backcast-validate for details", file=sys.stderr)
    raise SystemExit(1)
PY
