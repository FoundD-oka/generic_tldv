from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api import sweeps
from meeting_api.drive_export import (
    build_drive_markdown,
    queue_drive_export_if_needed,
    run_drive_export,
)
from meeting_api.models import Transcription
from meeting_api.schemas import MeetingStatus

from .conftest import TEST_MEETING_ID, MockResult, make_meeting
from .test_final_transcription import FetchAllResult


def _calendar_event():
    return {
        "source": "google_calendar",
        "calendar_event_id": 7,
        "external_event_id": "gcal-1",
        "title": "週次定例",
        "start_time": "2026-07-03T10:00:00+09:00",
        "end_time": "2026-07-03T11:00:00+09:00",
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "platform": "google_meet",
    }


def test_queue_drive_export_only_for_calendar_origin():
    meeting = make_meeting(
        status=MeetingStatus.COMPLETED.value,
        data={"calendar_event": _calendar_event()},
    )

    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()):
        changed = queue_drive_export_if_needed(meeting, triggered_by="final_transcription_sweep")

    assert changed is True
    assert meeting.data["drive_export"]["status"] == "queued"
    assert meeting.data["drive_export"]["attempts"] == 0
    assert meeting.data["drive_export_status"] == "queued"


def test_queue_drive_export_defaults_to_all_meetings():
    meeting = make_meeting(status=MeetingStatus.COMPLETED.value, data={})

    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()):
        changed = queue_drive_export_if_needed(meeting, triggered_by="final_transcription_sweep")

    assert changed is True
    assert meeting.data["drive_export"]["status"] == "queued"


def test_queue_drive_export_calendar_only_skips_non_calendar(monkeypatch):
    monkeypatch.setenv("KABOSU_DRIVE_EXPORT_CALENDAR_ONLY", "true")
    meeting = make_meeting(status=MeetingStatus.COMPLETED.value, data={})

    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()):
        changed = queue_drive_export_if_needed(meeting, triggered_by="final_transcription_sweep")

    assert changed is False
    assert "drive_export" not in meeting.data


def test_build_drive_markdown_uses_calendar_meta_and_speaker_labels():
    meeting = make_meeting(data={"calendar_event": _calendar_event()})
    rows = [
        Transcription(meeting_id=meeting.id, start_time=0, end_time=2, speaker="Alice", text="こんにちは", language="ja"),
        Transcription(meeting_id=meeting.id, start_time=62, end_time=65, speaker="Bob", text="進めます", language="ja"),
    ]

    markdown = build_drive_markdown(meeting, _calendar_event(), rows)

    assert "# 週次定例" in markdown
    assert "- 参加者: Alice, Bob" in markdown
    assert "- [00:00] **Alice**: こんにちは" in markdown
    assert "- [01:02] **Bob**: 進めます" in markdown


@pytest.mark.asyncio
async def test_run_drive_export_uploads_markdown_and_marks_done():
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "calendar_event": _calendar_event(),
            "drive_export": {"status": "queued", "attempts": 0},
        },
        created_at=datetime(2026, 7, 3, 1, 0, 0),
    )
    transcripts = [
        Transcription(meeting_id=meeting.id, start_time=0, end_time=2, speaker="Alice", text="完了です", language="ja"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(transcripts),
    ])
    db.commit = AsyncMock()
    uploaded = AsyncMock(return_value={"id": "drive-file-1", "webViewLink": "https://drive/file"})

    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.drive_export.upload_markdown_to_drive", new=uploaded):
        result = await run_drive_export(TEST_MEETING_ID, db)

    assert result["status"] == "done"
    assert result["id"] == "drive-file-1"
    assert meeting.data["drive_export"]["status"] == "done"
    assert meeting.data["drive_export"]["file_id"] == "drive-file-1"
    filename, content = uploaded.await_args.args
    assert filename == "2026-07-03_1000_週次定例.md"
    assert "**Alice**: 完了です" in content
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_run_drive_export_non_calendar_meeting_uses_meeting_metadata():
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={"drive_export": {"status": "queued", "attempts": 0}},
        start_time=datetime(2026, 7, 3, 10, 0, 0),
    )
    transcripts = [
        Transcription(meeting_id=meeting.id, start_time=0, end_time=2, speaker="Alice", text="手動参加", language="ja"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[MockResult([meeting]), MockResult(transcripts)])
    db.commit = AsyncMock()
    uploaded = AsyncMock(return_value={"id": "drive-file-2", "webViewLink": "https://drive/file2"})

    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.drive_export.upload_markdown_to_drive", new=uploaded):
        result = await run_drive_export(TEST_MEETING_ID, db)

    assert result["status"] == "done"
    assert meeting.data["drive_export"]["status"] == "done"
    filename, content = uploaded.await_args.args
    assert filename.endswith(".md")
    assert "**Alice**: 手動参加" in content


@pytest.mark.asyncio
async def test_upload_markdown_to_drive_supports_shared_drives(monkeypatch):
    from meeting_api.drive_export import upload_markdown_to_drive

    monkeypatch.setenv("KABOSU_DRIVE_FOLDER_ID", "shared-drive-folder")
    response = MagicMock()
    response.status_code = 200
    response.json = MagicMock(return_value={"id": "f1", "webViewLink": "https://drive/f1"})
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client_ctx = MagicMock()
    client_ctx.__aenter__ = AsyncMock(return_value=client)
    client_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("meeting_api.drive_export.refresh_google_access_token", new=AsyncMock(return_value="tok")), \
         patch("meeting_api.drive_export.httpx.AsyncClient", return_value=client_ctx):
        await upload_markdown_to_drive("note.md", "# hi")

    assert client.post.await_args.kwargs["params"]["supportsAllDrives"] == "true"


@pytest.mark.asyncio
async def test_sweep_drive_export_jobs_runs_queued_job():
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "calendar_event": _calendar_event(),
            "drive_export": {"status": "queued", "attempts": 0},
        },
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        FetchAllResult([(TEST_MEETING_ID,)]),
        MockResult([meeting]),
    ])
    db.commit = AsyncMock()

    @asynccontextmanager
    async def db_session_factory():
        yield db

    with patch("meeting_api.drive_export.run_drive_export", new=AsyncMock(return_value={"status": "done"})) as run:
        swept = await sweeps._sweep_drive_export_jobs(db_session_factory)

    assert swept == 1
    run.assert_awaited_once_with(TEST_MEETING_ID, db)
