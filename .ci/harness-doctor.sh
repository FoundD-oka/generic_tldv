#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(pwd)"

if [ -f "$PROJECT_ROOT/.harness-init/runtime/harnessctl" ]; then
  python3 "$PROJECT_ROOT/.harness-init/runtime/harnessctl" \
    --project-root "$PROJECT_ROOT" parity >/dev/null || {
      echo "[FAIL] harness v2 runtime parity failed" >&2
      exit 1
    }
fi

for doctor in \
  "${HARNESS_INIT_DOCTOR:-}" \
  "$HOME/.codex/skills/harness-init/scripts/harness_doctor.sh" \
  "$HOME/.claude/skills/harness-init/scripts/harness_doctor.sh"
do
  if [ -n "$doctor" ] && [ -f "$doctor" ]; then
    exec bash "$doctor" "$PROJECT_ROOT"
  fi
done

echo "[FAIL] harness doctor script not found; set HARNESS_INIT_DOCTOR or reinstall harness-init" >&2
exit 1
