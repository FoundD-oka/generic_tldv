from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api import sweeps
from meeting_api.drive_export import (
    build_drive_markdown,
    queue_drive_export_if_needed,
    requeue_drive_export,
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
        MockResult([meeting]),  # pre-done refresh (mid-export mutation check)
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
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(transcripts),
        MockResult([meeting]),  # pre-done refresh (mid-export mutation check)
    ])
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


# ---------------------------------------------------------------------------
# Speaker-update requeue (issue #23) — including the mid-export race
# ---------------------------------------------------------------------------


def test_requeue_drive_export_requeues_done_export():
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={"drive_export": {"status": "done", "file_id": "f-1", "attempts": 2}},
    )
    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()):
        assert requeue_drive_export(meeting, triggered_by="speaker_update") is True
    state = meeting.data["drive_export"]
    assert state["status"] == "queued"
    assert state["requeued_from"] == "done"
    assert state["file_id"] == "f-1"
    assert state["attempts"] == 0


def test_requeue_during_running_export_flags_rerun_instead_of_requeue():
    """A running export is rendering OLD rows; flag rerun_requested so the
    exporter re-queues itself instead of finishing as stale `done`."""
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={"drive_export": {"status": "running", "attempts": 1}},
    )
    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()):
        assert requeue_drive_export(meeting, triggered_by="speaker_update") is True
    state = meeting.data["drive_export"]
    assert state["status"] == "running"  # untouched
    assert state["rerun_requested"] is True


def test_requeue_while_already_queued_is_noop():
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={"drive_export": {"status": "queued", "attempts": 0}},
    )
    assert requeue_drive_export(meeting, triggered_by="speaker_update") is False
    assert meeting.data["drive_export"]["status"] == "queued"


@pytest.mark.asyncio
async def test_run_drive_export_requeues_when_content_changed_mid_export():
    """rerun_requested committed by a speaker PATCH during the upload must
    send the export back to queued (the uploaded file has stale names)."""
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
    # The refresh select returns a meeting whose drive_export was mutated
    # mid-export by the speaker-update endpoint.
    refreshed = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "calendar_event": _calendar_event(),
            "drive_export": {"status": "running", "attempts": 1, "rerun_requested": True},
        },
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(transcripts),
        MockResult([refreshed]),
    ])
    db.commit = AsyncMock()
    uploaded = AsyncMock(return_value={"id": "drive-file-1", "webViewLink": "https://drive/file"})

    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.drive_export.upload_markdown_to_drive", new=uploaded):
        result = await run_drive_export(TEST_MEETING_ID, db)

    assert result["status"] == "queued"
    state = refreshed.data["drive_export"]
    assert state["status"] == "queued"
    assert state["rerun_requested"] is False
    assert state["file_id"] == "drive-file-1"  # next run updates the same file


@pytest.mark.asyncio
async def test_run_drive_export_updates_existing_drive_file_in_place():
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "calendar_event": _calendar_event(),
            "drive_export": {"status": "queued", "attempts": 0, "file_id": "existing-file"},
        },
        created_at=datetime(2026, 7, 3, 1, 0, 0),
    )
    transcripts = []
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(transcripts),
        MockResult([meeting]),
    ])
    db.commit = AsyncMock()
    uploaded = AsyncMock(return_value={"id": "existing-file", "webViewLink": "https://drive/file"})

    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.drive_export.upload_markdown_to_drive", new=uploaded):
        result = await run_drive_export(TEST_MEETING_ID, db)

    assert result["status"] == "done"
    assert uploaded.await_args.kwargs["file_id"] == "existing-file"


# ---------------------------------------------------------------------------
# Domain read permission on the exported Drive file
# ---------------------------------------------------------------------------


def _permission_client(status_code=200, body=None):
    """AsyncClient context manager double that records POST calls."""
    response = MagicMock()
    response.status_code = status_code
    response.text = "permission error" if status_code >= 400 else "ok"
    response.json = MagicMock(return_value=body or {"id": "perm-1", "type": "domain", "role": "reader"})
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return client, ctx


