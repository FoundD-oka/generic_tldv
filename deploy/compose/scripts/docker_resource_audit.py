#!/usr/bin/env python3
"""Read-only Docker resource audit (p24-docker-resource-guard).

Reports how much disk Docker is holding (images / volumes / build cache), how
close the host filesystem is to full, and how the running containers sit
against their own memory limits. It only *reads*: every docker invocation goes
through a single allowlisted helper, and nothing is ever deleted or pruned.

Design notes:
  * fail-closed. A missing daemon, a non-zero docker exit, a corrupt JSON line,
    an unknown size unit or a bad flag all end as status "error" / exit 3.
    A partial read is never reported as "ok".
  * "reclaimable" is what `docker system df` claims could be freed. It is shown
    as a candidate figure only; whether it *should* be freed (re-pull / rebuild
    cost) is a human decision, so no wording here asserts it is fine to delete.
  * a container without a memory limit is reported as *info*, never as waste.
    `docker stats` prints the host total in the limit column of MemUsage, so the
    limit is taken from `docker inspect .HostConfig.Memory` instead.

Only Python stdlib + the docker CLI are required.

Usage:
  python3 deploy/compose/scripts/docker_resource_audit.py [--json] [thresholds]
  make docker-audit DOCKER_AUDIT_ARGS="--json"

Exit code: 0 ok / 1 warn / 2 critical / 3 error.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

SCHEMA_VERSION = 1

# SI GB (10^9) everywhere, so a "20 GB" threshold means the same number the
# docker CLI prints (docker system df also reports SI).
GB = 10 ** 9

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_CRITICAL = "critical"
STATUS_ERROR = "error"
STATUS_INFO = "info"

# info is deliberately absent: it must not move the overall status or exit code.
STATUS_RANK = {STATUS_OK: 0, STATUS_WARN: 1, STATUS_CRITICAL: 2, STATUS_ERROR: 3}
STATUS_EXIT = {STATUS_OK: 0, STATUS_WARN: 1, STATUS_CRITICAL: 2, STATUS_ERROR: 3}

# The only docker invocations this tool is allowed to make, keyed by the leading
# argument tuple. Anything else raises before reaching subprocess: the audit is
# read-only by construction, not by convention.
ALLOWED_DOCKER_COMMANDS = (
    ("system", "df"),
    ("stats", "--no-stream"),
    ("ps", "-q"),
    ("inspect",),
)

# Both the SI and the binary ladders, because docker mixes them: `system df`
# prints SI ("82.03GB") while `stats` prints binary ("683MiB / 1.5GiB").
SIZE_UNITS = {
    "B": 1,
    "kB": 10 ** 3,
    "KB": 10 ** 3,
    "MB": 10 ** 6,
    "GB": 10 ** 9,
    "TB": 10 ** 12,
    "KiB": 2 ** 10,
    "MiB": 2 ** 20,
    "GiB": 2 ** 30,
    "TiB": 2 ** 40,
}

SIZE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([A-Za-z]+)\s*$")

# `docker system df` always emits these four rows. A missing row means the read
# was partial, which is an error rather than a zero.
REQUIRED_DF_TYPES = ("Images", "Containers", "Local Volumes", "Build Cache")

RECLAIMABLE_WORDING = (
    "reclaimable は削除候補の目安。削除可否は再取得コスト等を確認のうえ各自判断。"
)
CLEANUP_HINT = (
    "解放を検討する場合の代表コマンド名: docker system prune "
    "(このツールは読み取り専用で、削除は一切実行しない)"
)


class AuditError(Exception):
    """Anything that must end as status error / exit 3."""


class RunResult(object):
    """Minimal result object so tests can fake the runner without subprocess."""

    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def subprocess_runner(argv, env):
    """Real runner. Kept tiny so the fake in tests is a faithful stand-in."""
    completed = subprocess.run(
        argv,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )
    return RunResult(completed.returncode, completed.stdout, completed.stderr)


def docker_env():
    """LC_ALL/LANG pinned to C so docker's number/word output is locale-stable."""
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


