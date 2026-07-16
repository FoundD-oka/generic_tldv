"""Tests for meetings CRUD — POST /bots, GET /bots/status, DELETE, PUT config.

Validates frozen API contracts (response shapes, field names) and
verifies Runtime API delegation via httpx mocks.
"""

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from meeting_api.schemas import MeetingStatus, MeetingResponse, BotStatusResponse, Platform, MeetingCreate
from meeting_api.meetings import _meeting_list_data_summary, list_user_bots

from .conftest import (
    TEST_USER_ID,
    TEST_MEETING_ID,
    TEST_PLATFORM,
    TEST_NATIVE_MEETING_ID,
    TEST_CONTAINER_ID,
    TEST_CONTAINER_NAME,
    TEST_API_KEY,
    make_meeting,
    make_session,
    make_user,
    MockResult,
)


def _setup_create_meeting_db(mock_db):
    """Set up mock_db for the POST /bots standard flow.

    The endpoint makes several queries:
    1. Duplicate check (select existing meeting) → empty
    2. Count active meetings → 0
    After that: add, commit, refresh for the new meeting.
    """
    call_count = 0

    async def multi_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Duplicate check → no existing meeting
            return MockResult([])
        elif call_count == 2:
            # Count active meetings → 0
            return MockResult(scalar_value=0)
        return MockResult()

    mock_db.execute = AsyncMock(side_effect=multi_execute)


# ===================================================================
# POST /bots — create meeting
# ===================================================================


def test_meeting_create_defaults_voice_agent_enabled_true():
    req = MeetingCreate(platform="google_meet", native_meeting_id="abc-defg-hij")

    assert req.voice_agent_enabled is True


def test_meeting_list_summary_keeps_manual_and_calendar_titles_separate():
    summary = _meeting_list_data_summary({
        "name": "手動で変更した名前",
        "calendar_event": {"title": "週次定例"},
        "final_transcription": {"status": "running", "run_id": "secret-run-id"},
    })

    assert summary["name"] == "手動で変更した名前"
    assert summary["calendar_title"] == "週次定例"
    assert summary["final_transcription"] == {"status": "running"}


@pytest.mark.asyncio
async def test_meeting_list_search_includes_calendar_title():
    db = AsyncMock()
    db.execute.return_value = MockResult([])

    await list_user_bots(
        auth_data=(None, SimpleNamespace(id=TEST_USER_ID)),
        db=db,
        search="週次定例",
    )

    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    ))
    assert "calendar_event" in sql
    assert "title" in sql