@pytest.mark.asyncio
async def test_grant_domain_reader_permission_posts_domain_reader(monkeypatch):
    from meeting_api.drive_export import grant_domain_reader_permission

    monkeypatch.setenv("KABOSU_DRIVE_SHARE_DOMAIN", "bonginkan.ai")
    client, ctx = _permission_client()

    with patch("meeting_api.drive_export.refresh_google_access_token", new=AsyncMock(return_value="tok")), \
         patch("meeting_api.drive_export.httpx.AsyncClient", return_value=ctx):
        granted = await grant_domain_reader_permission("drive-file-1")

    assert granted["id"] == "perm-1"
    url = client.post.await_args.args[0]
    assert url.endswith("/drive/v3/files/drive-file-1/permissions")
    assert client.post.await_args.kwargs["params"]["supportsAllDrives"] == "true"
    assert client.post.await_args.kwargs["json"] == {
        "type": "domain",
        "role": "reader",
        "domain": "bonginkan.ai",
        "allowFileDiscovery": False,
    }


@pytest.mark.asyncio
async def test_grant_domain_reader_permission_skips_without_domain(monkeypatch):
    from meeting_api.drive_export import grant_domain_reader_permission

    monkeypatch.delenv("KABOSU_DRIVE_SHARE_DOMAIN", raising=False)
    client, ctx = _permission_client()

    with patch("meeting_api.drive_export.refresh_google_access_token", new=AsyncMock(return_value="tok")), \
         patch("meeting_api.drive_export.httpx.AsyncClient", return_value=ctx):
        granted = await grant_domain_reader_permission("drive-file-1")

    assert granted == {"skipped": True}
    assert client.post.await_count == 0


@pytest.mark.asyncio
async def test_run_drive_export_records_domain_permission_once(monkeypatch):
    monkeypatch.setenv("KABOSU_DRIVE_SHARE_DOMAIN", "bonginkan.ai")
    monkeypatch.delenv("KABOSU_DRIVE_EXPORT_WEBHOOK_URL", raising=False)
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "calendar_event": _calendar_event(),
            "drive_export": {"status": "queued", "attempts": 0},
        },
        created_at=datetime(2026, 7, 3, 1, 0, 0),
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult([]),
        MockResult([meeting]),
    ])
    db.commit = AsyncMock()
    uploaded = AsyncMock(return_value={"id": "drive-file-1", "webViewLink": "https://drive/file"})
    client, ctx = _permission_client()

    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.drive_export.upload_markdown_to_drive", new=uploaded), \
         patch("meeting_api.drive_export.refresh_google_access_token", new=AsyncMock(return_value="tok")), \
         patch("meeting_api.drive_export.httpx.AsyncClient", return_value=ctx):
        await run_drive_export(TEST_MEETING_ID, db)

    state = meeting.data["drive_export"]
    assert state["status"] == "done"
    assert state["domain_permission"]["permission_id"] == "perm-1"
    assert state["domain_permission"]["domain"] == "bonginkan.ai"
    assert client.post.await_count == 1

    # Re-export of the same meeting must not grant the permission twice.
    meeting.data["drive_export"]["status"] = "queued"
    db2 = AsyncMock()
    db2.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult([]),
        MockResult([meeting]),
    ])
    db2.commit = AsyncMock()

    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.drive_export.upload_markdown_to_drive", new=uploaded), \
         patch("meeting_api.drive_export.refresh_google_access_token", new=AsyncMock(return_value="tok")), \
         patch("meeting_api.drive_export.httpx.AsyncClient", return_value=ctx):
        await run_drive_export(TEST_MEETING_ID, db2)

    assert client.post.await_count == 1
    assert meeting.data["drive_export"]["domain_permission"]["permission_id"] == "perm-1"


