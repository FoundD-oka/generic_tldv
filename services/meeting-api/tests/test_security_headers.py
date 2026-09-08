import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.security_headers import SecurityHeadersMiddleware


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/b/{token}/vnc/vnc.html")
    async def vnc_page(token: str):
        return {"token": token}

    @app.get("/meetings/{meeting_id}")
    async def meeting(meeting_id: int):
        return {"id": meeting_id}

    return TestClient(app, base_url="http://172.238.172.98:8056")


@pytest.fixture(autouse=True)
def _clear_browser_frame_ancestors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to "env unset" so each test states its own configuration."""
    monkeypatch.delenv("BROWSER_FRAME_ANCESTORS", raising=False)


def test_vnc_frame_ancestors_allows_same_host_dashboard_port() -> None:
    response = _client().get(
        "/b/37/vnc/vnc.html",
        headers={"referer": "http://172.238.172.98:3000/meetings/37"},
    )

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "frame-ancestors 'self' http://localhost:* https://localhost:* http://172.238.172.98:3000"
    )
    assert "x-frame-options" not in response.headers


def test_vnc_frame_ancestors_rejects_cross_host_referer() -> None:
    response = _client().get(
        "/b/37/vnc/vnc.html",
        headers={"referer": "http://example.com/meetings/37"},
    )

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "frame-ancestors 'self' http://localhost:* https://localhost:*"
    )


def test_non_vnc_routes_keep_frame_deny() -> None:
    response = _client().get("/meetings/37")

    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"


def test_vnc_frame_ancestors_adds_configured_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "BROWSER_FRAME_ANCESTORS",
        "https://dashboard.a.run.app, http://192.0.2.10:3002/ ,",
    )

    response = _client().get("/b/37/vnc/vnc.html")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "frame-ancestors 'self' http://localhost:* https://localhost:* "
        "https://dashboard.a.run.app http://192.0.2.10:3002"
    )
    assert "x-frame-options" not in response.headers


@pytest.mark.parametrize(
    "invalid",
    [
        "https://dashboard.a.run.app/embed",
        "https://user:pw@dashboard.a.run.app",
        "https://dashboard.a.run.app?x=1",
        "https://dashboard.a.run.app#frag",
        "ftp://dashboard.a.run.app",
        "javascript:alert(1)",
        "dashboard.a.run.app",
        "https://",
        "*",
    ],
)
def test_vnc_frame_ancestors_rejects_invalid_entries(
    monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    monkeypatch.setenv("BROWSER_FRAME_ANCESTORS", invalid)

    response = _client().get("/b/37/vnc/vnc.html")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == (
        "frame-ancestors 'self' http://localhost:* https://localhost:*"
    )


def test_vnc_frame_ancestors_keeps_valid_entry_when_another_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "BROWSER_FRAME_ANCESTORS", "not-an-origin,https://dashboard.a.run.app"
    )

    response = _client().get("/b/37/vnc/vnc.html")

    assert response.headers["content-security-policy"] == (
        "frame-ancestors 'self' http://localhost:* https://localhost:* "
        "https://dashboard.a.run.app"
    )


def test_configured_origins_do_not_change_non_vnc_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BROWSER_FRAME_ANCESTORS", "https://dashboard.a.run.app")

    response = _client().get("/meetings/37")

    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in response.headers
