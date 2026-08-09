"""ST-18 — tests for runtime-api `GET /metrics`."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime_api import config, state
from runtime_api.main import (
    APIKeyMiddleware,
    METRICS_CONTENT_TYPE,
    register_metrics_route,
    render_prometheus_text,
)


@pytest.fixture
def app():
    """App with the real /metrics route, fakeredis, and auth enabled.

    API_KEYS is non-empty on purpose: a request without `X-API-Key` must still
    reach /metrics, which only holds if `/metrics` is in the middleware's skip
    list.
    """
    import fakeredis.aioredis

    original_keys = config.API_KEYS
    config.API_KEYS = ["test-key"]

    @asynccontextmanager
    async def lifespan(app):
        app.state.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield

    test_app = FastAPI(lifespan=lifespan)
    test_app.add_middleware(APIKeyMiddleware)
    register_metrics_route(test_app)

    yield test_app

    config.API_KEYS = original_keys


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def test_metrics_served_without_api_key(client):
    resp = client.get("/metrics")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == METRICS_CONTENT_TYPE


def test_metrics_requires_api_key_on_other_routes(app):
    """Sanity check: the skip list did not disable auth for everything."""

    @app.get("/guarded")
    async def guarded():
        return {"ok": True}

    with TestClient(app) as c:
        assert c.get("/guarded").status_code == 403


def test_metrics_exposes_idle_loop_counters(client, monkeypatch):
    from runtime_api import lifecycle

    monkeypatch.setattr(lifecycle, "idle_loop_iterations", 17)
    monkeypatch.setattr(lifecycle, "idle_loop_last_iteration_at", 1700000000.0)

    body = client.get("/metrics").text

    assert "# TYPE runtime_api_idle_loop_iterations_total counter" in body
    assert "runtime_api_idle_loop_iterations_total 17" in body
    assert (
        "runtime_api_idle_loop_last_iteration_timestamp_seconds 1700000000" in body
    )


def test_metrics_counts_containers_by_status(client):
    redis = client.app.state.redis

    async def seed():
        await redis.set(
            f"{state.KEY_PREFIX}a", json.dumps({"status": "running", "user_id": "u"})
        )
        await redis.set(
            f"{state.KEY_PREFIX}b", json.dumps({"status": "running", "user_id": "u"})
        )
        await redis.set(
            f"{state.KEY_PREFIX}c", json.dumps({"status": "stopped", "user_id": "u"})
        )

    client.portal.call(seed)

    body = client.get("/metrics").text

    assert 'runtime_api_containers{status="running"} 2' in body
    assert 'runtime_api_containers{status="stopped"} 1' in body


def test_metrics_survives_redis_failure(client, monkeypatch):
    """Redis down: container gauges disappear, counters and 200 remain."""
    monkeypatch.setattr(client.app.state, "redis", None)

    resp = client.get("/metrics")

    assert resp.status_code == 200
    assert "runtime_api_idle_loop_iterations_total" in resp.text
    assert "runtime_api_containers" not in resp.text


def test_render_prometheus_text_shape():
    body = render_prometheus_text([
        ("some_total", "counter", "A counter.", 3, ()),
        ("some_gauge", "gauge", "A gauge.", 1.5, (("k", 'a"b\\c'),)),
    ])

    assert body == (
        "# HELP some_total A counter.\n"
        "# TYPE some_total counter\n"
        "some_total 3\n"
        "# HELP some_gauge A gauge.\n"
        "# TYPE some_gauge gauge\n"
        'some_gauge{k="a\\"b\\\\c"} 1.5\n'
    )
    assert body.endswith("\n")
