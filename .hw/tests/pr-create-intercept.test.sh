#!/usr/bin/env bash
# pr-create-intercept.sh の単体テスト(検証契約 AT-001〜AT-011)。
#
# 使い方: bash .hw/tests/pr-create-intercept.test.sh <hook-path>
#
# このフックは全 Bash コマンドの PreToolUse で走るため、退行は全エージェントを
# 止める。だから .hw/verify.sh(= pr-ready-gate と CI)から毎回実行する。
#
# fixture: mktemp 下に git リポジトリ R と、ブランチ b1 の worktree W を作り、
# 双方へ「実行時の pwd を記録して指定 exit code で終わる」stub の
# .hw/hooks/pr-ready-gate.sh を未追跡ファイルとして置く。フックがどのディレクトリ
# でゲートを実行したかは、stub が記録した pwd で判定する。
set -u

HOOK_IN="${1:-}"
if [ -z "$HOOK_IN" ]; then
  echo "usage: bash $0 <hook-path>" >&2
  exit 2
fi
if [ ! -f "$HOOK_IN" ]; then
  echo "hook が見つかりません: $HOOK_IN" >&2
  exit 2
fi
HOOK="$(cd "$(dirname "$HOOK_IN")" && pwd -P)/$(basename "$HOOK_IN")"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# TMPDIR が git リポジトリ配下だと AT-011(git 外)が成立しない。設定エラーとして落とす。
if git -C "$TMP" rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "テスト設定エラー: TMPDIR が git リポジトリ配下です ($TMP)" >&2
  exit 2
fi

R="$TMP/R"
W="$TMP/W"
OUTSIDE="$TMP/outside"
RESULT="$TMP/gate-pwd"
mkdir -p "$R" "$OUTSIDE"

git init -q "$R" >/dev/null 2>&1 || { echo "git init 失敗" >&2; exit 2; }
git -C "$R" symbolic-ref HEAD refs/heads/main
git -C "$R" config user.email hw-test@example.com
git -C "$R" config user.name hw-test
git -C "$R" config commit.gpgsign false
printf 'hi\n' > "$R/README.md"
git -C "$R" add README.md
git -C "$R" commit -q -m init >/dev/null 2>&1 || { echo "git commit 失敗" >&2; exit 2; }
git -C "$R" worktree add -q -b b1 "$W" >/dev/null 2>&1 || { echo "git worktree add 失敗" >&2; exit 2; }

install_stub() { # $1=リポジトリ/worktree のパス
  mkdir -p "$1/.hw/hooks"
  cat > "$1/.hw/hooks/pr-ready-gate.sh" <<'STUB'
#!/usr/bin/env bash
# テスト用 stub。実行時の物理 pwd を記録し、指定の exit code で終わる。
pwd -P >> "$HW_TEST_RESULT"
exit "${HW_TEST_GATE_EXIT:-0}"
STUB
}
install_stub "$R"
install_stub "$W"

EXP_R="$(cd "$R" && pwd -P)"
EXP_W="$(cd "$W" && pwd -P)"

mk_payload() { # $1=command $2=cwd(空なら cwd フィールドを付けない)
  HW_T_CMD="$1" HW_T_CWD="${2:-}" python3 -c '
import json, os
data = {
    "session_id": "test",
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": os.environ["HW_T_CMD"]},
}
cwd = os.environ.get("HW_T_CWD", "")
if cwd:
    data["cwd"] = cwd
print(json.dumps(data))
'
}

RC=0
run_hook() { # $1=プロセスcwd $2=payload $3=stub gate の exit code
  : > "$RESULT"
  ( cd "$1" && printf '%s' "$2" | HW_TEST_RESULT="$RESULT" HW_TEST_GATE_EXIT="$3" bash "$HOOK" ) \
    > "$TMP/stdout" 2> "$TMP/stderr"
  RC=$?
}

