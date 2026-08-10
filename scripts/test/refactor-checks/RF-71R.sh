#!/usr/bin/env bash
# RF-71R: services/dashboard/src/app/meetings/[id]/page.tsx 分割の機械検証(第2版)。
# - prime 実行の停止条件(HW_PRIME_GATE)としてこのスクリプトを使う。
# - 最終権威は pr-ready-gate と CI。正の判定はコーディネータによる再実行のみ
#   (実行役のゲート出力は自己申告として扱う)。
# - 第2版の設計方針: 第1回試行で「本体を隣のファイルへ移動しただけ」で C04/C06/C07a
#   が通った(Goodhart)。page.tsx 単体ではなく「触れた全ファイル」に対する
#   行数・バイト上限、fetch/setInterval の配置規則、純増上限、passthrough 禁止で
#   「責務の分解」を機械判定に寄せる。迂回シナリオと対応は verification-contract.md。
# - 判定はすべて commit 済みの状態に対して行う(dirty tree は C01 で落とす)。
# - RF71R_BASE 環境変数: コーディネータが再検証時に既知の base を外部ピンする。
#   base-commit ファイルと不一致なら即 fail(実行役による base 差し替えの検出)。
# - RF71R_SKIP_NODE=1: node 依存検査(C11/C12/C13a/C15)を飛ばすが、必ず exit 1
#   (fail-closed。FAILパターンの確認専用で、合格判定には絶対に使えない)。
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"

TASK_ID="rf71r-meeting-detail-split"
PAGE="services/dashboard/src/app/meetings/[id]/page.tsx"
DASH="services/dashboard"
SKIPNODE="${RF71R_SKIP_NODE:-0}"
fail=0
check() { # $1=ID+名前 $2=exit code $3=補足
  if [ "$2" = "0" ]; then echo "[rf71r][ok]   $1"; else echo "[rf71r][FAIL] $1 ${3:-}"; fail=1; fi
}
count_in() { grep -c "$1" "$2" 2>/dev/null || true; }

BASE="$(cat ".hw/plans/${TASK_ID}/base-commit" 2>/dev/null | tr -d '[:space:]')"
C00_OK=0
if [ -z "$BASE" ] || ! git cat-file -e "${BASE}^{commit}" 2>/dev/null; then
  C00_OK=1
elif [ -n "${RF71R_BASE:-}" ] && [ "${RF71R_BASE}" != "$BASE" ]; then
  C00_OK=2
elif ! git merge-base --is-ancestor "$BASE" HEAD 2>/dev/null; then
  C00_OK=3
fi
check "C00 base-commit解決+外部ピン一致+ancestor (${BASE:-なし})" "$C00_OK" \
  "(1=解決不能 2=RF71R_BASE不一致=base差し替えの疑い 3=HEADの祖先でない)"
[ "$fail" = "1" ] && exit 1

[ -z "$(git status --porcelain)" ]
check "C01 clean-tree" $? "(commitしてから検証する)"

# C02: 差分 allowlist。許容は services/dashboard/ と、本タスクのメタデータ2種
# (base-commit 更新・review-verdict)のみ。plan.md・契約・scripts/・.hw/ 本体・
# .github/ への変更はすべて違反(ゲート・検証スクリプト・契約の書き換え防止)。
BAD="$(git diff --name-only "${BASE}..HEAD" \
  | grep -Ev "^services/dashboard/|^\.hw/plans/${TASK_ID}/(base-commit|review-verdict)" || true)"
[ -z "$BAD" ]
check "C02 diff-allowlist(dashboard+タスクメタ以外の変更0)" $? \
  "(許容外: $(printf '%s' "$BAD" | tr '\n' ' ' | head -c 300))"

# C19: dashboard 内でもテスト・lint・依存の設定は凍結(ゲート無効化経路の遮断)。
# lint-baseline.json は C13b が方向(下げのみ可)を守るのでここでは対象外。
FROZEN="$(git diff --name-only "${BASE}..HEAD" -- \
  "${DASH}/package.json" "${DASH}/package-lock.json" \
  "${DASH}/vitest.config*" "${DASH}/tsconfig*" "${DASH}/eslint*" \
  "${DASH}/next.config*" "${DASH}/postcss*" "${DASH}/tailwind*" \
  "${DASH}/components.json" "${DASH}/.npmrc" "${DASH}/.gitignore" \
  "${DASH}/scripts" 2>/dev/null || true)"
[ -z "$FROZEN" ]
check "C19 設定・CI補助・依存ファイルの凍結" $? \
  "(変更禁止: $(printf '%s' "$FROZEN" | tr '\n' ' ' | head -c 300))"

# C03: page.tsx は自ファイル内で default export の関数を定義する。
# `export default <識別子>;` の re-export だけの page(第1回の手口)は不可。
grep -qE 'export default (async )?function' "$PAGE" 2>/dev/null
check "C03 pageが自前のdefault export関数を定義(re-export不可)" $?

