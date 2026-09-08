#!/usr/bin/env bash
# Claude/Codex 共通 PreToolUse(Bash): `gh pr create` を物理的にインターセプトし、
# pr-ready-gate を通過するまで PR 作成をブロックする(Layer 2 の強制)。
# 助言は証明にならない — 例外ゼロで守らせたいものはフックに置く。
#
# ゲートを実行すべきディレクトリは次の優先順位で解決する:
#   --head のブランチが載る worktree > コマンド内 cd 連鎖の到達先 >
#   payload の cwd > フックプロセスの cwd
# 解決できない場合は exit 2 で block(fail-closed)。
# ただし `gh pr create` かどうかの判定経路は fail-open のままにする。このフックは
# 全 Bash コマンドで走るため、ここを fail-closed にすると全エージェントが止まる。
set -uo pipefail

payload="$(cat)"

# 解析スクリプトは変数へ読み込んでから実行する。macOS 標準の bash 3.2 は
# コマンド置換 $( ) の中のヒアドキュメントに含まれる引用符/バッククォートを
# 誤解析するため、ヒアドキュメントを $( ) の外に置く必要がある。
IFS='' read -r -d '' HW_INTERCEPT_PY <<'PY'
import json
import os
import re
import subprocess

UNRESOLVABLE = ("$", "`")


def emit(line):
    print(line)
    raise SystemExit(0)


raw = os.environ.get("HW_INTERCEPT_PAYLOAD", "")
try:
    data = json.loads(raw)
    command = data.get("tool_input", {}).get("command", "") or ""
    payload_cwd = data.get("cwd", "") or ""
except Exception:
    # JSON 形式の揺れで無関係コマンドを殺さない(現行と同じ fail-open)。
    emit("SKIP")

if not isinstance(command, str) or "gh pr create" not in command:
    emit("SKIP")

idx = command.find("gh pr create")
prefix, suffix = command[:idx], command[idx:]

base = payload_cwd if payload_cwd and os.path.isdir(payload_cwd) else os.getcwd()

CD_RE = re.compile(r"""(?:^|&&|\|\||;|\n)\s*cd\s+("([^"]*)"|'([^']*)'|([^\s;&|]+))""")
HEAD_RE = re.compile(
    r"""(?:^|\s)(?:--head|-H)(?:=|\s+)("([^"]*)"|'([^']*)'|([^\s;&|]+))"""
)


def token_of(match):
    for group in (2, 3, 4):
        value = match.group(group)
        if value is not None:
            return value
    return ""


exec_dir = base
for match in CD_RE.finditer(prefix):
    token = token_of(match)
    if any(ch in token for ch in UNRESOLVABLE):
        emit("FAIL cd の引数にシェル変数/コマンド置換があり解決できません: %s" % token)
    token = os.path.expanduser(token)
    exec_dir = os.path.normpath(os.path.join(exec_dir, token))

head = ""
head_match = HEAD_RE.search(suffix)
if head_match:
    head = token_of(head_match)
    if any(ch in head for ch in UNRESOLVABLE):
        emit("FAIL --head の値にシェル変数/コマンド置換があり解決できません: %s" % head)
    if ":" in head:
        head = head.split(":", 1)[1]


def git_stdout(cwd, *args):
    try:
        proc = subprocess.run(
            ["git", "-C", cwd] + list(args), capture_output=True, text=True
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


target = exec_dir
if head:
    listing = git_stdout(exec_dir, "worktree", "list", "--porcelain")
    if listing is None and exec_dir != base:
        listing = git_stdout(base, "worktree", "list", "--porcelain")
    if listing:
        current = ""
        wanted = "branch refs/heads/" + head
        for line in listing.splitlines():
            if line.startswith("worktree "):
                current = line[len("worktree "):].strip()
            elif line.strip() == wanted and current:
                # push 済みで checkout していないブランチはヒットしない。その場合は
                # exec_dir のまま検査する(現行と同じ挙動であり、緩和ではない)。
                target = current
                break

toplevel = git_stdout(target, "rev-parse", "--show-toplevel")
if not toplevel or not toplevel.strip():
    emit("FAIL ゲート実行先が git リポジトリではありません: %s" % target)
emit("DIR " + toplevel.strip())
PY

decision="$(
  HW_INTERCEPT_PAYLOAD="$payload" python3 -c "$HW_INTERCEPT_PY" 2>/dev/null || true
)"

case "$decision" in
  "DIR "*)
    TARGET_DIR="${decision#DIR }"
    ;;
  "FAIL "*)
    echo "hw: gh pr create を検出しましたが、pr-ready-gate を実行すべきディレクトリを解決できません。" >&2
    echo "hw: ${decision#FAIL }" >&2
    echo "cd を絶対パスで書くか、対象の worktree 内で PR を作成してください。" >&2
    exit 2
    ;;
  *)
    # SKIP / 想定外の出力 = `gh pr create` ではないとみなして素通し。
    exit 0
    ;;
esac

if ! cd "$TARGET_DIR"; then
  echo "hw: 解決したディレクトリへ移動できません: $TARGET_DIR" >&2
  exit 2
fi

TASK_ID=""
[ -f .hw/current/task-id ] && TASK_ID="$(cat .hw/current/task-id)"

mkdir -p .hw/state
if bash .hw/hooks/pr-ready-gate.sh "$TASK_ID" > .hw/state/intercept.log 2>&1; then
  exit 0
fi

echo "hw: pr-ready-gate がblock(実行先: $TARGET_DIR)。詳細は $TARGET_DIR/.hw/state/intercept.log。" >&2
echo "bash .hw/hooks/pr-ready-gate.sh ${TASK_ID:-<task-id>} を通してからPRを作成してください。" >&2
exit 2
