#!/usr/bin/env bash
# hw の機械検証。pr-ready-gate と CI(hw-ci.yml)がこれを実行する。
#
# make smoke = tests3 の docs(ドキュメント齟齬) + locks(静的リグレッション 97件)。
# どちらも静的で python3 だけで動く。ライブ層(env/health/contracts/browser/meeting)
# はデプロイ済みスタックを要求するのでここには入れない(make validate 側の責務)。
#
# locks には hw 導入時点で既に失敗しているものがあり .hw/verify-baseline に列挙して
# ある。ベースラインに載っていないIDが1つでも失敗したら block する = 「新規回帰を
# 入れない」という契約。負債を返したら baseline から該当行を消す(消し忘れても検査は
# 緩まない。解消済みは警告として出る)。
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

BASELINE=".hw/verify-baseline"

output="$(make smoke 2>&1)"
smoke_status=$?
printf '%s\n' "$output"

# PreToolUse フック自身の退行検査。全 Bash コマンドで走るフックなので、壊すと
# 全エージェントが止まる。「CI > ローカルゲート > エージェント」の序列にフックを載せる。
if [ -f .hw/tests/pr-create-intercept.test.sh ]; then
  bash .hw/tests/pr-create-intercept.test.sh .hw/hooks/pr-create-intercept.sh || exit 1
fi

# checks/run は失敗を "  <赤>CHECK_ID<リセット>" の行で出す。集計行("3 failed out of
# 97 checks")は数字始まりなのでこのパターンに合わない。
failed="$(printf '%s\n' "$output" | python3 -c '
import re, sys
pattern = re.compile(r"^\s*(?:\x1b\[31m)?([A-Z][A-Z0-9_]{3,})(?:\x1b\[0m)?\s*$")
ids = set()
for line in sys.stdin:
    match = pattern.match(line.rstrip("\n"))
    if match:
        ids.add(match.group(1))
print("\n".join(sorted(ids)))
')"

known="$(grep -vE '^[[:space:]]*(#|$)' "$BASELINE" 2>/dev/null | sort -u || true)"

if [ -z "$failed" ]; then
  if [ "$smoke_status" = "0" ]; then
    echo "[hw][verify] ok: make smoke 全通過"
    exit 0
  fi
  echo "[hw][verify][FAIL] 失敗IDを特定できないまま make smoke が失敗(exit $smoke_status)"
  exit 1
fi

if [ -z "$known" ]; then
  new="$failed"
  fixed=""
else
  new="$(comm -23 <(printf '%s\n' "$failed") <(printf '%s\n' "$known"))"
  fixed="$(comm -13 <(printf '%s\n' "$failed") <(printf '%s\n' "$known"))"
fi

if [ -n "$fixed" ]; then
  echo "[hw][verify][warn] ベースラインの既知失敗が解消している。$BASELINE から消すこと:"
  printf '  %s\n' $fixed
fi

if [ -n "$new" ]; then
  echo "[hw][verify][FAIL] ベースラインに無い失敗(新規回帰):"
  printf '  %s\n' $new
  exit 1
fi

echo "[hw][verify] ok: 既知の失敗のみ($(printf '%s\n' "$failed" | grep -c .) 件)。新規回帰なし"
exit 0