LINES="$(wc -l < "$PAGE" 2>/dev/null | tr -d ' ')"
BYTES="$(wc -c < "$PAGE" 2>/dev/null | tr -d ' ')"
[ -n "$LINES" ] && [ "$LINES" -le 600 ] && [ -n "$BYTES" ] && [ "$BYTES" -le 32000 ]
check "C04 page行数<=600かつ<=32000B (現在 ${LINES:-なし}行/${BYTES:-なし}B)" $?

N="$(count_in 'setInterval' "$PAGE")"
[ "$N" = "0" ]
check "C05a page内setInterval=0 (現在 ${N})" $?

# C05b/C06b: 触れた src ファイル(page以外)で、hooks/lib(fetchはapp/apiも)以外に
# setInterval / fetch を増やしていない(=巨大ファイルごと components へ移す手口の遮断)。
place_check() { # $1=mode
python3 - "$BASE" "$1" <<'PY'
import re, subprocess, sys
base, mode = sys.argv[1], sys.argv[2]
if mode == "interval":
    pat = re.compile(r"\bsetInterval\b")
    allowed = ("services/dashboard/src/hooks/", "services/dashboard/src/lib/")
else:
    pat = re.compile(r"\bfetch\s*\(|\bfetch\s*\.\s*(call|apply|bind)\b")
    allowed = ("services/dashboard/src/hooks/", "services/dashboard/src/lib/",
               "services/dashboard/src/app/api/")
exts = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
page = "services/dashboard/src/app/meetings/[id]/page.tsx"
out = subprocess.run(["git", "diff", "--name-status", "--no-renames",
                      f"{base}..HEAD", "--", "services/dashboard/src"],
                     capture_output=True, text=True).stdout
bad = []
for line in out.splitlines():
    parts = line.split("\t")
    if len(parts) < 2:
        continue
    st, path = parts[0], parts[-1]
    if st.startswith("D") or path == page:
        continue
    if not path.endswith(exts) or path.startswith(allowed):
        continue
    try:
        cur = len(pat.findall(open(path, encoding="utf-8", errors="ignore").read()))
    except OSError:
        continue
    prev_proc = subprocess.run(["git", "show", f"{base}:{path}"],
                               capture_output=True, text=True)
    prev = len(pat.findall(prev_proc.stdout)) if prev_proc.returncode == 0 else 0
    if cur > prev:
        bad.append(f"{path}({prev}->{cur})")
if bad:
    print("  配置違反:", ", ".join(bad[:10]))
    sys.exit(1)
PY
}
place_check interval
check "C05b hooks/lib以外の触れたファイルでsetInterval増加0" $?

N="$(grep -cE 'fetch[[:space:]]*\(|fetch[[:space:]]*\.[[:space:]]*(call|apply|bind)' "$PAGE" 2>/dev/null || true)"
[ "$N" = "0" ]
check "C06a page内raw fetch=0 (現在 ${N})" $?
place_check fetch
check "C06b hooks/lib/app-api以外の触れたファイルでfetch増加0" $?

N="$(grep -cE 'vnc\.html|websockify' "$PAGE" 2>/dev/null || true)"
[ "$N" = "0" ]
check "C07a page内VNC文字列(vnc.html|websockify)=0 (現在 ${N})" $?
V1="$(grep -rEo 'vnc/vnc\.html' "$DASH/src" 2>/dev/null | wc -l | tr -d ' ')"
V2="$(grep -rEo 'vnc/websockify' "$DASH/src" 2>/dev/null | wc -l | tr -d ' ')"
VF="$(grep -rlE 'vnc/(vnc\.html|websockify)' "$DASH/src" 2>/dev/null | wc -l | tr -d ' ')"
[ "$V1" -le 2 ] && [ "$V2" -le 2 ] && [ "$VF" -le 2 ]
check "C07b VNC URL組立の集約: vnc.html出現<=2/websockify出現<=2/含有ファイル<=2 (現在 ${V1}/${V2}/${VF})" $?

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
check "C10a 新規テストファイル(.test.ts)>=2 (現在 ${ADDED})" $?

# C10b: 新規テストは差分内の src モジュールを import し、expect を3個以上持つ
# (対象と無関係な水増しテストでの件数稼ぎの防止。質の最終判定は Fable レビュー)。
python3 - "$BASE" <<'PY'
import pathlib, re, subprocess, sys
base = sys.argv[1]
def diff(args):
    return subprocess.run(["git", "diff", *args, f"{base}..HEAD", "--"],
                          capture_output=True, text=True).stdout.splitlines()
new_tests = [p for p in diff(["--diff-filter=A", "--name-only", "--no-renames"])
             if p.startswith("services/dashboard/tests/") and p.endswith(".test.ts")]
