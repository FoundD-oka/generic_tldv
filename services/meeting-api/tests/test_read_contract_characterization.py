"""Read-path contracts that the loading refactor must preserve."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from meeting_api.auth import get_user_and_token
from meeting_api.meetings import list_user_bots
from meeting_api.main import app

from .conftest import MockResult, TEST_MEETING_ID, TEST_USER_ID, make_meeting, make_user


@pytest.mark.asyncio
async def test_R00_recording_owner_boundary(client, mock_db):
    recording = {
        "id": 1001,
        "meeting_id": TEST_MEETING_ID,
        "user_id": TEST_USER_ID,
        "session_uid": "session-a",
        "source": "bot",
        "status": "completed",
        "media_files": [
            {
                "id": 2001,
                "type": "audio",
                "format": "webm",
                "storage_backend": "minio",
                "storage_path": "recordings/5/1001/session-a/audio/master.webm",
                "file_size_bytes": 3,
            }
        ],
    }
    owned_meeting = make_meeting(data={"recordings": [recording]})
    storage = MagicMock()
    storage.file_exists.return_value = True
    storage.get_presigned_url.return_value = "https://storage.example.invalid/signed"

    async def auth_user_6():
        return ("test-token", make_user(user_id=6))

    async def auth_user_5():
        return ("test-token", make_user(user_id=TEST_USER_ID))

    app.dependency_overrides[get_user_and_token] = auth_user_6
    mock_db.execute = AsyncMock(return_value=MockResult([]))
    with patch(
        "meeting_api.recordings.get_storage_client_for", return_value=storage
    ) as storage_factory:
        rejected = await client.get("/recordings/1001/media/2001/download")
    assert rejected.status_code == 404
    storage_factory.assert_not_called()
    storage.file_exists.assert_not_called()
    rejected_statement = mock_db.execute.await_args.args[0]
    rejected_sql = str(
        rejected_statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "meetings.user_id = 6" in rejected_sql

    app.dependency_overrides[get_user_and_token] = auth_user_5
    mock_db.execute = AsyncMock(return_value=MockResult([owned_meeting]))
    with patch("meeting_api.recordings.get_storage_client_for", return_value=storage):
        accepted = await client.get("/recordings/1001/media/2001/download")
    assert accepted.status_code == 200
    assert accepted.json()["url"] == "https://storage.example.invalid/signed"


@pytest.mark.asyncio
async def test_R00_list_summary_and_include_data():
    transitions = [
        {"from": "active", "to": "stopping", "timestamp": "2026-01-01T00:10:00Z"},
        {"from": "stopping", "to": "completed", "timestamp": "2026-01-01T00:11:00Z"},
    ]
    source_data = {
        "name": "手動名",
        "calendar_event": {"title": "定例"},
        "participants": ["A", "B", "C", "D"],
        "notes": "あ" * 121,
        "status_transition": transitions,
        "recordings": [{"id": 1001}],
    }
    meeting = make_meeting(data=source_data, status="completed")
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[MockResult([meeting]), MockResult([meeting]), MockResult([])]
    )

    summary = await list_user_bots(
        auth_data=("token", SimpleNamespace(id=TEST_USER_ID)),
        db=db,
        limit=50,
        offset=7,
    )
    full = await list_user_bots(
        auth_data=("token", SimpleNamespace(id=TEST_USER_ID)),
        db=db,
        limit=50,
        offset=7,
        include="data",
    )
    foreign = await list_user_bots(
        auth_data=("token", SimpleNamespace(id=6)),
        db=db,
        limit=50,
        offset=7,
    )

    data = summary["meetings"][0]["data"]
    assert data["name"] == "手動名"
    assert data["calendar_title"] == "定例"
    assert data["participants"] == ["A", "B", "C"]
    assert data["participants_count"] == 4
    assert data["notes_preview"] == "あ" * 120
    assert data["last_transition"] == transitions[-1]
    assert data["has_recording"] is True
    assert summary["has_more"] is False
    assert full["meetings"][0]["data"] == source_data
    assert foreign == {"meetings": [], "has_more": False}

    statement = db.execute.await_args_list[2].args[0]
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "meetings.user_id = 6" in sql
    assert "LIMIT 51" in sql
    assert "OFFSET 7" in sql
