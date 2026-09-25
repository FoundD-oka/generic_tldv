"""R04 contracts for recording download metadata resolution."""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import TEST_MEETING_ID, TEST_USER_ID, MockResult, make_meeting


RECORDING_ID = 1001
MEDIA_FILE_ID = 2001


def _meeting(*, backend="minio", fmt="webm", exists=True):
    del exists
    return make_meeting(
        data={
            "recordings": [
                {
                    "id": RECORDING_ID,
                    "meeting_id": TEST_MEETING_ID,
                    "user_id": TEST_USER_ID,
                    "status": "completed",
                    "media_files": [
                        {
                            "id": MEDIA_FILE_ID,
                            "type": "video" if fmt == "mp4" else "audio",
                            "format": fmt,
                            "storage_backend": backend,
                            "storage_path": f"recordings/test/master.{fmt}",
                            "file_size_bytes": 123,
                            "duration_seconds": 12.5,
                            "finalized_by": "recording_finalizer.master",
                            "is_final": True,
                        }
                    ],
                }
            ]
        }
    )


def _storage(*, exists=True, presigned="https://storage.invalid/signed"):
    storage = MagicMock()
    storage.file_exists.return_value = exists
    storage.get_presigned_url.return_value = presigned
    return storage


@pytest.mark.asyncio
async def test_R04_master_queries_owned_recording_once(client, mock_db):
    mock_db.execute = AsyncMock(return_value=MockResult([_meeting()]))
    storage = _storage()

    with patch("meeting_api.recordings.get_storage_client_for", return_value=storage):
        response = await client.get(f"/recordings/{RECORDING_ID}/master?type=audio")

    assert response.status_code == 200
    assert mock_db.execute.await_count == 1
    assert response.json() == {
        "url": "https://storage.invalid/signed",
        "download_url": "https://storage.invalid/signed",
        "filename": f"{RECORDING_ID}_audio.webm",
        "content_type": "audio/webm",
        "file_size_bytes": 123,
        "expires_in": 3600,
        "media_file_id": MEDIA_FILE_ID,
        "raw_url": f"/recordings/{RECORDING_ID}/media/{MEDIA_FILE_ID}/raw",
        "duration_seconds": 12.5,
    }

    mock_db.execute.reset_mock()
    no_master = _meeting()
    no_master.data["recordings"][0]["media_files"][0]["finalized_by"] = "chunk"
    mock_db.execute.return_value = MockResult([no_master])
    storage.reset_mock()
    response = await client.get(f"/recordings/{RECORDING_ID}/master?type=audio")
    assert response.status_code == 404
    assert mock_db.execute.await_count == 1
    storage.file_exists.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "backend,fmt,presigned,exists,expected_url",
    [
        ("local", "webm", "unused", True, f"/recordings/{RECORDING_ID}/media/{MEDIA_FILE_ID}/raw"),
        ("minio", "wav", "https://minio.invalid/signed", True, "https://minio.invalid/signed"),
        ("gcs", "mp4", "https://gcs.invalid/signed", True, "https://gcs.invalid/signed"),
        ("minio", "webm", None, True, f"/recordings/{RECORDING_ID}/media/{MEDIA_FILE_ID}/raw"),
        ("gcs", "wav", None, True, f"/recordings/{RECORDING_ID}/media/{MEDIA_FILE_ID}/raw"),
        ("minio", "webm", "unused", False, None),
    ],
)
async def test_R04_download_and_master_share_metadata_contract(
    client, mock_db, backend, fmt, presigned, exists, expected_url
):
    meeting = _meeting(backend=backend, fmt=fmt)
    storage = _storage(exists=exists, presigned=presigned)
    mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

    with patch("meeting_api.recordings.get_storage_client_for", return_value=storage):
        download = await client.get(
            f"/recordings/{RECORDING_ID}/media/{MEDIA_FILE_ID}/download"
        )
        master_type = "video" if fmt == "mp4" else "audio"
        master = await client.get(
            f"/recordings/{RECORDING_ID}/master?type={master_type}"
        )

    if not exists:
        assert download.status_code == master.status_code == 404
        return

    assert download.status_code == master.status_code == 200
    download_body = download.json()
    master_body = master.json()
    assert master_body == {
        **download_body,
        "media_file_id": MEDIA_FILE_ID,
        "raw_url": f"/recordings/{RECORDING_ID}/media/{MEDIA_FILE_ID}/raw",
        "duration_seconds": 12.5,
    }
    assert download_body["url"] == expected_url
    assert download_body["download_url"] == expected_url
    assert download_body["expires_in"] == 3600


@pytest.mark.asyncio
async def test_R04_slow_storage_does_not_block_event_loop(client, mock_db):
    mock_db.execute = AsyncMock(return_value=MockResult([_meeting()]))
    sdk_started = threading.Event()
    sdk_release = threading.Event()
    sdk_thread_ids = []
    storage = _storage()

    def blocking_exists(_path):
        sdk_thread_ids.append(threading.get_ident())
        sdk_started.set()
        assert sdk_release.wait(timeout=2)
        return True

    storage.file_exists.side_effect = blocking_exists
    loop_thread_id = threading.get_ident()
    heartbeat = asyncio.Event()

    async def beat():
        await asyncio.sleep(0)
        heartbeat.set()

    with patch("meeting_api.recordings.get_storage_client_for", return_value=storage):
        request_task = asyncio.create_task(
            client.get(f"/recordings/{RECORDING_ID}/master?type=audio")
        )
        heartbeat_task = asyncio.create_task(beat())
        try:
            assert await asyncio.to_thread(sdk_started.wait, 1)
            await asyncio.wait_for(heartbeat.wait(), timeout=1)
        finally:
            sdk_release.set()
        response = await asyncio.wait_for(request_task, timeout=2)
        await heartbeat_task

    assert response.status_code == 200
    assert sdk_thread_ids and sdk_thread_ids[0] != loop_thread_id


@pytest.mark.asyncio
async def test_R04_owner_rejection_precedes_storage(client, mock_db):
    observed = {}

    async def reject_foreign_owner(statement):
        params = statement.compile().params
        observed.update(params)
        return MockResult()

    mock_db.execute = AsyncMock(side_effect=reject_foreign_owner)
    storage = _storage()
    with patch("meeting_api.recordings.get_storage_client_for", return_value=storage):
        response = await client.get(f"/recordings/{RECORDING_ID}/master?type=audio")

    assert response.status_code == 404
    assert TEST_USER_ID in observed.values()
    storage.file_exists.assert_not_called()
    storage.get_presigned_url.assert_not_called()
