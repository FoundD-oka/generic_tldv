"""Google認証失敗の履歴は会議の最終状態と独立して保持する。"""
from unittest.mock import AsyncMock, patch

import pytest

from .conftest import make_meeting, MockResult, TEST_SESSION_UID
from .test_callbacks import _patch_find_meeting, _patch_flag_modified


MARKER = {"reason": "google_sign_in_required", "occurred_at": "2026-09-29T01:28:07Z"}


@pytest.mark.asyncio
async def test_guest_retry_records_history_without_failing_join(client, mock_db):
    meeting = make_meeting(status="joining", data={"calendar_event": {"title": "Fixture"}})
    mock_db.execute.return_value = MockResult([meeting])
    with (_patch_find_meeting(meeting), _patch_flag_modified(),
          patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock),
          patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock),
          patch("meeting_api.callbacks.update_meeting_status", new_callable=AsyncMock) as transition):
        response = await client.post("/bots/internal/callback/status_change", json={
            "connection_id": TEST_SESSION_UID, "status": "joining", "google_auth_fallback": MARKER,
        })
        assert response.json()["status"] == "processed"
        transition.assert_not_awaited()
        assert meeting.status == "joining"
        assert meeting.data["google_auth_fallback"] == MARKER
        assert meeting.data["calendar_event"] == {"title": "Fixture"}
        assert "failure_stage" not in meeting.data
        assert mock_db.commit.await_count > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["awaiting_admission", "active", "completed", "failed"])
async def test_later_callbacks_keep_first_auth_failure(client, mock_db, status):
    meeting = make_meeting(status="joining", data={"google_auth_fallback": dict(MARKER)})
    mock_db.execute.return_value = MockResult([meeting])
    async def transition(obj, new_status, *_args, **_kwargs):
        obj.status = new_status.value
        return True
    with (_patch_find_meeting(meeting), _patch_flag_modified(),
          patch("meeting_api.callbacks.publish_meeting_status_change", new_callable=AsyncMock),
          patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock),
          patch("meeting_api.callbacks.run_all_tasks", new_callable=AsyncMock),
          patch("meeting_api.callbacks.update_meeting_status", side_effect=transition)):
        response = await client.post("/bots/internal/callback/status_change", json={
            "connection_id": TEST_SESSION_UID, "status": status,
            "google_auth_fallback": {**MARKER, "occurred_at": "2026-09-29T02:00:00Z"},
        })
        assert response.status_code == 200
        assert meeting.data["google_auth_fallback"] == MARKER
        # A later callback without the optional marker cannot clear its history.
        await client.post("/bots/internal/callback/status_change", json={
            "connection_id": TEST_SESSION_UID, "status": status,
        })
        assert meeting.data["google_auth_fallback"] == MARKER


@pytest.mark.asyncio
async def test_stop_race_still_commits_auth_failure(client, mock_db):
    meeting = make_meeting(status="stopping", data={"stop_requested": True})
    mock_db.execute.return_value = MockResult([meeting])
    with (_patch_find_meeting(meeting), _patch_flag_modified(),
          patch("meeting_api.callbacks.schedule_status_webhook_task", new_callable=AsyncMock)):
        response = await client.post("/bots/internal/callback/status_change", json={
            "connection_id": TEST_SESSION_UID, "status": "joining", "google_auth_fallback": MARKER,
        })
        assert response.json()["status"] == "ignored"
        assert meeting.data["google_auth_fallback"] == MARKER
        mock_db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_unrecognized_reason_is_rejected(client):
    response = await client.post("/bots/internal/callback/status_change", json={
        "connection_id": TEST_SESSION_UID, "status": "joining",
        "google_auth_fallback": {**MARKER, "reason": "host_denied"},
    })
    assert response.status_code == 422
