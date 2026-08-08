"""Tests for the async master-finalizer background job (ST-13).

The bot exit callback used to await finalize_recording_master inline, which
put storage/ffmpeg I/O in the HTTP response path: runtime-api times out at
10s and re-sends the callback forever, so the finalizer ran concurrently for
the same meeting. It is now a background job with a Redis idempotency lock.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.background import BackgroundTasks as _StarletteBackgroundTasks

from meeting_api.schemas import MeetingStatus, MeetingCompletionReason

from .conftest import (
    TEST_MEETING_ID,
    TEST_SESSION_UID,
    TEST_USER_ID,
    make_meeting,
    make_session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSessionFactory:
    """Stand-in for async_session_local: `async with factory() as db`."""

    def __init__(self, session):
        self.session = session
        self.entered = 0

    def __call__(self):
        return self

    async def __aenter__(self):
        self.entered += 1
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_job_env(redis_client, finalize_mock, session=None):
    """Patch get_redis / async_session_local / finalize_recording_master."""
    session = session or AsyncMock()
    factory = _FakeSessionFactory(session)
    ctx = (
        patch("meeting_api.meetings.get_redis", MagicMock(return_value=redis_client)),
        patch("meeting_api.database.async_session_local", factory),
        patch(
            "meeting_api.recording_finalizer.finalize_recording_master",
            finalize_mock,
        ),
    )
    return ctx, factory, session


def _make_redis(set_result=True, get_result=None):
    r = AsyncMock()
    r.set = AsyncMock(return_value=set_result)
    r.get = AsyncMock(return_value=get_result)
    r.delete = AsyncMock(return_value=1)
    return r


def _patch_find_meeting(meeting, session=None):
    ms = session or make_session()
    return patch(
        "meeting_api.callbacks._find_meeting_by_session",
        new_callable=AsyncMock,
        return_value=(ms, meeting),
    )


# ---------------------------------------------------------------------------
# The job itself
# ---------------------------------------------------------------------------


class TestFinalizeRecordingMasterJob:

    @pytest.mark.asyncio
    async def test_runs_finalizer_with_its_own_session_when_lock_acquired(self):
        """Lock acquired → finalize runs against a session the job owns (AT-004)."""
        from meeting_api.recording_finalizer import finalize_recording_master_job

        redis_client = _make_redis(set_result=True)
        finalize = AsyncMock()
        patches, factory, session = _patch_job_env(redis_client, finalize)

        with patches[0], patches[1], patches[2]:
            await finalize_recording_master_job(TEST_MEETING_ID)

        assert factory.entered == 1
        finalize.assert_awaited_once_with(TEST_MEETING_ID, session)

        # Lock: SET NX EX on the per-meeting key.
        redis_client.set.assert_awaited_once()
        args, kwargs = redis_client.set.call_args
        assert args[0] == f"finalizer:master:{TEST_MEETING_ID}"
        assert kwargs["nx"] is True
        assert kwargs["ex"] == 900

    @pytest.mark.asyncio
    async def test_releases_lock_only_when_token_matches(self):
        """The job deletes its own lock, not one a later run took over."""
        from meeting_api.recording_finalizer import finalize_recording_master_job

        redis_client = _make_redis(set_result=True)
        finalize = AsyncMock()
        patches, _factory, _session = _patch_job_env(redis_client, finalize)

        with patches[0], patches[1], patches[2]:
            # Echo whatever token the job wrote so the release matches.
            async def _echo_get(key):
                return redis_client.set.call_args[0][1]
            redis_client.get = AsyncMock(side_effect=_echo_get)
            await finalize_recording_master_job(TEST_MEETING_ID)

        redis_client.delete.assert_awaited_once_with(
            f"finalizer:master:{TEST_MEETING_ID}"
        )

        # A foreign token must not be deleted.
        redis_client2 = _make_redis(set_result=True, get_result="someone-else")
        finalize2 = AsyncMock()
        patches2, _f2, _s2 = _patch_job_env(redis_client2, finalize2)
        with patches2[0], patches2[1], patches2[2]:
            await finalize_recording_master_job(TEST_MEETING_ID)
        redis_client2.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lock_not_acquired_skips_finalize(self):
        """AT-003: SET NX returned falsy → another run owns it; do nothing."""
        from meeting_api.recording_finalizer import finalize_recording_master_job

        redis_client = _make_redis(set_result=None)
        finalize = AsyncMock()
        patches, factory, _session = _patch_job_env(redis_client, finalize)

        with patches[0], patches[1], patches[2]:
            await finalize_recording_master_job(TEST_MEETING_ID)

        finalize.assert_not_awaited()
        assert factory.entered == 0
        redis_client.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_finalizer_exception_does_not_propagate(self):
        """AT-004: BackgroundTasks has no error channel; the sweep retries."""
        from meeting_api.recording_finalizer import finalize_recording_master_job

        redis_client = _make_redis(set_result=True)
        finalize = AsyncMock(side_effect=RuntimeError("storage exploded"))
        patches, _factory, _session = _patch_job_env(redis_client, finalize)

        with patches[0], patches[1], patches[2]:
            await finalize_recording_master_job(TEST_MEETING_ID)

        finalize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_redis_runs_fail_open(self):
        """AT-005: no Redis → finalize anyway; playback must not stall."""
        from meeting_api.recording_finalizer import finalize_recording_master_job

        finalize = AsyncMock()
        patches, factory, session = _patch_job_env(None, finalize)

        with patches[0], patches[1], patches[2]:
            await finalize_recording_master_job(TEST_MEETING_ID)

        assert factory.entered == 1
        finalize.assert_awaited_once_with(TEST_MEETING_ID, session)

    @pytest.mark.asyncio
    async def test_redis_error_runs_fail_open(self):
        """A Redis failure on SET must not block master construction."""
        from meeting_api.recording_finalizer import finalize_recording_master_job

        redis_client = _make_redis()
        redis_client.set = AsyncMock(side_effect=ConnectionError("redis down"))
        finalize = AsyncMock()
        patches, factory, session = _patch_job_env(redis_client, finalize)

        with patches[0], patches[1], patches[2]:
            await finalize_recording_master_job(TEST_MEETING_ID)

        assert factory.entered == 1
        finalize.assert_awaited_once_with(TEST_MEETING_ID, session)
        redis_client.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Registration from the exit callback
# ---------------------------------------------------------------------------


def _capture_add_task():
    """Record BackgroundTasks.add_task registrations without running them."""
    registered = []

    def _spy(self, func, *args, **kwargs):
        registered.append(func)

    return patch.object(_StarletteBackgroundTasks, "add_task", _spy), registered


class TestExitCallbackRegistersJob:

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "meeting_status,exit_code",
        [
            (MeetingStatus.ACTIVE.value, 0),           # exit_code == 0 branch
            (MeetingStatus.STOPPING.value, 1),         # stopping branch
            (MeetingStatus.ACTIVE.value, 1),           # else (non-stopping) branch
        ],
    )
    async def test_all_branches_register_job_before_run_all_tasks(
        self, client, mock_db, mock_redis, meeting_status, exit_code
    ):
        """AT-002: every branch queues the job, ahead of run_all_tasks."""
        meeting = make_meeting(status=meeting_status, user_id=TEST_USER_ID)
        spy, registered = _capture_add_task()

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock, return_value=True):
                with patch(
                    "meeting_api.callbacks._classify_stopped_exit",
                    new_callable=AsyncMock,
                    return_value=(MeetingStatus.COMPLETED, MeetingCompletionReason.STOPPED),
                ):
                    with patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock):
                        with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock) as mock_tasks:
                            with patch(
                                "meeting_api.callbacks.finalize_recording_master_job",
                                new_callable=AsyncMock,
                            ) as mock_job:
                                with spy:
                                    resp = await client.post("/bots/internal/callback/exited", json={
                                        "connection_id": TEST_SESSION_UID,
                                        "exit_code": exit_code,
                                        "reason": "self_initiated_leave",
                                    })

        assert resp.status_code == 200
        assert resp.json()["status"] == "callback processed"
        assert mock_job in registered
        assert registered.index(mock_job) < registered.index(mock_tasks)
        # Not awaited inside the handler — registration only.
        mock_job.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_terminal_callback_registers_no_job(
        self, client, mock_db, mock_redis
    ):
        """AT-006: the duplicate-terminal early return queues nothing."""
        from datetime import datetime

        completed_at = datetime.utcnow()
        meeting = make_meeting(
            status=MeetingStatus.COMPLETED.value,
            end_time=completed_at,
            data={
                "recordings": [],
                "exit_callback_processed_at": completed_at.isoformat(),
            },
        )
        spy, registered = _capture_add_task()

        with _patch_find_meeting(meeting):
            with patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock):
                with patch(
                    "meeting_api.callbacks.finalize_recording_master_job",
                    new_callable=AsyncMock,
                ) as mock_job:
                    with spy:
                        resp = await client.post("/bots/internal/callback/exited", json={
                            "connection_id": TEST_SESSION_UID,
                            "exit_code": 0,
                            "reason": "self_initiated_leave",
                        })

        assert resp.status_code == 200
        assert resp.json()["status"] == "ignored"
        assert registered == []
        mock_job.assert_not_awaited()
