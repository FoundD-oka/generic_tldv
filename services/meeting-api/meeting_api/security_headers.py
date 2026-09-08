"""Security headers middleware for FastAPI services."""

import logging
import os
import re
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("meeting-api.security_headers")

# Hostnames are space/semicolon free by construction here, so a configured origin
# can never inject extra CSP directives into the header.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _normalize_frame_ancestor(raw: str) -> str | None:
    """Validate one configured origin and return it as ``scheme://host[:port]``.

    Returns None for anything that is not a bare http(s) origin, so a typo in the
    env var cannot widen ``frame-ancestors`` beyond an origin.
    """
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None

    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.hostname or not _HOSTNAME_RE.match(parsed.hostname):
        return None
    if parsed.username or parsed.password:
        return None
    if parsed.query or parsed.fragment or parsed.params:
        return None
    if parsed.path not in ("", "/"):
        return None

    try:
        port = parsed.port
    except ValueError:
        return None

    origin = f"{parsed.scheme}://{parsed.hostname}"
    if port:
        origin = f"{origin}:{port}"
    return origin


def _configured_frame_ancestors() -> list[str]:
    """Extra origins allowed to iframe the VNC pages (e.g. a Cloud Run dashboard)."""
    raw_value = os.getenv("BROWSER_FRAME_ANCESTORS", "")
    origins: list[str] = []
    for entry in raw_value.split(","):
        candidate = entry.strip()
        if not candidate:
            continue
        origin = _normalize_frame_ancestor(candidate)
        if origin is None:
            logger.warning(
                "Ignoring invalid BROWSER_FRAME_ANCESTORS entry: %r", candidate
            )
            continue
        if origin not in origins:
            origins.append(origin)
    return origins


def _same_host_frame_ancestor(request: Request) -> str | None:
    """Allow dashboard-on-same-host embedding for remote browser VNC pages."""
    referer = request.headers.get("referer") or request.headers.get("origin")
    if not referer:
        return None

    try:
        referer_url = urlparse(referer)
    except ValueError:
        return None

    if referer_url.scheme not in {"http", "https"} or not referer_url.hostname:
        return None
    if referer_url.hostname != request.url.hostname:
        return None

    origin = f"{referer_url.scheme}://{referer_url.hostname}"
    if referer_url.port:
        origin = f"{origin}:{referer_url.port}"
    return origin


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Allow iframe embedding for browser session VNC pages (noVNC embedded in dashboard).
        # Dashboard runs on a different port (3002) than the gateway (8066), so SAMEORIGIN
        # won't work. Use Content-Security-Policy frame-ancestors instead (modern browsers)
        # and omit X-Frame-Options for VNC paths. All other routes keep DENY.
        path = request.url.path
        if path.startswith("/b/") and "/vnc/" in path:
            frame_ancestors = ["'self'", "http://localhost:*", "https://localhost:*"]
            for configured in _configured_frame_ancestors():
                if configured not in frame_ancestors:
                    frame_ancestors.append(configured)
            same_host_ancestor = _same_host_frame_ancestor(request)
            if same_host_ancestor and same_host_ancestor not in frame_ancestors:
                frame_ancestors.append(same_host_ancestor)
            response.headers["Content-Security-Policy"] = f"frame-ancestors {' '.join(frame_ancestors)}"
            # Don't set X-Frame-Options — it overrides CSP in some browsers
        else:
            response.headers["X-Frame-Options"] = "DENY"
        return response
