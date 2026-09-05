"""Frozen byte/header contract before the gateway streaming refactor."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport

from main import app


@pytest.mark.asyncio
async def test_R00_raw_range_preserves_headers():
    seen_requests: list[httpx.Request] = []

    def backend(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            206,
            content=b"abc",
            headers={
                "Content-Type": "audio/webm",
                "Content-Range": "bytes 2-4/10",
                "Content-Length": "3",
                "Accept-Ranges": "bytes",
            },
            request=request,
        )

    prior_client = getattr(app.state, "http_client", None)
    user = {
        "user_id": 5,
        "scopes": ["bot", "tx", "browser"],
        "max_concurrent": 1,
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as upstream:
        app.state.http_client = upstream
        try:
            with patch("main._resolve_token", AsyncMock(return_value=user)):
                async with httpx.AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://gateway.test"
                ) as client:
                    response = await client.get(
                        "/recordings/42/media/7/raw",
                        headers={"X-API-Key": "test-key", "Range": "bytes=2-4"},
                    )
        finally:
            app.state.http_client = prior_client

    assert len(seen_requests) == 1
    assert seen_requests[0].headers["range"] == "bytes=2-4"
    assert response.status_code == 206
    assert response.content == b"abc"
    assert response.headers["content-range"] == "bytes 2-4/10"
    assert response.headers["content-length"] == "3"
    assert response.headers["accept-ranges"] == "bytes"
