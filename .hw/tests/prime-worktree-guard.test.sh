#!/usr/bin/env bash
# prime_run.py の ensure_worktree fail-closed 化の単体テスト
# (検証契約 AT-101〜AT-110 / FP-101〜FP-103 / NFT-101)。
#
# 使い方: bash .hw/tests/prime-worktree-guard.test.sh <prime_run.py のパス>
#
# fixture: mktemp 下に runtime=prime のプラン一式を commit した使い捨てリポジトリを
# 作り、HW_PRIME_CLI に「実行時の pwd をマーカーファイルへ書いて exit する」stub、
# HW_PRIME_WORKTREE_ROOT に一時ディレクトリを差して prime_run.py を丸ごと実行する。
# 実 Prime Agent は起動しない。「Prime が起動したか」はマーカーの有無だけで判定する
# (出力文字列では判定しない = 警告を print して続行する迂回を通さない)。
set -u

PRIME_IN="${1:-}"
if [ -z "$PRIME_IN" ]; then
  echo "usage: bash $0 <prime_run.py>" >&2
  exit 2
fi
if [ ! -f "$PRIME_IN" ]; then
  echo "prime_run.py が見つかりません: $PRIME_IN" >&2
  exit 2
fi
PRIME="$(cd "$(dirname "$PRIME_IN")" && pwd -P)/$(basename "$PRIME_IN")"

# 実行者の環境が漏れると判定が変わるので、この検査が使う env は必ず未設定から始める。
unset HW_PRIME_WORKTREE_MODE HW_PRIME_WORKTREE HW_PRIME_WORKTREE_ROOT \
      HW_PRIME_CLI HW_PRIME_GATE HW_PRIME_ON_FAIL 2>/dev/null || true

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

TASK="demo-task"
BRANCH="hw/prime/$TASK"
STUB="$TMP/prime-agent-stub.sh"

cat > "$STUB" <<'STUB'
#!/usr/bin/env bash
# テスト用 stub。実行時の物理 pwd を記録し、指定の exit code で終わる。
pwd -P >> "$HW_TEST_MARKER"
exit "${HW_TEST_STUB_EXIT:-0}"
STUB
chmod +x "$STUB"

die() { echo "テスト設定エラー: $*" >&2; exit 2; }

REPO=""; WTROOT=""; MARKER=""; WT=""; RC=0

setup_repo() { # $1=ケース名
  REPO="$TMP/$1/repo"
  WTROOT="$TMP/$1/wt"
  MARKER="$TMP/$1/marker"
  WT="$WTROOT/$TASK"
  mkdir -p "$REPO" "$WTROOT"
  git init -q "$REPO" >/dev/null 2>&1 || die "git init 失敗 ($1)"
  git -C "$REPO" symbolic-ref HEAD refs/heads/main
  git -C "$REPO" config user.email hw-test@example.com
  git -C "$REPO" config user.name hw-test
  git -C "$REPO" config commit.gpgsign false
  # 本番リポジトリと同じく .hw/state と .hw/gates は追跡しない
  # (追跡すると prime_run.py 自身の書き込みで clean-tree 検査が落ちる)。
  printf '.hw/state/\n.hw/gates/\n' > "$REPO/.gitignore"
  printf 'hi\n' > "$REPO/README.md"
  git -C "$REPO" add .gitignore README.md
  git -C "$REPO" commit -q -m init >/dev/null 2>&1 || die "git commit 失敗 ($1)"

  mkdir -p "$REPO/.hw/plans/$TASK"
  cat > "$REPO/.hw/plans/$TASK/plan.md" <<'PLAN'
---
generated_by: fable
task_id: demo-task
size: M
runtime: prime
---

# demo-task

## How

テスト用のダミープラン。

## Why(実装者に渡さない)

テスト用。
PLAN
  printf '# contract\n\n- AT-001: dummy\n' > "$REPO/.hw/plans/$TASK/verification-contract.md"
  printf '{"runtime": "prime"}\n' > "$REPO/.hw/plans/$TASK/runtime-decision.json"
  git -C "$REPO" rev-parse HEAD > "$REPO/.hw/plans/$TASK/base-commit"
  git -C "$REPO" add .hw
  git -C "$REPO" commit -q -m plan >/dev/null 2>&1 || die "plan commit 失敗 ($1)"
}

