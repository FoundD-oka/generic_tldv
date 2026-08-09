"""Real-PostgreSQL tests for cross-meeting transcript search (FT-3).

Covers the behaviours that only a real DB can prove: Japanese ILIKE matching
through the pg_trgm index, meeting grouping/ordering, literal (escaped) match
semantics, and — most importantly — the authorization boundary (FP-301):
user A's search must never surface user B's meetings or transcript text.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, text

from meeting_api.database import async_session_local, engine
from meeting_api.models import Base, Meeting, Transcription
from meeting_api.search import search_transcripts

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
    reason="実PostgreSQLが必要。RUN_POSTGRES_INTEGRATION_TESTS=1 で有効化",
)

USER_A = 90001
USER_B = 90002


async def _search(user_id: int, q: str, **kwargs):
    async with async_session_local() as db:
        return await search_transcripts(
            q=q,
            limit=kwargs.pop("limit", 20),
            offset=kwargs.pop("offset", 0),
            auth_data=(None, SimpleNamespace(id=user_id)),
            db=db,
        )


def _meeting_ids(response) -> list[int]:
    return [r["meeting"]["id"] for r in response["results"]]


@pytest.fixture(autouse=True)
async def _schema_and_clean():
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)

    async def purge():
        async with async_session_local() as db:
            ids = (
                await db.execute(
                    select(Meeting.id).where(Meeting.user_id.in_([USER_A, USER_B, USER_A + 999]))
                )
            ).scalars().all()
            if ids:
                await db.execute(delete(Transcription).where(Transcription.meeting_id.in_(ids)))
                await db.execute(delete(Meeting).where(Meeting.id.in_(ids)))
            await db.commit()

    await purge()
    yield
    await purge()
    # pytest-asyncio gives each test a fresh event loop; pooled asyncpg
    # connections belong to the previous one, so drop them between tests.
    await engine.dispose()


async def _add_meeting(user_id: int, *, title: str, created_at: datetime, lines: list[tuple]) -> int:
    async with async_session_local() as db:
        meeting = Meeting(
            user_id=user_id,
            platform="google_meet",
            platform_specific_id=f"native-{user_id}-{int(created_at.timestamp())}",
            status="completed",
            data={"name": title},
            created_at=created_at,
        )
        db.add(meeting)
        await db.flush()
        for start_time, speaker, body in lines:
            db.add(
                Transcription(
                    meeting_id=meeting.id,
                    start_time=start_time,
                    end_time=start_time + 2.0,
                    speaker=speaker,
                    text=body,
                )
            )
        await db.commit()
        return meeting.id


# --- FP-301: authorization boundary ----------------------------------------

async def test_search_never_returns_another_users_transcripts():
    """FP-301: identical wording is stored for both users; A must see only A's."""
    now = datetime.utcnow()
    a_id = await _add_meeting(
        USER_A, title="Aの会議", created_at=now,
        lines=[(0.0, "佐藤", "買収監査の進捗を共有します")],
    )
    b_id = await _add_meeting(
        USER_B, title="Bの会議", created_at=now + timedelta(minutes=1),
        lines=[
            (0.0, "鈴木", "買収監査の進捗を共有します"),
            (5.0, "鈴木", "買収監査の結論は来週です"),
        ],
    )

    a_response = await _search(USER_A, "買収監査")
    assert _meeting_ids(a_response) == [a_id]
    assert a_response["results"][0]["match_count"] == 1
    texts = [m["text"] for r in a_response["results"] for m in r["matches"]]
    assert texts == ["買収監査の進捗を共有します"]

    b_response = await _search(USER_B, "買収監査")
    assert _meeting_ids(b_response) == [b_id]
    assert b_response["results"][0]["match_count"] == 2

    # A user with no data of their own sees nothing, even though the phrase
    # exists in the table for other users.
    empty = await _search(USER_A + 999, "買収監査")
    assert empty["results"] == [] and empty["has_more"] is False


async def test_other_users_meeting_is_absent_even_when_it_would_sort_first():
    """B's meeting is the newest, so a missing user filter would rank it #1."""
    now = datetime.utcnow()
    await _add_meeting(
        USER_B, title="Bの最新会議", created_at=now + timedelta(hours=1),
        lines=[(0.0, "鈴木", "予算計画の見直しについて")],
    )
    a_id = await _add_meeting(
        USER_A, title="Aの会議", created_at=now,
        lines=[(0.0, "佐藤", "予算計画の見直しについて")],
    )

    response = await _search(USER_A, "予算計画")
    assert _meeting_ids(response) == [a_id]
    assert all(r["meeting"]["title"] != "Bの最新会議" for r in response["results"])