class TestCreateMeeting:

    @pytest.mark.asyncio
    async def test_create_meeting_success(self, client, mock_db, mock_redis):
        """POST /bots with valid request → 201 with MeetingResponse shape."""
        _setup_create_meeting_db(mock_db)

        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}
        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp):
            with patch("meeting_api.meetings.mint_meeting_token", return_value="fake.jwt.token"):
                with patch("meeting_api.meetings.async_session_local") as mock_session_factory:
                    # Mock the session used for MeetingSession creation
                    inner_db = AsyncMock()
                    inner_db.add = MagicMock()
                    inner_db.commit = AsyncMock()
                    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=inner_db)
                    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

                    resp = await client.post("/bots", json={
                        "platform": "google_meet",
                        "native_meeting_id": "abc-defg-hij",
                    })

        assert resp.status_code == 201
        data = resp.json()
        # Frozen field set
        expected_fields = {
            "id", "user_id", "platform", "native_meeting_id",
            "constructed_meeting_url", "status", "bot_container_id",
            "start_time", "end_time", "data", "created_at", "updated_at",
        }
        assert expected_fields.issubset(set(data.keys()))

    @pytest.mark.asyncio
    async def test_create_meeting_calls_runtime_api(self, client, mock_db, mock_redis):
        """POST /bots delegates container creation to Runtime API."""
        _setup_create_meeting_db(mock_db)

        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}
        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp) as mock_spawn:
            with patch("meeting_api.meetings.mint_meeting_token", return_value="fake.jwt.token"):
                with patch("meeting_api.meetings.async_session_local") as mock_sf:
                    inner = AsyncMock()
                    inner.add = MagicMock()
                    inner.commit = AsyncMock()
                    mock_sf.return_value.__aenter__ = AsyncMock(return_value=inner)
                    mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

                    resp = await client.post("/bots", json={
                        "platform": "google_meet",
                        "native_meeting_id": "abc-defg-hij",
                    })

        assert resp.status_code == 201
        mock_spawn.assert_called_once()
        call_args = mock_spawn.call_args
        assert call_args[1].get("profile") == "meeting" or call_args[0][0] == "meeting"

    @pytest.mark.asyncio
    async def test_video_recording_enables_participant_video_receive(self, client, mock_db, mock_redis):
        """video=true should render participant video in the bot browser."""
        _setup_create_meeting_db(mock_db)

        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}
        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp) as mock_spawn:
            with patch("meeting_api.meetings.mint_meeting_token", return_value="fake.jwt.token"):
                with patch("meeting_api.meetings.async_session_local") as mock_sf:
                    inner = AsyncMock()
                    inner.add = MagicMock()
                    inner.commit = AsyncMock()
                    mock_sf.return_value.__aenter__ = AsyncMock(return_value=inner)
                    mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

                    resp = await client.post("/bots", json={
                        "platform": "google_meet",
                        "native_meeting_id": "abc-defg-hij",
                        "video": True,
                    })

        assert resp.status_code == 201
        mock_spawn.assert_awaited_once()
        kwargs = mock_spawn.await_args.kwargs
        bot_config = json.loads(kwargs["config"]["env"]["BOT_CONFIG"])
        assert bot_config["captureModes"] == ["audio", "video"]
        assert bot_config["videoReceiveEnabled"] is True

    @pytest.mark.asyncio
    async def test_create_meeting_defaults_voice_agent_enabled_in_runtime_config(self, client, mock_db, mock_redis):
        """voice_agent_enabled defaults to true and reaches the bot config."""
        _setup_create_meeting_db(mock_db)

        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}
        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp) as mock_spawn:
            with patch("meeting_api.meetings.mint_meeting_token", return_value="fake.jwt.token"):
                with patch("meeting_api.meetings.async_session_local") as mock_sf:
                    inner = AsyncMock()
                    inner.add = MagicMock()
                    inner.commit = AsyncMock()
                    mock_sf.return_value.__aenter__ = AsyncMock(return_value=inner)
                    mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

                    resp = await client.post("/bots", json={
                        "platform": "google_meet",
                        "native_meeting_id": "abc-defg-hij",
                    })

        assert resp.status_code == 201
        kwargs = mock_spawn.await_args.kwargs
        bot_config = json.loads(kwargs["config"]["env"]["BOT_CONFIG"])
        assert bot_config["voiceAgentEnabled"] is True

    @pytest.mark.asyncio
    async def test_create_meeting_respects_explicit_voice_agent_disabled(self, client, mock_db, mock_redis):
        """Explicit voice_agent_enabled=false is preserved for recording-only bots."""
        _setup_create_meeting_db(mock_db)

        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}
        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp) as mock_spawn:
            with patch("meeting_api.meetings.mint_meeting_token", return_value="fake.jwt.token"):
                with patch("meeting_api.meetings.async_session_local") as mock_sf:
                    inner = AsyncMock()
                    inner.add = MagicMock()
                    inner.commit = AsyncMock()
                    mock_sf.return_value.__aenter__ = AsyncMock(return_value=inner)
                    mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

                    resp = await client.post("/bots", json={
                        "platform": "google_meet",
                        "native_meeting_id": "abc-defg-hij",
                        "voice_agent_enabled": False,
                    })

        assert resp.status_code == 201
        kwargs = mock_spawn.await_args.kwargs
        bot_config = json.loads(kwargs["config"]["env"]["BOT_CONFIG"])
        assert bot_config["voiceAgentEnabled"] is False

    @pytest.mark.asyncio
    async def test_create_meeting_runtime_failure(self, client, mock_db, mock_redis):
        """POST /bots → 500 when Runtime API fails."""
        _setup_create_meeting_db(mock_db)

        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=None):
            with patch("meeting_api.meetings.mint_meeting_token", return_value="fake.jwt.token"):
                resp = await client.post("/bots", json={
                    "platform": "google_meet",
                    "native_meeting_id": "abc-defg-hij",
                })

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_create_meeting_auth_required(self, unauthed_client):
        """POST /bots without X-API-Key → 403."""
        resp = await unauthed_client.post("/bots", json={
            "platform": "google_meet",
            "native_meeting_id": "abc-defg-hij",
        })
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_meeting_invalid_platform(self, client):
        """POST /bots with invalid platform → 422."""
        resp = await client.post("/bots", json={
            "platform": "invalid_platform",
            "native_meeting_id": "abc-defg-hij",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_meeting_agent_mode(self, client, mock_db, mock_redis):
        """POST /bots with agent_enabled=true, no platform → 201."""
        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}

        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp):
            resp = await client.post("/bots", json={
                "agent_enabled": True,
            })

        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_create_browser_session_sets_exit_callback_contract(self, client, mock_db, mock_redis, monkeypatch):
        """browser_session DELETE can complete because runtime gets a routable connection_id."""
        monkeypatch.setenv("INTERNAL_API_SECRET", "test-internal-secret")
        _setup_create_meeting_db(mock_db)

        runtime_resp = {"container_id": TEST_CONTAINER_ID, "name": TEST_CONTAINER_NAME}
        with patch("meeting_api.meetings._spawn_via_runtime_api", new_callable=AsyncMock, return_value=runtime_resp) as mock_spawn:
            resp = await client.post("/bots", json={"mode": "browser_session"})

        assert resp.status_code == 201
        mock_spawn.assert_awaited_once()
        kwargs = mock_spawn.await_args.kwargs
        assert kwargs["profile"] == "browser-session"
        assert kwargs["metadata"] == {
            "meeting_id": TEST_MEETING_ID,
            "connection_id": f"bs:{TEST_MEETING_ID}",
        }
        assert kwargs["callback_headers"] == {"X-Internal-Secret": "test-internal-secret"}
        bot_config = json.loads(kwargs["config"]["env"]["BOT_CONFIG"])
        assert bot_config["internalSecret"] == "test-internal-secret"


