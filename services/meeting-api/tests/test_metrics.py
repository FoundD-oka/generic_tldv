"""ST-18 — tests for meeting-api `GET /metrics` and its renderer."""

import inspect
import re
from unittest.mock import patch

import pytest

from meeting_api import metrics as metrics_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeDB:
    """Minimal async DB stub: dispatches on the SQL text it is given."""

    def __init__(self, rows_by_marker):
        self.rows_by_marker = rows_by_marker
        self.statements = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        for marker, rows in self.rows_by_marker.items():
            if marker in sql:
                return FakeResult(rows)
        return FakeResult([])


class FakeSessionFactory:
    """Stands in for `async_session_local`."""

    def __init__(self, db):
        self.db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *exc_info):
        return False


def _patch_db(db):
    return patch.object(
        metrics_mod, "_get_session_factory", return_value=FakeSessionFactory(db)
    )


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql)


SAMPLE_DB_ROWS = {
    "GROUP BY status": [("completed", 12), ("failed", 3)],
    "AS reason": [("join_failure", 2), ("stopped", 10)],
    "AS bucket": [("queued", 4), ("running", 1), ("failed_retryable", 2)],
    "AS status": [
        ("succeeded", "2026-08-08T10:00:00", "2026-08-08T10:00:30"),
        ("succeeded", "2026-08-08T11:00:00", "2026-08-08T11:02:00"),
        ("failed", "2026-08-08T12:00:00", None),
    ],
}


# ---------------------------------------------------------------------------
# AT-003 — text format conformance (pure renderer)
# ---------------------------------------------------------------------------


def test_render_emits_help_and_type_once_per_metric():
    body = metrics_mod.render_prometheus_text([
        metrics_mod.Sample("m_total", "counter", "A counter.", 5),
        metrics_mod.Sample("m_gauge", "gauge", "A gauge.", 2, (("status", "ok"),)),
        metrics_mod.Sample("m_gauge", "gauge", "A gauge.", 7, (("status", "bad"),)),
    ])

    assert body == (
        "# HELP m_total A counter.\n"
        "# TYPE m_total counter\n"
        "m_total 5\n"
        "# HELP m_gauge A gauge.\n"
        "# TYPE m_gauge gauge\n"
        'm_gauge{status="ok"} 2\n'
        'm_gauge{status="bad"} 7\n'
    )


def test_render_escapes_label_values_and_ends_with_newline():
    body = metrics_mod.render_prometheus_text([
        metrics_mod.Sample(
            "m_gauge", "gauge", "A gauge.", 1, (("reason", 'a"b\\c\nd'),)
        ),
    ])

    assert 'm_gauge{reason="a\\"b\\\\c\\nd"} 1' in body
    assert body.endswith("\n")
    assert "\n" not in body[:-1].split("m_gauge{")[1]


def test_render_keeps_float_precision():
    body = metrics_mod.render_prometheus_text([
        metrics_mod.Sample("m_gauge", "gauge", "A gauge.", 1.25),
    ])

    assert "m_gauge 1.25\n" in body


# ---------------------------------------------------------------------------
# AT-002 — in-process counters are read at scrape time
# ---------------------------------------------------------------------------


def test_process_counters_read_current_module_values(monkeypatch):
    from meeting_api import sweeps

    monkeypatch.setattr(sweeps, "sweep_iterations", 41)
    monkeypatch.setattr(sweeps, "sweep_last_iteration_at", 1700000001.0)
    monkeypatch.setattr(sweeps, "final_transcription_worker_iterations", 7)
    monkeypatch.setattr(sweeps, "final_transcription_worker_last_iteration_at", 1700000002.0)

    body = metrics_mod.render_prometheus_text(metrics_mod.process_counter_samples())

    assert "# TYPE meeting_api_sweep_iterations_total counter" in body
    assert "meeting_api_sweep_iterations_total 41" in body
    assert "# TYPE meeting_api_sweep_last_iteration_timestamp_seconds gauge" in body
    assert "meeting_api_sweep_last_iteration_timestamp_seconds 1700000001" in body
    assert "meeting_api_final_transcription_worker_iterations_total 7" in body
    assert (
        "meeting_api_final_transcription_worker_last_iteration_timestamp_seconds 1700000002"
        in body
    )


# ---------------------------------------------------------------------------
# AT-006 — backlog query counts the same rows the sweep would pick up
# ---------------------------------------------------------------------------


def test_backlog_sql_matches_sweep_row_selection():
    from meeting_api import sweeps

    sweep_sql = _normalize(inspect.getsource(sweeps._sweep_final_transcription_jobs))
    backlog_sql = _normalize(metrics_mod.FINAL_TRANSCRIPTION_BACKLOG_SQL)

    predicates = [
        "status IN (:completed, :failed)",
        "COALESCE(data->>'transcribe_enabled', 'true') <> 'false'",
        "COALESCE(data->>'recording_enabled', 'true') <> 'false'",
        "data #>> '{final_transcription,status}' = 'queued'",
        "data #>> '{final_transcription,status}' = 'failed' AND "
        "COALESCE(data #>> '{final_transcription,retryable}', 'false') = 'true'",
        "data #>> '{final_transcription,status}' = 'running'",
    ]
    for predicate in predicates:
        assert predicate in backlog_sql, predicate
        assert predicate in sweep_sql, predicate