def run_docker(runner, args):
    """Run one allowlisted `docker ...` invocation and return stdout."""
    key = None
    for candidate in ALLOWED_DOCKER_COMMANDS:
        if tuple(args[: len(candidate)]) == candidate:
            key = candidate
            break
    if key is None:
        raise AuditError("読み取り専用 allowlist 外の docker 呼び出し: %s" % " ".join(args))

    argv = ["docker"] + list(args)
    try:
        result = runner(argv, docker_env())
    except FileNotFoundError:
        raise AuditError("docker コマンドが見つからない(daemon/CLI 不在)")
    except OSError as exc:
        raise AuditError("docker 呼び出しに失敗: %s" % exc)

    if result.returncode != 0:
        raise AuditError(
            "docker %s が非ゼロ終了 (rc=%s): %s"
            % (" ".join(args), result.returncode, (result.stderr or "").strip())
        )
    return result.stdout


def parse_size(text):
    """"82.03GB" / "683MiB" / "0B" -> bytes. Unknown unit or shape -> AuditError."""
    if text is None:
        raise AuditError("サイズ文字列が空")
    match = SIZE_RE.match(str(text))
    if not match:
        raise AuditError("サイズ文字列を解釈できない: %r" % text)
    number, unit = match.group(1), match.group(2)
    if unit not in SIZE_UNITS:
        raise AuditError("未知の単位: %r (入力 %r)" % (unit, text))
    return int(round(float(number) * SIZE_UNITS[unit]))


def parse_reclaimable(text):
    """`docker system df` prints "22.29GB (27%)"; only the size part is used."""
    if text is None:
        raise AuditError("reclaimable 文字列が空")
    head = str(text).strip().split(" ")[0]
    return parse_size(head)


def parse_mem_usage(text):
    """"683MiB / 1.5GiB" -> usage bytes.

    The limit side is intentionally discarded: for an unconstrained container
    docker puts the *host* total there, which would look like a limit.
    """
    if text is None:
        raise AuditError("MemUsage 文字列が空")
    usage = str(text).split("/")[0]
    return parse_size(usage)


def parse_json_lines(stdout, what):
    """One JSON object per line ({{json .}} format). Any bad line -> AuditError."""
    rows = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise AuditError("%s の JSON 行が破損: %s" % (what, exc))
        if not isinstance(row, dict):
            raise AuditError("%s の JSON 行が object でない: %r" % (what, line))
        rows.append(row)
    return rows


def _field(row, key, what):
    if key not in row:
        raise AuditError("%s の行に %s がない: %r" % (what, key, row))
    return row[key]


def collect_df(runner):
    """`docker system df` rows keyed by Type, all four rows required."""
    stdout = run_docker(runner, ["system", "df", "--format", "{{json .}}"])
    rows = parse_json_lines(stdout, "docker system df")
    by_type = {}
    for row in rows:
        by_type[_field(row, "Type", "docker system df")] = row
    missing = [name for name in REQUIRED_DF_TYPES if name not in by_type]
    if missing:
        raise AuditError("docker system df の行が欠損: %s" % ", ".join(missing))
    return by_type


def collect_containers(runner):
    """[(name, usage_bytes, limit_bytes)] for running containers.

    limit comes from inspect (0 = unlimited), usage from stats.
    """
    ids = [line.strip() for line in run_docker(runner, ["ps", "-q"]).splitlines()]
    ids = [cid for cid in ids if cid]
    if not ids:
        return []

    inspect_out = run_docker(
        runner,
        ["inspect", "--format", "{{.Name}}\t{{.HostConfig.Memory}}"] + ids,
    )
    limits = []
    for line in inspect_out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            raise AuditError("docker inspect の出力形式が想定外: %r" % line)
        name = parts[0].lstrip("/")
        try:
            limit = int(parts[1])
        except ValueError:
            raise AuditError("docker inspect の memory 値が整数でない: %r" % line)
        limits.append((name, limit))

    stats_rows = parse_json_lines(
        run_docker(runner, ["stats", "--no-stream", "--format", "{{json .}}"]),
        "docker stats",
    )
    usage_by_name = {}
    for row in stats_rows:
        name = str(_field(row, "Name", "docker stats")).lstrip("/")
        usage_by_name[name] = parse_mem_usage(_field(row, "MemUsage", "docker stats"))

    containers = []
    for name, limit in limits:
        if name not in usage_by_name:
            raise AuditError("docker stats に %s の行がない(取得が不完全)" % name)
        containers.append((name, usage_by_name[name], limit))
    return containers


