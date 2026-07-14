from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport

from main import ROUTE_SCOPES, app


@pytest.mark.asyncio
async def test_dictionary_requires_tx_scope():
    assert ROUTE_SCOPES["/transcription-dictionary"] == {"tx"}
    app.state.http_client = AsyncMock(spec=httpx.AsyncClient)
    with patch("main._resolve_token", AsyncMock(return_value={"user_id": 7, "scopes": ["bot"], "max_concurrent": 1})):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/transcription-dictionary", headers={"x-api-key": "key"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_dictionary_route_forwards_validated_user_identity():
    captured = {}

    async def request(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return httpx.Response(200, json={"terms": []})

    app.state.http_client = AsyncMock(spec=httpx.AsyncClient)
    app.state.http_client.request = request
    with patch("main._resolve_token", AsyncMock(return_value={"user_id": 7, "scopes": ["tx"], "max_concurrent": 1})):
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/transcription-dictionary",
                headers={"x-api-key": "key", "x-user-id": "999"},
            )
    assert response.status_code == 200
    assert captured["headers"]["x-user-id"] == "7"
    assert captured["headers"]["x-user-scopes"] == "tx"
