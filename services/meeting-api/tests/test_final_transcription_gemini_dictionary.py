from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from meeting_api.final_transcription import FinalTranscriptionSource, ProviderTranscriptionError, run_deferred_transcription
from meeting_api.models import Transcription
from meeting_api.schemas import MeetingStatus

from .conftest import TEST_MEETING_ID, MockResult, make_meeting


def _meeting():
    return make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data={
            "transcribe_enabled": True,
            "recording_enabled": True,
            "speaker_events": [],
            "recordings": [{
                "id": 1,
                "session_uid": "s1",
                "status": "completed",
                "media_files": [{
                    "type": "audio", "format": "wav", "storage_path": "master.wav",
                    "storage_backend": "minio", "finalized_by": "recording_finalizer.master",
                }],
            }],
        },
    )


@pytest.mark.asyncio
async def test_gemini_snapshots_owner_dictionary_once_and_sends_prompt(monkeypatch):
    meeting = _meeting()
    term = SimpleNamespace(term="Bonginkan", reading="ボンギンカン")
    db = AsyncMock()
    async def execute(statement, *args, **kwargs):
        sql = str(statement)
        if "transcription_dictionary_terms" in sql:
            return MockResult([term])
        if "count(transcriptions.id)" in sql:
            return MockResult(scalar_value=0)
        if "FROM meetings" in sql:
            return MockResult([meeting])
        return MockResult()
    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    db.add = MagicMock()
    call = AsyncMock(return_value={
        "language": "ja",
        "segments": [{"start": 0, "end": 1, "text": "Bonginkan", "speaker": "g:abcd:s1"}],
    })
    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_MODEL", "gemini-3.5-flash")
    with patch("meeting_api.final_transcription.attributes.flag_modified", MagicMock()), \
         patch("meeting_api.final_transcription.find_final_transcription_source", AsyncMock(return_value=FinalTranscriptionSource("master.wav", "wav"))), \
         patch("meeting_api.final_transcription._download_recording_audio", AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", call), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", AsyncMock()), \
         patch("meeting_api.final_transcription.queue_drive_export_if_needed", MagicMock()), \
         patch("meeting_api.voiceprint_matching.run_voiceprint_matching_followup", AsyncMock()):
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace")
    assert result.segment_count == 1
    assert call.call_count == 1
    assert "Bonginkan" in call.await_args.kwargs["prompt"]
    assert meeting.data["final_transcription"]["dictionary_term_count"] == 1
    assert meeting.data["final_transcription"]["requested_model"] == "gemini-3.5-flash"


@pytest.mark.asyncio
async def test_gemini_uses_only_mixed_master_when_lane_media_exists(monkeypatch):
    meeting = _meeting()
    meeting.data["recordings"][0]["media_files"].append({
        "type": "lane-participant-1",
        "format": "wav",
        "storage_path": "lane-participant-1/master.wav",
        "storage_backend": "minio",
        "finalized_by": "recording_finalizer.master",
        "lane": {
            "lane_id": "participant-1",
            "lane_label": "参加者1",
            "lane_id_source": "participant-id",
        },
    })
    term = SimpleNamespace(term="Bonginkan", reading="ボンギンカン")
    dictionary_queries = 0
    db = AsyncMock()

    async def execute(statement, *args, **kwargs):
        nonlocal dictionary_queries
        sql = str(statement)
        if "transcription_dictionary_terms" in sql:
            dictionary_queries += 1
            return MockResult([term])
        if "count(transcriptions.id)" in sql:
            return MockResult(scalar_value=0)
        if "FROM meetings" in sql:
            return MockResult([meeting])
        return MockResult()

    added = []
    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    db.add = MagicMock(side_effect=lambda value: added.append(value))
    call = AsyncMock(return_value={
        "language": "ja",
        "segments": [
            {"start": 0, "end": 3, "text": "一人目", "speaker": "g:aaaaaaaa:s1"},
            {"start": 4, "end": 7, "text": "二人目", "speaker": "g:bbbbbbbb:s1"},
        ],
    })
    matching = AsyncMock()
    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_MODEL", "gemini-3.5-flash")
    with patch("meeting_api.final_transcription.attributes.flag_modified", MagicMock()), \
         patch("meeting_api.final_transcription.find_final_transcription_source", AsyncMock(return_value=FinalTranscriptionSource("master.wav", "wav"))), \
         patch("meeting_api.final_transcription._lane_master_sources", side_effect=AssertionError("Gemini must not inspect lane masters")) as lanes, \
         patch("meeting_api.final_transcription._transcribe_lanes", AsyncMock()) as transcribe_lanes, \
         patch("meeting_api.final_transcription._download_recording_audio", AsyncMock(return_value=b"wav")) as download, \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", call), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", AsyncMock()), \
         patch("meeting_api.final_transcription.queue_drive_export_if_needed", MagicMock()), \
         patch("meeting_api.voiceprint_matching.run_voiceprint_matching_followup", matching):
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace")

    assert result.segment_count == 2
    assert dictionary_queries == 1
    assert lanes.call_count == 0
    assert transcribe_lanes.await_count == 0
    assert download.await_count == 1
    assert call.await_count == 1
    assert "Bonginkan" in call.await_args.kwargs["prompt"]
    stored = [value for value in added if isinstance(value, Transcription)]
    assert {value.speaker_cluster for value in stored} == {"g:aaaaaaaa:s1", "g:bbbbbbbb:s1"}
    assert all(not str(value.speaker_cluster).startswith("lane:") for value in stored)
    state = meeting.data["final_transcription"]
    assert state["source"] == "deferred_recording_master"
    assert state["lane_count"] == 0
    assert state["shared_mic_lanes"] == []
    assert matching.await_args.kwargs["lane_sources"] == []


