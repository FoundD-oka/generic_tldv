"""FastAPI application — container lifecycle API.

Startup: connects Redis, initializes the configured backend, reconciles state,
starts the idle management loop and event listener.

Shutdown: cancels background tasks, closes connections.
"""

from __future__ import annotations

import asyncio
import logging
import os

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from runtime_api import config
from runtime_api.api import router
from runtime_api.lifecycle import handle_container_exit, idle_loop, reconcile_state
from runtime_api.profiles import install_sighup_handler, load_profiles
from runtime_api.scheduler import start_executor, stop_executor
from runtime_api.scheduler_api import scheduler_router

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("runtime_api")


# ---------------------------------------------------------------------------
# ST-18 — Prometheus text format (0.0.4) exposition.
#
# runtime-api ships as an independent package, so the renderer is a local
# copy rather than a shared import from meeting-api. Only counters and gauges
# are exposed, so no `prometheus_client` dependency is needed.
# ---------------------------------------------------------------------------

METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_metric_value(value) -> str:
    number = float(value)
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


def render_prometheus_text(samples) -> str:
    """Render `(name, type, help, value, labels)` tuples as text format 0.0.4.

    `# HELP` / `# TYPE` are emitted once per metric name; output ends with a
    newline.
    """
    grouped: dict = {}
    order: list = []
    for name, mtype, mhelp, value, labels in samples:
        if name not in grouped:
            grouped[name] = []
            order.append(name)
        grouped[name].append((mtype, mhelp, value, labels))

    lines: list = []
    for name in order:
        series = grouped[name]
        lines.append(f"# HELP {name} {series[0][1]}")
        lines.append(f"# TYPE {name} {series[0][0]}")
        for _mtype, _mhelp, value, labels in series:
            if labels:
                rendered = ",".join(
                    f'{key}="{_escape_label_value(str(val))}"' for key, val in labels
                )
                lines.append(f"{name}{{{rendered}}} {_format_metric_value(value)}")
            else:
                lines.append(f"{name} {_format_metric_value(value)}")
    return "".join(line + "\n" for line in lines)