@pytest.mark.asyncio
async def test_run_drive_export_permission_failure_marks_failed_with_file_id(monkeypatch):
    from meeting_api.drive_export import DriveExportError

    monkeypatch.setenv("KABOSU_DRIVE_SHARE_DOMAIN", "bonginkan.ai")
    monkeypatch.setenv("KABOSU_DRIVE_EXPORT_WEBHOOK_URL", "http://calendar-service:8050/internal/webhooks/drive-export-completed")
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "calendar_event": _calendar_event(),
            "drive_export": {"status": "queued", "attempts": 0},
        },
        created_at=datetime(2026, 7, 3, 1, 0, 0),
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult([]),
    ])
    db.commit = AsyncMock()
    uploaded = AsyncMock(return_value={"id": "drive-file-1", "webViewLink": "https://drive/file"})
    client, ctx = _permission_client(status_code=500)
    delivered = AsyncMock()

    with patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.drive_export.upload_markdown_to_drive", new=uploaded), \
         patch("meeting_api.drive_export.refresh_google_access_token", new=AsyncMock(return_value="tok")), \
         patch("meeting_api.drive_export.httpx.AsyncClient", return_value=ctx), \
         patch("meeting_api.webhook_delivery.deliver_with_result", new=delivered):
        with pytest.raises(DriveExportError):
            await run_drive_export(TEST_MEETING_ID, db)

    state = meeting.data["drive_export"]
    assert state["status"] == "failed"
    assert state["retryable"] is True
    assert state["file_id"] == "drive-file-1"  # retry PATCHes the same file
    assert "domain_permission" not in state
    assert delivered.await_count == 0


# ---------------------------------------------------------------------------
# drive_export.completed internal hook
# ---------------------------------------------------------------------------


HOOK_URL = "http://calendar-service:8050/internal/webhooks/drive-export-completed"


def _hook_patches(claim, mark, delivered, uploaded):
    return [
        patch("meeting_api.drive_export.attributes.flag_modified", new=MagicMock()),
        patch("meeting_api.drive_export.upload_markdown_to_drive", new=uploaded),
        patch("meeting_api.outbound_events.claim_outbound_event", new=claim),
        patch("meeting_api.outbound_events.mark_outbound_event", new=mark),
        patch("meeting_api.webhook_delivery.deliver_with_result", new=delivered),
    ]


@pytest.mark.asyncio
async def test_run_drive_export_sends_completed_hook_with_deterministic_event_id(monkeypatch):
    from meeting_api.outbound_events import event_key
    from meeting_api.webhook_delivery import DeliveryResult

    monkeypatch.setenv("KABOSU_DRIVE_EXPORT_WEBHOOK_URL", HOOK_URL)
    monkeypatch.setenv("KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET", "hook-secret")
    monkeypatch.delenv("KABOSU_DRIVE_SHARE_DOMAIN", raising=False)
    expected_key = event_key("drive_export_hooks", "drive_export.completed", TEST_MEETING_ID, HOOK_URL)

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

    claim = AsyncMock(side_effect=[
        (expected_key, {"attempts": 0}, True),
        (expected_key, {"attempts": 1, "status": "delivered"}, False),
    ])
    mark = AsyncMock()
    delivered = AsyncMock(return_value=DeliveryResult(status="delivered"))
    uploaded = AsyncMock(return_value={"id": "drive-file-1", "webViewLink": "https://drive/file"})

    async def _run():
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            MockResult([meeting]),
            MockResult(transcripts),
            MockResult([meeting]),
            MockResult([meeting]),  # hook payload lookup
        ])
        db.commit = AsyncMock()
        patches = _hook_patches(claim, mark, delivered, uploaded)
        for p in patches:
            p.start()
        try:
            return await run_drive_export(TEST_MEETING_ID, db)
        finally:
            for p in patches:
                p.stop()

    await _run()
    meeting.data["drive_export"]["status"] = "queued"
    await _run()

    # Re-export claims the same ledger key, so the hook is delivered once.
    assert delivered.await_count == 1
    assert [call.kwargs["payload"]["event_id"] for call in claim.await_args_list] == [
        expected_key, expected_key,
    ]

    payload = delivered.await_args.kwargs["payload"]
    assert payload["event_type"] == "drive_export.completed"
    assert payload["event_id"] == expected_key
    assert delivered.await_args.kwargs["url"] == HOOK_URL
    assert delivered.await_args.kwargs["webhook_secret"] == "hook-secret"
    data = payload["data"]
    assert data["drive_export"]["web_view_link"] == "https://drive/file"
    assert data["drive_export"]["file_id"] == "drive-file-1"
    assert data["title"] == "週次定例"
    assert "完了です" in data["context_excerpt"]
    for field in ("id", "platform", "native_meeting_id", "status", "start_time", "end_time"):
        assert field in data["meeting"]
    assert data["meeting"]["id"] == TEST_MEETING_ID
    assert mark.await_args.kwargs["status"] == "delivered"