fail_count=0
check_case() { # $1=ID $2=期待exit $3=期待pwd(NONE=stub未実行)
  id="$1"; exp_rc="$2"; exp_pwd="$3"
  got_pwd="$(cat "$RESULT" 2>/dev/null)"
  ok=1
  [ "$RC" = "$exp_rc" ] || ok=0
  if [ "$exp_pwd" = "NONE" ]; then
    [ -z "$got_pwd" ] || ok=0
  else
    [ "$got_pwd" = "$exp_pwd" ] || ok=0
  fi
  if [ "$ok" = "1" ]; then
    printf 'ok   %s (exit=%s gate_pwd=%s)\n' "$id" "$RC" "${got_pwd:--}"
  else
    printf 'FAIL %s: exit 期待=%s 実際=%s / gate_pwd 期待=%s 実際=%s\n' \
      "$id" "$exp_rc" "$RC" "$exp_pwd" "${got_pwd:--}"
    if [ -s "$TMP/stderr" ]; then
      printf '     stderr: %s\n' "$(tr '\n' ' ' < "$TMP/stderr" | cut -c1-300)"
    fi
    fail_count=$((fail_count + 1))
  fi
}

echo "[hook] $HOOK"
echo "[fixture] R=$EXP_R W=$EXP_W"

# AT-001: gh pr create を含まないコマンドは exit 0 で素通し、gate は呼ばれない。
# 全 Bash コマンドで走る経路なので最重要の不変条件(FP-001)。
run_hook "$R" "$(mk_payload 'ls -la' "$R")" 0
check_case "AT-001" 0 NONE

# AT-001b: 解決不能な要素(未展開シェル変数・git 外 cwd)を含んでも、
# gh pr create でない限り exit 0(fail-closed をこの経路へ漏らさない)。
run_hook "$OUTSIDE" "$(mk_payload 'cd "$VAR" && ls' "$OUTSIDE")" 0
check_case "AT-001b" 0 NONE

# AT-002: cwd=R で cd <W> してから gh pr create → gate は W で走る。
run_hook "$R" "$(mk_payload "cd $W && gh pr create --title x" "$R")" 0
check_case "AT-002" 0 "$EXP_W"

# AT-003: cwd=R で --head b1 → git worktree list 解決で gate は W で走る。
run_hook "$R" "$(mk_payload 'gh pr create --head b1' "$R")" 0
check_case "AT-003" 0 "$EXP_W"

# AT-004: cwd=W で素の gh pr create(EnterWorktree セッションの回帰なし)。
run_hook "$W" "$(mk_payload 'gh pr create' "$W")" 0
check_case "AT-004" 0 "$EXP_W"

# AT-005: cwd=R で素の gh pr create(従来ケースの回帰なし)。
run_hook "$R" "$(mk_payload 'gh pr create' "$R")" 0
check_case "AT-005" 0 "$EXP_R"

# AT-006: payload に cwd フィールドが無い(Codex 互換)→ プロセス cwd を使う。
run_hook "$R" "$(mk_payload 'gh pr create' '')" 0
check_case "AT-006" 0 "$EXP_R"

# AT-007: 存在しないパスへ cd → 解決不能なので exit 2(fail-closed)。
run_hook "$R" "$(mk_payload 'cd /hw-no-such-dir-st11 && gh pr create' "$R")" 0
check_case "AT-007" 2 NONE

# AT-008: 存在しないブランチの --head → exec_dir(R)へフォールバックして検査。
run_hook "$R" "$(mk_payload 'gh pr create --head no-such-branch' "$R")" 0
check_case "AT-008" 0 "$EXP_R"

# AT-009: gate が失敗したらフックは exit 2 で block する。
run_hook "$R" "$(mk_payload 'gh pr create' "$R")" 1
check_case "AT-009" 2 "$EXP_R"

# AT-010: 未展開シェル変数を含む cd は解決できない → exit 2(fail-closed)。
run_hook "$R" "$(mk_payload 'cd "$VAR" && gh pr create' "$R")" 0
check_case "AT-010" 2 NONE

# AT-011: git リポジトリ外で gh pr create → exit 2。
run_hook "$OUTSIDE" "$(mk_payload 'gh pr create' "$OUTSIDE")" 0
check_case "AT-011" 2 NONE

if [ "$fail_count" != "0" ]; then
  echo "[hw][test][FAIL] pr-create-intercept: $fail_count 件不一致"
  exit 1
fi
echo "ok"
exit 0
