"""Unit tests for cross-meeting transcript search (FT-3).

DB-free: input validation, LIKE escaping (literal-match contract) and the
structural response bounds. The SQL behaviour itself is covered by
tests/integration/test_transcript_search_postgres.py.
"""
from __future__ import annotations

import pytest

from meeting_api.search import (
    MAX_MATCHES_PER_MEETING,
    MAX_MEETING_LIMIT,
    MAX_QUERY_LENGTH,
    MIN_QUERY_LENGTH,
    escape_like,
    normalize_query,
)
from fastapi import HTTPException


# --- LIKE escaping (AT-303) -------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("50%", "50\\%"),
        ("a_b", "a\\_b"),
        ("back\\slash", "back\\\\slash"),
        ("%_\\", "\\%\\_\\\\"),
        ("日本語", "日本語"),
        ("100%_off\\", "100\\%\\_off\\\\"),
    ],
)
def test_escape_like_escapes_all_metacharacters(raw, expected):
    assert escape_like(raw) == expected


def test_escape_like_escapes_backslash_before_wildcards():
    # `\%` must become `\\\%` (escaped backslash + escaped percent), not `\\%`
    # which would turn the user's literal backslash into an escape for `%`.
    assert escape_like("\\%") == "\\\\\\%"


# --- Query normalization (AT-304) -------------------------------------------

@pytest.mark.parametrize("raw", ["", " ", "   ", "a", " a ", None])
def test_normalize_query_rejects_too_short(raw):
    with pytest.raises(HTTPException) as exc:
        normalize_query(raw)
    assert exc.value.status_code == 422


def test_normalize_query_rejects_too_long():
    with pytest.raises(HTTPException) as exc:
        normalize_query("あ" * (MAX_QUERY_LENGTH + 1))
    assert exc.value.status_code == 422


@pytest.mark.parametrize("raw,expected", [("  会議  ", "会議"), ("ab", "ab"), ("あ" * MAX_QUERY_LENGTH, "あ" * MAX_QUERY_LENGTH)])
def test_normalize_query_strips_and_accepts(raw, expected):
    assert normalize_query(raw) == expected


def test_min_query_length_allows_two_char_japanese_terms():
    """NFT-301: 2-char queries must not be rejected (seq-scan fallback is OK)."""
    assert MIN_QUERY_LENGTH == 2
    assert normalize_query("会議") == "会議"


# --- Endpoint-level validation ----------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {},                       # q missing
        {"q": ""},                # empty
        {"q": "a"},               # 1 char after strip
        {"q": "   "},             # whitespace only
        {"q": "あ" * 101},        # 101 chars
        {"q": "会議", "limit": 0},
        {"q": "会議", "limit": 51},
        {"q": "会議", "offset": -1},
    ],
)
async def test_search_rejects_invalid_input(client, params):
    response = await client.get("/transcripts/search", params=params)
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_search_returns_empty_result_shape(client):
    response = await client.get("/transcripts/search", params={"q": "  会議  "})
    assert response.status_code == 200
    body = response.json()
    assert body == {"query": "会議", "results": [], "has_more": False}


@pytest.mark.asyncio
async def test_search_accepts_boundary_limits(client):
    for limit in (1, MAX_MEETING_LIMIT):
        response = await client.get(
            "/transcripts/search", params={"q": "会議", "limit": limit, "offset": 0}
        )
        assert response.status_code == 200


# --- Structural response bounds (NFT-302) -----------------------------------

def test_response_is_structurally_bounded():
    assert MAX_MEETING_LIMIT == 50
    assert MAX_MATCHES_PER_MEETING == 3
