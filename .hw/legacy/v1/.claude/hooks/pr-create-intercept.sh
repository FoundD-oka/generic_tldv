#!/usr/bin/env bash
# Claude互換パス。stdinのhook payloadを共通実装へ渡す。
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
exec bash "$ROOT/.hw/hooks/pr-create-intercept.sh" "$@"
