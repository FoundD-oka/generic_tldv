"""collector GET /meetings — slim + paged list contract.

The list endpoint used to return every meeting with the full `data` JSONB
(no default limit), which made a single page tens of MB. It now mirrors
meetings.py::list_user_bots: default limit=50 (max 100), limit+1 peek for
`has_more`, and a summary `data` unless the caller opts in with
`?include=data`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from meeting_api import meetings as meetings_mod
from meeting_api import meeting_summary
from meeting_api.collector.endpoints import get_meetings
from meeting_api.meeting_summary import meeting_list_data_summary
from meeting_api.schemas import MeetingResponse, MeetingStatus

from ..conftest import TEST_USER_ID, MockResult, make_meeting


def _meeting(idx: int, **overrides):
    base = dict(
        id=1000 + idx,
        user_id=TEST_USER_ID,
        platform="google_meet",
        platform_specific_id=f"abc-defg-{idx:03d}",
        native_meeting_id=f"abc-defg-{idx:03d}",
        status=MeetingStatus.COMPLETED.value,
        created_at=datetime.utcnow() - timedelta(minutes=idx),
        updated_at=datetime.utcnow(),
        data={
            "name": f"会議 {idx}",
            "participants": ["山田", "佐藤", "鈴木", "田中"],
            "notes": "あ" * 500,
            "status_transition": [{"status": "completed", "at": "2026-01-01T00:00:00Z"}],
            "recordings": [{"id": idx, "media_files": [{"id": idx}]}],
        },
    )
    base.update(overrides)
    return make_meeting(**base)


def _db_returning(meetings):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult(items=list(meetings)))
    return db


def _compiled_sql(db):
    statement = db.execute.call_args.args[0]
    return str(statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))


def _where_clause(sql: str) -> str:
    return sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]


@pytest.mark.asyncio
async def test_default_list_is_slim_capped_at_50_with_has_more():
    rows = [_meeting(i) for i in range(51)]
    db = _db_returning(rows)

    response = await get_meetings(
        current_user=SimpleNamespace(id=TEST_USER_ID),
        db=db,
        limit=50,
        offset=0,
        status=None,
        platform=None,
        include=None,
    )

    assert len(response.meetings) == 50
    assert response.has_more is True
    for item, row in zip(response.meetings, rows[:50]):
        assert item.data == meeting_list_data_summary(row.data)
    # the peek is limit + 1, never the whole table
    sql = _compiled_sql(db)
    assert "LIMIT 51" in sql
    assert "ORDER BY meetings.created_at DESC" in sql


@pytest.mark.asyncio
async def test_last_page_reports_has_more_false():
    rows = [_meeting(i) for i in range(3)]
    db = _db_returning(rows)

    response = await get_meetings(
        current_user=SimpleNamespace(id=TEST_USER_ID),
        db=db,
        limit=50,
        offset=0,
        status=None,
        platform=None,
        include=None,
    )

    assert len(response.meetings) == 3
    assert response.has_more is False


@pytest.mark.asyncio
async def test_include_data_returns_full_jsonb():
    rows = [_meeting(0)]
    db = _db_returning(rows)

    response = await get_meetings(
        current_user=SimpleNamespace(id=TEST_USER_ID),
        db=db,
        limit=50,
        offset=0,
        status=None,
        platform=None,
        include="data",
    )

    assert response.meetings[0].data == MeetingResponse.model_validate(rows[0]).data
    assert response.meetings[0].data["notes"] == "あ" * 500


@pytest.mark.asyncio
async def test_limit_offset_status_platform_are_applied():
    rows = [_meeting(i) for i in range(3)]
    db = _db_returning(rows)

    response = await get_meetings(
        current_user=SimpleNamespace(id=TEST_USER_ID),
        db=db,
        limit=2,
        offset=1,
        status="completed",
        platform="teams",
        include=None,
    )

    assert len(response.meetings) == 2
    assert response.has_more is True
    sql = _compiled_sql(db)
    assert "LIMIT 3 OFFSET 1" in sql
    assert "meetings.status = 'completed'" in _where_clause(sql)
    assert "meetings.platform = 'teams'" in _where_clause(sql)


@pytest.mark.asyncio
async def test_default_query_has_no_status_condition():
    """履歴一覧が active-only に変わっていないこと(既定に status 条件なし)。"""
    db = _db_returning([])

    await get_meetings(
        current_user=SimpleNamespace(id=TEST_USER_ID),
        db=db,
        limit=50,
        offset=0,
        status=None,
        platform=None,
        include=None,
    )

    where = _where_clause(_compiled_sql(db))
    assert "status" not in where
    assert where.strip() == f"meetings.user_id = {TEST_USER_ID}"


@pytest.mark.asyncio
async def test_limit_above_100_is_rejected_with_422():
    from fastapi import FastAPI

    from meeting_api.collector.auth import get_current_user
    from meeting_api.collector.endpoints import router
    from meeting_api.database import get_db

    app = FastAPI()
    app.include_router(router)
    db = _db_returning([])

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=TEST_USER_ID)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        too_big = await client.get("/meetings?limit=101")
        ok = await client.get("/meetings")

    assert too_big.status_code == 422
    assert ok.status_code == 200
    assert ok.json() == {"meetings": [], "has_more": False}


def test_summary_implementation_is_shared_by_both_list_endpoints():
    assert meetings_mod._meeting_list_data_summary is meeting_summary.meeting_list_data_summary


@pytest.mark.asyncio
async def test_default_page_payload_is_small_and_include_data_is_large():
    """FP-004: 既定はサマリのみ、`include=data` だけが full JSONB を運ぶ。"""
    heavy_data = {
        "name": "重い会議",
        "participants": [f"参加者{i}" for i in range(50)],
        "notes": "の" * 2000,
        "status_transition": [{"status": "completed", "at": "2026-01-01T00:00:00Z"}] * 200,
        "recordings": [{
            "id": 1,
            "media_files": [{"id": i, "storage_path": "x" * 200} for i in range(30)],
        }],
        "webhook_deliveries": [{"payload": "y" * 500} for _ in range(30)],
    }
    rows = [_meeting(i, data=dict(heavy_data)) for i in range(41)]

    slim = await get_meetings(
        current_user=SimpleNamespace(id=TEST_USER_ID),
        db=_db_returning(rows),
        limit=50, offset=0, status=None, platform=None, include=None,
    )
    full = await get_meetings(
        current_user=SimpleNamespace(id=TEST_USER_ID),
        db=_db_returning(rows),
        limit=50, offset=0, status=None, platform=None, include="data",
    )

    slim_bytes = len(slim.model_dump_json().encode("utf-8"))
    full_bytes = len(full.model_dump_json().encode("utf-8"))
    assert slim_bytes < 100_000, slim_bytes
    assert full_bytes > 1_000_000, full_bytes