run_case() { # $@ = 追加の環境変数(VAR=value 形式)
  rm -f "$MARKER"
  (
    cd "$REPO" || exit 99
    env HW_PRIME_CLI="$STUB" \
        HW_PRIME_WORKTREE_ROOT="$WTROOT" \
        HW_TEST_MARKER="$MARKER" \
        ${1+"$@"} \
        python3 "$PRIME" "$TASK"
  ) > "$TMP/stdout" 2> "$TMP/stderr"
  RC=$?
}

snapshot_dir() { # $1=対象ディレクトリ。中身(パスと内容)の一覧を出す
  ( cd "$1" && find . -mindepth 1 | sort | while read -r entry; do
      if [ -f "$entry" ]; then
        printf 'F %s %s\n' "$entry" "$(cksum < "$entry")"
      else
        printf 'D %s\n' "$entry"
      fi
    done )
}

fail_count=0
CASE_ID=""
CASE_BASE=0

case_start() { CASE_ID="$1"; CASE_BASE="$fail_count"; printf '[case] %s\n' "$1"; }
case_end() {
  if [ "$fail_count" = "$CASE_BASE" ]; then
    printf 'ok   %s\n' "$CASE_ID"
  fi
}
bad() {
  printf 'FAIL %s: %s\n' "$CASE_ID" "$1"
  if [ -s "$TMP/stderr" ]; then
    printf '     stderr: %s\n' "$(tr '\n' ' ' < "$TMP/stderr" | cut -c1-400)"
  fi
  fail_count=$((fail_count + 1))
}

expect_rc_zero() { [ "$RC" = "0" ] || bad "exit code 期待=0 実際=$RC"; }
expect_rc_nonzero() { [ "$RC" != "0" ] || bad "exit code 期待=非0 実際=$RC"; }
expect_rc_is() { [ "$RC" = "$1" ] || bad "exit code 期待=$1 実際=$RC"; }
expect_marker() { [ -e "$MARKER" ] || bad "stubが起動していない(マーカー不在)"; }
expect_no_marker() {
  if [ -e "$MARKER" ]; then
    bad "stubが起動してしまった(cwd=$(tr '\n' ' ' < "$MARKER"))"
  fi
}
expect_eq() { # $1=ラベル $2=期待 $3=実際
  [ "$2" = "$3" ] || bad "$1 期待=$2 実際=$3"
}
expect_stdout_has() { grep -qF -- "$1" "$TMP/stdout" || bad "stdoutに $1 が無い"; }
expect_stderr_has() { grep -qF -- "$1" "$TMP/stderr" || bad "stderrに $1 が無い"; }

echo "[prime_run] $PRIME"
echo "[fixture] TMP=$TMP"

# ---------------------------------------------------------------------------
# AT-101 / AT-110 / FP-103 / AT-102 / NFT-101 / AT-103 / AT-104 / AT-105
# ---------------------------------------------------------------------------
setup_repo base
HEAD0="$(git -C "$REPO" rev-parse HEAD)"

# AT-101: 初回(worktree も branch も無い)。mode 未設定で現行どおり作成される。
case_start "AT-101"
run_case
expect_rc_zero
expect_marker
expect_eq "worktreeのHEAD" "$HEAD0" "$(git -C "$WT" rev-parse HEAD 2>/dev/null)"
expect_eq "worktreeのブランチ" "$BRANCH" "$(git -C "$WT" symbolic-ref --short HEAD 2>/dev/null)"
expect_eq "stubのcwd" "$(cd "$WT" 2>/dev/null && pwd -P)" "$(cat "$MARKER" 2>/dev/null)"
case_end

# AT-110: ready 時に後始末コマンドが出て、prime-run.json に worktree_mode が載る。
case_start "AT-110"
expect_stdout_has "git worktree remove"
expect_stdout_has "git branch -d"
mode_recorded="$(python3 -c '
import json, sys
print(json.load(open(sys.argv[1])).get("worktree_mode", ""))
' "$REPO/.hw/gates/$TASK/prime-run.json" 2>/dev/null)"
expect_eq "prime-run.json の worktree_mode" "initial" "$mode_recorded"
case_end

