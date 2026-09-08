#!/usr/bin/env python3
"""Validate that a Fable READY verdict still matches the current material."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

from fable_review import ReviewError, load_review_context, repo_root


def fail(message: str) -> int:
    print(f"[hw][review][FAIL] {message}", file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) != 2:
        return fail("使い方: python3 .hw/check_review_verdict.py <task-id>")
    task_id = sys.argv[1]
    try:
        context = load_review_context(repo_root(), task_id)
        verdict_path: pathlib.Path = context["verdict_path"]
        if not verdict_path.is_file():
            return fail(f"{verdict_path} がありません")
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        if not isinstance(verdict, dict):
            return fail("review-verdict.json はJSON objectである必要があります")

        expected = {
            "schema_version": "1.0",
            "task_id": task_id,
            "provider": "claude-fable-cli",
            "reviewer_engine": "fable",
            "review_mode": "contract",
            "reviewed_base": context["base"],
            "target_sha256": context["target_sha256"],
            "contract_sha256": context["contract_sha256"],
            "verdict": "READY",
        }
        mismatches = [
            f"{key}: expected={value!r} actual={verdict.get(key)!r}"
            for key, value in expected.items()
            if verdict.get(key) != value
        ]
        if mismatches:
            return fail("Fable verdictが現在の対象と一致しません: " + "; ".join(mismatches))
        if verdict.get("violations"):
            return fail("READY verdict に violations が残っています")

        reviewed_commit = str(verdict.get("reviewed_commit", ""))
        if not reviewed_commit:
            return fail("reviewed_commit がありません")
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", reviewed_commit, "HEAD"],
            cwd=context["root"],
            capture_output=True,
        )
        if ancestor.returncode != 0:
            return fail(
                f"reviewed_commit {reviewed_commit} は現在のHEADの祖先ではありません"
            )
        print(
            f"[hw][review][ok] Fable READY: "
            f"target={context['target_sha256']} reviewed_commit={reviewed_commit}"
        )
        return 0
    except (OSError, json.JSONDecodeError, ReviewError) as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
