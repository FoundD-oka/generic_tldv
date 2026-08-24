#!/usr/bin/env python3
"""Unit tests for docker_resource_audit.py (contract R2, R4-R9).

No live Docker daemon: every docker invocation is served by FakeRunner from
canned fixtures, so this runs identically in CI and on a laptop.

Run: python3 -m unittest discover -s deploy/compose/scripts/tests
"""

import io
import json
import os
import sys
import unittest

SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import docker_resource_audit as audit  # noqa: E402

# Every docker invocation the audit is allowed to make, as the leading argv
# tuple. A new subcommand (in particular a destructive one) fails the R2 test.
ALLOWED_PREFIXES = (
    ("docker", "system", "df"),
    ("docker", "stats", "--no-stream"),
    ("docker", "ps", "-q"),
    ("docker", "inspect"),
)


def fixture(name):
    with io.open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as handle:
        return handle.read()


class FakeRunner(object):
    """Serves canned stdout per docker subcommand and records what was asked."""

    def __init__(self, df=None, stats=None, ps=None, inspect=None, rc=0, raises=None):
        self.responses = {
            ("system", "df"): df if df is not None else "",
            ("stats", "--no-stream"): stats if stats is not None else "",
            ("ps", "-q"): ps if ps is not None else "",
            ("inspect",): inspect if inspect is not None else "",
        }
        self.rc = rc
        self.raises = raises
        self.calls = []

    def __call__(self, argv, env):
        self.calls.append((list(argv), dict(env)))
        if self.raises is not None:
            raise self.raises
        for key in self.responses:
            if tuple(argv[1 : 1 + len(key)]) == key:
                return audit.RunResult(self.rc, self.responses[key], "boom" if self.rc else "")
        raise AssertionError("unexpected docker invocation: %r" % (argv,))


class FakeDisk(object):
    def __init__(self, total, used):
        self.total = total
        self.used = used
        self.free = total - used

    def __call__(self, path):
        return self


def parse_args(extra=None):
    return audit.build_parser().parse_args(extra or [])


def busy_runner(df_fixture="system_df_measured.json", **kwargs):
    kwargs.setdefault("ps", fixture("ps_two.txt"))
    kwargs.setdefault("inspect", fixture("inspect_two.txt"))
    kwargs.setdefault("stats", fixture("stats_two.json"))
    return FakeRunner(df=fixture(df_fixture), **kwargs)


class ReadOnlyTest(unittest.TestCase):
    """R2: only allowlisted read-only docker subcommands are ever issued."""

    def test_only_allowlisted_commands_are_issued(self):
        runner = busy_runner()
        audit.run_audit(runner, parse_args(), disk_usage=FakeDisk(1000, 100))
        self.assertTrue(runner.calls, "no docker command was issued at all")
        for argv, _env in runner.calls:
            self.assertEqual(argv[0], "docker")
            matched = [p for p in ALLOWED_PREFIXES if tuple(argv[: len(p)]) == p]
            self.assertTrue(matched, "non-allowlisted docker invocation: %r" % (argv,))

    def test_non_allowlisted_command_is_refused_before_subprocess(self):
        runner = busy_runner()
        with self.assertRaises(audit.AuditError):
            audit.run_docker(runner, ["image", "ls"])

    def test_locale_env_is_pinned(self):
        """R8: LC_ALL/LANG=C on every docker call, so parsing is locale-stable."""
        runner = busy_runner()
        audit.run_audit(runner, parse_args(), disk_usage=FakeDisk(1000, 100))
        for argv, env in runner.calls:
            self.assertEqual(env.get("LC_ALL"), "C", argv)
            self.assertEqual(env.get("LANG"), "C", argv)


class FailClosedTest(unittest.TestCase):
    """R4: an incomplete or unparsable read is never reported as ok."""

    def _assert_error(self, runner):
        result = audit.run_audit(runner, parse_args(), disk_usage=FakeDisk(1000, 100))
        self.assertEqual(result.status, "error")
        self.assertEqual(result.exit_code, 3)
        return result

    def test_docker_cli_missing(self):
        result = self._assert_error(busy_runner(raises=FileNotFoundError("docker")))
        self.assertTrue(result.notes_ja)

    def test_docker_non_zero_exit(self):
        self._assert_error(busy_runner(rc=1))

    def test_corrupt_json_line(self):
        self._assert_error(busy_runner(df_fixture="system_df_broken.json"))

    def test_unknown_unit(self):
        self._assert_error(busy_runner(df_fixture="system_df_unknown_unit.json"))

    def test_missing_df_row(self):
        self._assert_error(busy_runner(df_fixture="system_df_missing_row.json"))

    def test_inspect_row_malformed(self):
        self._assert_error(busy_runner(inspect="/vexa-runtime-api 536870912\n"))

    def test_container_missing_from_stats(self):
        self._assert_error(busy_runner(stats=fixture("stats_mem_at_warn.json")))

    def test_bad_cli_argument_exits_3(self):
        with self.assertRaises(SystemExit) as caught:
            audit.build_parser().parse_args(["--no-such-flag"])
        self.assertEqual(caught.exception.code, 3)

    def test_bad_threshold_value_exits_3(self):
        with self.assertRaises(SystemExit) as caught:
            audit.build_parser().parse_args(["--disk-warn-percent", "abc"])
        self.assertEqual(caught.exception.code, 3)


