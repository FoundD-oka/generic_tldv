#!/usr/bin/env python3
"""Run an R-axis `prime` task through Prime Agent with the hw gate as stop condition.

Prime Agent は「終わった」と自己申告できない。--autonomous-gate に pr-ready-gate を
渡すので、ゲートが通るまでセッションが終われない。長時間走ったことは完了の証拠に
ならない、という hw の原則をそのまま実行基盤に持ち込む。

既定値は決め打ちで、実運用で外したら HW_PRIME_* で上書きする(fable_review.py と
同じ方針)。Prime Agent は開発が速いので、ここは薄く保って追従コストを下げる。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Optional


TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# 既定値。R軸で prime を選ぶのは長時間・大量状態のタスクなので、
# Prime Agent 側の既定(30分 / 12ターン / 80k tokens)より大きく取る。
DEFAULTS = {
    "HW_PRIME_CLI": "prime-agent",
    "HW_PRIME_WORKTREE": "1",
    "HW_PRIME_ON_FAIL": "escalate",  # escalate | inline
    "HW_PRIME_MAX_PROMPT_BYTES": "200000",
    "HW_PRIME_TIMEOUT_SECONDS": "10800",  # プロセス全体の上限(3h)
    "HW_PRIME_AUTONOMOUS_TIMEOUT_MS": "7200000",  # 自律継続の上限(2h)
    "HW_PRIME_MAX_TURNS": "60",
    "HW_PRIME_MAX_TOKENS": "500000",
    "HW_PRIME_MAX_CONTINUATIONS": "20",
    "HW_PRIME_GATE_RETRIES": "3",
    "HW_PRIME_GATE_TIMEOUT_MS": "600000",
}


class PrimeRunError(RuntimeError):
    pass


def env(name: str) -> str:
    return os.environ.get(name, DEFAULTS[name])


def env_int(name: str) -> int:
    raw = env(name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise PrimeRunError(f"{name} は整数で指定してください: {raw!r}") from exc
    if value <= 0:
        raise PrimeRunError(f"{name} は正の整数で指定してください: {value}")
    return value


def run_git(root: pathlib.Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise PrimeRunError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def repo_root(cwd: Optional[pathlib.Path] = None) -> pathlib.Path:
    start = cwd or pathlib.Path.cwd()
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise PrimeRunError("Gitリポジトリ内で実行してください")
    return pathlib.Path(proc.stdout.strip())


def validate_task_id(task_id: str) -> None:
    if not TASK_ID_RE.fullmatch(task_id):
        raise PrimeRunError(
            "task-id は英数字で始まり、英数字・.・_・- だけを使用してください"
        )


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrimeRunError(f"{path} を有効なJSONとして読めません: {exc}") from exc
    if not isinstance(value, dict):
        raise PrimeRunError(f"{path} はJSON objectである必要があります")
    return value


def plan_generated_by_fable(text: str) -> bool:
    """plan.md の frontmatter が generated_by: fable かどうか。

    frontmatter だけを見る。ファイル全体を検索すると、本文のどこかに
    "generated_by: fable" と書くだけでこの検査を迂回できてしまう。
    """
    matched = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not matched:
        return False
    return bool(
        re.search(r"^generated_by:[ \t]*fable[ \t]*$", matched.group(1), re.M)
    )


def strip_why(text: str) -> str:
    """plan.md から `## Why(実装者に渡さない)` セクションを落とす。

    実行役に Why を渡さないのは、ゴールの背景から自己合格判定へ回る経路を断つため。
    ファイルごと読ませず prompt へ埋め込むのは、この除去を確実にするため。
    """
    kept, skipping = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            skipping = line.startswith("## Why")
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip()


def load_run_context(root: pathlib.Path, task_id: str) -> dict[str, Any]:
    validate_task_id(task_id)
    plan_dir = root / ".hw" / "plans" / task_id
    if not plan_dir.is_dir():
        raise PrimeRunError(f"{plan_dir} がありません")

    decision_path = plan_dir / "runtime-decision.json"
    if not decision_path.is_file():
        raise PrimeRunError(
            f"{decision_path} がありません。R軸を判定してから実行してください"
        )
    runtime = str(load_json(decision_path).get("runtime", ""))
    if runtime != "prime":
        raise PrimeRunError(
            f"runtime が prime ではありません({runtime or '未設定'})。"
            "inline タスクは implementer subagent で実行してください"
        )

    plan_path = plan_dir / "plan.md"
    contract_path = plan_dir / "verification-contract.md"
    base_path = plan_dir / "base-commit"
    for path in (plan_path, contract_path, base_path):
        if not path.is_file():
            raise PrimeRunError(f"{path} がありません")

    plan_text = plan_path.read_text(encoding="utf-8")
    if not plan_generated_by_fable(plan_text):
        raise PrimeRunError(
            "plan.md の frontmatter に generated_by: fable がありません。"
            "プランは planner 経由必須"
        )

    return {
        "root": root,
        "plan_dir": plan_dir,
        "how": strip_why(plan_text),
        "contract": contract_path.read_text(encoding="utf-8"),
        "base": base_path.read_text(encoding="utf-8").strip(),
        "head": run_git(root, "rev-parse", "HEAD"),
    }


def build_prompt(task_id: str, context: dict[str, Any], gate: str) -> str:
    return f"""あなたは hw ハーネスの実行役です。承認済みプランの How と検証契約に従って
