"""sweep / finalizer が storage I/O をトランザクションの外で行うことを固定する。

Postgres は idle-in-transaction を 60 秒で切る。旧実装は行ロックと tx を
握ったまま storage の list / download / upload を回していたため、長い録画で
セッションごと切断されていた。read → (tx を閉じて) I/O → 再ロックして
冪等マージ、の3相であることをここで固定する。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api import recording_finalizer as fin
from meeting_api import sweeps
from meeting_api.schemas import MeetingStatus

from .conftest import MockResult, make_meeting, make_session


class FetchAllResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _recording_db(results, order):
    db = AsyncMock()

    async def _execute(*args, **kwargs):
        return results.pop(0)

    async def _rollback():
        order.append("rollback")

    async def _commit():
        order.append("commit")

    db.execute = AsyncMock(side_effect=_execute)
    db.rollback = AsyncMock(side_effect=_rollback)
    db.commit = AsyncMock(side_effect=_commit)
    return db


@pytest.mark.asyncio
async def test_sweep_closes_tx_before_storage_io_then_rewrites_under_lock():
    order: list = []
    meeting = make_meeting(
        id=10080,
        user_id=1523,
        status=MeetingStatus.COMPLETED.value,
        data={"recording_enabled": True},
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    session = make_session(meeting_id=10080, session_uid="sess-tx")

    db = _recording_db(
        [FetchAllResult([(10080,)]), MockResult(items=[meeting]), MockResult(items=[session])],
        order,
    )

    async def _refresh(obj, **kwargs):
        order.append(f"refresh:{kwargs.get('with_for_update')}")

    db.refresh = AsyncMock(side_effect=_refresh)

    @asynccontextmanager
    async def db_session_factory():
        yield db

    storage = MagicMock()

    def _list(prefix):
        order.append("list_objects_bounded")
        return ["recordings/1523/735125303990/sess-tx/audio/000000.webm"]

    storage.list_objects_bounded.side_effect = _list

    async def _finalize(meeting_id, session):
        order.append("finalize")

    with patch.object(sweeps, "_get_default_storage_client", return_value=storage), \
         patch("meeting_api.recording_finalizer.finalize_recording_master",
               new=AsyncMock(side_effect=_finalize)), \
         patch.object(sweeps.attributes, "flag_modified", new=MagicMock()):
        swept = await sweeps._sweep_unfinalized_recordings(db_session_factory)

    assert swept == 1
    assert order == ["rollback", "list_objects_bounded", "refresh:True", "commit", "finalize"]
    assert meeting.data["recordings"][0]["session_uid"] == "sess-tx"


@pytest.mark.asyncio
async def test_sweep_write_phase_merges_idempotently_by_session_uid():
    """I/O 中に他の書き手が同じ session を追加していたら二重登録しない。"""
    order: list = []
    meeting = make_meeting(
        id=10081,
        user_id=1523,
        status=MeetingStatus.COMPLETED.value,
        data={"recording_enabled": True},
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    session = make_session(meeting_id=10081, session_uid="sess-dup")

    db = _recording_db(
        [FetchAllResult([(10081,)]), MockResult(items=[meeting]), MockResult(items=[session])],
        order,
    )

    async def _refresh(obj, **kwargs):
        order.append("refresh")
        # 別の書き手が I/O 中にコミット済み
        obj.data = {
            "recording_enabled": True,
            "recordings": [{
                "id": 735125303991,
                "session_uid": "sess-dup",
                "status": "completed",
                "media_files": [{
                    "id": 1,
                    "type": "audio",
                    "format": "webm",
                    "storage_path": "recordings/1523/735125303991/sess-dup/audio/master.webm",
                    "finalized_by": "recording_finalizer.master",
                }],
                "playback_url": {"audio": "/recordings/735125303991/master?type=audio", "video": None},
            }],
        }

    db.refresh = AsyncMock(side_effect=_refresh)

    @asynccontextmanager
    async def db_session_factory():
        yield db

    storage = MagicMock()
    storage.list_objects_bounded.return_value = [
        "recordings/1523/735125303991/sess-dup/audio/000000.webm",
    ]

    with patch.object(sweeps, "_get_default_storage_client", return_value=storage), \
         patch("meeting_api.recording_finalizer.finalize_recording_master", new=AsyncMock()), \
         patch.object(sweeps.attributes, "flag_modified", new=MagicMock()) as flag_modified:
        await sweeps._sweep_unfinalized_recordings(db_session_factory)

    db.commit.assert_not_called()
    flag_modified.assert_not_called()
    assert len(meeting.data["recordings"]) == 1
    assert "commit" not in order


@pytest.mark.asyncio
async def test_finalizer_builds_masters_outside_tx_and_writes_by_media_file_id():
    order: list = []
    base = "recordings/1523/999/sess-fin"
    meeting = make_meeting(
        id=10082,
        user_id=1523,
        data={"recordings": [{
            "id": 999,
            "session_uid": "sess-fin",
            "status": "completed",
            "media_files": [
                {"id": 1, "type": "audio", "format": "wav",
                 "storage_path": f"{base}/audio/000000.wav"},
                {"id": 2, "type": "video", "format": "webm",
                 "storage_path": f"{base}/video/000000.webm"},
            ],
        }]},
    )

    db = AsyncMock()

    async def _execute(*args, **kwargs):
        return MockResult(items=[meeting])

    async def _rollback():
        order.append("rollback")

    async def _commit():
        order.append("commit")

    async def _refresh(obj, **kwargs):
        order.append(f"refresh:{kwargs.get('with_for_update')}")
        # 書き込み相の再読込で media_files の順序が入れ替わっていても、
        # id 一致で正しいエントリを更新できること。
        obj.data = {"recordings": [{
            "id": 999,
            "session_uid": "sess-fin",
            "status": "completed",
            "media_files": [
                {"id": 2, "type": "video", "format": "webm",
                 "storage_path": f"{base}/video/000000.webm"},
                {"id": 1, "type": "audio", "format": "wav",
                 "storage_path": f"{base}/audio/000000.wav"},
            ],
        }]}

    db.execute = AsyncMock(side_effect=_execute)
    db.rollback = AsyncMock(side_effect=_rollback)
    db.commit = AsyncMock(side_effect=_commit)
    db.refresh = AsyncMock(side_effect=_refresh)

    def _fake_finalize_one(storage, media_file_id, storage_path, declared_format,
                           media_type, duration_seconds=None):
        # I/O 実行時点で read tx は閉じている
        assert order and order[0] == "rollback"
        order.append(f"io:{media_file_id}")
        return f"{storage_path.rsplit('/', 1)[0]}/master.{declared_format}"

    with patch.object(fin, "create_storage_client", return_value=MagicMock()), \
         patch.object(fin, "_finalize_one_media_file_sync", new=_fake_finalize_one), \
         patch("sqlalchemy.orm.attributes.flag_modified", new=MagicMock()):
        await fin.finalize_recording_master(10082, db)

    assert order == ["rollback", "io:1", "io:2", "refresh:True", "commit"]
    db.commit.assert_awaited_once()

    media_files = {mf["id"]: mf for mf in meeting.data["recordings"][0]["media_files"]}
    assert media_files[1]["storage_path"] == f"{base}/audio/master.wav"
    assert media_files[1]["is_final"] is True
    assert media_files[1]["finalized_by"] == "recording_finalizer.master"
    assert media_files[2]["storage_path"] == f"{base}/video/master.webm"
    playback = meeting.data["recordings"][0]["playback_url"]
    assert playback["audio"] == "/recordings/999/master?type=audio"
    assert playback["video"] == "/recordings/999/master?type=video"


@pytest.mark.asyncio
async def test_finalizer_keeps_no_fallback_and_idempotent_contract():
    """chunk 0件(master_key None)は書き換えず、既に master のものは再書き込みしない。"""
    order: list = []
    base = "recordings/1523/1000/sess-nofb"
    meeting = make_meeting(
        id=10083,
        user_id=1523,
        data={"recordings": [{
            "id": 1000,
            "session_uid": "sess-nofb",
            "status": "completed",
            "media_files": [
                {"id": 1, "type": "audio", "format": "wav",
                 "storage_path": f"{base}/audio/000000.wav"},
                {"id": 2, "type": "video", "format": "webm",
                 "storage_path": f"{base}/video/master.webm"},
            ],
        }]},
    )

    db = AsyncMock()

    async def _execute(*args, **kwargs):
        return MockResult(items=[meeting])

    async def _rollback():
        order.append("rollback")

    async def _commit():
        order.append("commit")

    async def _refresh(obj, **kwargs):
        order.append("refresh")

    db.execute = AsyncMock(side_effect=_execute)
    db.rollback = AsyncMock(side_effect=_rollback)
    db.commit = AsyncMock(side_effect=_commit)
    db.refresh = AsyncMock(side_effect=_refresh)

    def _fake_finalize_one(storage, media_file_id, storage_path, declared_format,
                           media_type, duration_seconds=None):
        if media_file_id == 1:
            return None  # chunk 0件 → no-fallback
        return f"{base}/video/master.webm"  # 既に master(冪等)

    with patch.object(fin, "create_storage_client", return_value=MagicMock()), \
         patch.object(fin, "_finalize_one_media_file_sync", new=_fake_finalize_one), \
         patch("sqlalchemy.orm.attributes.flag_modified", new=MagicMock()):
        await fin.finalize_recording_master(10083, db)

    media_files = {mf["id"]: mf for mf in meeting.data["recordings"][0]["media_files"]}
    assert media_files[1]["storage_path"] == f"{base}/audio/000000.wav"
    assert "finalized_by" not in media_files[1]
    assert media_files[2]["storage_path"] == f"{base}/video/master.webm"
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_finalizer_raises_after_closing_the_read_transaction():
    order: list = []
    base = "recordings/1523/1001/sess-raise"
    meeting = make_meeting(
        id=10084,
        user_id=1523,
        data={"recordings": [{
            "id": 1001,
            "session_uid": "sess-raise",
            "status": "completed",
            "media_files": [{"id": 1, "type": "audio", "format": "wav",
                             "storage_path": f"{base}/audio/000000.wav"}],
        }]},
    )

    db = AsyncMock()

    async def _execute(*args, **kwargs):
        return MockResult(items=[meeting])

    async def _rollback():
        order.append("rollback")

    db.execute = AsyncMock(side_effect=_execute)
    db.rollback = AsyncMock(side_effect=_rollback)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    def _boom(*args, **kwargs):
        raise RuntimeError("concat failed")

    with patch.object(fin, "create_storage_client", return_value=MagicMock()), \
         patch.object(fin, "_finalize_one_media_file_sync", new=_boom), \
         patch("sqlalchemy.orm.attributes.flag_modified", new=MagicMock()):
        with pytest.raises(RuntimeError):
            await fin.finalize_recording_master(10084, db)

    assert order == ["rollback"]
    db.commit.assert_not_called()