class OutputShapeTest(unittest.TestCase):
    """R5: default summary is Japanese, --json is a parsable schema v1 doc."""

    def test_json_only_on_stdout_and_has_required_keys(self):
        stream = io.StringIO()
        code = audit.main(
            ["--json"],
            runner=busy_runner(),
            disk_usage=FakeDisk(1000, 100),
            stream=stream,
        )
        doc = json.loads(stream.getvalue())
        self.assertEqual(doc["schema_version"], 1)
        for key in ("generated_at", "status", "exit_code", "checks", "notes_ja"):
            self.assertIn(key, doc)
        self.assertEqual(doc["exit_code"], code)
        self.assertEqual(
            audit.STATUS_EXIT[doc["status"]], code, "status と exit code が不一致"
        )
        ids = set()
        for check in doc["checks"]:
            for key in ("id", "status", "summary_ja", "metrics", "thresholds"):
                self.assertIn(key, check)
            ids.add(check["id"])
        self.assertEqual(
            ids,
            {
                "build_cache",
                "reclaimable_images",
                "reclaimable_volumes",
                "host_disk",
                "container_memory",
                "unlimited_memory",
            },
        )

    def test_default_output_is_japanese_summary_not_json(self):
        stream = io.StringIO()
        audit.main(
            [], runner=busy_runner(), disk_usage=FakeDisk(1000, 100), stream=stream
        )
        text = stream.getvalue()
        self.assertIn("Docker 資源監査", text)
        self.assertIn("総合判定", text)
        with self.assertRaises(ValueError):
            json.loads(text)

    def test_status_exit_code_mapping(self):
        self.assertEqual(audit.STATUS_EXIT["ok"], 0)
        self.assertEqual(audit.STATUS_EXIT["warn"], 1)
        self.assertEqual(audit.STATUS_EXIT["critical"], 2)
        self.assertEqual(audit.STATUS_EXIT["error"], 3)

    def test_info_does_not_change_overall_status(self):
        checks = [
            audit.Check("host_disk", "ok", "", {}, {}),
            audit.Check("unlimited_memory", "info", "", {}, {}),
        ]
        self.assertEqual(audit.overall_status(checks), "ok")


