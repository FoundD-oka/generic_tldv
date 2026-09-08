#!/usr/bin/env bash
# Claude互換パス。実体はエージェント共通の.hwに置く。
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
exec bash "$ROOT/.hw/hooks/hd-gate.sh" "$@"