@pytest.mark.asyncio
async def test_gemini_replace_guard_does_not_inspect_lane_masters(monkeypatch):
    meeting = _meeting()
    meeting.data["recordings"][0]["media_files"].append({
        "type": "lane-participant-1",
        "format": "wav",
        "storage_path": "lane-participant-1/master.wav",
        "storage_backend": "minio",
        "finalized_by": "recording_finalizer.master",
        "lane": {"lane_id": "participant-1", "lane_label": "参加者1"},
    })
    db = AsyncMock()

    async def execute(statement, *args, **kwargs):
        sql = str(statement)
        if "count(transcriptions.id)" in sql:
            return MockResult(scalar_value=1)
        if "FROM meetings" in sql:
            return MockResult([meeting])
        return MockResult()

    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    skipped = SimpleNamespace(segment_count=1)
    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_MODEL", "gemini-3.5-flash")
    with patch("meeting_api.final_transcription._lane_master_sources", side_effect=AssertionError("Gemini must not inspect lane masters")) as lanes, \
         patch("meeting_api.final_transcription._has_meaningful_existing_speakers", AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._skip_no_speaker_events", AsyncMock(return_value=skipped)) as skip:
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace")

    assert result is skipped
    assert lanes.call_count == 0
    skip.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_gemini_result_is_terminal_and_not_retryable(monkeypatch):
    meeting = _meeting()
    db = AsyncMock()
    async def execute(statement, *args, **kwargs):
        sql = str(statement)
        if "transcription_dictionary_terms" in sql:
            return MockResult([])
        if "count(transcriptions.id)" in sql:
            return MockResult(scalar_value=0)
        if "FROM meetings" in sql:
            return MockResult([meeting])
        return MockResult()
    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_MODEL", "gemini-3.5-flash")
    call = AsyncMock(side_effect=ProviderTranscriptionError(
        409, "unknown", code="unknown_manual_reconcile",
    ))
    with patch("meeting_api.final_transcription.attributes.flag_modified", MagicMock()), \
         patch("meeting_api.final_transcription.find_final_transcription_source", AsyncMock(return_value=FinalTranscriptionSource("master.wav", "wav"))), \
         patch("meeting_api.final_transcription._download_recording_audio", AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", call):
        with pytest.raises(HTTPException):
            await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace")
    state = meeting.data["final_transcription"]
    assert state["status"] == "unknown_manual_reconcile"
    assert state["retryable"] is False
    assert state["error_code"] == "unknown_manual_reconcile"
    assert state["provider_started_at"] is not None
    assert call.call_count == 1


@pytest.mark.asyncio
async def test_gemini_admission_timeout_is_retryable_and_clears_provider_started_marker(monkeypatch):
    meeting = _meeting()
    db = AsyncMock()

    async def execute(statement, *args, **kwargs):
        sql = str(statement)
        if "transcription_dictionary_terms" in sql:
            return MockResult([])
        if "count(transcriptions.id)" in sql:
            return MockResult(scalar_value=0)
        if "FROM meetings" in sql:
            return MockResult([meeting])
        return MockResult()

    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    monkeypatch.setenv("DEFERRED_TRANSCRIPTION_MODEL", "gemini-3.5-flash")
    call = AsyncMock(side_effect=ProviderTranscriptionError(
        503,
        "capacity wait expired before provider start",
        code="admission_timeout",
    ))
    with patch("meeting_api.final_transcription.attributes.flag_modified", MagicMock()), \
         patch("meeting_api.final_transcription.find_final_transcription_source", AsyncMock(return_value=FinalTranscriptionSource("master.wav", "wav"))), \
         patch("meeting_api.final_transcription._download_recording_audio", AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", call):
        with pytest.raises(HTTPException):
            await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace")

    state = meeting.data["final_transcription"]
    assert state["status"] == "failed"
    assert state["retryable"] is True
    assert state["error_code"] == "admission_timeout"
    assert state["provider_started_at"] is None
