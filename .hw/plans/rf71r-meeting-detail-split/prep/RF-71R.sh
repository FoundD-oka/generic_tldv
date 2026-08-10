#!/usr/bin/env bash
# RF-71R: services/dashboard/src/app/meetings/[id]/page.tsx 分割の機械検証。
# - prime 実行の停止条件(HW_PRIME_GATE)としてこのスクリプトを使う。
# - 最終権威は pr-ready-gate と CI(このスクリプトは合格ラインの機械判定のみ)。
# - 判定はすべて commit 済みの状態に対して行う(dirty tree は C01 で落とす)。
# - 基準値の根拠は .hw/plans/rf71r-meeting-detail-split/verification-contract.md。
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

TASK_ID="rf71r-meeting-detail-split"
PAGE="services/dashboard/src/app/meetings/[id]/page.tsx"
DASH="services/dashboard"
fail=0
check() { # $1=ID+名前 $2=exit code $3=補足
  if [ "$2" = "0" ]; then echo "[rf71r][ok]   $1"; else echo "[rf71r][FAIL] $1 ${3:-}"; fail=1; fi
}
count_in() { grep -c "$1" "$2" 2>/dev/null || true; }

BASE="$(cat ".hw/plans/${TASK_ID}/base-commit" 2>/dev/null | tr -d '[:space:]')"
git cat-file -e "${BASE}^{commit}" 2>/dev/null
check "C00 base-commit解決 (${BASE:-なし})" $?
[ "$fail" = "1" ] && exit 1

[ -z "$(git status --porcelain)" ]
check "C01 clean-tree" $? "(commitしてから検証する)"

# 差分は services/dashboard/ のみ。.hw/plans はハーネス規約上のメタデータ
# (base-commit更新・review-verdict)なので除外する。それ以外の .hw/・scripts/・
# .github/ への変更はここで落ちる(ゲート・検証スクリプトの書き換え防止)。
BAD="$(git diff --name-only "${BASE}..HEAD" -- ':(exclude).hw/plans' | grep -v '^services/dashboard/' || true)"
[ -z "$BAD" ]
check "C02 diff-allowlist(dashboard以外の変更0)" $? "(許容外: $(printf '%s' "$BAD" | tr '\n' ' ' | head -c 300))"

grep -q 'export default' "$PAGE" 2>/dev/null
check "C03 pageのdefault export残存" $?

LINES="$(wc -l < "$PAGE" 2>/dev/null | tr -d ' ')"
[ -n "$LINES" ] && [ "$LINES" -le 1200 ]
check "C04 page行数<=1200 (現在 ${LINES:-なし})" $?

N="$(count_in 'setInterval' "$PAGE")"
[ "$N" = "0" ]
check "C05 page内setInterval=0 (現在 ${N})" $?

N="$(count_in 'fetch(' "$PAGE")"
[ "$N" = "0" ]
check "C06 page内raw fetch=0 (現在 ${N})" $?

N="$(count_in 'vnc/vnc.html' "$PAGE")"
[ "$N" = "0" ]
check "C07a page内VNC URL組立=0 (現在 ${N})" $?
VNC_FILES="$(grep -rl 'vnc/vnc.html' "$DASH/src" 2>/dev/null | wc -l | tr -d ' ')"
[ "$VNC_FILES" -le 2 ]
check "C07b VNC URL文字列を含むsrcファイル<=2 (現在 ${VNC_FILES})" $?

# meeting-card.tsx は会議一覧側の独立したリネームUIなので対象外
TITLE_OK="$(grep -ro 'タイトルを更新しました' "$DASH/src" 2>/dev/null | grep -v 'meeting-card.tsx' | wc -l | tr -d ' ')"
TITLE_NG="$(grep -ro 'タイトルの更新に失敗しました' "$DASH/src" 2>/dev/null | grep -v 'meeting-card.tsx' | wc -l | tr -d ' ')"
[ "$TITLE_OK" = "1" ] && [ "$TITLE_NG" = "1" ]
check "C08 title保存実装の1本化 (成功toast ${TITLE_OK}=1 / 失敗toast ${TITLE_NG}=1)" $?

DELETED="$(git diff --diff-filter=D --name-only "${BASE}..HEAD" -- "$DASH/tests")"
MODIFIED="$(git diff --diff-filter=M --name-only "${BASE}..HEAD" -- "$DASH/tests" \
  | grep -v -E '(test_meeting_detail_wiring|test_meeting_detail_title|test_transcript_reprocess_ui)\.test\.ts$' || true)"
[ -z "${DELETED}${MODIFIED}" ]
check "C09a 既存テスト削除0・改変はソース結合3ファイルのみ" $? "(違反: ${DELETED} ${MODIFIED})"
E1="$(count_in 'expect(' "$DASH/tests/test_meeting_detail_wiring.test.ts")"
E2="$(count_in 'expect(' "$DASH/tests/test_meeting_detail_title.test.ts")"
E3="$(count_in 'expect(' "$DASH/tests/test_transcript_reprocess_ui.test.ts")"
[ "${E1:-0}" -ge 21 ] && [ "${E2:-0}" -ge 5 ] && [ "${E3:-0}" -ge 20 ]
check "C09b 許容3ファイルのassertion数維持 (${E1:-0}/${E2:-0}/${E3:-0} >= 21/5/20)" $?
SKIPONLY="$(grep -rn -E '\.(only|skip)\(' "$DASH/tests" 2>/dev/null | wc -l | tr -d ' ')"
[ "$SKIPONLY" = "0" ]
check "C09c テストの.only/.skip=0 (現在 ${SKIPONLY})" $?

