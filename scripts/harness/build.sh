#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/build.sh <task-id> [--worktree <path>] [--commit-message <text>] [--no-commit] [--no-verify] [--no-pack] [--no-state] -- <build command>

Runs the build command inside the chosen checkout, then collects evidence through
backcast-manifest.sh. This is the thin Phase-1 runner; Codex or another agent can
be placed behind the build command later.

Writes:
  .pipeline/evidence/<task-id>/build/build.log
  .pipeline/evidence/<task-id>/build/build-summary.json
  .pipeline/evidence/<task-id>/evidence-manifest.json
  .pipeline/evidence/<task-id>/evidence-pack.md
USAGE
}

task_id="${1:-}"
if [[ -z "$task_id" ]]; then
  usage
  exit 2
fi
shift

worktree=""
run_verify=1
run_pack=1
run_state=1
auto_commit=1
commit_message="harness: build $task_id"
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --worktree)
      worktree="${2:-}"
      shift 2
      ;;
    --no-verify)
      run_verify=0
      shift
      ;;
    --no-pack)
      run_pack=0
      shift
      ;;
    --commit-message)
      commit_message="${2:-}"
      shift 2
      ;;
    --no-commit)
      auto_commit=0
      shift
      ;;
    --no-state)
      run_state=0
      shift
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument before --: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ "$#" -eq 0 ]]; then
  echo "build command is required after --" >&2
  usage
  exit 2
fi

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$git_root"
else
  echo "not inside a git repository" >&2
  exit 1
fi

root="$(pwd)"
if [[ -z "$worktree" && -f ".pipeline/worktrees/$task_id/worktree.json" ]]; then
  worktree="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("path",""))' ".pipeline/worktrees/$task_id/worktree.json")"
fi
if [[ -z "$worktree" ]]; then
  worktree="$root"
fi
if [[ "$worktree" != /* ]]; then
  worktree="$root/$worktree"
fi
if [[ ! -d "$worktree/.git" && ! -f "$worktree/.git" ]]; then
  echo "worktree is not a git checkout: $worktree" >&2
  exit 1
fi

mkdir -p "$root/.pipeline/evidence/$task_id/build"
mkdir -p "$worktree/.pipeline/evidence/$task_id/build"
build_log="$worktree/.pipeline/evidence/$task_id/build/build.log"
summary="$worktree/.pipeline/evidence/$task_id/build/build-summary.json"
command_text="$*"

state_script="$worktree/scripts/harness/backcast-state.sh"
if [[ ! -x "$state_script" ]]; then
  state_script="$root/scripts/harness/backcast-state.sh"
fi

if [[ "$run_state" = "1" && -x "$state_script" ]]; then
  (cd "$worktree" && "$state_script" "$task_id" planned --allow-same --reason "build runner started") >/dev/null 2>&1 || true
  (cd "$worktree" && "$state_script" "$task_id" build_authorized --reason "build command accepted") >/dev/null 2>&1 || true
  (cd "$worktree" && "$state_script" "$task_id" worktree_created --reason "worktree selected: $worktree") >/dev/null 2>&1 || true
  (cd "$worktree" && "$state_script" "$task_id" building --reason "running build command") >/dev/null 2>&1 || true
fi

started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
set +e
(
  cd "$worktree"
  printf '$ %s\n' "$command_text"
  "$@"
) >"$build_log" 2>&1
build_exit=$?
set -e
finished_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

python3 - "$task_id" "$worktree" "$command_text" "$build_exit" "$started_at" "$finished_at" "$build_log" "$summary" <<'PY'
import json
import pathlib
import subprocess
import sys

task_id, worktree, command, exit_code, started_at, finished_at, log_path, summary_path = sys.argv[1:9]
worktree_path = pathlib.Path(worktree)
root = pathlib.Path.cwd()


def git(args):
    proc = subprocess.run(["git", *args], cwd=worktree_path, capture_output=True, text=True)
    return proc.stdout.strip(), proc.returncode


head, _ = git(["rev-parse", "HEAD"])
branch, _ = git(["rev-parse", "--abbrev-ref", "HEAD"])
status, _ = git(["status", "--short"])
payload = {
    "schema_version": "1.0",
    "task_id": task_id,
    "worktree_path": str(worktree_path),
    "branch_name": branch,
    "head_sha": head,
    "command": command,
    "exit_code": int(exit_code),
    "started_at": started_at,
    "finished_at": finished_at,
    "log_path": str(pathlib.Path(log_path).relative_to(root)),
    "dirty": bool(status),
    "status": "passed" if int(exit_code) == 0 else "failed",
}
path = pathlib.Path(summary_path)
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {path.relative_to(root)}")
PY

if [[ "$build_exit" -ne 0 ]]; then
  if [[ "$run_state" = "1" && -x "$state_script" ]]; then
    (cd "$worktree" && "$state_script" "$task_id" blocked --reason "build command failed: $build_exit") >/dev/null 2>&1 || true
  fi
  echo "build failed with exit code $build_exit; see $build_log" >&2
  exit "$build_exit"
fi

if [[ "$run_state" = "1" && -x "$state_script" ]]; then
  (cd "$worktree" && "$state_script" "$task_id" built --reason "build command passed") >/dev/null 2>&1 || true
fi

if [[ "$auto_commit" = "1" ]]; then
  implementation_status="$(cd "$worktree" && git status --short -- . ':(exclude).pipeline' ':(exclude).gitnexus' || true)"
  if [[ -n "$implementation_status" ]]; then
    (
      cd "$worktree"
      git add -A -- . ':(exclude).pipeline' ':(exclude).gitnexus'
      git commit -m "$commit_message"
    ) >>"$build_log" 2>&1
  fi
fi

if [[ "$run_verify" = "1" ]]; then
  manifest_script="$root/scripts/harness/backcast-manifest.sh"
  if [[ ! -x "$manifest_script" ]]; then
    manifest_script="$worktree/scripts/harness/backcast-manifest.sh"
  fi
  if [[ "$run_state" = "1" && -x "$state_script" ]]; then
    (cd "$worktree" && "$state_script" "$task_id" verifying --reason "collecting evidence manifest") >/dev/null 2>&1 || true
  fi
  (cd "$worktree" && "$manifest_script" "$task_id")
  if [[ "$run_state" = "1" && -x "$state_script" ]]; then
    (cd "$worktree" && "$state_script" "$task_id" verified --reason "verification commands passed") >/dev/null 2>&1 || true
    (cd "$worktree" && "$state_script" "$task_id" evidence_ready --reason "manifest has no blocking gaps") >/dev/null 2>&1 || true
  fi
fi

if [[ "$run_pack" = "1" ]]; then
  pack_script="$root/scripts/harness/backcast-evidence-pack.sh"
  if [[ ! -x "$pack_script" ]]; then
    pack_script="$worktree/scripts/harness/backcast-evidence-pack.sh"
  fi
  (cd "$worktree" && "$pack_script" "$task_id")
  if [[ "$run_state" = "1" && -x "$state_script" ]]; then
    (cd "$worktree" && "$state_script" "$task_id" awaiting_approval --reason "evidence pack generated") >/dev/null 2>&1 || true
  fi
fi

echo "build complete for $task_id"
