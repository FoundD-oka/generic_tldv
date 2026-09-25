"""Storage stalls must not stop health probes or multiply per sweep."""

import asyncio
import threading
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from meeting_api import recordings, sweeps

from .conftest import MockResult, make_meeting, make_session
from .test_recordings_concurrent_chunks import _make_upload_call, _StatefulMockDB
from .test_sweeps_unfinalized_recordings import FetchAllResult


def sweep_db(meetings_and_sessions):
    results = [FetchAllResult([(meeting.id,) for meeting, _ in meetings_and_sessions])]
    for meeting, sessions in meetings_and_sessions:
        results.extend([MockResult([meeting]), MockResult(sessions)])
    db = AsyncMock()
    db.execute.side_effect = results

    @asynccontextmanager
    async def factory():
        yield db

    return db, factory


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["sweep_init", "sweep_list", "upload_init", "upload_write"])
async def test_health_responds_while_storage_is_blocked(operation):
    from meeting_api import main

    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    entered = asyncio.Event()
    release = threading.Event()
    storage = MagicMock()
    storage.list_objects_bounded.return_value = []

    def stalled_io(*args, **kwargs):
        # Fail immediately on the original synchronous implementation, without
        # freezing pytest's own event loop or relying on latency thresholds.
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5), "test did not release storage I/O"
        return storage if operation.endswith("init") else []

    meeting, session = make_meeting(data={}), make_session()
    if operation.startswith("sweep"):
        db, factory = sweep_db([(meeting, [session])])
        work = sweeps._sweep_unfinalized_recordings(factory)
        target = sweeps
        getter = "_get_default_storage_client"
        if operation == "sweep_list":
            storage.list_objects_bounded.side_effect = stalled_io
    else:
        db = _StatefulMockDB(session, meeting)
        work = recordings.internal_upload_recording(db=db, **_make_upload_call("audio"))
        target = recordings
        getter = "get_storage_client"
        if operation == "upload_write":
            storage.upload_file.side_effect = stalled_io

    with patch.object(target, getter, side_effect=stalled_io if operation.endswith("init") else None,
                      return_value=storage), \
         patch.object(recordings.attributes, "flag_modified"), \
         patch.object(main, "_startup_complete", True):
        task = asyncio.create_task(work)
        try:
            await asyncio.wait_for(entered.wait(), 2)
            async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as client:
                for path in ("/health", "/readyz"):
                    response = await asyncio.wait_for(client.get(path), 2)
                    assert response.status_code == 200
            assert not task.done(), "probes must respond before storage finishes"
            db.commit.assert_not_awaited()
        finally:
            release.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_sweep_lists_once_per_user_and_matches_each_session():
    first = make_meeting(id=1, user_id=10, data={})
    second = make_meeting(id=2, user_id=10, data={})
    third = make_meeting(id=3, user_id=20, data={})
    db, factory = sweep_db([
        (first, [make_session(session_uid="a"), make_session(session_uid="b")]),
        (second, [make_session(session_uid="c")]),
        (third, [make_session(session_uid="a")]),
    ])
    storage = MagicMock()
    storage.list_objects_bounded.side_effect = lambda prefix: {
        "recordings/10/": [f"recordings/10/{100 + i}/{uid}/audio/000000.webm"
                           for i, uid in enumerate(("a", "b", "c"))],
        "recordings/20/": ["recordings/20/200/a/audio/000000.webm"],
    }[prefix]
    with patch.object(sweeps, "_get_default_storage_client", return_value=storage) as get_storage, \
         patch.object(sweeps.attributes, "flag_modified"), \
         patch("meeting_api.recording_finalizer.finalize_recording_master", new=AsyncMock()) as finalize:
        assert await sweeps._sweep_unfinalized_recordings(factory) == 3

    assert storage.list_objects_bounded.call_args_list == [call("recordings/10/"), call("recordings/20/")]
    get_storage.assert_called_once_with()
    assert [rec["session_uid"] for rec in first.data["recordings"]] == ["a", "b"]
    assert second.data["recordings"][0]["id"] == 102
    assert third.data["recordings"][0]["id"] == 200
    assert db.commit.await_count == 3
    assert finalize.await_args_list == [call(1, db), call(2, db), call(3, db)]


@pytest.mark.asyncio
@pytest.mark.parametrize("first_result", [[], RuntimeError("storage temporarily unavailable")])
async def test_empty_or_failed_listing_is_cached_only_until_next_sweep(first_result):
    meetings = [(make_meeting(id=i, user_id=10, data={}), [make_session(session_uid=str(i))])
                for i in (1, 2)]
    other = (make_meeting(id=3, user_id=20, data={}), [make_session(session_uid="3")])
    storage = MagicMock()
    storage.list_objects_bounded.side_effect = [
        first_result, ["recordings/20/200/3/audio/000000.webm"],
        [f"recordings/10/{100 + i}/{i}/audio/000000.webm" for i in (1, 2)],
    ]
    with patch.object(sweeps, "_get_default_storage_client", return_value=storage), \
         patch.object(sweeps.attributes, "flag_modified"), \
         patch("meeting_api.recording_finalizer.finalize_recording_master", new=AsyncMock()):
        _, factory = sweep_db(meetings + [other])
        assert await sweeps._sweep_unfinalized_recordings(factory) == 1
        assert all(not meeting.data.get("recordings") for meeting, _ in meetings)
        _, factory = sweep_db(meetings)
        assert await sweeps._sweep_unfinalized_recordings(factory) == 2
    assert storage.list_objects_bounded.call_args_list == [
        call("recordings/10/"), call("recordings/20/"), call("recordings/10/"),
    ]


@pytest.mark.asyncio
async def test_failed_upload_does_not_commit_metadata():
    db = _StatefulMockDB(make_session(), make_meeting(data={}))
    storage = MagicMock()
    storage.upload_file.side_effect = RuntimeError("storage unavailable")
    with patch.object(recordings, "get_storage_client", return_value=storage):
        with pytest.raises(HTTPException) as error:
            await recordings.internal_upload_recording(db=db, **_make_upload_call("audio"))
    assert error.value.status_code == 500
    db.commit.assert_not_awaited()
    assert db.shared_meeting.data == {}


@pytest.mark.asyncio
async def test_concurrent_storage_initialization_shares_one_client():
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    client = object()

    def create():
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return client

    with patch.object(recordings, "_storage_client", None), \
         patch.object(recordings, "create_storage_client", side_effect=create) as create_storage:
        tasks = [asyncio.create_task(asyncio.to_thread(recordings.get_storage_client)) for _ in range(8)]
        try:
            await asyncio.wait_for(entered.wait(), 2)
        finally:
            release.set()
            clients = await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert all(result is client for result in clients)
        create_storage.assert_called_once_with()


def test_failed_storage_initialization_can_retry():
    client = object()
    with patch.object(recordings, "_storage_client", None), \
         patch.object(recordings, "create_storage_client", side_effect=[RuntimeError("unavailable"), client]):
        with pytest.raises(RuntimeError):
            recordings.get_storage_client()
        assert recordings.get_storage_client() is client