class TestDeferredTranscription:

    @pytest.mark.asyncio
    async def test_transcribe_replace_returns_202_run_id(self, client, mock_db):
        meeting = make_meeting(
            id=TEST_MEETING_ID,
            status=MeetingStatus.COMPLETED.value,
            data={
                "transcribe_enabled": True,
                "recording_enabled": True,
                "final_transcription": {
                    "status": "failed",
                    "last_error": "old provider failure",
                    "error_code": "schema_invalid",
                    "attempts": 3,
                    "provider_started_at": "2026-07-13T00:00:00",
                    "heartbeat_at": "2026-07-13T00:01:00",
                    "started_at": "2026-07-13T00:00:00",
                    "failed_at": "2026-07-13T00:02:00",
                    "completed_at": "2026-07-12T00:00:00",
                },
            },
        )
        mock_db.execute = AsyncMock(side_effect=[
            MockResult([meeting]),
            MockResult(scalar_value=0),
        ])
        with patch("meeting_api.final_transcription.attributes.flag_modified", MagicMock()), \
             patch("meeting_api.meetings._execute_manual_transcription", AsyncMock()):
            resp = await client.post(
                f"/meetings/{TEST_MEETING_ID}/transcribe",
                json={"mode": "replace", "language": "ja"},
            )
            await asyncio.sleep(0)

        assert resp.status_code == 202
        payload = resp.json()
        assert payload["status"] == "queued"
        assert len(payload["run_id"]) == 32
        assert meeting.data["final_transcription"]["run_id"] == payload["run_id"]
        assert meeting.data["final_transcription"]["last_error"] is None
        assert meeting.data["final_transcription"]["error_code"] is None
        assert meeting.data["final_transcription"]["attempts"] == 0
        assert meeting.data["final_transcription"]["provider_started_at"] is None
        assert meeting.data["final_transcription"]["heartbeat_at"] is None
        assert meeting.data["final_transcription"]["started_at"] is None
        assert meeting.data["final_transcription"]["failed_at"] is None
        assert meeting.data["final_transcription"]["completed_at"] is None

    @pytest.mark.asyncio
    async def test_transcription_status_maps_succeeded_to_completed(self, client, mock_db):
        meeting = make_meeting(
            id=TEST_MEETING_ID,
            status=MeetingStatus.COMPLETED.value,
            data={"final_transcription": {"status": "succeeded", "run_id": "r1", "segment_count": 9}},
        )
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
        resp = await client.get(f"/meetings/{TEST_MEETING_ID}/transcription-status")
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "completed", "run_id": "r1", "segment_count": 9,
            "error_code": None, "message": None,
        }

    @pytest.mark.asyncio
    async def test_transcribe_meeting_default_mode_rejects_existing_transcript(self, client, mock_db):
        meeting = make_meeting(
            id=TEST_MEETING_ID,
            status=MeetingStatus.COMPLETED.value,
            data={"transcribe_enabled": True, "recording_enabled": True},
        )
        mock_db.execute = AsyncMock(side_effect=[
            MockResult([meeting]),
            MockResult(scalar_value=1),
        ])

        resp = await client.post(f"/meetings/{TEST_MEETING_ID}/transcribe", json={})

        assert resp.status_code == 409
        assert "already transcribed" in resp.json()["detail"]