def classify(value, warn, critical):
    """`>=` fires, critical wins. Shared by the size and percent checks."""
    if value >= critical:
        return STATUS_CRITICAL
    if value >= warn:
        return STATUS_WARN
    return STATUS_OK


def format_gb(num_bytes):
    return "%.2f GB" % (float(num_bytes) / GB)


class Check(object):
    def __init__(self, check_id, status, summary_ja, metrics, thresholds):
        self.id = check_id
        self.status = status
        self.summary_ja = summary_ja
        self.metrics = metrics
        self.thresholds = thresholds

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "summary_ja": self.summary_ja,
            "metrics": self.metrics,
            "thresholds": self.thresholds,
        }


class AuditResult(object):
    def __init__(self, status, checks, notes_ja, generated_at=None):
        self.status = status
        self.checks = checks
        self.notes_ja = notes_ja
        self.generated_at = generated_at or _now_iso()

    @property
    def exit_code(self):
        return STATUS_EXIT[self.status]

    def to_dict(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "status": self.status,
            "exit_code": self.exit_code,
            "checks": [check.to_dict() for check in self.checks],
            "notes_ja": list(self.notes_ja),
        }


def _now_iso():
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _size_check(check_id, label, size_bytes, warn_gb, crit_gb, suffix=""):
    status = classify(size_bytes, warn_gb * GB, crit_gb * GB)
    return Check(
        check_id,
        status,
        "%s %s (warn %s GB / crit %s GB)%s"
        % (label, format_gb(size_bytes), warn_gb, crit_gb, suffix),
        {"bytes": size_bytes, "human": format_gb(size_bytes)},
        {"warn_bytes": warn_gb * GB, "critical_bytes": crit_gb * GB},
    )


def build_checks(df, containers, disk, args):
    checks = []

    checks.append(
        _size_check(
            "build_cache",
            "build cache 総量",
            parse_size(_field(df["Build Cache"], "Size", "docker system df")),
            args.build_cache_warn_gb,
            args.build_cache_crit_gb,
        )
    )
    checks.append(
        _size_check(
            "reclaimable_images",
            "images の reclaimable",
            parse_reclaimable(_field(df["Images"], "Reclaimable", "docker system df")),
            args.images_reclaimable_warn_gb,
            args.images_reclaimable_crit_gb,
            suffix="。" + RECLAIMABLE_WORDING,
        )
    )
    checks.append(
        _size_check(
            "reclaimable_volumes",
            "volumes の reclaimable",
            parse_reclaimable(
                _field(df["Local Volumes"], "Reclaimable", "docker system df")
            ),
            args.volumes_reclaimable_warn_gb,
            args.volumes_reclaimable_crit_gb,
            suffix="。" + RECLAIMABLE_WORDING,
        )
    )

    total, used = disk
    if total <= 0:
        raise AuditError("ホストディスクの総容量が取得できない")
    percent = float(used) / float(total) * 100.0
    checks.append(
        Check(
            "host_disk",
            classify(percent, args.disk_warn_percent, args.disk_crit_percent),
            "ホストディスク使用率 %.1f%% (warn %s%% / crit %s%%)"
            % (percent, args.disk_warn_percent, args.disk_crit_percent),
            {"total_bytes": total, "used_bytes": used, "percent": round(percent, 2)},
            {
                "warn_percent": args.disk_warn_percent,
                "critical_percent": args.disk_crit_percent,
            },
        )
    )

    rows = []
    limited_statuses = []
    unlimited = []
    for name, usage, limit in containers:
        if limit > 0:
            percent = float(usage) / float(limit) * 100.0
            rows.append(
                {
                    "name": name,
                    "usage_bytes": usage,
                    "limit_bytes": limit,
                    "percent": round(percent, 2),
                }
            )
            limited_statuses.append(
                classify(percent, args.mem_warn_percent, args.mem_crit_percent)
            )
        else:
            rows.append(
                {
                    "name": name,
                    "usage_bytes": usage,
                    "limit_bytes": 0,
                    "percent": None,
                }
            )
            unlimited.append(name)

    if not containers:
        mem_summary = "稼働コンテナなし。メモリ使用率の評価対象なし"
        mem_status = STATUS_OK
    elif not limited_statuses:
        mem_summary = "limit 設定済みコンテナなし。使用率の評価対象なし"
        mem_status = STATUS_OK
    else:
        mem_status = max(limited_statuses, key=lambda s: STATUS_RANK[s])
        worst = max(
            (row for row in rows if row["percent"] is not None),
            key=lambda row: row["percent"],
        )
        mem_summary = "limit 有り %d 件の最大使用率 %s %.1f%% (warn %s%% / crit %s%%)" % (
            len(limited_statuses),
            worst["name"],
            worst["percent"],
            args.mem_warn_percent,
            args.mem_crit_percent,
        )
    checks.append(
        Check(
            "container_memory",
            mem_status,
            mem_summary,
            {"containers": rows},
            {
                "warn_percent": args.mem_warn_percent,
                "critical_percent": args.mem_crit_percent,
            },
        )
    )

    # Always info: no limit is a configuration fact, not proof of waste.
    checks.append(
        Check(
            "unlimited_memory",
            STATUS_INFO,
            "メモリ未制限のコンテナ %d 件%s(情報提供のみ。判定には影響しない)"
            % (len(unlimited), ": " + ", ".join(unlimited) if unlimited else ""),
            {"containers": list(unlimited), "count": len(unlimited)},
            {},
        )
    )
    return checks