@pytest.mark.asyncio
async def test_run_drive_export_skips_hook_when_ledger_already_owns_event(monkeypatch):
    monkeypatch.setenv("KABOSU_DRIVE_EXPORT_WEBHOOK_URL", HOOK_URL)
    monkeypatch.delenv("KABOSU_DRIVE_SHARE_DOMAIN", raising=False)
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "calendar_event": _calendar_event(),
            "drive_export": {"status": "queued", "attempts": 0},
        },
        created_at=datetime(2026, 7, 3, 1, 0, 0),
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult([]),
        MockResult([meeting]),
        MockResult([meeting]),
    ])
    db.commit = AsyncMock()
    claim = AsyncMock(return_value=("k", {"status": "pending", "attempts": 1}, False))
    mark = AsyncMock()
    delivered = AsyncMock()
    uploaded = AsyncMock(return_value={"id": "drive-file-1", "webViewLink": "https://drive/file"})

    patches = _hook_patches(claim, mark, delivered, uploaded)
    for p in patches:
        p.start()
    try:
        result = await run_drive_export(TEST_MEETING_ID, db)
    finally:
        for p in patches:
            p.stop()

    assert result["status"] == "done"
    assert delivered.await_count == 0
    assert mark.await_count == 0


@pytest.mark.asyncio
async def test_run_drive_export_rerun_branch_does_not_send_hook(monkeypatch):
    monkeypatch.setenv("KABOSU_DRIVE_EXPORT_WEBHOOK_URL", HOOK_URL)
    monkeypatch.delenv("KABOSU_DRIVE_SHARE_DOMAIN", raising=False)
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "calendar_event": _calendar_event(),
            "drive_export": {"status": "queued", "attempts": 0},
        },
        created_at=datetime(2026, 7, 3, 1, 0, 0),
    )
    refreshed = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "calendar_event": _calendar_event(),
            "drive_export": {"status": "running", "attempts": 1, "rerun_requested": True},
        },
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult([]),
        MockResult([refreshed]),
    ])
    db.commit = AsyncMock()
    claim = AsyncMock(return_value=("k", {"attempts": 0}, True))
    mark = AsyncMock()
    delivered = AsyncMock()
    uploaded = AsyncMock(return_value={"id": "drive-file-1", "webViewLink": "https://drive/file"})

    patches = _hook_patches(claim, mark, delivered, uploaded)
    for p in patches:
        p.start()
    try:
        result = await run_drive_export(TEST_MEETING_ID, db)
    finally:
        for p in patches:
            p.stop()

    assert result["status"] == "queued"
    assert claim.await_count == 0
    assert delivered.await_count == 0


@pytest.mark.asyncio
async def test_run_drive_export_without_hook_url_does_not_deliver(monkeypatch):
    monkeypatch.delenv("KABOSU_DRIVE_EXPORT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("KABOSU_DRIVE_SHARE_DOMAIN", raising=False)
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "calendar_event": _calendar_event(),
            "drive_export": {"status": "queued", "attempts": 0},
        },
        created_at=datetime(2026, 7, 3, 1, 0, 0),
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult([]),
        MockResult([meeting]),
    ])
    db.commit = AsyncMock()
    claim = AsyncMock()
    mark = AsyncMock()
    delivered = AsyncMock()
    uploaded = AsyncMock(return_value={"id": "drive-file-1", "webViewLink": "https://drive/file"})

    patches = _hook_patches(claim, mark, delivered, uploaded)
    for p in patches:
        p.start()
    try:
        result = await run_drive_export(TEST_MEETING_ID, db)
    finally:
        for p in patches:
            p.stop()

    assert result["status"] == "done"
    assert claim.await_count == 0
    assert delivered.await_count == 0
    assert "domain_permission" not in meeting.data["drive_export"]
