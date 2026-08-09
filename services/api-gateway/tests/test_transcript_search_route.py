"""Gateway route for cross-meeting transcript search (FT-3).

Pins RF-304: ROUTE_SCOPES resolution is a plain prefix match on the request
path, so /transcripts/search inherits the same {"tx"} scope semantics as the
existing /transcripts/{platform}/{native_meeting_id} route.
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from main import MEETING_API_URL, ROUTE_SCOPES, app


@pytest.mark.asyncio
async def test_transcript_search_requires_tx_scope():
    assert ROUTE_SCOPES["/transcripts"] == {"tx"}
    app.state.http_client = AsyncMock(spec=httpx.AsyncClient)
    with patch("main._resolve_token", AsyncMock(return_value={"user_id": 7, "scopes": ["bot"], "max_concurrent": 1})):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/transcripts/search?q=会議", headers={"x-api-key": "key"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_transcript_search_forwards_to_meeting_api_with_query_params():
    captured = {}

    async def request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return httpx.Response(200, json={"query": "会議", "results": [], "has_more": False})

    app.state.http_client = AsyncMock(spec=httpx.AsyncClient)
    app.state.http_client.request = request
    with patch("main._resolve_token", AsyncMock(return_value={"user_id": 7, "scopes": ["tx"], "max_concurrent": 1})):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/transcripts/search",
                params={"q": "会議", "limit": 5},
                headers={"x-api-key": "key", "x-user-id": "999"},
            )

    assert response.status_code == 200
    assert captured["url"] == f"{MEETING_API_URL}/transcripts/search"
    assert captured["params"] == {"q": "会議", "limit": "5"}
    # Client-supplied identity headers must be replaced by the validated ones.
    assert captured["headers"]["x-user-id"] == "7"
    assert captured["headers"]["x-user-scopes"] == "tx"


@pytest.mark.asyncio
async def test_transcript_search_requires_api_key():
    app.state.http_client = AsyncMock(spec=httpx.AsyncClient)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/transcripts/search", params={"q": "会議"})
    assert response.status_code == 401