def overall_status(checks):
    worst = STATUS_OK
    for check in checks:
        if check.status == STATUS_INFO:
            continue
        if STATUS_RANK[check.status] > STATUS_RANK[worst]:
            worst = check.status
    return worst


def run_audit(runner, args, disk_usage=None):
    """Collect, evaluate, and return an AuditResult. Never raises AuditError."""
    disk_usage = disk_usage or shutil.disk_usage
    try:
        df = collect_df(runner)
        containers = collect_containers(runner)
        usage = disk_usage("/")
        checks = build_checks(df, containers, (usage.total, usage.used), args)
    except AuditError as exc:
        return AuditResult(STATUS_ERROR, [], ["監査を完了できない: %s" % exc])

    notes = [RECLAIMABLE_WORDING, CLEANUP_HINT]
    return AuditResult(overall_status(checks), checks, notes)


def render_summary(result):
    lines = ["Docker 資源監査 (%s)" % result.generated_at]
    lines.append("総合判定: %s (exit %d)" % (result.status, result.exit_code))
    for check in result.checks:
        lines.append("[%s] %s: %s" % (check.status, check.id, check.summary_ja))
    if result.notes_ja:
        lines.append("注記:")
        for note in result.notes_ja:
            lines.append("- %s" % note)
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="docker_resource_audit.py",
        description="Docker の資源使用状況を読み取り専用で監査する",
    )
    # argparse exits 2 on a bad flag, which collides with "critical". A bad
    # invocation is an error, so it must be 3.
    def _error(message):
        sys.stderr.write("引数エラー: %s\n" % message)
        raise SystemExit(STATUS_EXIT[STATUS_ERROR])

    parser.error = _error

    parser.add_argument("--json", action="store_true", help="JSON のみを出力する")
    parser.add_argument("--build-cache-warn-gb", type=float, default=20.0)
    parser.add_argument("--build-cache-crit-gb", type=float, default=40.0)
    parser.add_argument("--images-reclaimable-warn-gb", type=float, default=20.0)
    parser.add_argument("--images-reclaimable-crit-gb", type=float, default=40.0)
    parser.add_argument("--volumes-reclaimable-warn-gb", type=float, default=5.0)
    parser.add_argument("--volumes-reclaimable-crit-gb", type=float, default=20.0)
    parser.add_argument("--disk-warn-percent", type=float, default=80.0)
    parser.add_argument("--disk-crit-percent", type=float, default=90.0)
    parser.add_argument("--mem-warn-percent", type=float, default=85.0)
    parser.add_argument("--mem-crit-percent", type=float, default=95.0)
    return parser


def main(argv=None, runner=None, disk_usage=None, stream=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    result = run_audit(runner or subprocess_runner, args, disk_usage=disk_usage)
    out = stream or sys.stdout
    if args.json:
        out.write(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        out.write(render_summary(result) + "\n")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