# --- AT-302: matching, grouping, ordering, snippets, has_more ---------------

async def test_groups_by_meeting_orders_by_created_at_desc_and_caps_snippets():
    now = datetime.utcnow()
    older = await _add_meeting(
        USER_A, title="古い会議", created_at=now - timedelta(days=1),
        lines=[(1.0, "佐藤", "議事録の共有です")],
    )
    newer = await _add_meeting(
        USER_A, title="新しい会議", created_at=now,
        lines=[
            (30.0, "田中", "議事録その3"),
            (10.0, "田中", "議事録その1"),
            (20.0, "田中", "議事録その2"),
            (40.0, "田中", "議事録その4"),
            (50.0, "田中", "無関係な発言"),
        ],
    )

    response = await _search(USER_A, "議事録")
    assert _meeting_ids(response) == [newer, older]
    first = response["results"][0]
    assert first["match_count"] == 4
    assert [m["text"] for m in first["matches"]] == ["議事録その1", "議事録その2", "議事録その3"]
    assert first["matches"][0]["start_time"] == 10.0
    assert first["matches"][0]["speaker"] == "田中"
    assert first["meeting"]["title"] == "新しい会議"
    assert response["has_more"] is False


async def test_has_more_and_offset_use_limit_plus_one():
    now = datetime.utcnow()
    ids = []
    for i in range(3):
        ids.append(
            await _add_meeting(
                USER_A, title=f"会議{i}", created_at=now - timedelta(minutes=i),
                lines=[(0.0, "佐藤", "四半期レビューの件")],
            )
        )

    page1 = await _search(USER_A, "四半期", limit=2)
    assert _meeting_ids(page1) == ids[:2]
    assert page1["has_more"] is True

    page2 = await _search(USER_A, "四半期", limit=2, offset=2)
    assert _meeting_ids(page2) == [ids[2]]
    assert page2["has_more"] is False


async def test_search_is_case_insensitive():
    await _add_meeting(
        USER_A, title="英語混じり", created_at=datetime.utcnow(),
        lines=[(0.0, "佐藤", "Quarterly Review の結果")],
    )
    response = await _search(USER_A, "quarterly review")
    assert len(response["results"]) == 1


# --- AT-303: literal match semantics ---------------------------------------

async def test_percent_is_matched_literally_not_as_wildcard():
    now = datetime.utcnow()
    hit = await _add_meeting(
        USER_A, title="達成率", created_at=now,
        lines=[(0.0, "佐藤", "進捗は50%です")],
    )
    await _add_meeting(
        USER_A, title="無関係", created_at=now - timedelta(minutes=1),
        lines=[(0.0, "佐藤", "進捗は順調です")],
    )

    response = await _search(USER_A, "50%")
    assert _meeting_ids(response) == [hit]


async def test_underscore_and_backslash_are_matched_literally():
    now = datetime.utcnow()
    underscore = await _add_meeting(
        USER_A, title="アンダースコア", created_at=now,
        lines=[(0.0, "佐藤", "変数名は user_id です")],
    )
    await _add_meeting(
        USER_A, title="ダミー", created_at=now - timedelta(minutes=1),
        lines=[(0.0, "佐藤", "変数名は userXid です")],
    )
    backslash = await _add_meeting(
        USER_A, title="バックスラッシュ", created_at=now - timedelta(minutes=2),
        lines=[(0.0, "佐藤", "パスは C:\\temp です")],
    )

    assert _meeting_ids(await _search(USER_A, "user_id")) == [underscore]
    assert _meeting_ids(await _search(USER_A, "C:\\")) == [backslash]


# --- NFT-301: two-character queries must still work -------------------------

async def test_two_character_query_returns_results():
    meeting_id = await _add_meeting(
        USER_A, title="短いクエリ", created_at=datetime.utcnow(),
        lines=[(0.0, "佐藤", "会議の予定を確認します")],
    )
    response = await _search(USER_A, "会議")
    assert _meeting_ids(response) == [meeting_id]