async def collect_metrics_samples(app: FastAPI) -> list:
    """Collect samples at scrape time. Never raises for a partial outage."""
    # Imported here so the live module attribute is read on every scrape
    # (import-time binding would freeze the counter at its startup value).
    from runtime_api import lifecycle

    samples = [
        (
            "runtime_api_idle_loop_iterations_total",
            "counter",
            "Iterations of the idle-management loop since process start.",
            getattr(lifecycle, "idle_loop_iterations", 0),
            (),
        ),
        (
            "runtime_api_idle_loop_last_iteration_timestamp_seconds",
            "gauge",
            "Unix timestamp of the last idle-management loop iteration (0 if none yet).",
            getattr(lifecycle, "idle_loop_last_iteration_at", 0.0),
            (),
        ),
    ]

    try:
        from runtime_api import state

        redis = getattr(app.state, "redis", None)
        if redis is None:
            raise RuntimeError("redis not initialized")
        containers = await state.list_containers(redis)
        counts: dict = {}
        for container in containers:
            status = str(container.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        for status in sorted(counts):
            samples.append((
                "runtime_api_containers",
                "gauge",
                "Tracked containers by status.",
                counts[status],
                (("status", status),),
            ))
    except Exception as exc:
        # A monitoring endpoint must not die before what it monitors: drop the
        # Redis-derived series and still serve the in-process counters.
        logger.debug(f"/metrics container gauges unavailable: {exc}")

    return samples


def register_metrics_route(app: FastAPI) -> None:
    """Mount `GET /metrics` on `app` (unauthenticated; runtime-api binds to 127.0.0.1)."""

    @app.get("/metrics")
    async def metrics(request: Request):
        body = render_prometheus_text(await collect_metrics_samples(request.app))
        return Response(content=body, media_type=METRICS_CONTENT_TYPE)


def warn_missing_bot_env() -> None:
    """Warn once at startup when bots would be launched without a transcription URL."""
    if not os.getenv("TRANSCRIPTION_SERVICE_URL", "").strip():
        logger.warning(
            "TRANSCRIPTION_SERVICE_URL is not set; bot containers will "
            "receive an empty URL and realtime transcription will fail silently "
            "(profiles.yaml injects this value into every bot)"
        )


def create_app() -> FastAPI:
    vexa_env = os.getenv("VEXA_ENV", "development")
    public_docs = vexa_env != "production"
    app = FastAPI(
        title="Container Lifecycle API",
        description="Generic container orchestration with pluggable backends",
        version="0.1.0",
        docs_url="/docs" if public_docs else None,
        redoc_url="/redoc" if public_docs else None,
        openapi_url="/openapi.json" if public_docs else None,
    )

    # CORS
    cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API key auth middleware (optional — disabled when API_KEYS is empty)
    if config.API_KEYS:
        app.add_middleware(APIKeyMiddleware)

    app.include_router(router)
    app.include_router(scheduler_router)
    register_metrics_route(app)

    @app.on_event("startup")
    async def startup():
        # Redis — v0.10.5 Pack C.1 hardened client config (#267).
        app.state.redis = aioredis.from_url(
            config.REDIS_URL,
            decode_responses=True,
            socket_timeout=10,
            socket_connect_timeout=5,
            socket_keepalive=True,
            health_check_interval=30,
            retry_on_timeout=True,
        )
        await app.state.redis.ping()
        logger.info("Redis connected")

        # Load profiles
        load_profiles()
        warn_missing_bot_env()
        install_sighup_handler()

        # Initialize backend
        backend = _create_backend()
        app.state.backend = backend
        await backend.startup()
        logger.info(f"Backend '{config.ORCHESTRATOR_BACKEND}' initialized")

        # Process backend needs Redis reference
        if config.ORCHESTRATOR_BACKEND == "process":
            backend.set_redis(app.state.redis)

        # Reconcile state with backend reality
        await reconcile_state(app.state.redis, backend)

        # Start event listener for exit detection
        async def on_exit(name: str, exit_code: int):
            await handle_container_exit(app.state.redis, name, exit_code)
            try:
                await backend.remove(name)
            except Exception:
                logger.warning(f"Failed to remove exited container {name}", exc_info=True)

        await backend.listen_events(on_exit)

        # Start idle management loop
        app.state.idle_task = asyncio.create_task(idle_loop(app.state.redis, backend))

        # Start scheduler executor
        app.state.scheduler_task = asyncio.create_task(start_executor(app.state.redis))
        logger.info("Runtime API ready")

    @app.on_event("shutdown")
    async def shutdown():
        if hasattr(app.state, "idle_task"):
            app.state.idle_task.cancel()
        if hasattr(app.state, "scheduler_task"):
            await stop_executor()
            app.state.scheduler_task.cancel()
        if hasattr(app.state, "backend"):
            await app.state.backend.shutdown()
        if hasattr(app.state, "redis"):
            await app.state.redis.close()

    return app


def _create_backend():
    """Create the configured backend instance."""
    backend_name = config.ORCHESTRATOR_BACKEND.lower()

    if backend_name == "docker":
        from runtime_api.backends.docker import DockerBackend
        return DockerBackend()
    elif backend_name == "kubernetes":
        from runtime_api.backends.kubernetes import KubernetesBackend
        return KubernetesBackend()
    elif backend_name == "process":
        from runtime_api.backends.process import ProcessBackend
        return ProcessBackend()
    else:
        raise ValueError(f"Unknown backend: {backend_name}. Use: docker, kubernetes, process")


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Simple API key validation from X-API-Key header."""

    async def dispatch(self, request: Request, call_next):
        # Skip auth for health and metrics endpoints
        if request.url.path in ("/health", "/metrics", "/docs", "/openapi.json"):
            return await call_next(request)

        # Skip auth if no API keys configured (dev mode)
        if not config.API_KEYS:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key", "")
        if api_key not in config.API_KEYS:
            return JSONResponse(status_code=403, content={"detail": "Missing API token (X-API-Key header)"})

        return await call_next(request)


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
