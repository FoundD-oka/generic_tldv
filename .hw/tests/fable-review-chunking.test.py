#!/usr/bin/env python3
"""fable_review.py のチャンク分割レビューの単体テスト(検証契約 AT-001..AT-009 /
FP-001 / FP-002 / NFT-001..NFT-003)。

使い方: python3 .hw/tests/fable-review-chunking.test.py [fable_review.py のパス]

fixture: mktemp 下に git リポジトリを作り、`.hw/plans/<t>/` (size M の
sml-decision・検証契約・base-commit) を commit した後、複数ファイルの大きな差分を
commit する。`HW_FABLE_CLI` には stub を差し、受信プロンプトを連番ファイルへ保存
させる。「CLI が呼ばれたか」は stub の出力文字列ではなく保存ファイルの有無で判定
する。実 API は一切呼ばない。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

TASK = "chunk-fixture"
DEFAULT_MAX_PROMPT_BYTES = 200000
DEFAULT_MAX_CHUNKS = 4
MATERIAL_FLOOR = 246456  # PR #77 実測値。これ以上の材料でチャンク経路を検査する

TESTS_DIR = pathlib.Path(__file__).resolve().parent
HW_DIR = TESTS_DIR.parent
ROOT = HW_DIR.parent
FABLE = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HW_DIR / "fable_review.py"
CHECKER = HW_DIR / "check_review_verdict.py"
VERIFY = HW_DIR / "verify.sh"

MATERIAL_MARKER = re.compile(r"^--- レビュー対象差分[^\n]*\n", re.M)

failures: list[str] = []


def check(case_id: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {case_id}")
    else:
        print(f"FAIL {case_id}: {detail}")
        failures.append(case_id)


def git(repo: pathlib.Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失敗: {proc.stderr.strip()}")
    return proc.stdout.strip()


def blob(nbytes: int, tag: str) -> str:
    """決定的な ASCII 本文。1行64バイト。"""
    line_len = 64
    lines = []
    for index in range(nbytes // line_len):
        head = f"{tag}-{index:08d}-"
        lines.append(head + "z" * (line_len - 1 - len(head)) + "\n")
    return "".join(lines)


CONTRACT = """# Verification Contract — fixture

このファイルは fixture 用のダミー契約全文。チャンクプロンプトに毎回全文が入る。

| ID | Requirement |
|---|---|
| FX-001 | fixture の差分が契約どおりであること |
| FX-002 | fixture の差分が打ち切られていないこと |
"""


def make_repo(tmp: pathlib.Path, name: str, files: list[tuple[str, int]]) -> pathlib.Path:
    repo = tmp / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    git(repo, "config", "user.email", "hw-test@example.com")
    git(repo, "config", "user.name", "hw-test")
    git(repo, "config", "commit.gpgsign", "false")

    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-q", "-m", "init")
    base = git(repo, "rev-parse", "HEAD")

    plan_dir = repo / ".hw" / "plans" / TASK
    plan_dir.mkdir(parents=True)
    (plan_dir / "sml-decision.json").write_text(
        json.dumps({"size": "M", "reason": "fixture"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (plan_dir / "verification-contract.md").write_text(CONTRACT, encoding="utf-8")
    (plan_dir / "base-commit").write_text(base + "\n", encoding="utf-8")
    git(repo, "add", ".hw")
    git(repo, "commit", "-q", "-m", "plan")

    for path, size in files:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(blob(size, pathlib.Path(path).stem), encoding="utf-8")
    git(repo, "add", "src")
    git(repo, "commit", "-q", "-m", "impl")
    return repo


def material_of(repo: pathlib.Path) -> bytes:
    """検証対象の材料をテスト側で独立に再現する。"""
    base = (repo / ".hw" / "plans" / TASK / "base-commit").read_text().strip()
    proc = subprocess.run(
        [
            "git",
            "diff",
            "--binary",
            "--no-ext-diff",
            "--full-index",
            base,
            "HEAD",
            "--",
            ".",
            f":(exclude).hw/plans/{TASK}/review-verdict.json",
            ":(exclude).hw/gates",
            ":(exclude).hw/state",
        ],
        cwd=repo,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise SystemExit("fixture の材料生成に失敗: " + proc.stderr.decode())
    return proc.stdout


def split_by_file(material: bytes) -> dict[bytes, bytes]:
    """テスト独立の per-file 分割。キーは `diff --git` 行、値は差分 bytes。"""
    if not material:
        return {}
    parts = material.split(b"\ndiff --git ")
    segments = [parts[0]] + [b"diff --git " + part for part in parts[1:]]
    result: dict[bytes, bytes] = {}
    for segment in segments:
        # 区切りに使った改行を戻す(材料側と最終チャンク側で末尾を揃える)
        if not segment.endswith(b"\n"):
            segment += b"\n"
        header = segment.split(b"\n", 1)[0]
        if header in result:
            raise SystemExit(f"fixture に重複ヘッダ: {header!r}")
        result[header] = segment
    return result


def prompt_material(prompt: str) -> bytes:
    matches = list(MATERIAL_MARKER.finditer(prompt))
    if not matches:
        raise SystemExit("プロンプトに差分セクションがありません")
    body = prompt[matches[-1].end() :]
    if not body.endswith("\n"):
        raise SystemExit("差分セクションが改行で終わっていません")
    return body[:-1].encode("utf-8")


STUB = """#!/usr/bin/env python3
import json, os, pathlib, sys

