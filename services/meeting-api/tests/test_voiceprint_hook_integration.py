"""Issue #27 Phase 4 — the voiceprint matching follow-up is wired into
`run_deferred_transcription` as a POST-COMMIT step that can never affect
the function's own success/failure result (plan §6, Codex critique
FC-4/5/20)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api.final_transcription import run_deferred_transcription
from meeting_api.schemas import MeetingStatus

from .conftest import TEST_MEETING_ID, MockResult, make_meeting


def _meeting_with_audio_master(**overrides):
    data = {
        "transcribe_enabled": True,
        "recording_enabled": True,
        "speaker_events": [],
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
    return make_meeting(id=TEST_MEETING_ID, status=MeetingStatus.COMPLETED.value, data=data, **overrides)


@pytest.mark.asyncio
async def test_transcript_succeeds_even_when_voiceprint_followup_raises():
    """A bug inside the followup (even one that escapes its own internal
    catch-all, defense in depth) must not turn a successful deferred
    transcription into a failure."""
    meeting = _meeting_with_audio_master()
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(scalar_value=0),
    ])
    db.commit = AsyncMock()
    db.add = MagicMock()

    call_transcription = AsyncMock(return_value={
        "language": "ja",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
    })

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=call_transcription), \
         patch("meeting_api.voiceprint_matching.run_voiceprint_matching_followup",
               new=AsyncMock(side_effect=RuntimeError("followup exploded"))):
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    assert result.segment_count == 1
    assert meeting.data["final_transcription"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_followup_called_with_final_segments_and_sources_after_commit():
    meeting = _meeting_with_audio_master()
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(scalar_value=0),
    ])
    commit_order = []
    db.commit = AsyncMock(side_effect=lambda: commit_order.append("transcript_commit"))
    db.add = MagicMock()

    call_transcription = AsyncMock(return_value={
        "language": "ja",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
    })
    followup = AsyncMock(side_effect=lambda *a, **kw: commit_order.append("followup_called"))

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=call_transcription), \
         patch("meeting_api.voiceprint_matching.run_voiceprint_matching_followup", new=followup):
        await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    # The success commit happens BEFORE the follow-up is invoked.
    assert commit_order[0] == "transcript_commit"
    assert "followup_called" in commit_order

    kwargs = followup.await_args.kwargs
    assert kwargs["mode"] == "reject_if_exists"
    assert kwargs["segments"][0]["text"] == "hello"
    assert kwargs["mixed_source"].storage_path.endswith("/audio/master.wav")
    assert kwargs["lane_sources"] == []
