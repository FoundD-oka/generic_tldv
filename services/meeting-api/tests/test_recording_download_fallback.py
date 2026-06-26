"""Issue #1 — /download endpoint signing-fallback.

When signing is unavailable (get_presigned_url returns None, e.g. GCS signBlob
not granted to the runtime SA) the /download endpoint must fall back to the
/raw proxy path rather than handing the client a null url — matching the local
backend's posture and keeping playback working without depending on the
consumer to interpret null.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import (
    TEST_MEETING_ID,
    TEST_USER_ID,
    make_meeting,
    MockResult,
)


@pytest.mark.asyncio
async def test_null_presigned_url_falls_back_to_raw(client, mock_db):
    meeting = make_meeting(data={
        "recordings": [{
            "id": 1001,
            "meeting_id": TEST_MEETING_ID,
            "user_id": TEST_USER_ID,
            "session_uid": "sess-1",
            "source": "bot",
            "status": "completed",
            "media_files": [{
                "id": 2001,
                "type": "audio",
                "format": "webm",
                "storage_backend": "minio",
                "storage_path": "recordings/test/master.webm",
                "file_size_bytes": 10,
            }],
        }],
    })
    mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
    mock_storage = MagicMock()
    mock_storage.file_exists.return_value = True
    mock_storage.get_presigned_url.return_value = None  # signing unavailable

    with patch("meeting_api.recordings.get_storage_client", return_value=mock_storage):
        resp = await client.get("/recordings/1001/media/2001/download")

    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == "/recordings/1001/media/2001/raw"
    assert body["download_url"] == "/recordings/1001/media/2001/raw"
