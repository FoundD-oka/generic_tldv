#!/usr/bin/env python3
"""check_ruff_baseline.py — runtime-api の既存 ruff 負債を凍結し、増加だけを止める。

使い方:
    ruff check services/runtime-api --output-format json > ruff-report.json
    python services/runtime-api/tests/ci/check_ruff_baseline.py \\
        ruff-report.json services/runtime-api/tests/ci/ruff-baseline.json

運用ルール(ruff-baseline.json は JSON でコメントを書けないためここに置く):
  - rules の各件数は「下げる」ことだけが許される。負債を返したら実測値まで
    下げて commit する。
  - 件数を「上げる」「rule を足す」のは禁止。上げれば新規違反が素通りするので、
    ラチェットがラチェットでなくなる。基準値の変更は人間の commit だけで行い、
    このスクリプトは baseline を書き換えない(読むだけ)。
  - ruff-baseline.json を変更する PR は、commit message または PR 本文に由来を
    記録すること: (1) 測定コマンド (2) ruff のバージョン (3) 実測した rule 別件数。
    過小方向(実測より小さく書く)はこのラチェットの「増加で赤」で CI が機械検出
    するが、過大方向(水増しして新規違反を隠す)は CI では捕まらない。過大方向は
    その PR のレビューでこの記録と突合して判定する。
  - ruff の既定 rule セットはバージョンで変わるため、CI では ruff を固定
    バージョンで install する(.github/workflows/test-runtime-api.yml)。
  - code が null の指摘(構文エラー等)は既存負債ではなく故障なので、baseline に
    関係なく必ず fail させる。

依存は Python 標準ライブラリのみ。
"""

from __future__ import annotations

import json
import sys
from collections import Counter

EXIT_OK = 0
EXIT_NG = 1


def _fail(message: str) -> int:
    print(f"ruff-ratchet: {message}", file=sys.stderr)
    return EXIT_NG


def _read_json(path: str, label: str) -> object | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as err:
        print(f"ruff-ratchet: {label} を読めない ({path}): {err}", file=sys.stderr)
        return None


def _load_baseline(path: str) -> dict[str, int] | None:
    data = _read_json(path, "baseline")
    if data is None:
        return None
    if not isinstance(data, dict):
        print(f"ruff-ratchet: baseline が JSON オブジェクトでない ({path})", file=sys.stderr)
        return None
    rules = data.get("rules")
    if not isinstance(rules, dict):
        print(f"ruff-ratchet: baseline.rules がオブジェクトでない ({path})", file=sys.stderr)
        return None
    for code, count in rules.items():
        if not isinstance(code, str) or not isinstance(count, int) or isinstance(count, bool):
            print(f"ruff-ratchet: baseline.rules の {code!r} が整数でない", file=sys.stderr)
            return None
        if count < 0:
            print(f"ruff-ratchet: baseline.rules の {code!r} が負の値", file=sys.stderr)
            return None
    return rules


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return _fail("usage: check_ruff_baseline.py <ruff-report.json> <ruff-baseline.json>")
    report_path, baseline_path = argv

    report = _read_json(report_path, "ruff report")
    if report is None:
        return EXIT_NG
    if not isinstance(report, list):
        return _fail("ruff report が配列でない (ruff check --output-format json の出力を渡すこと)")

    baseline = _load_baseline(baseline_path)
    if baseline is None:
        return EXIT_NG

    counts: Counter[str] = Counter()
    fatal = 0
    for item in report:
        code = item.get("code") if isinstance(item, dict) else None
        if isinstance(code, str) and code:
            counts[code] += 1
        else:
            fatal += 1

    total = sum(counts.values()) + fatal
    print(
        f"ruff-ratchet: current total={total} fatal={fatal} rules={len(counts)} "
        f"/ baseline total={sum(baseline.values())} rules={len(baseline)}"
    )

    if fatal > 0:
        return _fail(
            f"rule code のない指摘(構文エラー等)が {fatal} 件ある。"
            "既存負債ではなく故障なので baseline に関係なく fail。"
        )

    over = [
        f"{code} {count} > baseline {baseline.get(code, 0)} (+{count - baseline.get(code, 0)})"
        for code, count in sorted(counts.items())
        if count > baseline.get(code, 0)
    ]
    if over:
        print("ruff-ratchet: 新規 lint 負債が増えている:", file=sys.stderr)
        for line in over:
            print(f"  - {line}", file=sys.stderr)
        return _fail("この差分で追加された ruff 違反を直すこと(baseline を上げて通すのは禁止)。")

    under = [
        f"{code}: {baseline[code]} -> {counts.get(code, 0)}"
        for code in sorted(baseline)
        if counts.get(code, 0) < baseline[code]
    ]
    if under:
        print("ruff-ratchet: 負債が減った。ruff-baseline.json を下げて commit すること。")
        for line in under:
            print(f"  - {line}")

    print("ruff-ratchet: OK (新規増加なし)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