# FP-103: 成功しても worktree を自動削除しない。
case_start "FP-103"
[ -d "$WT" ] || bad "ready 後に worktree が消えている"
case_end

# AT-102 / NFT-101: 既存 worktree + mode 未設定 → 停止し、stub は起動しない。
case_start "AT-102"
run_case
expect_rc_nonzero
expect_no_marker
expect_stderr_has "$WT"
expect_stderr_has "HW_PRIME_WORKTREE_MODE=resume"
expect_stderr_has "HW_PRIME_WORKTREE_MODE=fresh"
expect_stderr_has "git worktree list"
expect_stderr_has "dirty_count"
expect_stderr_has "head_branch"
expect_stderr_has "$BRANCH"
case_end

case_start "NFT-101"
# 停止メッセージだけで次の一手が打てること(絶対path・状態・両モード・手動確認)。
grep -qF -- "$WTROOT/$TASK" "$TMP/stderr" || bad "worktree の絶対 path が無い"
grep -qE 'registered' "$TMP/stderr" || bad "registered 状態が無い"
grep -qE 'git -C .* status' "$TMP/stderr" || bad "手動確認コマンドが無い"
case_end

# AT-103: mode=resume + 登録済み・自ブランチ attach・clean → 再利用。
case_start "AT-103"
run_case HW_PRIME_WORKTREE_MODE=resume
expect_rc_zero
expect_marker
expect_eq "worktreeのHEAD(不変)" "$HEAD0" "$(git -C "$WT" rev-parse HEAD 2>/dev/null)"
expect_eq "stubのcwd" "$(cd "$WT" && pwd -P)" "$(cat "$MARKER" 2>/dev/null)"
case_end

# AT-104: mode=resume + dirty(未追跡ファイル)→ 停止。
case_start "AT-104"
printf 'wip\n' > "$WT/untracked.txt"
run_case HW_PRIME_WORKTREE_MODE=resume
expect_rc_nonzero
expect_no_marker
rm -f "$WT/untracked.txt"
case_end

# AT-105a: mode=resume + clean だが detached HEAD → 停止。
case_start "AT-105a"
git -C "$WT" checkout -q --detach || die "detach 失敗"
run_case HW_PRIME_WORKTREE_MODE=resume
expect_rc_nonzero
expect_no_marker
git -C "$WT" checkout -q "$BRANCH" || die "再attach 失敗"
case_end

# AT-105b: mode=resume + clean だが別ブランチに attach → 停止。
case_start "AT-105b"
git -C "$WT" checkout -q -b other-branch || die "別ブランチ作成 失敗"
run_case HW_PRIME_WORKTREE_MODE=resume
expect_rc_nonzero
expect_no_marker
case_end

# ---------------------------------------------------------------------------
# AT-106: 未登録ディレクトリはどのモードでも削除せず停止する。
# ---------------------------------------------------------------------------
setup_repo at106
mkdir -p "$WT/sub"
printf 'precious\n' > "$WT/keep.txt"
printf 'nested\n' > "$WT/sub/nested.txt"
before="$(snapshot_dir "$WT")"

for mode_arg in "" "HW_PRIME_WORKTREE_MODE=resume" "HW_PRIME_WORKTREE_MODE=fresh"; do
  case_start "AT-106 (${mode_arg:-mode未設定})"
  if [ -z "$mode_arg" ]; then
    run_case
  else
    run_case "$mode_arg"
  fi
  expect_rc_nonzero
  expect_no_marker
  [ -d "$WT" ] || bad "未登録ディレクトリが削除された"
  expect_eq "ディレクトリ内容" "$before" "$(snapshot_dir "$WT")"
  expect_eq "branch $BRANCH の有無" "" "$(git -C "$REPO" branch --list "$BRANCH")"
  case_end
done

