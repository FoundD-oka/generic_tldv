import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport

import main
from main import MEETING_API_URL, ROUTE_SCOPES, app


TX_USER = {"user_id": 7, "scopes": ["tx"], "max_concurrent": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "expected_url", "json_body"),
    [
        (
            "PATCH",
            "/meetings/30/transcripts/speakers",
            f"{MEETING_API_URL}/meetings/30/transcripts/speakers",
            {"rename": [{"speaker_cluster": "g:demo:s0", "display_name": "岡田"}]},
        ),
        (
            "DELETE",
            "/meetings/30/speaker-suggestions/g%3Ademo%3As0",
            f"{MEETING_API_URL}/meetings/30/speaker-suggestions/g:demo:s0",
            None,
        ),
        (
            "POST",
            "/voiceprints/preview-from-segments",
            f"{MEETING_API_URL}/voiceprints/preview-from-segments",
            {"meeting_id": 30, "segment_ids": ["seg-1", "seg-2"]},
        ),
        (
            "POST",
            "/voiceprints/enroll-from-segments",
            f"{MEETING_API_URL}/voiceprints/enroll-from-segments",
            {
                "meeting_id": 30,
                "segment_ids": ["seg-1", "seg-2"],
                "display_name": "岡田",
                "clip_sha256": "a" * 64,
                "source_fingerprint": "b" * 64,
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        ),
        (
            "POST",
            "/voiceprints/enroll-from-audio",
            f"{MEETING_API_URL}/voiceprints/enroll-from-audio",
            {
                "audio_base64": "UklGRg==",
                "media_format": "webm",
                "display_name": "岡田",
                "audio_review_confirmed": True,
                "consent_confirmed": True,
            },
        ),
        ("GET", "/speaker-profiles", f"{MEETING_API_URL}/speaker-profiles", None),
        ("DELETE", "/speaker-profiles/9", f"{MEETING_API_URL}/speaker-profiles/9", None),
    ],
)
async def test_speaker_voiceprint_routes_forward_to_meeting_api(
    method: str,
    path: str,
    expected_url: str,
    json_body: dict | None,
):
    captured = {}

    async def request(forwarded_method, url, **kwargs):
        captured.update({"method": forwarded_method, "url": url, **kwargs})
        return httpx.Response(200, json={"ok": True})

    app.state.http_client = SimpleNamespace(request=request)
    with patch("main._resolve_token", AsyncMock(return_value=TX_USER)):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            request_kwargs = {
                "headers": {"x-api-key": "key", "x-user-id": "999"},
            }
            if json_body is not None:
                request_kwargs["json"] = json_body
            response = await client.request(
                method,
                path,
                **request_kwargs,
            )

    assert response.status_code == 200
    assert captured["method"] == method
    assert captured["url"] == expected_url
    assert captured["headers"]["x-user-id"] == "7"
    assert captured["headers"]["x-user-scopes"] == "tx"
    if json_body is not None:
        assert json.loads(captured["content"]) == json_body
    if path.startswith("/voiceprints/"):
        assert captured["timeout"] == 180.0


