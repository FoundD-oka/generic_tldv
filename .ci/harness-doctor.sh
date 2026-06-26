#!/usr/bin/env bash
set -euo pipefail

HARNESS_DOCTOR="/Users/bonginkan-3-gouki/project/claude-dotfiles/skills/harness-init/scripts/harness_doctor.sh"

if [ -x "$HARNESS_DOCTOR" ]; then
  exec bash "$HARNESS_DOCTOR" "$(pwd)"
fi

echo "[FAIL] harness doctor script not found"
exit 1