class TestDeleteMeetingArtifacts:

    @pytest.mark.asyncio
    async def test_delete_meeting_uses_specific_meeting_id_when_provided(self, mock_db):
        """DELETE /meetings can target the exact meeting row, not just latest native ID."""
        from meeting_api.collector.endpoints import delete_meeting

        meeting = make_meeting(id=77, status=MeetingStatus.FAILED.value)
        mock_db.execute = AsyncMock(side_effect=[
            MockResult([meeting]),
            MockResult([]),
        ])
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis_client=None)))

        with patch(
            "meeting_api.collector.endpoints._purge_recordings_for_meeting",
            new_callable=AsyncMock,
            return_value={
                "model_recordings_deleted": 0,
                "storage_files_deleted": 0,
                "storage_files_targeted": 0,
            },
        ):
            response = await delete_meeting(
                platform=Platform.GOOGLE_MEET,
                native_meeting_id=TEST_NATIVE_MEETING_ID,
                request=request,
                meeting_id=77,
                current_user=make_user(),
                db=mock_db,
            )

        assert "transcripts and recording artifacts deleted" in response["message"]
        first_stmt = mock_db.execute.await_args_list[0].args[0]
        assert "meetings.id = :id_1" in str(first_stmt)
        assert meeting.platform_specific_id is None
        assert meeting.data["redacted"] is True

    @pytest.mark.asyncio
    async def test_delete_meeting_rejects_ambiguous_native_id_without_meeting_id(self, mock_db):
        """DELETE /meetings requires meeting_id when the native ID maps to multiple rows."""
        from fastapi import HTTPException
        from meeting_api.collector.endpoints import delete_meeting

        meetings = [
            make_meeting(id=77, status=MeetingStatus.COMPLETED.value),
            make_meeting(id=88, status=MeetingStatus.COMPLETED.value),
        ]
        mock_db.execute = AsyncMock(return_value=MockResult(meetings))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis_client=None)))

        with pytest.raises(HTTPException) as exc_info:
            await delete_meeting(
                platform=Platform.GOOGLE_MEET,
                native_meeting_id=TEST_NATIVE_MEETING_ID,
                request=request,
                meeting_id=None,
                current_user=make_user(),
                db=mock_db,
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["meeting_ids"] == [77, 88]

    @pytest.mark.asyncio
    async def test_delete_meeting_removes_segments_and_chat_messages_from_redis(self, mock_db):
        """DELETE /meetings cleans both transcript and chat Redis keys."""
        from meeting_api.collector.endpoints import delete_meeting

        class FakePipeline:
            def __init__(self):
                self.commands = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def delete(self, key):
                self.commands.append(("delete", key))
                return self

            def srem(self, key, value):
                self.commands.append(("srem", key, value))
                return self

            async def execute(self):
                return [1, 1, 1]

        meeting = make_meeting(id=77, status=MeetingStatus.COMPLETED.value)
        mock_db.execute = AsyncMock(side_effect=[
            MockResult([meeting]),
            MockResult([]),
        ])
        pipeline = FakePipeline()
        redis_client = SimpleNamespace(pipeline=lambda transaction=True: pipeline)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis_client=redis_client)))

        with patch(
            "meeting_api.collector.endpoints._purge_recordings_for_meeting",
            new_callable=AsyncMock,
            return_value={
                "model_recordings_deleted": 0,
                "storage_files_deleted": 0,
                "storage_files_targeted": 0,
            },
        ):
            response = await delete_meeting(
                platform=Platform.GOOGLE_MEET,
                native_meeting_id=TEST_NATIVE_MEETING_ID,
                request=request,
                meeting_id=None,
                current_user=make_user(),
                db=mock_db,
            )

        assert "transcripts and recording artifacts deleted" in response["message"]
        assert ("delete", "meeting:77:segments") in pipeline.commands
        assert ("delete", "meeting:77:chat_messages") in pipeline.commands
        assert ("srem", "active_meetings", "77") in pipeline.commands