ADDED="$(git diff --diff-filter=A --name-only "${BASE}..HEAD" -- "$DASH/tests" | grep -c '\.test\.ts$' || true)"
[ "$ADDED" -ge 2 ]
check "C10 新規テストファイル(.test.ts)>=2 (現在 ${ADDED})" $?

# --- ここから node 依存の検査(worktree では node_modules が無いので自前で準備) ---
(
  cd "$DASH" || exit 1
  if [ ! -d node_modules ]; then
    npm ci --no-audit --no-fund >/dev/null 2>&1 || exit 1
  fi
  # release-version.generated.json は gitignore された生成物。tsc/lint の前提。
  npm run generate-release-version >/dev/null 2>&1
)
check "C11a npm環境準備(npm ci + generate-release-version)" $?

VITEST_JSON="$(mktemp)"
( cd "$DASH" && npx vitest run --reporter=json --outputFile="$VITEST_JSON" >/dev/null 2>&1 )
VITEST_EXIT=$?
python3 - "$VITEST_JSON" "$VITEST_EXIT" <<'PY'
import json, sys
path, code = sys.argv[1], int(sys.argv[2])
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    print("  vitest: JSON出力を読めない")
    sys.exit(1)
passed = data.get("numPassedTests", 0)
failed = data.get("numFailedTests", 1)
print(f"  vitest: passed={passed} failed={failed} exit={code}")
sys.exit(0 if code == 0 and failed == 0 and passed >= 271 else 1)
PY
check "C11b vitest全緑かつ合計>=271件(基準263+新規8以上)" $?
rm -f "$VITEST_JSON"

( cd "$DASH" && npx tsc --noEmit >/dev/null 2>&1 )
check "C12 tsc --noEmit" $?

ESLINT_JSON="$(mktemp)"
( cd "$DASH" && npx eslint . --format json --output-file "$ESLINT_JSON" >/dev/null 2>&1 )
ESLINT_EXIT=$?
if [ "$ESLINT_EXIT" -ge 2 ]; then
  check "C13a lintラチェット" 1 "(eslint内部エラー exit ${ESLINT_EXIT})"
else
  ( cd "$DASH" && node scripts/ci/lint-ratchet.mjs "$ESLINT_JSON" lint-baseline.json )
  check "C13a lintラチェット(lint-baseline.json比)" $?
fi
rm -f "$ESLINT_JSON"

python3 - "$BASE" <<'PY'
import json, subprocess, sys
base = sys.argv[1]
proc = subprocess.run(
    ["git", "show", f"{base}:services/dashboard/lint-baseline.json"],
    capture_output=True, text=True,
)
try:
    old = json.loads(proc.stdout)
    new = json.load(open("services/dashboard/lint-baseline.json", encoding="utf-8"))
    ok = all(int(new[k]) <= int(old[k]) for k in ("errors", "warnings"))
except Exception:
    ok = False
sys.exit(0 if ok else 1)
PY
check "C13b lint-baselineを上げていない(下げるのは可)" $?

# base 時点の page.tsx にある日本語文字列(2文字以上の連続)が、現在の src のどこかに
# 全て残っていること。文言の消失・書き換えを機械的に検出する(移動は許容)。
python3 - "$BASE" <<'PY'
import pathlib, re, subprocess, sys
base = sys.argv[1]
proc = subprocess.run(
    ["git", "show", f"{base}:services/dashboard/src/app/meetings/[id]/page.tsx"],
    capture_output=True, text=True,
)
if proc.returncode != 0:
    print("  base版page.tsxを取得できない")
    sys.exit(1)
runs = set(re.findall(r"[ぁ-んァ-ヶー一-龠々]{2,}", proc.stdout))
corpus = ""
for p in pathlib.Path("services/dashboard/src").rglob("*"):
    if p.is_file() and p.suffix in (".ts", ".tsx"):
        corpus += p.read_text(encoding="utf-8", errors="ignore")
missing = sorted(r for r in runs if r not in corpus)
if missing:
    print("  消えた文言:", ", ".join(missing[:20]))
    sys.exit(1)
print(f"  文言 {len(runs)} 件すべて残存")
PY
check "C14 日本語文言の不変(base版page.tsxの全文字列がsrcに残存)" $?

bash .hw/verify.sh >/dev/null 2>&1
check "C15 リポジトリ検証(.hw/verify.sh)" $?

if [ "$fail" = "0" ]; then
  echo "[rf71r] ready: 全検査通過"
else
  echo "[rf71r] block: 上のFAILを修正してcommit後に再実行"
fi
exit "$fail"