@pytest.mark.asyncio
async def test_backlog_buckets_are_classified_and_defaulted():
    db = FakeDB({"AS bucket": [("queued", 4), ("running", 1)], "AS status": []})

    stats = await metrics_mod.query_final_transcription_stats(db)

    assert stats["backlog"] == {"queued": 4, "running": 1, "failed_retryable": 0}


@pytest.mark.asyncio
async def test_latency_and_job_counts_from_final_transcription_rows():
    db = FakeDB({"AS bucket": [], "AS status": SAMPLE_DB_ROWS["AS status"]})

    stats = await metrics_mod.query_final_transcription_stats(db)

    assert stats["jobs"] == {"completed": 2, "failed": 1}
    assert stats["latencies"] == [30.0, 120.0]


# ---------------------------------------------------------------------------
# AT-001 — the three audited metrics are on the endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_audited_series(client):
    db = FakeDB(SAMPLE_DB_ROWS)

    with _patch_db(db):
        resp = await client.get("/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    body = resp.text

    # join success — numerator/denominator, not a ratio
    assert 'meeting_api_meetings_24h{status="completed"} 12' in body
    assert 'meeting_api_meetings_24h{status="failed"} 3' in body
    assert 'meeting_api_meetings_by_reason_24h{reason="join_failure"} 2' in body
    assert not any(
        line.startswith("meeting_api_") and "rate" in line for line in body.splitlines()
    )

    # final transcription latency
    assert 'meeting_api_final_transcription_latency_seconds{quantile="0.5"} 30' in body
    assert 'meeting_api_final_transcription_latency_seconds{quantile="0.95"} 120' in body

    # sweep backlog
    assert 'meeting_api_final_transcription_backlog{status="queued"} 4' in body
    assert 'meeting_api_final_transcription_backlog{status="running"} 1' in body
    assert 'meeting_api_final_transcription_backlog{status="failed_retryable"} 2' in body

    assert 'meeting_api_final_transcription_jobs_24h{result="completed"} 2' in body
    assert 'meeting_api_final_transcription_jobs_24h{result="failed"} 1' in body


@pytest.mark.asyncio
async def test_metrics_endpoint_reflects_live_counters(client, monkeypatch):
    from meeting_api import sweeps

    monkeypatch.setattr(sweeps, "sweep_iterations", 99)
    monkeypatch.setattr(sweeps, "final_transcription_worker_iterations", 5)
    db = FakeDB(SAMPLE_DB_ROWS)

    with _patch_db(db):
        body = (await client.get("/metrics")).text

    assert "meeting_api_sweep_iterations_total 99" in body
    assert "meeting_api_final_transcription_worker_iterations_total 5" in body


# ---------------------------------------------------------------------------
# AT-004 — partial failure never takes the endpoint down
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_survives_db_failure(client, monkeypatch):
    from meeting_api import sweeps

    monkeypatch.setattr(sweeps, "sweep_iterations", 12)

    async def boom(db):
        raise RuntimeError("postgres unreachable")

    db = FakeDB(SAMPLE_DB_ROWS)
    with _patch_db(db), patch.object(metrics_mod, "query_join_stats", boom):
        resp = await client.get("/metrics")

    assert resp.status_code == 200
    assert "meeting_api_sweep_iterations_total 12" in resp.text
    assert "meeting_api_meetings_24h" not in resp.text


@pytest.mark.asyncio
async def test_metrics_endpoint_survives_session_factory_failure(client):
    def boom():
        raise RuntimeError("no DB configured")

    with patch.object(metrics_mod, "_get_session_factory", boom):
        resp = await client.get("/metrics")

    assert resp.status_code == 200
    assert "meeting_api_sweep_iterations_total" in resp.text


@pytest.mark.asyncio
async def test_collector_lag_is_minus_one_without_redis(client):
    db = FakeDB(SAMPLE_DB_ROWS)

    with _patch_db(db):
        body = (await client.get("/metrics")).text

    assert "meeting_api_collector_stream_lag -1" in body


@pytest.mark.asyncio
async def test_collector_lag_is_minus_one_on_redis_error():
    class BoomRedis:
        async def xinfo_groups(self, *args, **kwargs):
            raise RuntimeError("redis down")

    sample = await metrics_mod.collector_lag_sample(BoomRedis())

    assert sample.name == "meeting_api_collector_stream_lag"
    assert sample.value == -1


@pytest.mark.asyncio
async def test_collector_lag_reports_group_lag():
    from meeting_api.collector.config import REDIS_CONSUMER_GROUP

    class OkRedis:
        async def xinfo_groups(self, stream):
            return [{"name": REDIS_CONSUMER_GROUP, "lag": 42}]

    sample = await metrics_mod.collector_lag_sample(OkRedis())

    assert sample.value == 42
