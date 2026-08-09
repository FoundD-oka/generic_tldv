#!/usr/bin/env bash
# scripts/test/run-refactor-item.sh — リファクタ項目ごとの機械検証エントリポイント。
# リファクタv2プランの完了条件が参照するコマンドの実体(導入時点の実装項目は RF-71R のみ)。
# 項目を追加するときは scripts/test/refactor-checks/<ITEM>.sh を置く。
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
ITEM="${1:?usage: bash scripts/test/run-refactor-item.sh <ITEM-ID>}"
case "$ITEM" in
  */*|*..*) echo "[refactor][FAIL] 不正なITEM-ID: $ITEM"; exit 1 ;;
esac
CHECK="scripts/test/refactor-checks/${ITEM}.sh"
if [ ! -f "$CHECK" ]; then
  echo "[refactor][FAIL] $CHECK がありません"
  exit 1
fi
exec bash "$CHECK"
