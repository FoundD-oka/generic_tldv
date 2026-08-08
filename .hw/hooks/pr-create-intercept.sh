#!/usr/bin/env bash
# Claude/Codex 共通 PreToolUse(Bash): `gh pr create` を物理的にインターセプトし、
# pr-ready-gate を通過するまで PR 作成をブロックする(Layer 2 の強制)。
# 助言は証明にならない — 例外ゼロで守らせたいものはフックに置く。
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"
payload="$(cat)"
cmd="$(
  printf '%s' "$payload" |
    python3 -c "import json,sys; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" \
      2>/dev/null || true
)"

case "$cmd" in
  *"gh pr create"*) ;;
  *) exit 0 ;;
esac

TASK_ID=""
[ -f .hw/current/task-id ] && TASK_ID="$(cat .hw/current/task-id)"

mkdir -p .hw/state
if bash .hw/hooks/pr-ready-gate.sh "$TASK_ID" > .hw/state/intercept.log 2>&1; then
  exit 0
fi

echo "hw: pr-ready-gate がblock。詳細は .hw/state/intercept.log。" >&2
echo "bash .hw/hooks/pr-ready-gate.sh ${TASK_ID:-<task-id>} を通してからPRを作成してください。" >&2
exit 2
