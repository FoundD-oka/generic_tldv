#!/usr/bin/env python3
"""check_pytest_baseline.py — runtime-api の既知 fail を凍結し、新規回帰だけを止める。

使い方:
    python -m pytest services/runtime-api/tests --junitxml=pytest-junit.xml
    python services/runtime-api/tests/ci/check_pytest_baseline.py \\
        pytest-junit.xml services/runtime-api/tests/ci/pytest-baseline.json

運用ルール(pytest-baseline.json は JSON でコメントを書けないためここに置く):
  - known_failures は「減らす」ことだけが許される。fail を直したら実測値まで
    減らして commit する。
  - known_failures を「増やす」のは禁止。増やせば新規回帰が素通りするので、
    ラチェットがラチェットでなくなる。凍結対象を増やす必要が生じた場合は、
    その PR の本文に (1) 測定コマンド (2) 実行環境 (3) 増やす理由 を記録し、
    人間のレビューで判定する。このスクリプトは baseline を書き換えない(読むだけ)。
  - collection error / teardown error は baseline に関係なく必ず fail させる。
    収集エラーで「0件緑」にさせないため(services/mcp の同種事故の教訓)。
  - 収集件数 0 も必ず fail。テストが1件も動いていないのに緑にしない。

エントリの書式:
  JUnit XML の classname と name を "::" で連結した文字列。
    例 tests.test_backends::test_process_backend_inspect
  収集エラーは classname が空になるため name のみ。
    例 tests.test_broken
  新規 fail を検出したとき、このスクリプトはそのまま貼れる形式でキーを出力する。

依存は Python 標準ライブラリのみ。
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET

EXIT_OK = 0
EXIT_NG = 1


def _fail(message: str) -> int:
    print(f"pytest-baseline: {message}", file=sys.stderr)
    return EXIT_NG


def _case_key(classname: str, name: str) -> str:
    if classname:
        return f"{classname}::{name}"
    return name


def _parse_report(path: str) -> tuple[list[str], list[str], int] | None:
    """JUnit XML から (failures, errors, collected) を返す。解析不能なら None。"""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as err:
        print(f"pytest-baseline: JUnit XML を読めない ({path}): {err}", file=sys.stderr)
        return None

    failures: list[str] = []
    errors: list[str] = []
    collected = 0
    for case in root.iter("testcase"):
        collected += 1
        key = _case_key(case.get("classname", ""), case.get("name", ""))
        if case.find("error") is not None:
            errors.append(key)
        elif case.find("failure") is not None:
            failures.append(key)
    return failures, errors, collected


def _load_baseline(path: str) -> list[str] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as err:
        print(f"pytest-baseline: baseline を読めない ({path}): {err}", file=sys.stderr)
        return None

    if not isinstance(data, dict):
        print(f"pytest-baseline: baseline が JSON オブジェクトでない ({path})", file=sys.stderr)
        return None
    known = data.get("known_failures")
    if not isinstance(known, list) or not all(isinstance(item, str) for item in known):
        print(
            f"pytest-baseline: baseline.known_failures が文字列の配列でない ({path})",
            file=sys.stderr,
        )
        return None
    return known


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return _fail("usage: check_pytest_baseline.py <junit.xml> <pytest-baseline.json>")
    report_path, baseline_path = argv

    parsed = _parse_report(report_path)
    if parsed is None:
        return EXIT_NG
    failures, errors, collected = parsed

    known = _load_baseline(baseline_path)
    if known is None:
        return EXIT_NG
    known_set = set(known)

    print(
        f"pytest-baseline: collected={collected} failed={len(failures)} "
        f"errored={len(errors)} / baseline known_failures={len(known_set)}"
    )

    if errors:
        for key in errors:
            print(f"  - error: {key}", file=sys.stderr)
        return _fail(
            f"collection/teardown error が {len(errors)} 件ある。"
            "これは既存負債ではなく故障なので baseline に関係なく fail。"
        )

    if collected == 0:
        return _fail("テストが1件も収集されていない。収集0件を緑にしない。")

    new_failures = sorted(key for key in failures if key not in known_set)
    if new_failures:
        print("pytest-baseline: baseline にない fail がある:", file=sys.stderr)
        for key in new_failures:
            print(f"  - {key}", file=sys.stderr)
        return _fail(
            "この差分で壊れたテストを直すこと"
            "(baseline へ追記して通すのは禁止。凍結が必要なら PR 本文で根拠を示す)。"
        )

    fixed = sorted(known_set - set(failures))
    if fixed:
        print("pytest-baseline: baseline の fail が解消している。")
        for key in fixed:
            print(f"  - {key}")
        print("pytest-baseline: pytest-baseline.json から上記を削除して commit すること。")

    print("pytest-baseline: OK (新規 fail なし)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