class ThresholdTest(unittest.TestCase):
    """R6: documented defaults, `>=` firing, and CLI overrides."""

    def _run(self, df_fixture, extra=None, disk=None, **kwargs):
        return audit.run_audit(
            busy_runner(df_fixture, **kwargs),
            parse_args(extra),
            disk_usage=disk or FakeDisk(1000, 100),
        )

    def test_documented_defaults(self):
        args = parse_args()
        self.assertEqual(args.build_cache_warn_gb, 20.0)
        self.assertEqual(args.build_cache_crit_gb, 40.0)
        self.assertEqual(args.images_reclaimable_warn_gb, 20.0)
        self.assertEqual(args.images_reclaimable_crit_gb, 40.0)
        self.assertEqual(args.volumes_reclaimable_warn_gb, 5.0)
        self.assertEqual(args.volumes_reclaimable_crit_gb, 20.0)
        self.assertEqual(args.disk_warn_percent, 80.0)
        self.assertEqual(args.disk_crit_percent, 90.0)
        self.assertEqual(args.mem_warn_percent, 85.0)
        self.assertEqual(args.mem_crit_percent, 95.0)

    def test_gb_thresholds_are_si(self):
        self.assertEqual(audit.GB, 10 ** 9)

    def test_measured_fixture_is_critical_exit_2(self):
        """build cache 49.62GB >= 40GB crit → critical / exit 2 (現行環境の実測値)."""
        result = self._run("system_df_measured.json")
        self.assertEqual(result.status, "critical")
        self.assertEqual(result.exit_code, 2)
        by_id = {c.id: c for c in result.checks}
        self.assertEqual(by_id["build_cache"].status, "critical")
        # images reclaimable 22.29GB: >= 20 warn, < 40 crit
        self.assertEqual(by_id["reclaimable_images"].status, "warn")

    def test_threshold_boundary_fires_at_exact_value(self):
        result = self._run("system_df_threshold_exact.json")
        by_id = {c.id: c for c in result.checks}
        self.assertEqual(by_id["build_cache"].status, "warn")
        self.assertEqual(result.status, "warn")
        self.assertEqual(result.exit_code, 1)

    def test_threshold_does_not_fire_below_value(self):
        result = self._run("system_df_threshold_below.json")
        by_id = {c.id: c for c in result.checks}
        self.assertEqual(by_id["build_cache"].status, "ok")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.exit_code, 0)

    def test_cli_override_changes_verdict(self):
        result = self._run(
            "system_df_measured.json",
            extra=[
                "--build-cache-warn-gb",
                "60",
                "--build-cache-crit-gb",
                "100",
                "--images-reclaimable-warn-gb",
                "60",
                "--images-reclaimable-crit-gb",
                "100",
                "--volumes-reclaimable-warn-gb",
                "60",
                "--volumes-reclaimable-crit-gb",
                "100",
            ],
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.exit_code, 0)

    def test_disk_percent_boundary(self):
        at = self._run("system_df_quiet.json", disk=FakeDisk(100, 80))
        below = self._run("system_df_quiet.json", disk=FakeDisk(100, 79))
        crit = self._run("system_df_quiet.json", disk=FakeDisk(100, 90))
        self.assertEqual({c.id: c for c in at.checks}["host_disk"].status, "warn")
        self.assertEqual({c.id: c for c in below.checks}["host_disk"].status, "ok")
        self.assertEqual({c.id: c for c in crit.checks}["host_disk"].status, "critical")

    def test_mem_percent_boundary(self):
        at = self._run(
            "system_df_quiet.json",
            ps=fixture("ps_one.txt"),
            inspect=fixture("inspect_one_limited.txt"),
            stats=fixture("stats_mem_at_warn.json"),
        )
        below = self._run(
            "system_df_quiet.json",
            ps=fixture("ps_one.txt"),
            inspect=fixture("inspect_one_limited.txt"),
            stats=fixture("stats_mem_below_warn.json"),
        )
        self.assertEqual(
            {c.id: c for c in at.checks}["container_memory"].status, "warn"
        )
        self.assertEqual(
            {c.id: c for c in below.checks}["container_memory"].status, "ok"
        )


class UnlimitedMemoryTest(unittest.TestCase):
    """R7: no memory limit is info, never waste; limits come from inspect."""

    def test_unlimited_container_is_info_and_does_not_change_exit(self):
        result = audit.run_audit(
            busy_runner(
                "system_df_quiet.json",
                ps=fixture("ps_two.txt"),
                inspect=fixture("inspect_two.txt"),
                stats=fixture("stats_unlimited_high.json"),
            ),
            parse_args(),
            disk_usage=FakeDisk(1000, 100),
        )
        by_id = {c.id: c for c in result.checks}
        self.assertEqual(by_id["unlimited_memory"].status, "info")
        self.assertIn("vexa-dashboard", by_id["unlimited_memory"].metrics["containers"])
        # 12.4GiB used with no limit must not raise the verdict.
        self.assertEqual(by_id["container_memory"].status, "ok")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.exit_code, 0)

    def test_limit_comes_from_inspect_not_stats(self):
        result = audit.run_audit(
            busy_runner(
                "system_df_quiet.json",
                ps=fixture("ps_two.txt"),
                inspect=fixture("inspect_two.txt"),
                stats=fixture("stats_two.json"),
            ),
            parse_args(),
            disk_usage=FakeDisk(1000, 100),
        )
        by_id = {c.id: c for c in result.checks}
        rows = {r["name"]: r for r in by_id["container_memory"].metrics["containers"]}
        # inspect says 536870912 for runtime-api and 0 for dashboard; the stats
        # MemUsage limit column (512MiB / 1.5GiB) must not be used as the limit.
        self.assertEqual(rows["vexa-runtime-api"]["limit_bytes"], 536870912)
        self.assertEqual(rows["vexa-dashboard"]["limit_bytes"], 0)
        self.assertIsNone(rows["vexa-dashboard"]["percent"])

    def test_only_limited_containers_are_rate_evaluated(self):
        result = audit.run_audit(
            busy_runner(
                "system_df_quiet.json",
                ps=fixture("ps_two.txt"),
                inspect="/only-unlimited\t0\n",
                stats='{"Name":"only-unlimited","MemUsage":"9GiB / 15.2GiB"}\n',
            ),
            parse_args(),
            disk_usage=FakeDisk(1000, 100),
        )
        by_id = {c.id: c for c in result.checks}
        self.assertEqual(by_id["container_memory"].status, "ok")
        self.assertIn("評価対象なし", by_id["container_memory"].summary_ja)


