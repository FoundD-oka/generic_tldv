from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from meeting_api import sweeps
from meeting_api.final_transcription import (
    DeferredTranscriptionResult,
    _call_transcription_service,
    _parse_segments,
    find_final_transcription_source,
    queue_final_transcription,
    run_deferred_transcription,
)
from meeting_api.models import Transcription
from meeting_api.schemas import MeetingStatus

from .conftest import TEST_MEETING_ID, MockResult, make_meeting


class FetchAllResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


def _meeting_with_audio_master(**overrides):
    data = {
        "transcribe_enabled": True,
        "recording_enabled": True,
        "speaker_events": [
            {"participant_name": "Alice", "event_type": "SPEAKER_START", "relative_timestamp_ms": 0},
            {"participant_name": "Alice", "event_type": "SPEAKER_END", "relative_timestamp_ms": 2000},
        ],
        "recordings": [{
            "id": 1001,
            "session_uid": "sess-1",
            "status": "completed",
            "media_files": [{
                "id": 2001,
                "type": "audio",
                "format": "wav",
                "storage_backend": "minio",
                "storage_path": "recordings/5/1001/sess-1/audio/master.wav",
                "finalized_by": "recording_finalizer.master",
            }],
        }],
    }
    data.update(overrides.pop("data", {}))
    return make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data=data,
        **overrides,
    )


@pytest.mark.asyncio
async def test_run_deferred_transcription_replace_replaces_existing_rows_after_success():
    meeting = _meeting_with_audio_master()
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(scalar_value=2),
        MockResult(scalar_value=2),
        MockResult(),
    ])
    db.commit = AsyncMock()
    added = []
    db.add = MagicMock(side_effect=added.append)
    clear_cache = AsyncMock(return_value=True)

    call_transcription = AsyncMock(return_value={
        "language": "ja",
        "segments": [{"start": 0.25, "end": 1.25, "text": "  hello final  "}],
    })
    publish_finalized = AsyncMock(return_value=True)

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=call_transcription), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=clear_cache), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=publish_finalized):
        result = await run_deferred_transcription(
            TEST_MEETING_ID,
            db,
            mode="replace",
            triggered_by="final_transcription_sweep",
        )

    assert result.segment_count == 1
    assert result.replaced_realtime_count == 2
    assert len(added) == 1
    assert isinstance(added[0], Transcription)
    assert added[0].speaker == "Alice"
    assert added[0].text == "hello final"
    assert meeting.data["speaker_events"][0]["participant_name"] == "Alice"
    assert meeting.data["final_transcription"]["status"] == "succeeded"
    assert meeting.data["final_transcription"]["source_recording_path"].endswith("/audio/master.wav")
    assert meeting.data["final_transcription"]["redis_cache_cleared"] is True
    assert meeting.data["final_transcription"]["language"] == "ja"
    assert meeting.data["drive_export"]["status"] == "queued"
    call_transcription.assert_awaited_once()
    assert call_transcription.await_args.kwargs["language"] == "ja"
    clear_cache.assert_awaited_once_with(TEST_MEETING_ID)
    publish_finalized.assert_awaited_once_with(
        TEST_MEETING_ID,
        segment_count=1,
        triggered_by="final_transcription_sweep",
    )


@pytest.mark.asyncio
async def test_run_deferred_transcription_replace_skips_when_speaker_events_missing():
    meeting = _meeting_with_audio_master(data={"speaker_events": []})
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(scalar_value=2),
        MockResult(scalar_value=1),
    ])
    db.commit = AsyncMock()
    db.add = MagicMock()

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock()) as download_audio:
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace")

    assert result.segment_count == 0
    assert result.replaced_realtime_count == 0
    assert meeting.data["final_transcription"]["status"] == "skipped_no_speaker_events"
    assert meeting.data["final_transcription"]["skipped_reason"] == "no_speaker_events"
    assert meeting.data["drive_export"]["status"] == "queued"
    db.add.assert_not_called()
    download_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_deferred_transcription_calendar_success_queues_drive_export():
    meeting = _meeting_with_audio_master(data={
        "calendar_event": {
            "source": "google_calendar",
            "title": "週次定例",
            "start_time": "2026-07-03T10:00:00+09:00",
            "meeting_url": "https://meet.google.com/abc-defg-hij",
            "platform": "google_meet",
        },
    })
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(scalar_value=2),
        MockResult(scalar_value=2),
        MockResult(),
    ])
    db.commit = AsyncMock()
    db.add = MagicMock()

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=AsyncMock(return_value={
             "language": "ja",
             "segments": [{"start": 0.25, "end": 1.25, "text": "  final  "}],
         })), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        await run_deferred_transcription(
            TEST_MEETING_ID,
            db,
            mode="replace",
            triggered_by="final_transcription_sweep",
        )

    assert meeting.data["final_transcription"]["status"] == "succeeded"
    assert meeting.data["drive_export"]["status"] == "queued"
    assert meeting.data["drive_export"]["triggered_by"] == "final_transcription_sweep"