# ---------------------------------------------------------------------------
# AT-107: mode=fresh は worktree を除去し branch を現 HEAD から作り直す。
# ---------------------------------------------------------------------------
setup_repo at107
run_case
[ "$RC" = "0" ] || die "AT-107 の前提となる初回作成に失敗 (exit $RC)"
printf 'work\n' > "$WT/work.txt"
git -C "$WT" add work.txt
git -C "$WT" commit -q -m "前回の作業" || die "worktree での commit 失敗"
OLD="$(git -C "$WT" rev-parse HEAD)"
printf 'dirty\n' > "$WT/dirty.txt"
printf 'next\n' > "$REPO/NEXT.md"
git -C "$REPO" add NEXT.md
git -C "$REPO" commit -q -m "main を進める" || die "main での commit 失敗"
HEAD1="$(git -C "$REPO" rev-parse HEAD)"

case_start "AT-107"
run_case HW_PRIME_WORKTREE_MODE=fresh
expect_rc_zero
expect_marker
expect_eq "新worktreeのHEAD" "$HEAD1" "$(git -C "$WT" rev-parse HEAD 2>/dev/null)"
expect_eq "新worktreeのブランチ" "$BRANCH" "$(git -C "$WT" symbolic-ref --short HEAD 2>/dev/null)"
if git -C "$REPO" rev-list "$BRANCH" | grep -qF "$OLD"; then
  bad "旧コミット $OLD が $BRANCH の履歴に残っている"
fi
[ -f "$WT/dirty.txt" ] && bad "旧worktreeの作業ファイルが残っている"
mode_recorded="$(python3 -c '
import json, sys
print(json.load(open(sys.argv[1])).get("worktree_mode", ""))
' "$REPO/.hw/gates/$TASK/prime-run.json" 2>/dev/null)"
expect_eq "prime-run.json の worktree_mode" "fresh" "$mode_recorded"
case_end

# ---------------------------------------------------------------------------
# AT-108: 不正な mode 値は既定にも fresh にも落とさず停止する。
# ---------------------------------------------------------------------------
setup_repo at108
run_case
[ "$RC" = "0" ] || die "AT-108 の前提となる初回作成に失敗 (exit $RC)"

case_start "AT-108"
run_case HW_PRIME_WORKTREE_MODE=yolo
expect_rc_nonzero
expect_no_marker
expect_stderr_has "resume"
expect_stderr_has "fresh"
case_end

# ---------------------------------------------------------------------------
# AT-109: branch だけ残存 + mode 未設定 → 黙った再作成をしない。
# ---------------------------------------------------------------------------
setup_repo at109
run_case
[ "$RC" = "0" ] || die "AT-109 の前提となる初回作成に失敗 (exit $RC)"
git -C "$REPO" worktree remove --force "$WT" || die "worktree remove 失敗"
[ -d "$WT" ] && die "AT-109 の前提: worktree ディレクトリが残っている"
[ -n "$(git -C "$REPO" branch --list "$BRANCH")" ] || die "AT-109 の前提: branch が残っていない"

case_start "AT-109"
run_case
expect_rc_nonzero
expect_no_marker
[ -e "$WT" ] && bad "branch 先端から worktree が黙って再作成された"
case_end

# ---------------------------------------------------------------------------
# FP-101: HW_PRIME_WORKTREE=0 は現行どおり root で走る(モード検査に入らない)。
# ---------------------------------------------------------------------------
setup_repo fp101
run_case
[ "$RC" = "0" ] || die "FP-101 の前提となる初回作成に失敗 (exit $RC)"

case_start "FP-101"
run_case HW_PRIME_WORKTREE=0
expect_rc_zero
expect_marker
expect_eq "stubのcwd" "$(cd "$REPO" && pwd -P)" "$(cat "$MARKER" 2>/dev/null)"
case_end

# ---------------------------------------------------------------------------
# FP-102: 既存ガード(clean tree 必須)が worktree 分岐より先に効く。
# ---------------------------------------------------------------------------
setup_repo fp102
printf 'dirty\n' > "$REPO/dirty.txt"

case_start "FP-102"
run_case
expect_rc_is 2
expect_no_marker
[ -e "$WT" ] && bad "dirty tree なのに worktree が作られた"
[ -n "$(git -C "$REPO" branch --list "$BRANCH")" ] && bad "dirty tree なのに branch が作られた"
case_end

if [ "$fail_count" != "0" ]; then
  echo "[hw][test][FAIL] prime-worktree-guard: $fail_count 件不一致"
  exit 1
fi
echo "ok"
exit 0
