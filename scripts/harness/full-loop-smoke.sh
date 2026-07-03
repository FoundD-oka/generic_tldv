#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/full-loop-smoke.sh [task-id]

Creates a temporary fixture repository under /tmp, installs harness-init into
that fixture, runs the full harness loop in a task worktree, and verifies PR
readiness. The current repository is not modified.
USAGE
}

task_id="${1:-smoke-$(date +%Y%m%d%H%M%S)}"
skill_root="/Users/bonginkan-3-gouki/.claude/skills/harness-init"
if [[ "$skill_root" == "/Users/bonginkan-3-gouki/.claude/skills/harness-init" ]]; then
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  project_root="$(cd "$script_dir/../.." && pwd)"
  if [[ -x "$project_root/.ci/harness-doctor.sh" && -d "$project_root/scripts/harness" ]]; then
    skill_root=""
  else
    skill_root="$(cd "$script_dir/../../.." && pwd)"
  fi
fi
install_script=""
if [[ -n "$skill_root" && -x "$skill_root/scripts/install_harness.sh" ]]; then
  install_script="$skill_root/scripts/install_harness.sh"
elif [[ -n "${HARNESS_INIT_INSTALLER:-}" && -x "${HARNESS_INIT_INSTALLER:-}" ]]; then
  install_script="$HARNESS_INIT_INSTALLER"
fi

fixture="$(mktemp -d /tmp/harness-full-loop-smoke.XXXXXX)"
cd "$fixture"
git init -q
git config user.email "harness-smoke@example.com"
git config user.name "Harness Smoke"
mkdir -p src
printf 'export function message() { return "before"; }\n' > src/smoke.js
git add src/smoke.js
git commit -qm "seed smoke fixture"

if [[ -n "$install_script" ]]; then
  HARNESS_SKIP_GITNEXUS=1 bash "$install_script" "HarnessSmoke" >/tmp/harness-full-loop-smoke-install.log
else
  source_root="$project_root"
  mkdir -p .claude
  cp -R "$source_root/.ai" "$source_root/.pipeline" "$source_root/.ci" "$source_root/.codex" "$source_root/docs" "$source_root/schemas" "$source_root/scripts" .
  cp "$source_root/CLAUDE.md" "$source_root/AGENTS.md" .
  cp -R "$source_root/.claude" .
  chmod +x .ci/harness-doctor.sh scripts/harness/*.sh 2>/dev/null || true
fi
git add .
git commit -qm "install harness"

scripts/harness/backcast-checkpoint.sh "$task_id" \
  --goal "prove the harness full loop works" \
  --current "src/smoke.js returns before" \
  --target "smoke return value has the required after state" \
  --condition "qc-smoke::smoke return value::smoke file contains after::verify-smoke" \
  --command "verify-smoke::grep -q after src/smoke.js" \
  --allowed "src/**" \
  --approval-required

scripts/harness/sml-decision.sh "$task_id" --size S --write-verification-contract
scripts/harness/worktree.sh create "$task_id" --base HEAD

worktree="$(python3 - "$task_id" <<'PY'
import json
import sys
print(json.load(open(f".pipeline/worktrees/{sys.argv[1]}/worktree.json"))["path"])
PY
)"

scripts/harness/build.sh "$task_id" --worktree "$worktree" -- sh -lc "python3 - <<'PY'
from pathlib import Path
p = Path('src/smoke.js')
p.write_text(p.read_text().replace('before', 'after'))
PY"

(
  cd "$worktree"
  scripts/harness/backcast-approval.sh "$task_id" approved --approver "full-loop-smoke" --role "harness"
  scripts/harness/backcast-current.sh update "$task_id" --actor "full-loop-smoke"
  scripts/harness/backcast-next-checkpoint.sh "$task_id" \
    --next-task "$task_id-next" \
    --target "next checkpoint draft is recorded after current update" \
    --condition "qc-next::checkpoint continuity::next checkpoint draft exists::verify-next" \
    --command "verify-next::test -f .pipeline/plans/$task_id-next/checkpoint-contract.json" \
    --allowed ".pipeline/plans/$task_id-next/**"
  HARNESS_BACKCAST_REQUIRED=1 .claude/hooks/backcast-validate.sh "$task_id" >/dev/null
  .claude/hooks/pr-ready-gate.sh "$task_id"
)

echo "full-loop smoke passed: $task_id"
echo "fixture: $fixture"
echo "worktree: $worktree"
