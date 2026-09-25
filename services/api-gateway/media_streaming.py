"""Streaming response lifecycle for recording binary proxy routes."""

import logging
from collections.abc import AsyncIterator

import anyio
import httpx
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _response_headers(upstream: httpx.Response) -> dict[str, str]:
    connection_tokens = {
        token.strip().lower()
        for value in upstream.headers.get_list("connection")
        for token in value.split(",")
        if token.strip()
    }
    excluded = _HOP_BY_HOP_HEADERS | connection_tokens
    return {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() not in excluded
    }


class ClosingStreamingResponse(StreamingResponse):
    """Relay raw upstream bytes and close the manual HTTPX stream on every exit."""

    def __init__(self, upstream: httpx.Response) -> None:
        self.upstream = upstream
        super().__init__(
            self._iter_upstream(),
            status_code=upstream.status_code,
            headers=_response_headers(upstream),
        )

    async def _iter_upstream(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.upstream.aiter_raw():
                yield chunk
        except (httpx.HTTPError, OSError):
            logger.warning(
                "recording_stream_read_failed status=%s",
                self.upstream.status_code,
            )
            raise

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.upstream.aclose()
