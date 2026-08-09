"""Cross-meeting transcript search (FT-3).

Literal (non-pattern) substring search over ``transcriptions.text`` for the
meetings owned by the authenticated user. Backed by the pg_trgm GIN index
``ix_transcription_text_trgm``; queries shorter than 3 characters fall back to
a sequential scan, which is intentionally allowed rather than rejected.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_user_and_token
from .database import get_db
from .meetings import _meeting_list_data_summary
from .models import Meeting, Transcription

router = APIRouter()

MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 100
DEFAULT_MEETING_LIMIT = 20
MAX_MEETING_LIMIT = 50
MAX_MATCHES_PER_MEETING = 3

LIKE_ESCAPE_CHAR = "\\"


def escape_like(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters so the query matches literally.

    The escape character itself must be escaped first, otherwise the escapes
    added for ``%``/``_`` would themselves be escaped.
    """
    return (
        value.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR * 2)
        .replace("%", LIKE_ESCAPE_CHAR + "%")
        .replace("_", LIKE_ESCAPE_CHAR + "_")
    )


def normalize_query(value: Optional[str]) -> str:
    """Validate and normalize the raw ``q`` parameter (422 on bad input)."""
    normalized = (value or "").strip()
    if len(normalized) < MIN_QUERY_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"検索キーワードは{MIN_QUERY_LENGTH}文字以上で指定してください",
        )
    if len(normalized) > MAX_QUERY_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"検索キーワードは{MAX_QUERY_LENGTH}文字以内で指定してください",
        )
    return normalized


def _meeting_title(data: Optional[dict]) -> Optional[str]:
    """data.name → data.title → calendar_event.title (list endpoint's order)."""
    summary = _meeting_list_data_summary(data)
    return summary.get("name") or summary.get("calendar_title")


@router.get(
    "/transcripts/search",
    summary="Search transcript text across the authenticated user's meetings",
)
async def search_transcripts(
    q: str = Query(..., description="検索キーワード(リテラル一致)"),
    limit: int = Query(DEFAULT_MEETING_LIMIT, ge=1, le=MAX_MEETING_LIMIT),
    offset: int = Query(0, ge=0),
    auth_data: tuple = Depends(get_user_and_token),
    db: AsyncSession = Depends(get_db),
):
    _, current_user = auth_data
    query = normalize_query(q)
    pattern = f"%{escape_like(query)}%"

    def text_matches():
        return Transcription.text.ilike(pattern, escape=LIKE_ESCAPE_CHAR)

    # 1. Meetings that contain at least one matching segment, newest first.
    #    `Meeting.user_id` is applied here and again on every follow-up query
    #    so no path can leak another user's transcripts.
    owned = Meeting.user_id == current_user.id
    meeting_stmt = (
        select(Meeting)
        .where(
            owned,
            select(Transcription.id)
            .where(Transcription.meeting_id == Meeting.id, text_matches())
            .exists(),
        )
        .order_by(desc(Meeting.created_at), desc(Meeting.id))
        .offset(offset)
        .limit(limit + 1)
    )
    meetings = (await db.execute(meeting_stmt)).scalars().all()
    has_more = len(meetings) > limit
    meetings = meetings[:limit]

    if not meetings:
        return {"query": query, "results": [], "has_more": False}

    meeting_ids = [m.id for m in meetings]

    # 2. Total match count per meeting.
    count_stmt = (
        select(Transcription.meeting_id, func.count(Transcription.id))
        .join(Meeting, Meeting.id == Transcription.meeting_id)
        .where(owned, Transcription.meeting_id.in_(meeting_ids), text_matches())
        .group_by(Transcription.meeting_id)
    )
    match_counts = {row[0]: row[1] for row in (await db.execute(count_stmt)).all()}

    # 3. Up to MAX_MATCHES_PER_MEETING snippets per meeting, earliest first.
    ranked = (
        select(
            Transcription.meeting_id.label("meeting_id"),
            Transcription.start_time.label("start_time"),
            Transcription.end_time.label("end_time"),
            Transcription.speaker.label("speaker"),
            Transcription.text.label("text"),
            func.row_number()
            .over(
                partition_by=Transcription.meeting_id,
                order_by=(Transcription.start_time, Transcription.id),
            )
            .label("rank"),
        )
        .join(Meeting, Meeting.id == Transcription.meeting_id)
        .where(owned, Transcription.meeting_id.in_(meeting_ids), text_matches())
        .subquery()
    )
    match_stmt = (
        select(ranked)
        .where(ranked.c.rank <= MAX_MATCHES_PER_MEETING)
        .order_by(ranked.c.meeting_id, ranked.c.rank)
    )
    matches_by_meeting: dict[int, list] = {}
    for row in (await db.execute(match_stmt)).all():
        matches_by_meeting.setdefault(row.meeting_id, []).append(
            {
                "start_time": row.start_time,
                "end_time": row.end_time,
                "speaker": row.speaker,
                "text": row.text,
            }
        )

    return {
        "query": query,
        "results": [
            {
                "meeting": {
                    "id": m.id,
                    "platform": m.platform,
                    "native_meeting_id": m.platform_specific_id,
                    "status": m.status,
                    "title": _meeting_title(m.data),
                    "start_time": m.start_time.isoformat() if m.start_time else None,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                },
                "match_count": match_counts.get(m.id, 0),
                "matches": matches_by_meeting.get(m.id, []),
            }
            for m in meetings
        ],
        "has_more": has_more,
    }