class EdgeCaseAndWordingTest(unittest.TestCase):
    """R8: zero containers, unit conversion, non-assertive reclaimable wording."""

    def test_no_running_containers_is_not_an_error(self):
        result = audit.run_audit(
            busy_runner(
                "system_df_quiet.json",
                ps=fixture("ps_empty.txt"),
                inspect="",
                stats="",
            ),
            parse_args(),
            disk_usage=FakeDisk(1000, 100),
        )
        by_id = {c.id: c for c in result.checks}
        self.assertEqual(by_id["container_memory"].status, "ok")
        self.assertEqual(by_id["container_memory"].metrics["containers"], [])
        self.assertEqual(by_id["unlimited_memory"].status, "info")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.exit_code, 0)

    def test_no_running_containers_skips_inspect_and_stats(self):
        runner = busy_runner(
            "system_df_quiet.json", ps=fixture("ps_empty.txt"), inspect="", stats=""
        )
        audit.run_audit(runner, parse_args(), disk_usage=FakeDisk(1000, 100))
        issued = [tuple(argv[1:2]) for argv, _env in runner.calls]
        self.assertNotIn(("inspect",), issued)
        self.assertNotIn(("stats",), issued)

    def test_si_and_binary_units(self):
        self.assertEqual(audit.parse_size("1.5GiB"), 1610612736)
        self.assertEqual(audit.parse_size("1GB"), 10 ** 9)
        self.assertEqual(audit.parse_size("1kB"), 10 ** 3)
        self.assertEqual(audit.parse_size("1KB"), 10 ** 3)
        self.assertEqual(audit.parse_size("1MB"), 10 ** 6)
        self.assertEqual(audit.parse_size("1TB"), 10 ** 12)
        self.assertEqual(audit.parse_size("1KiB"), 1024)
        self.assertEqual(audit.parse_size("1MiB"), 1048576)
        self.assertEqual(audit.parse_size("1TiB"), 1099511627776)
        self.assertEqual(audit.parse_size("0B"), 0)
        self.assertEqual(audit.parse_size("82.03GB"), 82030000000)

    def test_reclaimable_percentage_suffix_is_stripped(self):
        self.assertEqual(audit.parse_reclaimable("22.29GB (27%)"), 22290000000)

    def test_mem_usage_takes_usage_side_only(self):
        self.assertEqual(audit.parse_mem_usage("683MiB / 1.5GiB"), 716177408)

    def test_unknown_unit_raises(self):
        for bad in ("12XB", "GB", "", "1.2.3GB"):
            with self.assertRaises(audit.AuditError):
                audit.parse_size(bad)

    def test_no_assertive_deletion_wording_anywhere(self):
        stream = io.StringIO()
        audit.main(
            [], runner=busy_runner(), disk_usage=FakeDisk(1000, 100), stream=stream
        )
        summary = stream.getvalue()
        json_stream = io.StringIO()
        audit.main(
            ["--json"],
            runner=busy_runner(),
            disk_usage=FakeDisk(1000, 100),
            stream=json_stream,
        )
        for text in (summary, json_stream.getvalue()):
            self.assertNotIn("安全に削除可能", text)
        self.assertIn("削除候補の目安", summary)

    def test_cleanup_hint_names_command_without_running_it(self):
        runner = busy_runner()
        result = audit.run_audit(runner, parse_args(), disk_usage=FakeDisk(1000, 100))
        self.assertTrue(
            any("docker system prune" in note for note in result.notes_ja),
            "cleanup コマンド名の提示がない",
        )
        for argv, _env in runner.calls:
            self.assertNotIn("prune", " ".join(argv))


class DependencyTest(unittest.TestCase):
    """R9: stdlib only, no new pip dependency."""

    def test_audit_module_imports_stdlib_only(self):
        allowed = {
            "argparse",
            "datetime",
            "json",
            "os",
            "re",
            "shutil",
            "subprocess",
            "sys",
        }
        path = os.path.join(SCRIPTS_DIR, "docker_resource_audit.py")
        with io.open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("import ") or line.startswith("from "):
                    module = line.split()[1].split(".")[0]
                    self.assertIn(module, allowed, "非 stdlib 依存: %s" % module)


if __name__ == "__main__":
    unittest.main()