実装し、commit してください。プランの再解釈と契約の変更はしません。

対象task: {task_id}
base commit: {context["base"]}

規律:
- 契約の合格ラインを満たすことがゴール。それを超える作り込みはしない。
- テストの削除・skip・期待値の緩和で通さない。
- How が実装不能・矛盾していると判明したら、独自判断で別解に切り替えず、
  その事実を報告して停止する。
- 完了条件は次のゲートが通ることであり、あなたの自己判断ではない:
  `{gate}`
- 検証は commit 済みの状態に対して行われる。dirty tree のまま終了しない。
- 人間向けの報告は日本語。

--- プラン(How) ---
{context["how"]}

--- 検証契約 ---
{context["contract"]}
"""


def resolve_cli(command: str) -> str:
    if os.sep in command:
        path = pathlib.Path(command)
        if not path.is_file():
            raise PrimeRunError(f"Prime Agent CLIが見つかりません: {command}")
        return str(path)
    resolved = shutil.which(command)
    if not resolved:
        raise PrimeRunError(
            f"Prime Agent CLIが見つかりません: {command}。"
            "https://github.com/PrimeIntellect-ai/prime-agent の手順で導入してください"
        )
    return resolved


WORKTREE_MODES = ("resume", "fresh")


def worktree_state(
    root: pathlib.Path,
    path: pathlib.Path,
    branch: str,
    base: str = "",
) -> dict[str, Any]:
    """worktree path と branch の実状態を集める(判定と状態レポートの材料)。

    `path.is_dir()` だけでは「git が知らないゴミディレクトリ」と「正規の worktree」
    を区別できないので、`git worktree list --porcelain` の登録有無を必ず見る。
    """
    resolved = path.expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:  # pragma: no cover - resolve は strict=False で基本落ちない
        pass

    registered = False
    for line in run_git(root, "worktree", "list", "--porcelain").splitlines():
        if not line.startswith("worktree "):
            continue
        entry = pathlib.Path(line[len("worktree ") :])
        try:
            entry = entry.resolve()
        except OSError:  # pragma: no cover
            pass
        if entry == resolved:
            registered = True

    branch_exists = bool(run_git(root, "branch", "--list", branch))

    head_branch: Optional[str] = None
    dirty_count = 0
    if registered and path.is_dir():
        head_branch = (
            run_git(path, "symbolic-ref", "--short", "HEAD", check=False) or None
        )
        status = run_git(
            path, "status", "--porcelain", "--untracked-files=all", check=False
        )
        dirty_count = len([line for line in status.splitlines() if line.strip()])

    # 表示専用。branch 作成時点は記録が無いので base-commit からの本数で近似する。
    ahead_count: Optional[int] = None
    if branch_exists and base:
        raw = run_git(root, "rev-list", "--count", f"{base}..{branch}", check=False)
        if raw.isdigit():
            ahead_count = int(raw)

    return {
        "path": path,
        "exists": path.exists(),
        "registered": registered,
        "branch": branch,
        "branch_exists": branch_exists,
        "head_branch": head_branch,
        "dirty_count": dirty_count,
        "ahead_count": ahead_count,
    }


def format_worktree_state(state: dict[str, Any]) -> str:
    ahead = state["ahead_count"]
    return (
        f"  worktree path : {state['path']}\n"
        f"  存在(exists)  : {'yes' if state['exists'] else 'no'}\n"
        f"  git登録(registered): {'yes' if state['registered'] else 'no'}\n"
        f"  branch {state['branch']} : {'あり' if state['branch_exists'] else 'なし'}\n"
        f"  worktreeのHEAD(head_branch): {state['head_branch'] or 'detached/不明'}\n"
        f"  未コミット変更(dirty_count): {state['dirty_count']}\n"
        f"  base-commitからの先行コミット(ahead_count): "
        f"{'不明' if ahead is None else ahead}"
    )


def worktree_choice_help(state: dict[str, Any]) -> str:
    return (
        "既存の worktree / branch があります。再開か作り直しかは機械には判断できません。\n"
        f"{format_worktree_state(state)}\n"
        "次のどちらかを明示してください:\n"
        "  HW_PRIME_WORKTREE_MODE=resume  前回の続きから再開する"
        "(登録済み・自ブランチにattach・cleanのときだけ許可)\n"
        "  HW_PRIME_WORKTREE_MODE=fresh   worktreeとbranchを作り直す"
        "(現在のHEADから)\n"
        "手動確認: git worktree list / "
        f"git -C {state['path']} status\n"
        f"手動で処分する場合: git worktree remove --force {state['path']} && "
        f"git branch -D {state['branch']}"
    )


def ensure_worktree(
    root: pathlib.Path, task_id: str, head: str, base: str = ""
) -> tuple[pathlib.Path, str]:
    """作業用の worktree を用意し、(作業ディレクトリ, 使ったモード) を返す。

    隔離するのは Prime Agent がサンドボックスではないから。base-commit ではなく
    現在の HEAD から切るのは、plan.md 自体が base-commit の後に commit されるため。
    成功しても自動で消さない(人間が成果を確認してから消す)。

    既存の worktree / branch が在るときは、状態を検査せずに再利用しない。
    「前回の続き」か「作り直し」かは人間しか決められないので、既定は停止し
    HW_PRIME_WORKTREE_MODE の明示を求める(HW_PRIME_GATE と同型の上書き)。
    """
    mode = os.environ.get("HW_PRIME_WORKTREE_MODE", "").strip()
    if mode and mode not in WORKTREE_MODES:
        raise PrimeRunError(
            f"HW_PRIME_WORKTREE_MODE の値が不正です: {mode!r}。"
            f"有効値は {' / '.join(WORKTREE_MODES)} のみです"
        )

    if env("HW_PRIME_WORKTREE") != "1":
        return root, "disabled"

    default_root = root.parent / f"{root.name}.hw-worktrees"
    worktree_root = pathlib.Path(
        os.environ.get("HW_PRIME_WORKTREE_ROOT", str(default_root))
    ).expanduser()
    path = worktree_root / task_id
    branch = f"hw/prime/{task_id}"

    state = worktree_state(root, path, branch, base)

    def create() -> pathlib.Path:
        worktree_root.mkdir(parents=True, exist_ok=True)
        run_git(root, "worktree", "add", "-b", branch, str(path), head)
        print(f"[hw][prime] worktree作成: {path} (branch: {branch})")
        return path

    if not state["exists"] and not state["branch_exists"]:
        return create(), "initial"

    if not mode:
        raise PrimeRunError(worktree_choice_help(state))

    if mode == "resume":
        missing = []
        if not state["exists"]:
            missing.append(f"worktree {path} が存在しない")
        if not state["registered"]:
            missing.append("git worktree として登録されていない")
        if state["head_branch"] != branch:
            missing.append(
                f"worktreeのHEADが {branch} ではない"
                f"({state['head_branch'] or 'detached/不明'})"
            )
        if state["dirty_count"]:
            missing.append(f"未コミットの変更が {state['dirty_count']} 件ある")
        if missing:
            raise PrimeRunError(
                "resume の条件を満たしていません:\n"
                + "".join(f"  - {item}\n" for item in missing)
                + format_worktree_state(state)
                + "\n作り直してよいなら HW_PRIME_WORKTREE_MODE=fresh を指定してください"
            )
        print(f"[hw][prime] 既存のworktreeを再利用(resume): {path}")
        return path, "resume"

    # mode == "fresh"
    if state["exists"] and not state["registered"]:
        raise PrimeRunError(
            f"{path} は git worktree として登録されていないディレクトリです。"
            "中身を判断できないため自動削除しません。人間が確認して処分してから"
            "再実行してください\n" + format_worktree_state(state)
        )
    if state["registered"]:
        if state["exists"]:
            run_git(root, "worktree", "remove", "--force", str(path))
        run_git(root, "worktree", "prune")
        print(f"[hw][prime] 既存worktreeを除去(fresh): {path}")
    if state["branch_exists"]:
        run_git(root, "branch", "-D", branch)
        print(f"[hw][prime] 既存branchを削除(fresh): {branch}")
    return create(), "fresh"


def atomic_write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def run_prime(task_id: str) -> int:
    root = repo_root()
    if run_git(root, "status", "--porcelain", "--untracked-files=all"):
        raise PrimeRunError(
            "commit済みのclean treeで実行してください(プランを先にcommitする)"
        )

    context = load_run_context(root, task_id)
    default_gate = f"bash .hw/hooks/pr-ready-gate.sh {task_id}"
    # M/L の prime タスクでは、pr-ready-gate が要求する Fable READY を実行役が
    # 作れない(レビューは read-only の別工程で、READY は差分hashに束縛される)。
    # 停止条件だけを HW_PRIME_GATE で差し替え可能にする。既定は現行のまま
    # fail-closed。上書きした値は prime-run.json の gate_command に記録され、
    # 最終権威は変わらず pr-ready-gate と CI(PR前に必ず全ゲートを通す)。
    gate = os.environ.get("HW_PRIME_GATE", "").strip() or default_gate
    if gate != default_gate:
        print(f"[hw][prime] 停止条件を上書き: {gate}")
    prompt = build_prompt(task_id, context, gate)
    max_prompt = env_int("HW_PRIME_MAX_PROMPT_BYTES")
    if len(prompt.encode("utf-8")) > max_prompt:
        raise PrimeRunError(
            f"入力が上限 {max_prompt} bytes を超えました。タスクを分割してください"
        )

    workdir, worktree_mode = ensure_worktree(
        root, task_id, context["head"], context["base"]
    )
    cli = resolve_cli(env("HW_PRIME_CLI"))
    command = [
        cli,
        "--mode",
        "json",
        "--autonomous",
        "--autonomous-gate",
        gate,
        "--autonomous-gate-retries",
        str(env_int("HW_PRIME_GATE_RETRIES")),
        "--autonomous-gate-timeout-ms",
        str(env_int("HW_PRIME_GATE_TIMEOUT_MS")),
        "--autonomous-max-continuations",
        str(env_int("HW_PRIME_MAX_CONTINUATIONS")),
        "--autonomous-max-turns",
        str(env_int("HW_PRIME_MAX_TURNS")),
        "--autonomous-max-tokens",
        str(env_int("HW_PRIME_MAX_TOKENS")),
        "--autonomous-timeout-ms",
        str(env_int("HW_PRIME_AUTONOMOUS_TIMEOUT_MS")),
        prompt,
    ]

    state_dir = root / ".hw" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    transcript = state_dir / f"prime-{task_id}.jsonl"
    started = datetime.now(timezone.utc).isoformat()
    print(f"[hw][prime] 起動: {workdir}")
    print(f"[hw][prime] ゲート: {gate}")
    print(f"[hw][prime] 証跡: {transcript}")

    # イベントストリームは走行中に落ちても残るよう、逐次書き出す。
    with transcript.open("w", encoding="utf-8") as handle:
        proc = subprocess.run(
            command,
            cwd=workdir,
            stdout=handle,
            stderr=subprocess.PIPE,
            text=True,
            timeout=env_int("HW_PRIME_TIMEOUT_SECONDS"),
        )

    finished = datetime.now(timezone.utc).isoformat()
    head_after = run_git(workdir, "rev-parse", "HEAD", check=False)
    on_fail = env("HW_PRIME_ON_FAIL")
    if proc.returncode == 0:
        status = "ready"
    elif on_fail == "inline":
        status = "fallback-inline"
    else:
        status = "escalated"

    # 実行記録は .hw/gates/(gitignore済み)へ。.hw/plans/ に書くと、この JSON 自体が
    # 次回実行や pr-ready-gate の clean-tree 検査を壊す。
    atomic_write_json(
        root / ".hw" / "gates" / task_id / "prime-run.json",
        {
            "schema_version": "1.0",
            "task_id": task_id,
            "provider": "prime-agent",
            "runtime": "prime",
            "gate_command": gate,
            "base_commit": context["base"],
            "start_commit": context["head"],
            "head_commit": head_after,
            "worktree": str(workdir),
            "worktree_mode": worktree_mode,
            "started_at": started,
            "finished_at": finished,
            "exit_code": proc.returncode,
            "status": status,
            "transcript": str(transcript.relative_to(root)),
        },
    )

    if status == "ready":
        print(f"[hw][prime] ready: ゲート通過。worktree {workdir} を確認してPRを作る")
        if workdir != root:
            print(
                f"[hw][prime] PRマージ後の後始末: git worktree remove {workdir} && "
                f"git branch -d hw/prime/{task_id}"
            )
        return 0

    detail = (proc.stderr or "")[-2000:].strip()
    if detail:
        print(f"[hw][prime] stderr(末尾):\n{detail}", file=sys.stderr)
    if status == "fallback-inline":
        print(
            "[hw][prime] ゲート未通過。HW_PRIME_ON_FAIL=inline のため、"
            "implementer subagent での実行に切り替えてください",
            file=sys.stderr,
        )
    else:
        print(
            "[hw][prime] ゲート未通過。自動リトライは尽きた。"
            f"証跡 {transcript} を読み、人間が原因を判断すること",
            file=sys.stderr,
        )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="R軸 prime のタスクを Prime Agent で実行する"
    )
    parser.add_argument("task_id")
    args = parser.parse_args()
    try:
        return run_prime(args.task_id)
    except (PrimeRunError, subprocess.TimeoutExpired, ValueError) as exc:
        print(f"[hw][prime][FAIL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