@pytest.mark.asyncio
async def test_run_deferred_transcription_calendar_skip_queues_drive_export():
    meeting = _meeting_with_audio_master(data={
        "speaker_events": [],
        "calendar_event": {
            "source": "google_calendar",
            "title": "週次定例",
            "start_time": "2026-07-03T10:00:00+09:00",
            "meeting_url": "https://meet.google.com/abc-defg-hij",
            "platform": "google_meet",
        },
    })
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(scalar_value=2),
        MockResult(scalar_value=1),
    ])
    db.commit = AsyncMock()
    db.add = MagicMock()

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock()) as download_audio:
        result = await run_deferred_transcription(
            TEST_MEETING_ID,
            db,
            mode="replace",
            triggered_by="final_transcription_sweep",
        )

    assert result.segment_count == 0
    assert meeting.data["final_transcription"]["status"] == "skipped_no_speaker_events"
    assert meeting.data["drive_export"]["status"] == "queued"
    db.add.assert_not_called()
    download_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_deferred_transcription_force_overrides_missing_speaker_events_skip():
    meeting = _meeting_with_audio_master(data={"speaker_events": []})
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(scalar_value=2),
        MockResult(scalar_value=1),
        MockResult(scalar_value=2),
        MockResult(),
    ])
    db.commit = AsyncMock()
    db.add = MagicMock()

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=AsyncMock(return_value={
             "language": "ja",
             "segments": [{"start": 0.25, "end": 1.25, "text": "  forced final  "}],
         })), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace", force=True)

    assert result.segment_count == 1
    assert meeting.data["final_transcription"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_run_deferred_transcription_failure_keeps_existing_rows():
    meeting = _meeting_with_audio_master()
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(scalar_value=2),
    ])
    db.commit = AsyncMock()
    db.add = MagicMock()

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=AsyncMock(side_effect=HTTPException(status_code=503, detail="busy"))):
        with pytest.raises(HTTPException) as exc:
            await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace")

    assert exc.value.status_code == 503
    assert db.execute.await_count == 2
    db.add.assert_not_called()
    assert meeting.data["final_transcription"]["status"] == "failed"
    assert meeting.data["final_transcription"]["retryable"] is True


@pytest.mark.asyncio
async def test_run_deferred_transcription_missing_master_stays_queued():
    meeting = make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "transcribe_enabled": True,
            "recording_enabled": True,
            "recordings": [{
                "status": "completed",
                "media_files": [{
                    "type": "audio",
                    "format": "webm",
                    "storage_path": "recordings/5/1001/sess-1/audio/000001.webm",
                }],
            }],
        },
    )
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(scalar_value=0),
        MockResult([]),
    ])
    db.commit = AsyncMock()

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()):
        with pytest.raises(HTTPException) as exc:
            await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace")

    assert exc.value.status_code == 404
    assert meeting.data["final_transcription"]["status"] == "queued"
    assert meeting.data["final_transcription"]["last_error"] == "recording_master_not_ready"


@pytest.mark.asyncio
async def test_find_final_transcription_source_requires_finalized_audio_master():
    chunk_meeting = make_meeting(
        data={"recordings": [{"status": "completed", "media_files": [{
            "type": "audio",
            "format": "webm",
            "storage_path": "recordings/5/1001/sess-1/audio/000001.webm",
        }]}]},
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult([]))
    assert await find_final_transcription_source(chunk_meeting, db) is None

    master_meeting = _meeting_with_audio_master()
    source = await find_final_transcription_source(master_meeting, db)
    assert source is not None
    assert source.storage_path.endswith("/audio/master.wav")
    assert source.session_uid == "sess-1"