changed_src = {p for p in diff(["--diff-filter=AM", "--name-only", "--no-renames"])
               if p.startswith("services/dashboard/src/")}
if not new_tests:
    print("  新規テストなし")
    sys.exit(1)
SUFFIXES = ["", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx"]
def resolve(spec, from_path):
    if spec.startswith("@/"):
        cand = "services/dashboard/src/" + spec[2:]
    elif spec.startswith("."):
        cand = str((pathlib.PurePosixPath(from_path).parent / spec))
        cand = str(pathlib.PurePosixPath(*pathlib.PurePosixPath(cand).parts))
        # normalize ..
        parts = []
        for part in pathlib.PurePosixPath(cand).parts:
            if part == "..":
                parts and parts.pop()
            elif part != ".":
                parts.append(part)
        cand = "/".join(parts)
    else:
        return set()
    return {cand + s for s in SUFFIXES}
imp = re.compile(r"""from\s+["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)|require\s*\(\s*["']([^"']+)["']\s*\)""")
bad = []
for t in new_tests:
    try:
        text = open(t, encoding="utf-8", errors="ignore").read()
    except OSError:
        bad.append(f"{t}(読めない)")
        continue
    specs = [s for m in imp.findall(text) for s in m if s]
    resolved = set()
    for s in specs:
        resolved |= resolve(s, t)
    touches = bool(resolved & changed_src)
    expects = len(re.findall(r"\bexpect\s*\(", text))
    if not touches or expects < 3:
        bad.append(f"{t}(差分srcのimport={'有' if touches else '無'}, expect={expects})")
if bad:
    print("  水増し疑い:", ", ".join(bad))
    sys.exit(1)
print(f"  新規テスト{len(new_tests)}本すべて差分srcに結合・expect>=3")
PY
check "C10b 新規テストが差分内srcモジュールをimportしexpect>=3" $?

# C16: 触れた(A/M)コードファイル全部への上限。行数は minify で偽装できるので
# バイト上限を併設する。新規: <=600行 かつ <=32000B。既存の改変: base実測+80行 /
# +4000B まで(既に大きい既存ファイルの正当な小改変を許す)。page は C04 が管轄。
# 範囲は services/dashboard 全体(tests 含む=テストディレクトリへの退避も遮断)。
python3 - "$BASE" <<'PY'
import os, subprocess, sys
base = sys.argv[1]
exts = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
page = "services/dashboard/src/app/meetings/[id]/page.tsx"
out = subprocess.run(["git", "diff", "--name-status", "--no-renames",
                      f"{base}..HEAD", "--", "services/dashboard"],
                     capture_output=True, text=True).stdout
bad = []
for line in out.splitlines():
    parts = line.split("\t")
    if len(parts) < 2:
        continue
    st, path = parts[0], parts[-1]
    if st.startswith("D") or path == page or not path.endswith(exts):
        continue
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
        nbytes = os.path.getsize(path)
    except OSError:
        continue
    nlines = len(text.splitlines())
    prev = subprocess.run(["git", "show", f"{base}:{path}"], capture_output=True, text=True)
    if prev.returncode == 0:
        cap_l = max(600, len(prev.stdout.splitlines()) + 80)
        cap_b = max(32000, len(prev.stdout.encode("utf-8")) + 4000)
    else:
        cap_l, cap_b = 600, 32000
    if nlines > cap_l or nbytes > cap_b:
        bad.append(f"{path}({nlines}行/{nbytes}B > {cap_l}行/{cap_b}B)")
if bad:
    print("  上限超過:", ", ".join(bad[:10]))
    sys.exit(1)
PY
check "C16 触れた全コードファイルの行数・バイト上限(新規<=600行/32000B)" $?

# C17: import/re-export しかない passthrough ファイルの禁止(page含む触れた全ファイル)。
# 第1回の「pageを5行のre-exportにする」「薄いファイルを連鎖させる」手口の遮断。
python3 - "$BASE" <<'PY'
import re, subprocess, sys
base = sys.argv[1]
exts = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
out = subprocess.run(["git", "diff", "--name-status", "--no-renames",
                      f"{base}..HEAD", "--", "services/dashboard"],
                     capture_output=True, text=True).stdout
line_ok = re.compile(
    r"^(import\b.*"                      # import 文(先頭行)
    r"|export\s*\{[^}]*\}\s*(from\s*[\"'][^\"']+[\"'])?\s*;?"  # export {..} (from ..)?
    r"|export\s+\*\s+from\b.*"           # export * from
    r"|export\s+default\s+[A-Za-z0-9_$.]+\s*;?"  # export default <識別子>;
    r"|[\"']use (client|server)[\"'];?"
    r"|\}\s*from\s*[\"'][^\"']+[\"'];?"  # 複数行importの閉じ
    r"|[A-Za-z0-9_$]+\s*,?"              # 複数行importのメンバー行
    r")$")
bad = []
for line in out.splitlines():
    parts = line.split("\t")
    if len(parts) < 2:
        continue
    st, path = parts[0], parts[-1]
    if st.startswith("D") or not path.endswith(exts):
        continue
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        continue
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l and not l.startswith("//")]
    if lines and all(line_ok.match(l) for l in lines):
        bad.append(path)
