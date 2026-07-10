"""Issue #27 Phase 4 / PII policy §3 — voiceprint retention sweep."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from meeting_api import sweeps

from .conftest import MockResult


class _FakeVoiceprint:
    def __init__(self, *, id, user_id, profile_id, created_at, last_matched_at=None):
        self.id = id
        self.user_id = user_id
        self.profile_id = profile_id
        self.created_at = created_at
        self.last_matched_at = last_matched_at


@pytest.fixture(autouse=True)
def _reset_day_guard():
    sweeps._voiceprint_retention_last_run = None
    yield
    sweeps._voiceprint_retention_last_run = None


@pytest.mark.asyncio
async def test_retention_sweep_deletes_expired_voiceprint_and_audits():
    old_vp = _FakeVoiceprint(
        id=1, user_id=5, profile_id=7,
        created_at=datetime.utcnow() - timedelta(days=30 * 25),  # 25mo > 24mo default
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult([old_vp]))
    db.commit = AsyncMock()
    added = []
    db.add = MagicMock(side_effect=added.append)
    db.delete = AsyncMock()

    @asynccontextmanager
    async def db_session_factory():
        yield db

    swept = await sweeps._sweep_voiceprint_retention(db_session_factory)

    assert swept == 1
    db.delete.assert_awaited_once_with(old_vp)
    db.commit.assert_awaited_once()
    assert len(added) == 1
    audit = added[0]
    assert audit.event == "delete"
    assert audit.subject_profile_id == 7
    assert audit.detail["reason"] == "retention"
    assert audit.detail["voiceprint_id"] == 1


@pytest.mark.asyncio
async def test_retention_sweep_does_not_delete_recently_matched_voiceprint():
    """The SQL predicate itself (COALESCE(last_matched_at, created_at) <
    cutoff) is what a real DB would filter on; this test documents that a
    row the query never RETURNS is never touched — i.e. an empty result
    means zero deletes, not a client-side re-filter bug."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult([]))
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()

    @asynccontextmanager
    async def db_session_factory():
        yield db

    swept = await sweeps._sweep_voiceprint_retention(db_session_factory)

    assert swept == 0
    db.delete.assert_not_awaited()
    db.commit.assert_not_awaited()  # no rows swept — nothing to commit


@pytest.mark.asyncio
async def test_retention_sweep_is_day_guarded():
    """The 60s-cadence sweep loop must not re-scan every iteration — only
    once per VOICEPRINT_RETENTION_SWEEP_INTERVAL_SECONDS."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult([]))

    @asynccontextmanager
    async def db_session_factory():
        yield db

    first = await sweeps._sweep_voiceprint_retention(db_session_factory)
    assert first == 0
    assert db.execute.await_count == 1

    second = await sweeps._sweep_voiceprint_retention(db_session_factory)
    assert second == 0
    # Guarded — no additional query on the immediately-following call.
    assert db.execute.await_count == 1
