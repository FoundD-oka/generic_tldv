"""Tests for recording endpoints — /recordings/*."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api.recordings import _stream_window_bytes

from .conftest import (
    TEST_USER_ID,
    TEST_MEETING_ID,
    TEST_PLATFORM,
    TEST_NATIVE_MEETING_ID,
    make_meeting,
    MockResult,
)


def _raw_recording() -> dict:
    """Recording payload with a single playable audio master."""
    return {
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
        }],
    }


def _range_storage(content: bytes) -> MagicMock:
    """Mock storage serving `content` through end-inclusive range reads."""
    mock_storage = MagicMock()
    mock_storage.get_file_size.return_value = len(content)
    mock_storage.download_file_range.side_effect = (
        lambda path, start, end: content[start:end + 1]
    )
    return mock_storage


# ===================================================================
# GET /recordings
# ===================================================================


class TestListRecordings:

    @pytest.mark.asyncio
    async def test_list_recordings_empty(self, client, mock_db):
        """GET /recordings when no recordings → empty list."""
        mock_db.execute = AsyncMock(return_value=MockResult([]))

        resp = await client.get("/recordings")

        assert resp.status_code == 200
        data = resp.json()
        assert "recordings" in data
        assert data["recordings"] == []

    @pytest.mark.asyncio
    async def test_list_recordings_with_meeting_data(self, client, mock_db):
        """GET /recordings returns recordings from meeting.data."""
        meeting = make_meeting(data={
            "recordings": [{
                "id": 1001,
                "meeting_id": TEST_MEETING_ID,
                "user_id": TEST_USER_ID,
                "session_uid": "sess-1",
                "source": "bot",
                "status": "completed",
                "created_at": "2025-01-01T00:00:00",
                "completed_at": "2025-01-01T00:05:00",
                "media_files": [],
            }],
        })
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        resp = await client.get("/recordings")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["recordings"]) == 1
        rec = data["recordings"][0]
        # Frozen field set
        expected_fields = {
            "id", "meeting_id", "user_id", "session_uid", "source",
            "status", "error_message", "created_at", "completed_at",
            "media_files",
        }
        assert expected_fields.issubset(set(rec.keys()))

    @pytest.mark.asyncio
    async def test_list_recordings_auth_required(self, unauthed_client):
        """GET /recordings without auth → 403."""
        resp = await unauthed_client.get("/recordings")
        assert resp.status_code == 403


# ===================================================================
# GET /recordings/{id}
# ===================================================================


class TestGetRecording:

    @pytest.mark.asyncio
    async def test_get_recording_not_found(self, client, mock_db):
        """GET /recordings/{id} for nonexistent → 404."""
        mock_db.execute = AsyncMock(return_value=MockResult([]))
        mock_db.get = AsyncMock(return_value=None)

        resp = await client.get("/recordings/99999")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_recording_success(self, client, mock_db):
        """GET /recordings/{id} returns RecordingResponse shape."""
        meeting = make_meeting(data={
            "recordings": [{
                "id": 1001,
                "meeting_id": TEST_MEETING_ID,
                "user_id": TEST_USER_ID,
                "session_uid": "sess-1",
                "source": "bot",
                "status": "completed",
                "created_at": "2025-01-01T00:00:00",
                "completed_at": "2025-01-01T00:05:00",
                "media_files": [{
                    "id": 2001,
                    "type": "audio",
                    "format": "wav",
                    "storage_backend": "minio",
                    "file_size_bytes": 1024,
                    "duration_seconds": 60.0,
                    "metadata": {},
                    "created_at": "2025-01-01T00:05:00",
                }],
            }],
        })
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        resp = await client.get("/recordings/1001")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1001
        assert len(data["media_files"]) == 1


# ===================================================================
# GET /recordings/{id}/media/{id}/raw
# ===================================================================


class TestDownloadRecordingMediaRaw:

    @pytest.mark.asyncio
    async def test_range_request_reads_only_requested_storage_range(self, client, mock_db):
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
        mock_storage.get_file_size.return_value = 10
        mock_storage.download_file_range.return_value = b"bc"

        with patch("meeting_api.recordings.get_storage_client", return_value=mock_storage):
            resp = await client.get(
                "/recordings/1001/media/2001/raw",
                headers={"Range": "bytes=1-2"},
            )

        assert resp.status_code == 206
        assert resp.content == b"bc"
        assert resp.headers["content-range"] == "bytes 1-2/10"
        assert resp.headers["content-length"] == "2"
        mock_storage.download_file_range.assert_called_once_with("recordings/test/master.webm", 1, 2)
        mock_storage.download_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_request_streams_windowed_ranges_without_full_download(self, client, mock_db):
        """Range-less playback streams windowed range reads, never a full download."""
        meeting = make_meeting(data={"recordings": [_raw_recording()]})
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
        content = b"full-webm"
        mock_storage = _range_storage(content)

        with patch("meeting_api.recordings.get_storage_client", return_value=mock_storage):
            resp = await client.get("/recordings/1001/media/2001/raw")

        assert resp.status_code == 200
        assert resp.content == content
        assert resp.headers["content-length"] == str(len(content))
        assert resp.headers["accept-ranges"] == "bytes"
        mock_storage.download_file.assert_not_called()
        assert mock_storage.download_file_range.call_count >= 1

    @pytest.mark.asyncio
    async def test_large_content_is_split_into_multiple_windows(self, client, mock_db, monkeypatch):
        """A body larger than one window is reassembled byte-exactly (no off-by-one)."""
        monkeypatch.setenv("RECORDING_STREAM_WINDOW_BYTES", "4")
        meeting = make_meeting(data={"recordings": [_raw_recording()]})
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
        content = bytes(range(10))
        mock_storage = _range_storage(content)

        with patch("meeting_api.recordings.get_storage_client", return_value=mock_storage):
            resp = await client.get("/recordings/1001/media/2001/raw")

        assert resp.status_code == 200
        assert resp.content == content
        assert resp.headers["content-length"] == "10"
        assert [c.args[1:] for c in mock_storage.download_file_range.call_args_list] == [
            (0, 3), (4, 7), (8, 9),
        ]
        mock_storage.download_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_range_request_is_split_into_multiple_windows(self, client, mock_db, monkeypatch):
        """Windowed reads stay inside the requested range and rebuild it exactly."""
        monkeypatch.setenv("RECORDING_STREAM_WINDOW_BYTES", "4")
        meeting = make_meeting(data={"recordings": [_raw_recording()]})
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
        content = bytes(range(20))
        mock_storage = _range_storage(content)

        with patch("meeting_api.recordings.get_storage_client", return_value=mock_storage):
            resp = await client.get(
                "/recordings/1001/media/2001/raw",
                headers={"Range": "bytes=3-11"},
            )

        assert resp.status_code == 206
        assert resp.content == content[3:12]
        assert resp.headers["content-range"] == "bytes 3-11/20"
        assert resp.headers["content-length"] == "9"
        assert [c.args[1:] for c in mock_storage.download_file_range.call_args_list] == [
            (3, 6), (7, 10), (11, 11),
        ]
        mock_storage.download_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsatisfiable_range_returns_416(self, client, mock_db):
        meeting = make_meeting(data={"recordings": [_raw_recording()]})
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
        mock_storage = _range_storage(b"0123456789")

        with patch("meeting_api.recordings.get_storage_client", return_value=mock_storage):
            resp = await client.get(
                "/recordings/1001/media/2001/raw",
                headers={"Range": "bytes=99-200"},
            )

        assert resp.status_code == 416
        assert resp.headers["content-range"] == "bytes */10"
        mock_storage.download_file_range.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_storage_object_returns_404(self, client, mock_db):
        meeting = make_meeting(data={"recordings": [_raw_recording()]})
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
        mock_storage = MagicMock()
        mock_storage.get_file_size.side_effect = FileNotFoundError("recordings/test/master.webm")

        with patch("meeting_api.recordings.get_storage_client", return_value=mock_storage):
            resp = await client.get("/recordings/1001/media/2001/raw")

        assert resp.status_code == 404
        mock_storage.download_file.assert_not_called()
        mock_storage.download_file_range.assert_not_called()


class TestStreamWindowBytes:
    """RECORDING_STREAM_WINDOW_BYTES parsing (invalid values fall back)."""

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("RECORDING_STREAM_WINDOW_BYTES", raising=False)
        assert _stream_window_bytes() == 8388608

    @pytest.mark.parametrize("value", ["", "  ", "abc", "1.5", "0", "-1", "-8388608"])
    def test_invalid_values_fall_back_to_default(self, value, monkeypatch):
        monkeypatch.setenv("RECORDING_STREAM_WINDOW_BYTES", value)
        assert _stream_window_bytes() == 8388608

    def test_valid_value_is_used(self, monkeypatch):
        monkeypatch.setenv("RECORDING_STREAM_WINDOW_BYTES", " 65536 ")
        assert _stream_window_bytes() == 65536


# ===================================================================
# GET /recordings/{id}/media/{id}/mp3
# ===================================================================


class TestDownloadRecordingMediaMp3:

    @pytest.mark.asyncio
    async def test_range_request_uses_cached_mp3_storage_object(self, client, mock_db):
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
                }],
            }],
        })
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
        mock_storage = MagicMock()
        mock_storage.file_exists.return_value = True
        mock_storage.get_file_size.return_value = 3
        mock_storage.download_file_range.return_value = b"ID3"

        with patch("meeting_api.recordings.get_storage_client", return_value=mock_storage):
            resp = await client.get(
                "/recordings/1001/media/2001/mp3",
                headers={"Range": "bytes=0-2"},
            )

        assert resp.status_code == 206
        assert resp.content == b"ID3"
        assert resp.headers["content-range"] == "bytes 0-2/3"
        assert resp.headers["content-type"].startswith("audio/mpeg")
        assert resp.headers["content-disposition"] == 'inline; filename="1001_audio.mp3"'
        mock_storage.download_file_range.assert_called_once_with("recordings/test/master.mp3", 0, 2)
        mock_storage.download_file_to_path.assert_not_called()

    @pytest.mark.asyncio
    async def test_master_mp3_resolves_finalized_audio_master(self, client, mock_db):
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
                    "finalized_by": "recording_finalizer.master",
                }],
            }],
        })
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
        mock_storage = MagicMock()
        mock_storage.file_exists.return_value = True
        mock_storage.get_file_size.return_value = 3
        mock_storage.download_file_range.return_value = b"ID3"

        with patch("meeting_api.recordings.get_storage_client", return_value=mock_storage):
            resp = await client.get(
                "/recordings/1001/master/mp3?type=audio",
                headers={"Range": "bytes=0-2"},
            )

        assert resp.status_code == 206
        assert resp.content == b"ID3"
        mock_storage.download_file_range.assert_called_once_with("recordings/test/master.mp3", 0, 2)


# ===================================================================
# DELETE /recordings/{id}
# ===================================================================


class TestDeleteRecording:

    @pytest.mark.asyncio
    async def test_delete_recording_not_found(self, client, mock_db):
        """DELETE /recordings/{id} for nonexistent → 404."""
        mock_db.execute = AsyncMock(return_value=MockResult([]))
        mock_db.get = AsyncMock(return_value=None)

        resp = await client.delete("/recordings/99999")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_recording_success(self, client, mock_db):
        """DELETE /recordings/{id} → removes from meeting.data."""
        meeting = make_meeting(data={
            "recordings": [{
                "id": 1001,
                "meeting_id": TEST_MEETING_ID,
                "user_id": TEST_USER_ID,
                "session_uid": "sess-1",
                "source": "bot",
                "status": "completed",
                "media_files": [],
            }],
        })
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        mock_storage = MagicMock()
        with patch("meeting_api.recordings.get_storage_client", return_value=mock_storage):
            with patch("meeting_api.recordings.attributes.flag_modified", MagicMock()):
                resp = await client.delete("/recordings/1001")

        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
