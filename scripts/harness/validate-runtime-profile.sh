#!/usr/bin/env bash
set -euo pipefail

quiet=0
if [[ "${1:-}" == "--quiet" ]]; then
  quiet=1
  shift
fi

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$git_root"
fi

required_paths=(
  "AGENTS.md"
  ".codex/config.toml"
  ".codex/hooks.json"
  ".pipeline/config.json"
  ".pipeline/agents/codex-executor.agent.json"
  ".pipeline/agents/codex-reviewer.agent.json"
  ".pipeline/environments/local-worktree.environment.json"
  ".pipeline/environments/ci-readonly.environment.json"
  ".pipeline/adapters/codex-cli.adapter.json"
  ".pipeline/adapters/github.adapter.json"
  ".pipeline/adapters/claude-fable-cli.adapter.json"
  ".pipeline/rules/backcast-state-machine.json"
  "docs/managed-agent-harness-architecture.md"
  "docs/fable-consultation.md"
  "schemas/harness-agent.schema.json"
  "schemas/harness-environment.schema.json"
  "schemas/harness-adapter.schema.json"
  "schemas/outcome-card.schema.json"
  "schemas/session-event.schema.json"
  "schemas/codex-build-result.schema.json"
  "schemas/checkpoint-contract.schema.json"
  "schemas/evidence-manifest.schema.json"
  "schemas/approval-decision.schema.json"
  "schemas/state-machine.schema.json"
  "schemas/external-consultation.schema.json"
  "scripts/harness/codex-session-ledger.sh"
  "scripts/harness/outcome-judge.sh"
  "scripts/harness/backcast-checkpoint.sh"
  "scripts/harness/sml-decision.sh"
  "scripts/harness/backcast-state.sh"
  "scripts/harness/worktree.sh"
  "scripts/harness/build.sh"
  "scripts/harness/codex-build.sh"
  "scripts/harness/full-loop-smoke.sh"
  "scripts/harness/backcast-manifest.sh"
  "scripts/harness/backcast-evidence-pack.sh"
  "scripts/harness/backcast-approval.sh"
  "scripts/harness/external-consultation.sh"
)

missing=0
for path in "${required_paths[@]}"; do
  if [[ ! -e "$path" ]]; then
    echo "missing required harness path: $path" >&2
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

json_files=()
while IFS= read -r -d '' path; do
  json_files+=("$path")
done < <(find .codex .pipeline schemas -type f -name '*.json' -print0)

python3 -c '
import json
import sys

failed = False
for path in sys.argv[1:]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            json.load(handle)
    except Exception as exc:
        print(f"invalid json: {path}: {exc}", file=sys.stderr)
        failed = True

if failed:
    sys.exit(1)
' "${json_files[@]}"

python3 -c '
import sys

path = sys.argv[1]
try:
    import tomllib
except ModuleNotFoundError:
    sys.exit(0)

try:
    with open(path, "rb") as handle:
        tomllib.load(handle)
except Exception as exc:
    print(f"invalid toml: {path}: {exc}", file=sys.stderr)
    sys.exit(1)
' ".codex/config.toml"

for executable in scripts/harness/codex-session-ledger.sh scripts/harness/outcome-judge.sh scripts/harness/backcast-checkpoint.sh scripts/harness/sml-decision.sh scripts/harness/backcast-state.sh scripts/harness/worktree.sh scripts/harness/build.sh scripts/harness/codex-build.sh scripts/harness/full-loop-smoke.sh scripts/harness/backcast-manifest.sh scripts/harness/backcast-evidence-pack.sh scripts/harness/backcast-approval.sh scripts/harness/external-consultation.sh; do
  if [[ ! -x "$executable" ]]; then
    echo "not executable: $executable" >&2
    exit 1
  fi
done

if [[ "$quiet" -eq 0 ]]; then
  echo "managed-agent-harness runtime profile: ok"
fi