class TestAssistantContext:

    @pytest.mark.asyncio
    async def test_assistant_context_includes_redacted_transcript_chat_and_urls(self, mock_db):
        """Assistant context is shared, redacted, and available for completed meetings."""
        from meeting_api.collector.endpoints import get_meeting_assistant_context

        meeting = make_meeting(
            id=77,
            status=MeetingStatus.COMPLETED.value,
            data={
                "title": "api_key=secret",
                "participants": ["password=hunter2"],
                "chat_messages": [
                    {
                        "sender": "Alice",
                        "text": "カボス、このURL見て https://example.com/docs token:xyz",
                        "timestamp": 1760000000000,
                        "is_from_bot": False,
                    }
                ],
            },
        )
        segment = SimpleNamespace(
            speaker="Bob",
            text="Authorization: Bearer abc.def-123_token https://user:pass@example.com/db",
            start_time=0.0,
            end_time=1.0,
            absolute_start_time=None,
            language=None,
            completed=True,
            segment_id="seg-1",
        )
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis_client=None)))

        with patch(
            "meeting_api.collector.endpoints._get_full_transcript_segments",
            new_callable=AsyncMock,
            return_value=[segment],
        ):
            response = await get_meeting_assistant_context(
                platform=Platform.GOOGLE_MEET,
                native_meeting_id=TEST_NATIVE_MEETING_ID,
                request=request,
                meeting_id=None,
                limit=50,
                current_user=make_user(),
                db=mock_db,
            )

        assert response["meeting"]["title"] == "api_key=[REDACTED]"
        assert response["meeting"]["participants"] == ["password=[REDACTED]"]
        assert response["latest_segments"][0]["text"] == (
            "Authorization: Bearer [REDACTED] https://[REDACTED]@example.com/db"
        )
        assert response["latest_segments"][0]["language"] == "ja"
        assert response["chat_messages"][0]["text"] == "カボス、このURL見て https://example.com/docs token:[REDACTED]"
        assert response["shared_urls"] == ["https://[REDACTED]@example.com/db", "https://example.com/docs"]


