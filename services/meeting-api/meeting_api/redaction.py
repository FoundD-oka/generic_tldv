"""Shared redaction helpers for assistant-visible meeting context."""

from __future__ import annotations

import re

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.IGNORECASE),
        r"\1[REDACTED]@",
    ),
    (re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE), "Bearer [REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_-]+"), "[REDACTED_API_KEY]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd)\b\s*([:=])\s*([^\s,;]+)"),
        r"\1\2[REDACTED]",
    ),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
            re.IGNORECASE,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"\b[A-Za-z0-9_-]{48,}\b"), "[REDACTED_TOKEN]"),
)


def redact_secrets(text: str) -> str:
    """Mask obvious credentials before text is passed to LLM context."""

    redacted = str(text)
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
