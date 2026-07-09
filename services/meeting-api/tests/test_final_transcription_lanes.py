"""Issue #25 (Phase 2 audio lanes) — deferred lane-STT tests.

Verification-contract items covered here:

* lane master discovery from JSONB (lane metadata carried through)
* solo lane → auto-confirm: speaker_cluster="lane:{laneKey}",
  speaker=speaker_auto=lane_label, segment_id carries the laneKey
* all-or-nothing: any lane failure → full fallback to the mixed-master
  path with zero lane segments in the output
* duration budget exceeded → same full fallback
* saved cluster corrections keyed "lane:{laneKey}" override the lane label
  (user corrections win; auto label preserved in speaker_auto)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api.final_transcription import (
    _lane_master_sources,
    run_deferred_transcription,
)
from meeting_api.models import Transcription
from meeting_api.schemas import MeetingStatus

from .conftest import TEST_MEETING_ID, MockResult, make_meeting

LANE_A_KEY = "aaaaaaaaaa"
LANE_B_KEY = "bbbbbbbbbb"
BASE = "recordings/5/1001/sess-1"


def _meeting_with_lanes(*, corrections: dict | None = None):
    data = {
        "transcribe_enabled": True,
        "recording_enabled": True,
        "speaker_events": [],
        "recordings": [{
            "id": 1001,
            "session_uid": "sess-1",
            "status": "completed",
            "media_files": [
                {
                    "id": 2001, "type": "audio", "format": "wav",
                    "storage_backend": "minio",
                    "storage_path": f"{BASE}/audio/master.wav",
                    "finalized_by": "recording_finalizer.master",
                },
                {
                    "id": 2002, "type": f"lane-{LANE_A_KEY}", "format": "wav",
                    "storage_backend": "minio",
                    "storage_path": f"{BASE}/lane-{LANE_A_KEY}/master.wav",
                    "finalized_by": "recording_finalizer.master",
                    "lane": {"lane_id": "t1", "lane_label": "山森",
                             "lane_id_source": "participant-id"},
                },
                {
                    "id": 2003, "type": f"lane-{LANE_B_KEY}", "format": "wav",
                    "storage_backend": "minio",
                    "storage_path": f"{BASE}/lane-{LANE_B_KEY}/master.wav",
                    "finalized_by": "recording_finalizer.master",
                    "lane": {"lane_id": "t2", "lane_label": "岡田健一",
                             "lane_id_source": "generated"},
                },
            ],
        }],
    }
    if corrections:
        data["speaker_corrections"] = {"clusters": corrections}
    return make_meeting(
        id=TEST_MEETING_ID,
        status=MeetingStatus.COMPLETED.value,
        data=data,
    )


def _db_for(meeting, existing_count=0):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(scalar_value=existing_count),
        MockResult(scalar_value=existing_count),
        MockResult(),
    ])
    db.commit = AsyncMock()
    return db


def test_lane_master_sources_discovery():
    meeting = _meeting_with_lanes()
    sources = _lane_master_sources(meeting)
    assert [s.lane_key for s in sources] == [LANE_A_KEY, LANE_B_KEY]
    assert sources[0].lane_label == "山森"
    assert sources[1].lane_id_source == "generated"
    # unfinalized lane masters are ignored
    meeting.data["recordings"][0]["media_files"][1]["finalized_by"] = None
    assert [s.lane_key for s in _lane_master_sources(meeting)] == [LANE_B_KEY]


@pytest.mark.asyncio
async def test_lane_solo_auto_confirm_and_segment_ids():
    meeting = _meeting_with_lanes()
    db = _db_for(meeting)
    added: list[Transcription] = []
    db.add = MagicMock(side_effect=added.append)

    # Lane STT results carry no diarization clusters → each lane is solo.
    async def fake_stt(audio, fmt, *, language):
        return {"language": "ja",
                "segments": [{"start": 0.0, "end": 1.0, "text": "こんにちは"}]}

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=AsyncMock(side_effect=fake_stt)) as stt, \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    assert stt.await_count == 2, "one STT call per lane, no mixed-master call"
    assert result.segment_count == 2
    by_cluster = {t.speaker_cluster: t for t in added}
    lane_a = by_cluster[f"lane:{LANE_A_KEY}"]
    assert lane_a.speaker == "山森"
    assert lane_a.speaker_auto == "山森"
    assert f"lane-{LANE_A_KEY}" in lane_a.segment_id
    lane_b = by_cluster[f"lane:{LANE_B_KEY}"]
    assert lane_b.speaker == "岡田健一", "gm-id (generated) lanes auto-confirm too (user decision)"
    state = meeting.data["final_transcription"]
    assert state["source"] == "deferred_lane_masters"
    assert state["lane_count"] == 2
    assert sorted(state["lane_keys"]) == [LANE_A_KEY, LANE_B_KEY]


@pytest.mark.asyncio
async def test_lane_failure_falls_back_to_mixed_master_entirely():
    meeting = _meeting_with_lanes()
    db = _db_for(meeting)
    added: list[Transcription] = []
    db.add = MagicMock(side_effect=added.append)

    async def download(source):
        if "lane-" in source.storage_path:
            raise RuntimeError("lane object missing")
        return b"wav"

    mixed_stt = AsyncMock(return_value={
        "language": "ja",
        "segments": [{"start": 0.0, "end": 1.0, "text": "混合master経由"}],
    })

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(side_effect=download)), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=mixed_stt), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    mixed_stt.assert_awaited_once()
    assert result.segment_count == 1
    assert all(not (t.speaker_cluster or "").startswith("lane:") for t in added), (
        "all-or-nothing: after lane failure, NO lane-derived segment may appear"
    )
    state = meeting.data["final_transcription"]
    assert state["source"] == "deferred_recording_master"
    assert "lane" in (state["lane_fallback_reason"] or "")


@pytest.mark.asyncio
async def test_lane_budget_exceeded_falls_back():
    meeting = _meeting_with_lanes()
    db = _db_for(meeting)
    db.add = MagicMock()

    stt = AsyncMock(return_value={
        "language": "ja",
        "segments": [{"start": 0.0, "end": 1.0, "text": "混合"}],
    })

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._audio_duration_seconds", return_value=3 * 3600.0), \
         patch("meeting_api.final_transcription._call_transcription_service", new=stt), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    # 2 lanes × 3h = 6h > 4h cap → lanes abandoned BEFORE any lane STT call;
    # the single call is the mixed master.
    stt.assert_awaited_once()
    state = meeting.data["final_transcription"]
    assert "budget" in (state["lane_fallback_reason"] or "")


@pytest.mark.asyncio
async def test_saved_lane_cluster_corrections_win_over_lane_label():
    meeting = _meeting_with_lanes(
        corrections={f"lane:{LANE_A_KEY}": "訂正済みの名前"})
    db = _db_for(meeting)
    added: list[Transcription] = []
    db.add = MagicMock(side_effect=added.append)

    stt = AsyncMock(return_value={
        "language": "ja",
        "segments": [{"start": 0.0, "end": 1.0, "text": "テスト"}],
    })

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=stt), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    lane_a = next(t for t in added if t.speaker_cluster == f"lane:{LANE_A_KEY}")
    assert lane_a.speaker == "訂正済みの名前", "saved user correction wins"
    assert lane_a.speaker_auto == "山森", "auto lane label preserved for undo"