class TestUpdateMeetingData:

    @pytest.mark.asyncio
    async def test_participant_patch_marks_names_as_manual(self, client, mock_db, mock_redis):
        """A user edit must survive later automatic roster refreshes."""
        from meeting_api.participant_roster import merge_participant_roster_data

        meeting = make_meeting(data={
            "participants": ["Observed Alice"],
            "participants_source": "participant_roster",
        })
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        with patch("sqlalchemy.orm.attributes.flag_modified"):
            response = await client.patch(
                f"/meetings/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}",
                json={"data": {"participants": ["手動参加者"]}},
            )

        assert response.status_code == 200
        assert meeting.data["participants"] == ["手動参加者"]
        assert meeting.data["participants_source"] == "manual"

        refreshed = merge_participant_roster_data(meeting.data, [{
            "participant_id": "participant:1111111111111111",
            "participant_name": "Observed Alice",
            "first_seen_at_ms": 1000,
            "last_seen_at_ms": 2000,
            "source": "people_panel",
        }], id_salt="meeting:1")
        assert refreshed["participants"] == ["手動参加者"]


# ===================================================================
# GET /bots/status — list running bots
# ===================================================================


class TestGetBotsStatus:

    @pytest.mark.asyncio
    async def test_bots_status_returns_running_bots(self, client, mock_db, mock_redis):
        """GET /bots/status → {running_bots: [...]} with frozen fields."""
        with patch("meeting_api.meetings._get_running_bots_from_runtime", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [{
                "container_id": TEST_CONTAINER_ID,
                "container_name": f"meeting-bot-{TEST_MEETING_ID}-abc",
                "platform": TEST_PLATFORM,
                "native_meeting_id": TEST_NATIVE_MEETING_ID,
                "status": "running",
                "normalized_status": "Up",
                "created_at": "2023-11-14T22:13:20+00:00",
                "start_time": None,
                "labels": {},
                "meeting_id_from_name": str(TEST_MEETING_ID),
                "data": {},
            }]

            resp = await client.get("/bots/status")

        assert resp.status_code == 200
        data = resp.json()
        assert "running_bots" in data
        assert isinstance(data["running_bots"], list)
        if data["running_bots"]:
            bot = data["running_bots"][0]
            frozen_fields = {
                "container_id", "container_name", "platform",
                "native_meeting_id", "status", "normalized_status",
                "created_at", "start_time", "labels",
                "meeting_id_from_name", "data",
            }
            assert frozen_fields.issubset(set(bot.keys()))

    @pytest.mark.asyncio
    async def test_bots_status_empty(self, client, mock_db, mock_redis):
        """GET /bots/status when no bots running → empty list."""
        with patch("meeting_api.meetings._get_running_bots_from_runtime", new_callable=AsyncMock, return_value=[]):
            resp = await client.get("/bots/status")

        assert resp.status_code == 200
        assert resp.json()["running_bots"] == []

    @pytest.mark.asyncio
    async def test_bots_status_auth_required(self, unauthed_client):
        """GET /bots/status without auth → 403."""
        resp = await unauthed_client.get("/bots/status")
        assert resp.status_code == 403


# ===================================================================
# DELETE /bots/{platform}/{native_meeting_id} — stop bot
# ===================================================================


class TestStopBot:

    @pytest.mark.asyncio
    async def test_stop_bot_success(self, client, mock_db, mock_redis):
        """DELETE /bots/{platform}/{id} → 202 for active meeting."""
        meeting = make_meeting(
            status=MeetingStatus.ACTIVE.value,
            bot_container_id=TEST_CONTAINER_NAME,
        )
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        with patch("meeting_api.meetings.update_meeting_status", new_callable=AsyncMock, return_value=True):
            with patch("meeting_api.meetings._delayed_container_stop", new_callable=AsyncMock):
                with patch("meeting_api.meetings.attributes.flag_modified", MagicMock()):
                    resp = await client.delete(f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}")

        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_stop_bot_not_found(self, client, mock_db, mock_redis):
        """DELETE /bots/{platform}/{id} for non-existent meeting → 404."""
        mock_db.execute = AsyncMock(return_value=MockResult([]))

        resp = await client.delete(f"/bots/{TEST_PLATFORM}/nonexistent-id")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_stop_bot_already_completed(self, client, mock_db, mock_redis):
        """DELETE /bots/{platform}/{id} for completed meeting → message about already stopped."""
        meeting = make_meeting(status=MeetingStatus.COMPLETED.value)
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        resp = await client.delete(f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}")
        assert resp.status_code == 202
        assert "already" in resp.json().get("message", "").lower()

    @pytest.mark.asyncio
    async def test_stop_bot_sends_leave_via_redis(self, client, mock_db, mock_redis):
        """DELETE /bots publishes leave command to Redis channel."""
        meeting = make_meeting(
            status=MeetingStatus.ACTIVE.value,
            bot_container_id=TEST_CONTAINER_NAME,
        )
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        with patch("meeting_api.meetings.update_meeting_status", new_callable=AsyncMock, return_value=True):
            with patch("meeting_api.meetings._delayed_container_stop", new_callable=AsyncMock):
                with patch("meeting_api.meetings.attributes.flag_modified", MagicMock()):
                    resp = await client.delete(f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}")

        # Verify Redis publish with leave action
        publish_calls = mock_redis.publish.call_args_list
        leave_published = any(
            "leave" in str(call)
            for call in publish_calls
        )
        assert leave_published

    @pytest.mark.asyncio
    async def test_stop_bot_auth_required(self, unauthed_client):
        """DELETE /bots/{platform}/{id} without auth → 403."""
        resp = await unauthed_client.delete(f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}")
        assert resp.status_code == 403


# ===================================================================
# PUT /bots/{platform}/{meeting_id}/config — reconfigure
# ===================================================================


class TestUpdateBotConfig:

    @pytest.mark.asyncio
    async def test_reconfigure_success(self, client, mock_db, mock_redis):
        """PUT /bots/{platform}/{id}/config → 202, publishes to Redis."""
        meeting = make_meeting(status=MeetingStatus.ACTIVE.value)
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        resp = await client.put(
            f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/config",
            json={"language": "es", "task": "translate"},
        )

        assert resp.status_code == 202
        # Verify Redis publish with reconfigure action
        mock_redis.publish.assert_called()
        channel, payload = mock_redis.publish.call_args[0]
        assert f"bot_commands:meeting:{TEST_MEETING_ID}" == channel
        parsed = json.loads(payload)
        assert parsed["action"] == "reconfigure"
        assert parsed["language"] == "es"

    @pytest.mark.asyncio
    async def test_reconfigure_no_active_meeting(self, client, mock_db, mock_redis):
        """PUT config for non-active meeting → 404 or 409."""
        mock_db.execute = AsyncMock(return_value=MockResult([]))

        resp = await client.put(
            f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/config",
            json={"language": "es"},
        )
        assert resp.status_code in (404, 409)

    @pytest.mark.asyncio
    async def test_reconfigure_auth_required(self, unauthed_client):
        """PUT config without auth → 403."""
        resp = await unauthed_client.put(
            f"/bots/{TEST_PLATFORM}/{TEST_NATIVE_MEETING_ID}/config",
            json={"language": "es"},
        )
        assert resp.status_code == 403
