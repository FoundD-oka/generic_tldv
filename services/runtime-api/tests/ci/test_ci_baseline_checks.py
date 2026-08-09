"""CI ラチェット本体のユニットテスト。

合成レポートで両方向(新規 fail で赤 / 既知 fail のみで緑)を固定する。
ラチェットが黙って緑を返す方向に壊れたら、このテストが落ちる。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CI_DIR = Path(__file__).parent
PYTEST_CHECKER = CI_DIR / "check_pytest_baseline.py"
RUFF_CHECKER = CI_DIR / "check_ruff_baseline.py"


def _run(checker: Path, report: Path, baseline: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(checker), str(report), str(baseline)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _junit(cases: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f"<testsuites><testsuite>{cases}</testsuite></testsuites>"
    )


def _passed(classname: str, name: str) -> str:
    return f'<testcase classname="{classname}" name="{name}" time="0.1" />'


def _failed(classname: str, name: str) -> str:
    return (
        f'<testcase classname="{classname}" name="{name}" time="0.1">'
        '<failure message="AssertionError">boom</failure></testcase>'
    )


def _errored(name: str) -> str:
    return (
        f'<testcase classname="" name="{name}" time="0.0">'
        '<error message="collection failure">ImportError</error></testcase>'
    )


def _pytest_baseline(tmp_path: Path, known: list[str]) -> Path:
    return _write(tmp_path / "pytest-baseline.json", json.dumps({"known_failures": known}))


# --- pytest ベースライン比較 ---


def test_new_failure_outside_baseline_exits_nonzero(tmp_path):
    report = _write(
        tmp_path / "junit.xml",
        _junit(_passed("tests.test_a", "test_ok") + _failed("tests.test_a", "test_new")),
    )
    baseline = _pytest_baseline(tmp_path, ["tests.test_a::test_known"])

    result = _run(PYTEST_CHECKER, report, baseline)

    assert result.returncode != 0
    assert "tests.test_a::test_new" in result.stderr


def test_only_known_failures_exits_zero(tmp_path):
    report = _write(
        tmp_path / "junit.xml",
        _junit(_passed("tests.test_a", "test_ok") + _failed("tests.test_a", "test_known")),
    )
    baseline = _pytest_baseline(tmp_path, ["tests.test_a::test_known"])

    result = _run(PYTEST_CHECKER, report, baseline)

    assert result.returncode == 0, result.stderr


def test_all_green_with_empty_baseline_exits_zero(tmp_path):
    report = _write(tmp_path / "junit.xml", _junit(_passed("tests.test_a", "test_ok")))
    baseline = _pytest_baseline(tmp_path, [])

    result = _run(PYTEST_CHECKER, report, baseline)

    assert result.returncode == 0, result.stderr


def test_fixed_known_failure_is_notice_only(tmp_path):
    report = _write(tmp_path / "junit.xml", _junit(_passed("tests.test_a", "test_known")))
    baseline = _pytest_baseline(tmp_path, ["tests.test_a::test_known"])

    result = _run(PYTEST_CHECKER, report, baseline)

    assert result.returncode == 0, result.stderr
    assert "tests.test_a::test_known" in result.stdout


def test_collection_error_exits_nonzero_even_if_in_baseline(tmp_path):
    report = _write(tmp_path / "junit.xml", _junit(_errored("tests.test_broken")))
    baseline = _pytest_baseline(tmp_path, ["tests.test_broken"])

    result = _run(PYTEST_CHECKER, report, baseline)

    assert result.returncode != 0
    assert "tests.test_broken" in result.stderr


def test_zero_collected_exits_nonzero(tmp_path):
    report = _write(tmp_path / "junit.xml", _junit(""))
    baseline = _pytest_baseline(tmp_path, [])

    result = _run(PYTEST_CHECKER, report, baseline)

    assert result.returncode != 0


def test_broken_report_exits_nonzero(tmp_path):
    report = _write(tmp_path / "junit.xml", "<testsuites><not-closed>")
    baseline = _pytest_baseline(tmp_path, [])

    result = _run(PYTEST_CHECKER, report, baseline)

    assert result.returncode != 0


def test_invalid_baseline_exits_nonzero(tmp_path):
    report = _write(tmp_path / "junit.xml", _junit(_passed("tests.test_a", "test_ok")))
    baseline = _write(tmp_path / "pytest-baseline.json", json.dumps({"known_failures": "nope"}))

    result = _run(PYTEST_CHECKER, report, baseline)

    assert result.returncode != 0


def test_committed_pytest_baseline_is_valid(tmp_path):
    report = _write(tmp_path / "junit.xml", _junit(_passed("tests.test_a", "test_ok")))

    result = _run(PYTEST_CHECKER, report, CI_DIR / "pytest-baseline.json")

    assert result.returncode == 0, result.stderr


# --- ruff ラチェット ---


def _ruff_report(tmp_path: Path, codes: list[str | None]) -> Path:
    return _write(
        tmp_path / "ruff-report.json",
        json.dumps([{"code": code, "filename": "x.py"} for code in codes]),
    )


def _ruff_baseline(tmp_path: Path, rules: dict[str, int]) -> Path:
    return _write(tmp_path / "ruff-baseline.json", json.dumps({"rules": rules}))


def test_ruff_increase_exits_nonzero(tmp_path):
    report = _ruff_report(tmp_path, ["F401", "F401", "F401"])
    baseline = _ruff_baseline(tmp_path, {"F401": 2})

    result = _run(RUFF_CHECKER, report, baseline)

    assert result.returncode != 0
    assert "F401" in result.stderr


def test_ruff_new_rule_exits_nonzero(tmp_path):
    report = _ruff_report(tmp_path, ["F401", "B023"])
    baseline = _ruff_baseline(tmp_path, {"F401": 1})

    result = _run(RUFF_CHECKER, report, baseline)

    assert result.returncode != 0
    assert "B023" in result.stderr


def test_ruff_same_count_exits_zero(tmp_path):
    report = _ruff_report(tmp_path, ["F401", "F401", "B023"])
    baseline = _ruff_baseline(tmp_path, {"F401": 2, "B023": 1})

    result = _run(RUFF_CHECKER, report, baseline)

    assert result.returncode == 0, result.stderr


def test_ruff_decrease_is_notice_only(tmp_path):
    report = _ruff_report(tmp_path, ["F401"])
    baseline = _ruff_baseline(tmp_path, {"F401": 2, "B023": 1})

    result = _run(RUFF_CHECKER, report, baseline)

    assert result.returncode == 0, result.stderr
    assert "F401" in result.stdout


def test_ruff_null_code_exits_nonzero(tmp_path):
    report = _ruff_report(tmp_path, [None])
    baseline = _ruff_baseline(tmp_path, {"F401": 2})

    result = _run(RUFF_CHECKER, report, baseline)

    assert result.returncode != 0


def test_ruff_invalid_baseline_exits_nonzero(tmp_path):
    report = _ruff_report(tmp_path, [])
    baseline = _write(tmp_path / "ruff-baseline.json", json.dumps({"rules": {"F401": "many"}}))

    result = _run(RUFF_CHECKER, report, baseline)

    assert result.returncode != 0


def test_ruff_report_not_a_list_exits_nonzero(tmp_path):
    report = _write(tmp_path / "ruff-report.json", json.dumps({"diagnostics": []}))
    baseline = _ruff_baseline(tmp_path, {})

    result = _run(RUFF_CHECKER, report, baseline)

    assert result.returncode != 0


def test_committed_ruff_baseline_is_valid(tmp_path):
    report = _ruff_report(tmp_path, [])

    result = _run(RUFF_CHECKER, report, CI_DIR / "ruff-baseline.json")

    assert result.returncode == 0, result.stderr
