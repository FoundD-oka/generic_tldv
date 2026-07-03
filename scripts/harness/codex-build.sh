#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/codex-build.sh <task-id> [--worktree <path>] [--prompt <text>] [--prompt-file <path>] [--codex-arg <arg>]...

Runs Codex CLI inside the task worktree through codex-session-ledger.sh, then
hands the result to build.sh for commit, verification, manifest, and evidence
pack generation.

If Codex CLI is unavailable, the command fails closed before modifying files.
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

task_id="${1:-}"
if [[ -z "$task_id" ]]; then
  usage
  exit 2
fi
shift

worktree=""
prompt=""
prompt_file=""
codex_args=()
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --worktree)
      worktree="${2:-}"
      shift 2
      ;;
    --prompt)
      prompt="${2:-}"
      shift 2
      ;;
    --prompt-file)
      prompt_file="${2:-}"
      shift 2
      ;;
    --codex-arg)
      codex_args+=("${2:-}")
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
else
  echo "not inside a git repository" >&2
  exit 1
fi

root="$(pwd)"
if [[ -z "$worktree" && -f ".pipeline/worktrees/$task_id/worktree.json" ]]; then
  worktree="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("path",""))' ".pipeline/worktrees/$task_id/worktree.json")"
fi
if [[ -z "$worktree" ]]; then
  echo "missing --worktree and .pipeline/worktrees/$task_id/worktree.json" >&2
  exit 2
fi
if [[ "$worktree" != /* ]]; then
  worktree="$root/$worktree"
fi
if [[ ! -d "$worktree" ]]; then
  echo "worktree not found: $worktree" >&2
  exit 1
fi
if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI not found; cannot run Codex build unlock path" >&2
  exit 1
fi

if [[ -n "$prompt_file" ]]; then
  if [[ "$prompt_file" != /* ]]; then
    prompt_file="$root/$prompt_file"
  fi
  if [[ ! -f "$prompt_file" ]]; then
    echo "prompt file not found: $prompt_file" >&2
    exit 1
  fi
  prompt="$(cat "$prompt_file")"
fi
if [[ -z "$prompt" ]]; then
  prompt="$(python3 - "$task_id" <<'PY'
import json
import pathlib
import sys

task_id = sys.argv[1]
root = pathlib.Path.cwd()
checkpoint_path = root / ".pipeline" / "plans" / task_id / "checkpoint-contract.json"
verification_path = root / ".pipeline" / "plans" / task_id / "verification-contract.md"
checkpoint = {}
if checkpoint_path.exists():
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
target = (checkpoint.get("target_state") or {}).get("description", "")
quality_checkpoint = checkpoint.get("quality_checkpoint") if isinstance(checkpoint.get("quality_checkpoint"), dict) else {}
issue_goal = quality_checkpoint.get("issue_goal", "")
quality_conditions = checkpoint.get("quality_conditions") or []
condition_text = "\n".join(
    f"- {c.get('id')}: {c.get('condition')} -> {c.get('ok_line')}"
    for c in quality_conditions
    if isinstance(c, dict)
)
allowed = ", ".join((checkpoint.get("blast_radius") or {}).get("allowed_paths", []))
forbidden = ", ".join((checkpoint.get("blast_radius") or {}).get("forbidden_paths", []))
commands = checkpoint.get("verification_commands") or []
command_text = "; ".join(f"{c.get('id')}: {c.get('command')}" for c in commands)
print(f"""Run implementation for harness task {task_id}.

Issue goal:
{issue_goal or '(none recorded)'}

Target quality checkpoint:
{target}

Quality conditions:
{condition_text or '(none recorded)'}

Allowed paths:
{allowed or '(none recorded)'}

Forbidden paths:
{forbidden or '(none recorded)'}

Required verification commands:
{command_text or '(none recorded)'}

Rules:
- The checkpoint is a minimum quality line before continuing, not the whole issue goal.
- Modify only files needed to satisfy the recorded quality conditions.
- Do not edit .pipeline except through harness scripts.
- Do not claim verification success; the harness will run verification after Codex exits.
- Leave unrelated findings as notes, not code changes.
""")
if verification_path.exists():
    print("\nVerification contract:\n")
    print(verification_path.read_text(encoding="utf-8"))
PY
)"
fi

output_schema="$worktree/schemas/codex-build-result.schema.json"
if [[ ! -f "$output_schema" ]]; then
  output_schema="$root/schemas/codex-build-result.schema.json"
fi
build_command=(
  "$root/scripts/harness/codex-session-ledger.sh"
  --append
  "$task_id"
  --
  --sandbox
  workspace-write
  --cd
  "$worktree"
)
if [[ -f "$output_schema" ]]; then
  build_command+=(--output-schema "$output_schema")
fi
if [[ "${#codex_args[@]}" -gt 0 ]]; then
  build_command+=("${codex_args[@]}")
fi
build_command+=("$prompt")

"$root/scripts/harness/build.sh" "$task_id" --worktree "$worktree" --commit-message "harness: codex build $task_id" -- "${build_command[@]}"