@pytest.mark.asyncio
async def test_call_transcription_service_marks_deferred_tier(monkeypatch):
    captured = {}

    class FakeResponse:
        text = "ok"
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"language": "ja", "segments": []}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, files, data, headers):
            captured.update({"url": url, "files": files, "data": data, "headers": headers})
            return FakeResponse()

    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://tx/v1/audio/transcriptions")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "secret")
    monkeypatch.setattr("meeting_api.final_transcription.httpx.AsyncClient", FakeClient)

    result = await _call_transcription_service(b"wav", "wav", language="ja")

    assert result["language"] == "ja"
    assert captured["data"]["transcription_tier"] == "deferred"
    assert captured["headers"]["X-Transcription-Tier"] == "deferred"
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_parse_segments_falls_back_to_text_only_response():
    segments, language = _parse_segments(
        {"text": "  hello from text only  ", "language": "en"},
        language=None,
        speaker_events=[
            {"participant_name": "Alice", "event_type": "SPEAKER_START", "relative_timestamp_ms": 0},
            {"participant_name": "Alice", "event_type": "SPEAKER_END", "relative_timestamp_ms": 3000},
        ],
        fallback_duration=2.5,
    )

    assert language == "en"
    assert segments == [{
        "start": 0.0,
        "end": 2.5,
        "text": "hello from text only",
        "speaker": "Alice",
    }]


def test_queue_final_transcription_sets_queued_state():
    meeting = make_meeting(
        status=MeetingStatus.COMPLETED.value,
        data={"transcribe_enabled": True, "recording_enabled": True},
    )

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()):
        changed = queue_final_transcription(meeting, triggered_by="post_meeting")

    assert changed is True
    assert meeting.data["final_transcription"]["status"] == "queued"
    assert meeting.data["final_transcription"]["attempts"] == 0
    assert meeting.data["final_transcription_status"] == "queued"


@pytest.mark.asyncio
async def test_sweep_final_transcription_jobs_runs_replace_mode():
    meeting = _meeting_with_audio_master(data={
        "final_transcription": {"status": "queued", "attempts": 0},
    })
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        FetchAllResult([(TEST_MEETING_ID,)]),
        MockResult([meeting]),
    ])
    db.commit = AsyncMock()

    @asynccontextmanager
    async def db_session_factory():
        yield db

    with patch(
        "meeting_api.final_transcription.run_deferred_transcription",
        new=AsyncMock(return_value=DeferredTranscriptionResult(
            meeting_id=TEST_MEETING_ID,
            segment_count=1,
            speakers=["Alice"],
            source_recording_path="recordings/5/1001/sess-1/audio/master.wav",
            replaced_realtime_count=2,
        )),
    ) as run:
        swept = await sweeps._sweep_final_transcription_jobs(db_session_factory)

    assert swept == 1
    run.assert_awaited_once_with(
        TEST_MEETING_ID,
        db,
        mode="replace",
        triggered_by="final_transcription_sweep",
    )


def test_deferred_transcription_endpoint_override(monkeypatch):
    from meeting_api.final_transcription import _deferred_transcription_endpoint

    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://realtime:8091/v1/audio/transcriptions")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "realtime-token")
    monkeypatch.delenv("DEFERRED_TRANSCRIPTION_SERVICE_URL", raising=False)
    monkeypatch.delenv("DEFERRED_TRANSCRIPTION_SERVICE_TOKEN", raising=False)

    # Without an override the deferred path follows the realtime endpoint.
    assert _deferred_transcription_endpoint() == (
        "http://realtime:8091/v1/audio/transcriptions", "realtime-token",
    )

    # The deferred-only override wins without touching the realtime env.
    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_SERVICE_URL", "http://soniox-capable:8092/v1/audio/transcriptions")
    url, token = _deferred_transcription_endpoint()
    assert url == "http://soniox-capable:8092/v1/audio/transcriptions"
    assert token == "realtime-token"  # token falls back independently

    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_SERVICE_TOKEN", "deferred-token")
    assert _deferred_transcription_endpoint()[1] == "deferred-token"

    # Blank override strings are ignored, not treated as configured.
    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_SERVICE_URL", "  ")
    assert _deferred_transcription_endpoint()[0] == "http://realtime:8091/v1/audio/transcriptions"
