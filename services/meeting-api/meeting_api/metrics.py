"""ST-18 — Prometheus text format (0.0.4) exposition for meeting-api.

Renders the three metrics the operations audit asked for — join success
(numerator/denominator, not a ratio), final-transcription latency, and
final-transcription sweep backlog — plus the module-level worker heartbeat
counters that `sweeps.py` already maintains.

Design constraints:
  - No `prometheus_client` dependency: only counters and gauges are exposed
    and the text format is a few dozen lines of pure rendering.
  - Every DB/Redis read happens **on scrape** (`collect_samples`). There is no
    background collector task, no cache and no extra thread.
  - Counter reads go through `from . import sweeps` *inside* the function so
    the current module attribute is read on every scrape (import-time binding
    would freeze the value at import).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

logger = logging.getLogger("meeting_api.metrics")

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# Fixed observation window for the "last 24h" gauges. Deliberately not an env
# knob: the denominator/numerator are exposed raw, so any other window is a
# question for whoever reads the metrics, not a service configuration.
WINDOW_HOURS = 24

# Buckets of the final-transcription backlog. Kept in sync with the row
# selection of `sweeps._sweep_final_transcription_jobs` (see SQL below).
BACKLOG_BUCKETS = ("queued", "running", "failed_retryable")

LATENCY_QUANTILES = (0.5, 0.95)


@dataclass(frozen=True)
class Sample:
    """One exported time series point."""

    name: str
    type: str  # "counter" | "gauge"
    help: str
    value: float
    labels: tuple[tuple[str, str], ...] = field(default=())


# ---------------------------------------------------------------------------
# Rendering (pure)
# ---------------------------------------------------------------------------


def _escape_label_value(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _escape_help(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def _format_value(value: Any) -> str:
    number = float(value)
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


def render_prometheus_text(samples: Iterable[Sample]) -> str:
    """Render samples as Prometheus text format 0.0.4.

    `# HELP` / `# TYPE` are emitted once per metric name, before the first
    series of that name. Output ends with a newline (required by the format).
    """
    grouped: dict[str, list[Sample]] = {}
    order: list[str] = []
    for sample in samples:
        if sample.name not in grouped:
            grouped[sample.name] = []
            order.append(sample.name)
        grouped[sample.name].append(sample)

    lines: list[str] = []
    for name in order:
        series = grouped[name]
        lines.append(f"# HELP {name} {_escape_help(series[0].help)}")
        lines.append(f"# TYPE {name} {series[0].type}")
        for sample in series:
            if sample.labels:
                labels = ",".join(
                    f'{key}="{_escape_label_value(str(value))}"'
                    for key, value in sample.labels
                )
                lines.append(f"{name}{{{labels}}} {_format_value(sample.value)}")
            else:
                lines.append(f"{name} {_format_value(sample.value)}")
    return "".join(line + "\n" for line in lines)


# ---------------------------------------------------------------------------
# In-process counters (always available — no external dependency)
# ---------------------------------------------------------------------------


def process_counter_samples() -> list[Sample]:
    """Read the sweep/worker heartbeat counters at scrape time."""
    # Imported here on purpose: attribute lookup happens per scrape so the
    # live module value is read (and tests can monkeypatch it).
    from . import sweeps

    return [
        Sample(
            "meeting_api_sweep_iterations_total",
            "counter",
            "Iterations of the meeting-api sweep loop since process start.",
            getattr(sweeps, "sweep_iterations", 0),
        ),
        Sample(
            "meeting_api_sweep_last_iteration_timestamp_seconds",
            "gauge",
            "Unix timestamp of the last sweep loop iteration (0 if none yet).",
            getattr(sweeps, "sweep_last_iteration_at", 0.0),
        ),
        Sample(
            "meeting_api_final_transcription_worker_iterations_total",
            "counter",
            "Iterations of the final-transcription worker loop since process start.",
            getattr(sweeps, "final_transcription_worker_iterations", 0),
        ),
        Sample(
            "meeting_api_final_transcription_worker_last_iteration_timestamp_seconds",
            "gauge",
            "Unix timestamp of the last final-transcription worker iteration (0 if none yet).",
            getattr(sweeps, "final_transcription_worker_last_iteration_at", 0.0),
        ),
    ]


# ---------------------------------------------------------------------------
# Redis — collector stream lag
# ---------------------------------------------------------------------------


async def collector_lag_sample(redis_client: Any) -> Sample:
    """Consumer-group lag of the transcription_segments stream.

    Same read as `/health/collector`. Value is -1 when Redis is unreachable or
    not yet initialised — the scrape must not fail because of it.
    """
    from .collector.config import REDIS_STREAM_NAME, REDIS_CONSUMER_GROUP

    lag: float = -1
    if redis_client is not None:
        try:
            groups = await redis_client.xinfo_groups(REDIS_STREAM_NAME)
            our_group = next(
                (g for g in groups if g.get("name") == REDIS_CONSUMER_GROUP), None
            )
            lag = our_group.get("lag", 0) if our_group is not None else 0
        except Exception as exc:  # transient Redis errors must not break scrape
            logger.debug("/metrics collector lag unavailable: %s", exc)
            lag = -1
    return Sample(
        "meeting_api_collector_stream_lag",
        "gauge",
        "Consumer-group lag of the transcription_segments stream (-1 when Redis is unreachable).",
        lag,
    )


# ---------------------------------------------------------------------------
# Postgres — on-scrape aggregation
# ---------------------------------------------------------------------------

# Same status set + eligibility filters as the row selection in
# `sweeps._sweep_final_transcription_jobs` (queued / running / failed+retryable),
# grouped instead of ordered+limited. AT-006 asserts the predicates match.
FINAL_TRANSCRIPTION_BACKLOG_SQL = """
    SELECT
      CASE
        WHEN data #>> '{final_transcription,status}' = 'queued' THEN 'queued'
        WHEN data #>> '{final_transcription,status}' = 'running' THEN 'running'
        ELSE 'failed_retryable'
      END AS bucket,
      COUNT(*) AS n
    FROM meetings
    WHERE status IN (:completed, :failed)
      AND COALESCE(data->>'transcribe_enabled', 'true') <> 'false'
      AND COALESCE(data->>'recording_enabled', 'true') <> 'false'
      AND (
        data #>> '{final_transcription,status}' = 'queued'
        OR (
          data #>> '{final_transcription,status}' = 'failed'
          AND COALESCE(data #>> '{final_transcription,retryable}', 'false') = 'true'
        )
        OR data #>> '{final_transcription,status}' = 'running'
      )
    GROUP BY 1
"""

MEETINGS_BY_STATUS_SQL = """
    SELECT status, COUNT(*) AS n
    FROM meetings
    WHERE created_at >= :since
    GROUP BY status
"""

MEETINGS_BY_REASON_SQL = """
    SELECT COALESCE(data #>> '{completion_reason}', 'unset') AS reason, COUNT(*) AS n
    FROM meetings
    WHERE created_at >= :since
    GROUP BY 1
"""

FINAL_TRANSCRIPTION_JOBS_SQL = """
    SELECT
      data #>> '{final_transcription,status}' AS status,
      data #>> '{final_transcription,queued_at}' AS queued_at,
      data #>> '{final_transcription,completed_at}' AS completed_at
    FROM meetings
    WHERE created_at >= :since
      AND data #>> '{final_transcription,status}' IN ('succeeded', 'failed')
"""


def _window_start() -> datetime:
    return datetime.utcnow() - timedelta(hours=WINDOW_HOURS)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None)


def _percentile(values: Sequence[float], quantile: float) -> float:
    """Nearest-rank percentile (deterministic, no interpolation)."""
    ordered = sorted(values)
    index = int(-(-quantile * len(ordered) // 1)) - 1  # ceil(q*n) - 1
    index = max(0, min(index, len(ordered) - 1))
    return float(ordered[index])


async def query_join_stats(db) -> dict[str, dict[str, int]]:
    """Meetings created in the last 24h, counted by status and by reason.

    Join success is exposed as numerator/denominator (status counts plus the
    completion_reason breakdown); the ratio is left to the reader.
    """
    from sqlalchemy import text

    since = _window_start()
    by_status = {
        str(row[0]): int(row[1])
        for row in (await db.execute(text(MEETINGS_BY_STATUS_SQL), {"since": since})).fetchall()
    }
    by_reason = {
        str(row[0]): int(row[1])
        for row in (await db.execute(text(MEETINGS_BY_REASON_SQL), {"since": since})).fetchall()
    }
    return {"by_status": by_status, "by_reason": by_reason}


async def query_final_transcription_stats(db) -> dict[str, Any]:
    """Backlog (all-time, sweep-eligible rows) + last-24h outcomes and latency."""
    from sqlalchemy import text

    from .schemas import MeetingStatus

    backlog = {bucket: 0 for bucket in BACKLOG_BUCKETS}
    rows = (await db.execute(text(FINAL_TRANSCRIPTION_BACKLOG_SQL), {
        "completed": MeetingStatus.COMPLETED.value,
        "failed": MeetingStatus.FAILED.value,
    })).fetchall()
    for row in rows:
        bucket = str(row[0])
        if bucket in backlog:
            backlog[bucket] = int(row[1])

    since = _window_start()
    jobs = {"completed": 0, "failed": 0}
    latencies: list[float] = []
    for row in (await db.execute(text(FINAL_TRANSCRIPTION_JOBS_SQL), {"since": since})).fetchall():
        status = str(row[0])
        if status == "succeeded":
            jobs["completed"] += 1
            queued_at = _parse_iso(row[1])
            completed_at = _parse_iso(row[2])
            if queued_at is not None and completed_at is not None:
                seconds = (completed_at - queued_at).total_seconds()
                if seconds >= 0:
                    latencies.append(seconds)
        elif status == "failed":
            jobs["failed"] += 1

    return {"backlog": backlog, "jobs": jobs, "latencies": latencies}


def _get_session_factory():
    """Indirection so the scrape path can be exercised without a real DB."""
    from .database import async_session_local

    return async_session_local


async def db_samples() -> list[Sample]:
    """On-scrape DB aggregation. Returns [] when the DB is unreachable."""
    try:
        session_factory = _get_session_factory()
        async with session_factory() as db:
            join_stats = await query_join_stats(db)
            ft_stats = await query_final_transcription_stats(db)
    except Exception as exc:
        # A monitoring endpoint must not die before what it monitors.
        logger.warning("/metrics DB aggregation unavailable: %s", exc)
        return []

    samples: list[Sample] = []
    status_help = f"Meetings created in the last {WINDOW_HOURS}h, by status."
    for status, count in sorted(join_stats.get("by_status", {}).items()):
        samples.append(Sample(
            "meeting_api_meetings_24h", "gauge", status_help, count,
            (("status", status),),
        ))
    reason_help = f"Meetings created in the last {WINDOW_HOURS}h, by completion_reason."
    for reason, count in sorted(join_stats.get("by_reason", {}).items()):
        samples.append(Sample(
            "meeting_api_meetings_by_reason_24h", "gauge", reason_help, count,
            (("reason", reason),),
        ))

    backlog = ft_stats.get("backlog", {})
    backlog_help = (
        "Meetings awaiting final transcription, by sweep bucket "
        "(same row selection as the final-transcription sweep)."
    )
    for bucket in BACKLOG_BUCKETS:
        samples.append(Sample(
            "meeting_api_final_transcription_backlog", "gauge", backlog_help,
            int(backlog.get(bucket, 0)), (("status", bucket),),
        ))

    jobs = ft_stats.get("jobs", {})
    jobs_help = f"Final transcription jobs finished in the last {WINDOW_HOURS}h, by result."
    for result in ("completed", "failed"):
        samples.append(Sample(
            "meeting_api_final_transcription_jobs_24h", "gauge", jobs_help,
            int(jobs.get(result, 0)), (("result", result),),
        ))

    latencies = ft_stats.get("latencies") or []
    if latencies:
        latency_help = (
            f"Final transcription latency (queued_at to completed_at) in seconds "
            f"over the last {WINDOW_HOURS}h."
        )
        for quantile in LATENCY_QUANTILES:
            samples.append(Sample(
                "meeting_api_final_transcription_latency_seconds", "gauge", latency_help,
                _percentile(latencies, quantile), (("quantile", str(quantile)),),
            ))
    return samples


async def collect_samples(redis_client: Any = None) -> list[Sample]:
    """Collect every exported sample. Never raises for a partial outage."""
    samples = process_counter_samples()
    samples.append(await collector_lag_sample(redis_client))
    samples.extend(await db_samples())
    return samples


async def render_metrics(redis_client: Any = None) -> str:
    return render_prometheus_text(await collect_samples(redis_client))
