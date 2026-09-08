#!/usr/bin/env bash
# Compatibility entrypoint only; v2 returns checks_passed, never an authorization token.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
if [ $# -gt 0 ] && [[ "$1" != --* ]]; then
  TASK="$1"; shift
  exec python3 "$ROOT/.hw/runtime/hw.py" --project "$ROOT" gate --task "$TASK" "$@"
fi
exec python3 "$ROOT/.hw/runtime/hw.py" --project "$ROOT" gate "$@"