if bad:
    print("  passthroughファイル:", ", ".join(bad[:10]))
    sys.exit(1)
PY
check "C17 import/re-exportのみのpassthroughファイル=0" $?

# C18: src の純増上限(重複コピー・水増しの遮断。移動は純増ゼロ、正当な抽出の
# 界面コスト(import/型/シグネチャ)に +500行/+25000B の予算を与える)。
python3 - "$BASE" <<'PY'
import subprocess, sys
base = sys.argv[1]
num = subprocess.run(["git", "diff", "--numstat", "--no-renames",
                      f"{base}..HEAD", "--", "services/dashboard/src"],
                     capture_output=True, text=True).stdout
dl = 0
for line in num.splitlines():
    parts = line.split("\t")
    if len(parts) == 3 and parts[0] != "-":
        dl += int(parts[0]) - int(parts[1])
db = 0
st = subprocess.run(["git", "diff", "--name-status", "--no-renames",
                     f"{base}..HEAD", "--", "services/dashboard/src"],
                    capture_output=True, text=True).stdout
def size(rev, path):
    p = subprocess.run(["git", "cat-file", "-s", f"{rev}:{path}"],
                       capture_output=True, text=True)
    return int(p.stdout) if p.returncode == 0 else 0
for line in st.splitlines():
    parts = line.split("\t")
    if len(parts) < 2:
        continue
    path = parts[-1]
    db += size("HEAD", path) - size(base, path)
print(f"  src純増: {dl:+}行 / {db:+}B (上限 +500行 / +25000B)")
sys.exit(0 if dl <= 500 and db <= 25000 else 1)
PY
check "C18 srcの純増<=+500行かつ<=+25000B" $?

if [ "$SKIPNODE" = "1" ]; then
  echo "[rf71r][skip] C11a/C11b/C12/C13a/C15 (RF71R_SKIP_NODE=1)"
else
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
fi

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

# C14: base 時点の page.tsx にある日本語文字列(2文字以上の連続)が、page.tsx の
# import 閉包(page から辿れるファイルのみ)にすべて残っていること。
# 「未参照ファイルへ文言だけ退避する」手口を閉包制限で遮断する(移動は許容)。
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
imp = re.compile(r"""from\s+["']([^"']+)["']|import\s*\(\s*["']([^"']+)["']\s*\)|require\s*\(\s*["']([^"']+)["']\s*\)|^\s*import\s+["']([^"']+)["']""", re.M)
SUFFIXES = ["", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx"]
def resolve(spec, from_path):
    if spec.startswith("@/"):
        cand = "services/dashboard/src/" + spec[2:]
    elif spec.startswith("."):
        parts = []
        for part in (pathlib.PurePosixPath(from_path).parent / spec).parts:
            if part == "..":
                parts and parts.pop()
            elif part != ".":
                parts.append(part)
        cand = "/".join(parts)
    else:
        return None
    for s in SUFFIXES:
        p = pathlib.Path(cand + s)
        if p.is_file():
            return str(p)
    return None
start = "services/dashboard/src/app/meetings/[id]/page.tsx"
seen, stack, corpus = set(), [start], ""
while stack:
    path = stack.pop()
    if path in seen or not path.startswith("services/dashboard/"):
        continue
    seen.add(path)
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    corpus += text
    for m in imp.findall(text):
        for spec in m:
            if spec:
                r = resolve(spec, path)
                if r:
                    stack.append(r)
missing = sorted(r for r in runs if r not in corpus)
if missing:
    print("  閉包から消えた文言:", ", ".join(missing[:20]))
    sys.exit(1)
print(f"  文言 {len(runs)} 件すべてpage閉包({len(seen)}ファイル)に残存")
PY
check "C14 日本語文言の不変(base版page.tsxの全文字列がpageのimport閉包に残存)" $?

if [ "$SKIPNODE" != "1" ]; then
  bash .hw/verify.sh >/dev/null 2>&1
  check "C15 リポジトリ検証(.hw/verify.sh)" $?
fi

if [ "$SKIPNODE" = "1" ]; then
  echo "[rf71r] skip-node実行のため常にblock(合格判定には使えない)"
  exit 1
fi
if [ "$fail" = "0" ]; then
  echo "[rf71r] ready: 全検査通過"
else
  echo "[rf71r] block: 上のFAILを修正してcommit後に再実行"
fi
exit "$fail"
