"""ST-7: sweep の行ロックが skip_locked であることと、レプリカ単位の
sweep 無効化 env ガード(MEETING_API_SWEEPS_ENABLED)の検証。

skip_locked の検証は「実際に発行された Select 文を捕捉し、postgresql
dialect でコンパイルした SQL に FOR UPDATE SKIP LOCKED が出ること」で行う
(ソースの文字列 grep ではない)。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Select

from meeting_api import sweeps
from meeting_api.schemas import MeetingStatus

from .conftest import TEST_MEETING_ID, MockResult, make_meeting


class FetchAllResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _for_update_statements(db) -> list[Select]:
    """db.execute に渡された Select のうち FOR UPDATE 指定があるものを抽出。"""
    found = []
    for call in db.execute.call_args_list:
        stmt = call.args[0] if call.args else None
        if isinstance(stmt, Select) and getattr(stmt, "_for_update_arg", None) is not None:
            found.append(stmt)
    return found


def _assert_all_skip_locked(db) -> None:
    statements = _for_update_statements(db)
    assert len(statements) >= 1, "FOR UPDATE 付きの Select が1件も発行されていない"
    for stmt in statements:
        compiled = str(stmt.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE SKIP LOCKED" in compiled, compiled


# ---------------------------------------------------------------------------
# AT-001: unfinalized recordings sweep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unfinalized_recordings_sweep_uses_skip_locked():
    meeting = make_meeting(
        id=10063,
        user_id=1523,
        status=MeetingStatus.COMPLETED.value,
        data={
            "recording_enabled": True,
            "recordings": [{
                "id": 735125303958,
                "session_uid": "sess-existing",
                "status": "completed",
                "media_files": [{
                    "type": "audio",
                    "format": "webm",
                    "storage_path": "recordings/1523/735125303958/sess-existing/audio/000000.webm",
                }],
            }],
        },
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        FetchAllResult([(10063,)]),
        MockResult(items=[meeting]),
        MockResult(items=[]),
    ])
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    @asynccontextmanager
    async def db_session_factory():
        yield db

    with patch.object(sweeps, "_get_default_storage_client"), \
         patch("meeting_api.recording_finalizer.finalize_recording_master", new=AsyncMock()):
        swept = await sweeps._sweep_unfinalized_recordings(db_session_factory)

    assert swept == 1
    _assert_all_skip_locked(db)


# ---------------------------------------------------------------------------
# AT-002: final transcription sweep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_final_transcription_sweep_uses_skip_locked():
    from meeting_api.final_transcription import DeferredTranscriptionResult

    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "transcribe_enabled": True,
            "recording_enabled": True,
            "final_transcription": {"status": "queued", "attempts": 0},
        },
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        FetchAllResult([(TEST_MEETING_ID,)]),
        MockResult(items=[meeting]),
    ])
    db.commit = AsyncMock()

    @asynccontextmanager
    async def db_session_factory():
        yield db

    with patch(
        "meeting_api.final_transcription.run_deferred_transcription",
        new=AsyncMock(return_value=DeferredTranscriptionResult(
            meeting_id=TEST_MEETING_ID,
            segment_count=1,
            speakers=["Alice"],
            source_recording_path="recordings/5/1001/sess-1/audio/master.wav",
            replaced_realtime_count=2,
        )),
    ):
        swept = await sweeps._sweep_final_transcription_jobs(db_session_factory)

    assert swept == 1
    _assert_all_skip_locked(db)


# ---------------------------------------------------------------------------
# AT-003: drive export sweep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drive_export_sweep_uses_skip_locked():
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "calendar_event": {
                "source": "google_calendar",
                "calendar_event_id": 7,
                "external_event_id": "gcal-1",
                "title": "週次定例",
                "meeting_url": "https://meet.google.com/abc-defg-hij",
                "platform": "google_meet",
            },
            "drive_export": {"status": "queued", "attempts": 0},
        },
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        FetchAllResult([(TEST_MEETING_ID,)]),
        MockResult(items=[meeting]),
    ])
    db.commit = AsyncMock()

    @asynccontextmanager
    async def db_session_factory():
        yield db

    with patch("meeting_api.drive_export.run_drive_export", new=AsyncMock(return_value={"status": "done"})):
        swept = await sweeps._sweep_drive_export_jobs(db_session_factory)

    assert swept == 1
    _assert_all_skip_locked(db)


# ---------------------------------------------------------------------------
# AT-004 / AT-005: MEETING_API_SWEEPS_ENABLED env ガード
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_sweeps_disabled_by_env_returns_immediately(monkeypatch):
    monkeypatch.setenv("MEETING_API_SWEEPS_ENABLED", "false")
    factory = MagicMock()

    await asyncio.wait_for(sweeps.start_sweeps(factory), timeout=1.0)

    factory.assert_not_called()


def test_sweeps_enabled_env_parsing(monkeypatch):
    monkeypatch.delenv("MEETING_API_SWEEPS_ENABLED", raising=False)
    assert sweeps._sweeps_enabled() is True

    for value in ("true", "1"):
        monkeypatch.setenv("MEETING_API_SWEEPS_ENABLED", value)
        assert sweeps._sweeps_enabled() is True, value

    for value in ("false", "0", "no", "off", "FALSE"):
        monkeypatch.setenv("MEETING_API_SWEEPS_ENABLED", value)
        assert sweeps._sweeps_enabled() is False, value