@pytest.mark.asyncio
async def test_legacy_cluster_enrollment_returns_410_without_forwarding():
    app.state.http_client = AsyncMock(spec=httpx.AsyncClient)
    with patch("main._resolve_token", AsyncMock(return_value=TX_USER)):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/voiceprints/enroll-from-cluster",
                headers={"x-api-key": "key"},
                json={"meeting_id": 30, "cluster_id": "legacy"},
            )

    assert response.status_code == 410
    assert response.json()["detail"] == (
        "Cluster-based enrollment is disabled; review selected audio first"
    )
    app.state.http_client.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_cluster_enrollment_requires_auth_before_410():
    app.state.http_client = AsyncMock(spec=httpx.AsyncClient)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post("/voiceprints/enroll-from-cluster")

    assert response.status_code == 401
    app.state.http_client.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_audio_rejects_oversized_chunked_body_without_forwarding():
    async def chunks():
        yield b'{"audio_base64":"'
        yield b"SENSITIVE-AUDIO-SENTINEL"
        yield b'"}'

    app.state.http_client = AsyncMock(spec=httpx.AsyncClient)
    with patch("main._resolve_token", AsyncMock(return_value=TX_USER)), patch(
        "main.VOICEPRINT_DIRECT_AUDIO_BODY_MAX_BYTES", 24,
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/voiceprints/enroll-from-audio",
                headers={"x-api-key": "key"},
                content=chunks(),
            )

    assert response.status_code == 413
    assert "SENSITIVE-AUDIO-SENTINEL" not in response.text
    app.state.http_client.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_audio_admission_rejects_before_body_read_then_accepts_after_release():
    payload = {
        "audio_base64": "UklGRg==",
        "media_format": "wav",
        "display_name": "田中",
        "audio_review_confirmed": True,
        "consent_confirmed": True,
    }
    upstream_entered = asyncio.Event()
    upstream_release = asyncio.Event()
    upstream_calls = 0

    async def request(_method, _url, **_kwargs):
        nonlocal upstream_calls
        upstream_calls += 1
        if upstream_calls == 1:
            upstream_entered.set()
            await upstream_release.wait()
        return httpx.Response(200, json={"ok": True})

    rejected_body_reads = 0

    async def rejected_body():
        nonlocal rejected_body_reads
        rejected_body_reads += 1
        yield json.dumps(payload).encode("utf-8")

    app.state.http_client = SimpleNamespace(request=request)
    gate = main._ImmediateAdmissionGate(1)
    with patch("main._resolve_token", AsyncMock(return_value=TX_USER)), patch(
        "main._DIRECT_ENROLLMENT_ADMISSION_GATE", gate,
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            first = asyncio.create_task(client.post(
                "/voiceprints/enroll-from-audio",
                headers={"x-api-key": "key"},
                json=payload,
            ))
            await asyncio.wait_for(upstream_entered.wait(), timeout=1.0)

            busy = await client.post(
                "/voiceprints/enroll-from-audio",
                headers={"x-api-key": "key", "content-type": "application/json"},
                content=rejected_body(),
            )
            assert busy.status_code == 429
            assert busy.headers["retry-after"] == "1"
            assert busy.headers["cache-control"] == "no-store"
            assert rejected_body_reads == 0
            assert upstream_calls == 1

            upstream_release.set()
            assert (await asyncio.wait_for(first, timeout=1.0)).status_code == 200
            accepted = await client.post(
                "/voiceprints/enroll-from-audio",
                headers={"x-api-key": "key"},
                json=payload,
            )

    assert accepted.status_code == 200
    assert upstream_calls == 2


@pytest.mark.asyncio
async def test_direct_audio_admission_releases_on_cancel_and_upstream_exception():
    payload = {
        "audio_base64": "UklGRg==",
        "media_format": "wav",
        "display_name": "田中",
        "audio_review_confirmed": True,
        "consent_confirmed": True,
    }
    upstream_entered = asyncio.Event()
    upstream_calls = 0

    async def request(_method, _url, **_kwargs):
        nonlocal upstream_calls
        upstream_calls += 1
        if upstream_calls == 1:
            upstream_entered.set()
            await asyncio.Event().wait()
        if upstream_calls == 2:
            raise httpx.ConnectError("simulated upstream failure")
        return httpx.Response(200, json={"ok": True})

    app.state.http_client = SimpleNamespace(request=request)
    gate = main._ImmediateAdmissionGate(1)
    with patch("main._resolve_token", AsyncMock(return_value=TX_USER)), patch(
        "main._DIRECT_ENROLLMENT_ADMISSION_GATE", gate,
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            cancelled = asyncio.create_task(client.post(
                "/voiceprints/enroll-from-audio",
                headers={"x-api-key": "key"},
                json=payload,
            ))
            await asyncio.wait_for(upstream_entered.wait(), timeout=1.0)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(cancelled, timeout=1.0)

            failed = await client.post(
                "/voiceprints/enroll-from-audio",
                headers={"x-api-key": "key"},
                json=payload,
            )
            assert failed.status_code == 503
            accepted = await client.post(
                "/voiceprints/enroll-from-audio",
                headers={"x-api-key": "key"},
                json=payload,
            )

    assert accepted.status_code == 200
    assert upstream_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/voiceprints/preview-from-segments",
        "/voiceprints/enroll-from-segments",
    ],
)
async def test_selected_audio_rejects_oversized_chunked_body_without_forwarding(path: str):
    async def chunks():
        yield b'{"segment_ids":["'
        yield b"SENSITIVE-SEGMENT-SENTINEL"
        yield b'"]}'

    app.state.http_client = AsyncMock(spec=httpx.AsyncClient)
    with patch("main._resolve_token", AsyncMock(return_value=TX_USER)), patch(
        "main.VOICEPRINT_SEGMENTS_BODY_MAX_BYTES", 24,
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                path,
                headers={"x-api-key": "key"},
                content=chunks(),
            )

    assert response.status_code == 413
    assert "SENSITIVE-SEGMENT-SENTINEL" not in response.text
    app.state.http_client.request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PATCH", "/meetings/30/transcripts/speakers"),
        ("POST", "/voiceprints/enroll-from-cluster"),
        ("POST", "/voiceprints/preview-from-segments"),
        ("POST", "/voiceprints/enroll-from-segments"),
        ("POST", "/voiceprints/enroll-from-audio"),
        ("GET", "/speaker-profiles"),
    ],
)
async def test_speaker_voiceprint_routes_require_tx_scope(method: str, path: str):
    assert ROUTE_SCOPES["/meetings"] == {"tx"}
    assert ROUTE_SCOPES["/speaker-profiles"] == {"tx"}
    assert ROUTE_SCOPES["/voiceprints"] == {"tx"}

    app.state.http_client = AsyncMock(spec=httpx.AsyncClient)
    with patch(
        "main._resolve_token",
        AsyncMock(return_value={"user_id": 7, "scopes": ["bot"], "max_concurrent": 1}),
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.request(method, path, headers={"x-api-key": "key"})

    assert response.status_code == 403
