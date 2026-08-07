#!/usr/bin/env bash
# hw Layer 2: Opus 5 実装と Fable レビューを束ねる PR ready ゲート(fail closed)。
# CI が同じ検査を最終権威として再実行する。
# 使い方: bash .hw/hooks/pr-ready-gate.sh [task-id]
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"
TASK_ID="${1:-}"
if [ -z "$TASK_ID" ] && [ -f .hw/current/task-id ]; then
  TASK_ID="$(cat .hw/current/task-id)"
fi
if [ -z "$TASK_ID" ]; then
  echo "[hw][FAIL] task-idがありません(引数または .hw/current/task-id)"
  exit 1
fi
PLAN_DIR=".hw/plans/$TASK_ID"
fail=0

check() { # $1=名前 $2=直前コマンドの終了コード $3=失敗時の補足
  if [ "$2" = "0" ]; then
    echo "[hw][ok]   $1"
  else
    echo "[hw][FAIL] $1 ${3:-}"
    fail=1
  fi
}

# 1. 検証は commit 済みの状態に対して行う(dirty tree を検証しない)
[ -z "$(git status --porcelain)" ]
check "clean-tree" $? "(review-verdictを含めcommitしてから実行)"

# 2. プランの存在と Fable 産の検査
[ -f "$PLAN_DIR/plan.md" ]
check "plan-exists" $? "($PLAN_DIR/plan.md がありません)"
grep -q "generated_by:.*fable" "$PLAN_DIR/plan.md" 2>/dev/null
check "plan-by-fable" $? "(plan.mdに generated_by: fable がありません。プランは planner 経由必須)"

# 3. 不確定性ベース S/M/L 判定の記録
SIZE="$(
  python3 -c "import json;print(json.load(open('$PLAN_DIR/sml-decision.json')).get('size',''))" \
    2>/dev/null || true
)"
[ "$SIZE" = "S" ] || [ "$SIZE" = "M" ] || [ "$SIZE" = "L" ]
check "sml-decision" $? "(sizeがS/M/Lではありません: '${SIZE:-なし}')"

# 4. 実行基盤(R軸)の記録。S/M/L とは独立の軸で、値は inline か prime。
#    未記録は既定の inline とみなし警告に留める(R軸導入前のタスクとの後方互換)。
#    ファイルが在るのに読めない場合は未記録と区別して落とす(書きかけを通さない)。
if [ -f "$PLAN_DIR/runtime-decision.json" ]; then
  RUNTIME="$(
    python3 -c "import json;print(json.load(open('$PLAN_DIR/runtime-decision.json')).get('runtime',''))" \
      2>/dev/null || true
  )"
  [ "$RUNTIME" = "inline" ] || [ "$RUNTIME" = "prime" ]
  check "runtime-decision (${RUNTIME:-読めない})" $? "(runtimeがinline/primeではありません)"
else
  echo "[hw][warn] runtime-decision.json 未記録(inline とみなす)"
fi

# 5. M/L は検証契約・base-commit・現在の差分に一致する Fable READY が必須。
#    READY は対象差分hashと契約hashに束縛されるので、修復や契約変更で自動失効する。
if [ "$SIZE" = "M" ] || [ "$SIZE" = "L" ]; then
  [ -f "$PLAN_DIR/verification-contract.md" ]
  check "verification-contract" $? "(M/Lには検証契約が必要)"
  [ -f "$PLAN_DIR/base-commit" ]
  check "base-commit" $? "(Fableプラン時のHEADをbase-commitへ記録)"
  python3 .hw/check_review_verdict.py "$TASK_ID"
  check "fable-review" $? "(現在の差分に一致するFable READYが必要)"
fi

# 6. HD 再発ゲート(改訂の効果が証拠で否定された再発カテゴリはブロック)
bash .hw/hooks/hd-gate.sh "$TASK_ID"
check "hd-gate" $?

# 7. プロジェクト検証(ビルド/テスト)
if [ -f .hw/verify.sh ]; then
  bash .hw/verify.sh
  check "verify" $? "(.hw/verify.sh が失敗)"
else
  echo "[hw][warn] .hw/verify.sh 未定義(ビルド/テストの機械検証なし)"
fi

STATUS="block"
[ "$fail" = "0" ] && STATUS="ready"
mkdir -p ".hw/gates/$TASK_ID"
python3 - "$TASK_ID" "$STATUS" <<'PY'
import json, pathlib, subprocess, sys

task, status = sys.argv[1:3]
commit = subprocess.run(
    ["git", "rev-parse", "HEAD"], capture_output=True, text=True
).stdout.strip()
path = pathlib.Path(".hw/gates") / task / "pr-ready.json"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(
    json.dumps(
        {"task": task, "status": status, "commit": commit},
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
echo "[hw] pr-ready: $STATUS (task: $TASK_ID)"
exit "$fail"
