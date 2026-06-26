"""Tests for meeting-level recording storage cleanup."""

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from meeting_api.collector.endpoints import _purge_recordings_for_meeting

from .conftest import MockResult, TEST_USER_ID, make_meeting


@pytest.mark.asyncio
async def test_purge_recordings_deletes_mp3_sidecar_from_meeting_data(mock_db):
    meeting = make_meeting(data={
        "recordings": [{
            "id": 1001,
            "media_files": [{
                "id": 2001,
                "storage_backend": "gcs",
                "storage_path": "recordings/5/1001/sess/audio/master.webm",
            }],
        }],
    })
    mock_db.execute = AsyncMock(return_value=MockResult(scalar_value=False))
    storage = MagicMock()

    with patch("meeting_api.collector.endpoints.create_storage_client", return_value=storage) as factory:
        result = await _purge_recordings_for_meeting(mock_db, meeting, TEST_USER_ID)

    factory.assert_called_once_with("gcs")
    storage.delete_file.assert_has_calls(
        [
            call("recordings/5/1001/sess/audio/master.webm"),
            call("recordings/5/1001/sess/audio/master.mp3"),
        ],
        any_order=True,
    )
    assert result["storage_files_targeted"] == 2
    assert result["storage_files_deleted"] == 2
