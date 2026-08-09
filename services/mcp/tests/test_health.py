"""
Unit test for the mcp container healthcheck endpoint (GET /health).

The compose healthcheck for the `mcp` service hits http://localhost:18888/health,
so the route must exist. Asserting on `app.routes` is enough: no TestClient and
no network are needed.

Run with: cd services/mcp && pytest tests/ -v
"""
import sys
import types

# Stub out fastapi_mcp before importing main (it's not pip-installable outside Docker)
if "fastapi_mcp" not in sys.modules:
    stub = types.ModuleType("fastapi_mcp")

    class _FakeMCP:
        def __init__(self, *a, **kw): pass
        def mount_http(self, *a, **kw): pass
        server = types.SimpleNamespace(
            list_prompts=lambda: (lambda f: f),
            get_prompt=lambda: (lambda f: f),
        )

    stub.FastApiMCP = _FakeMCP
    sys.modules["fastapi_mcp"] = stub

# Stub mcp.types if not available
if "mcp" not in sys.modules:
    mcp_pkg = types.ModuleType("mcp")
    mcp_types = types.ModuleType("mcp.types")

    class _FakeStub:
        def __init__(self, **kw): pass

    mcp_types.Prompt = _FakeStub
    mcp_types.PromptArgument = _FakeStub
    mcp_types.TextContent = _FakeStub
    mcp_types.PromptMessage = _FakeStub
    mcp_types.ListPromptsResult = _FakeStub
    mcp_types.GetPromptResult = _FakeStub

    mcp_pkg.types = mcp_types
    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.types"] = mcp_types

from main import app


def _routes(path: str):
    return [r for r in app.routes if getattr(r, "path", None) == path]


class TestHealthRoute:
    def test_health_route_registered(self):
        assert _routes("/health"), "GET /health is not registered on the mcp app"

    def test_health_route_accepts_get(self):
        route = _routes("/health")[0]
        assert "GET" in route.methods

    def test_health_route_has_no_auth_dependency(self):
        # The container healthcheck runs without credentials, so /health must not
        # require the API key dependency used by the business endpoints.
        route = _routes("/health")[0]
        names = [
            getattr(d.call, "__name__", "")
            for d in route.dependant.dependencies
        ]
        assert "get_api_key" not in names