prompt = sys.stdin.read()
out_dir = pathlib.Path(os.environ["HW_TEST_PROMPT_DIR"])
out_dir.mkdir(parents=True, exist_ok=True)
index = len(list(out_dir.glob("prompt-*.txt"))) + 1
(out_dir / ("prompt-%02d.txt" % index)).write_text(prompt, encoding="utf-8")

mode = os.environ.get("HW_TEST_MODE", "all_ready")
result = {
    "verdict": "READY",
    "summary": "stub chunk %d" % index,
    "confidence": "medium",
    "violations": [],
    "advisory": [],
}
if mode == "all_high":
    result["confidence"] = "high"
elif mode == "repair_on_2" and index == 2:
    result = {
        "verdict": "NEEDS_REPAIR",
        "summary": "stub injected repair",
        "confidence": "medium",
        "violations": [
            {
                "contract_id": "FX-002",
                "title": "chunk2 injected violation",
                "detail": "stub injected",
                "file": "src/f3.txt",
                "reproduction": "stub",
            }
        ],
        "advisory": [],
    }
print(json.dumps({"structured_output": result}, ensure_ascii=False))
"""


def main() -> int:
    if not FABLE.is_file():
        print(f"fable_review.py が見つかりません: {FABLE}", file=sys.stderr)
        return 2
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="hw-fable-chunk-"))
    try:
        if (
            subprocess.run(
                ["git", "-C", str(tmp), "rev-parse", "--show-toplevel"],
                capture_output=True,
            ).returncode
            == 0
        ):
            print(f"テスト設定エラー: TMPDIR が git リポジトリ配下です ({tmp})", file=sys.stderr)
            return 2

        stub_path = tmp / "fable-stub.py"
        stub_path.write_text(STUB, encoding="utf-8")
        stub_path.chmod(0o755)
        run_seq = [0]

        def run_review(
            repo: pathlib.Path, mode: str = "all_ready", extra: dict[str, str] | None = None
        ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
            run_seq[0] += 1
            prompt_dir = tmp / f"prompts-{run_seq[0]:02d}"
            prompt_dir.mkdir()
            env = {k: v for k, v in os.environ.items() if not k.startswith("HW_")}
            env["HW_FABLE_CLI"] = str(stub_path)
            env["HW_TEST_PROMPT_DIR"] = str(prompt_dir)
            env["HW_TEST_MODE"] = mode
            env.update(extra or {})
            proc = subprocess.run(
                [sys.executable, str(FABLE), TASK],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
            )
            prompts = [
                path.read_text(encoding="utf-8")
                for path in sorted(prompt_dir.glob("prompt-*.txt"))
            ]
            return proc, prompts

        def verdict_of(repo: pathlib.Path) -> dict:
            path = repo / ".hw" / "plans" / TASK / "review-verdict.json"
            return json.loads(path.read_text(encoding="utf-8"))

        # ---- AT-001 / FP-001: 上限以下は現行と同一の1回実行 ----
        small = make_repo(tmp, "small", [("src/s1.txt", 1024)])
        proc, prompts = run_review(small)
        ok = proc.returncode == 0 and len(prompts) == 1
        if ok:
            prompt = prompts[0]
            ok = (
                "chunk: " not in prompt
                and "全体マニフェスト" not in prompt
                and "FX-001" in prompt
                and f"対象task: {TASK}" in prompt
                and prompt_material(prompt) == material_of(small)
            )
        verdict = verdict_of(small) if proc.returncode == 0 else {}
        ok = ok and verdict.get("chunked", False) is False
        check(
            "AT-001",
            ok,
            f"exit={proc.returncode} prompts={len(prompts)} "
            f"chunked={verdict.get('chunked')!r} stderr={proc.stderr.strip()[:200]}",
        )

        # ---- AT-002..AT-005 / NFT-001: 246,456 bytes 以上の材料でチャンク実行 ----
        big_files = [
            ("src/f1.txt", 110000),
            ("src/f2.txt", 68000),
            ("src/f3.txt", 40000),
            ("src/f4.txt", 28000),
        ]
        big = make_repo(tmp, "big", big_files)
        full_material = material_of(big)
        if len(full_material) < MATERIAL_FLOOR:
            print(
                f"テスト設定エラー: fixture 材料が {len(full_material)} bytes で "
                f"{MATERIAL_FLOOR} 未満",
                file=sys.stderr,
            )
            return 2
        proc, prompts = run_review(big)
        check(
            "AT-002",
            proc.returncode == 0 and len(prompts) >= 2,
            f"material={len(full_material)}B exit={proc.returncode} "
            f"prompts={len(prompts)} stderr={proc.stderr.strip()[:200]}",
        )

        sizes = [len(prompt.encode("utf-8")) for prompt in prompts]
        check(
            "AT-003",
            bool(sizes) and max(sizes) <= DEFAULT_MAX_PROMPT_BYTES,
            f"prompt sizes={sizes} (上限 {DEFAULT_MAX_PROMPT_BYTES})",
        )

        expected = split_by_file(full_material)
        seen: dict[bytes, bytes] = {}
        duplicated = []
        for prompt in prompts:
            for header, segment in split_by_file(prompt_material(prompt)).items():
                if header in seen:
                    duplicated.append(header)
                seen[header] = segment
        missing = sorted(set(expected) - set(seen))
        extra_files = sorted(set(seen) - set(expected))
        mismatched = sorted(
            header for header in set(expected) & set(seen) if expected[header] != seen[header]
        )
        check(
            "AT-004",
            not missing and not extra_files and not mismatched and not duplicated,
            f"files={len(expected)} missing={missing} extra={extra_files} "
            f"byte_mismatch={mismatched} duplicated={duplicated}",
        )

        verdict = verdict_of(big)
        expected_hash = "sha256:" + hashlib.sha256(full_material).hexdigest()
        check(
            "NFT-001",
            verdict.get("chunk_count") == len(prompts)
            and len(prompts) <= DEFAULT_MAX_CHUNKS
            and verdict.get("chunked") is True
            and len(verdict.get("chunks", [])) == len(prompts),
            f"chunk_count={verdict.get('chunk_count')} prompts={len(prompts)} "
            f"chunked={verdict.get('chunked')!r}",
        )

        hash_ok = verdict.get("target_sha256") == expected_hash
        git(big, "add", f".hw/plans/{TASK}/review-verdict.json")
        git(big, "commit", "-q", "-m", "verdict")
        env = {k: v for k, v in os.environ.items() if not k.startswith("HW_")}
        checked = subprocess.run(
            [sys.executable, str(CHECKER), TASK],
            cwd=big,
            env=env,
            capture_output=True,
            text=True,
        )
        check(
            "AT-005",
            hash_ok and checked.returncode == 0,
            f"target_sha256一致={hash_ok} check_review_verdict exit={checked.returncode} "
            f"{checked.stdout.strip()[:160]}{checked.stderr.strip()[:200]}",
        )

        # ---- AT-006: チャンク2の NEEDS_REPAIR で全体 fail-closed ----
        big2 = make_repo(tmp, "big2", big_files)
        proc, prompts = run_review(big2, mode="repair_on_2")
        verdict = verdict_of(big2) if prompts else {}
        violations = verdict.get("violations", [])
        check(
            "AT-006",
            proc.returncode == 1
            and verdict.get("verdict") == "NEEDS_REPAIR"
            and len(prompts) >= 2
            and any(item.get("title") == "chunk2 injected violation" for item in violations),
            f"exit={proc.returncode} verdict={verdict.get('verdict')!r} "
            f"violations={len(violations)} prompts={len(prompts)}",
        )

        # ---- NFT-003: 全チャンク high でも全体 medium ----
        big3 = make_repo(tmp, "big3", big_files)
        proc, prompts = run_review(big3, mode="all_high")
        verdict = verdict_of(big3) if prompts else {}
        check(
            "NFT-003",
            proc.returncode == 0
            and verdict.get("confidence") == "medium"
            and all(item.get("confidence") == "high" for item in verdict.get("chunks", [])),
            f"exit={proc.returncode} confidence={verdict.get('confidence')!r}",
        )

        # ---- AT-007: チャンク数上限超過は CLI を呼ばずに exit 2 ----
        many = make_repo(tmp, "many", [(f"src/g{i}.txt", 110000) for i in range(1, 6)])
        proc, prompts = run_review(many)
        check(
            "AT-007",
            proc.returncode == 2 and not prompts and "チャンク数" in proc.stderr,
            f"exit={proc.returncode} prompts={len(prompts)} stderr={proc.stderr.strip()[:200]}",
        )

        # ---- AT-008: 単一ファイルが予算超なら停止し、パスを示す ----
        huge = make_repo(tmp, "huge", [("src/h1.txt", 250000)])
        proc, prompts = run_review(huge)
        check(
            "AT-008",
            proc.returncode == 2 and not prompts and "src/h1.txt" in proc.stderr,
            f"exit={proc.returncode} prompts={len(prompts)} stderr={proc.stderr.strip()[:200]}",
        )

        # ---- FP-002: dirty tree はチャンク経路より先に落ちる ----
        dirty = make_repo(tmp, "dirty", big_files)
        (dirty / "src" / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        proc, prompts = run_review(dirty)
        check(
            "FP-002",
            proc.returncode == 2 and not prompts and "clean tree" in proc.stderr,
            f"exit={proc.returncode} prompts={len(prompts)} stderr={proc.stderr.strip()[:200]}",
        )

        # ---- NFT-002: git diff フラグに -M / -C を足していない ----
        source = FABLE.read_text(encoding="utf-8")
        check(
            "NFT-002",
            '"-M"' not in source and '"-C"' not in source,
            "fable_review.py の git diff 引数に -M/-C が入っている",
        )

        # ---- AT-009: verify.sh からこのテストが呼ばれている ----
        verify_text = VERIFY.read_text(encoding="utf-8") if VERIFY.is_file() else ""
        check(
            "AT-009",
            "fable-review-chunking.test.py" in verify_text and "exit 1" in verify_text,
            "verify.sh がこのテストを実行していない(死んだテスト)",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print(f"[hw][test][FAIL] fable-review-chunking: {len(failures)} 件不一致")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
