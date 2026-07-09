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
    LaneTranscriptionFallback,
    _lane_master_sources,
    run_deferred_transcription,
)
from meeting_api.models import Transcription
from meeting_api.schemas import MeetingStatus

from .conftest import TEST_MEETING_ID, MockResult, make_meeting

LANE_A_KEY = "aaaaaaaaaa"
LANE_B_KEY = "bbbbbbbbbb"
BASE = "recordings/5/1001/sess-1"


def _meeting_with_lanes(*, corrections: dict | None = None, lane_a_offset_ms: int | None = None,
                         speaker_events: list | None = None):
    lane_a = {"lane_id": "t1", "lane_label": "山森", "lane_id_source": "participant-id"}
    if lane_a_offset_ms is not None:
        lane_a["lane_start_offset_ms"] = lane_a_offset_ms
    data = {
        "transcribe_enabled": True,
        "recording_enabled": True,
        "speaker_events": speaker_events if speaker_events is not None else [],
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
                    "lane": lane_a,
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


def test_lane_master_sources_restricted_to_matching_session():
    """BUG-012/BUG-023: lanes from a different recording/session must not
    be pulled onto this recording's transcript."""
    meeting = _meeting_with_lanes()
    assert [s.lane_key for s in _lane_master_sources(meeting, recording_session_uid="sess-1")] == [
        LANE_A_KEY, LANE_B_KEY,
    ]
    assert _lane_master_sources(meeting, recording_session_uid="sess-other") == []


def test_lane_master_sources_all_or_nothing_on_unfinalized_lane():
    """BUG-012: one unfinalized lane makes the WHOLE lane path unavailable —
    it must never silently transcribe a subset and drop a participant."""
    meeting = _meeting_with_lanes()
    meeting.data["recordings"][0]["media_files"][1]["finalized_by"] = None
    with pytest.raises(LaneTranscriptionFallback):
        _lane_master_sources(meeting)


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


@pytest.mark.asyncio
async def test_lane_start_offset_shifts_segments_onto_master_timeline():
    """BUG-002: lane A joined 5s (5000ms) after the mixed recording started.
    Its STT segments are lane-relative (start at 0.0) and must land on the
    master timeline shifted by +5s; the speaker_events fed into DOM
    cluster-naming must be shifted by -5s (into lane A's own clock) so the
    vote lines up with lane-relative segment times. Uses a MULTI-cluster STT
    result so the DOM vote's name (not the solo-lane auto-confirm override)
    is what actually lands on the stored row.
    """
    # Absolute (master-relative) speaker_events: 山森 speaks 5s-6s.
    speaker_events = [
        {"participant_name": "山森", "event_type": "SPEAKER_START", "relative_timestamp_ms": 5000},
        {"participant_name": "山森", "event_type": "SPEAKER_END", "relative_timestamp_ms": 6000},
    ]
    meeting = _meeting_with_lanes(lane_a_offset_ms=5000, speaker_events=speaker_events)
    db = _db_for(meeting)
    added: list[Transcription] = []
    db.add = MagicMock(side_effect=added.append)

    async def fake_stt(audio, fmt, *, language):
        return {
            "language": "ja",
            "segments": [
                # Lane-relative 0s-1s — overlaps the shifted (lane-local)
                # DOM event at 0s-1s once offset is correctly subtracted.
                {"start": 0.0, "end": 1.0, "text": "こんにちは", "speaker": "spk0"},
                # Lane-relative 2s-3s — no DOM overlap either way; stays Unknown.
                {"start": 2.0, "end": 3.0, "text": "別の発言", "speaker": "spk1"},
            ],
        }

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=AsyncMock(side_effect=fake_stt)), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    lane_a_segs = sorted(
        (t for t in added if t.speaker_cluster and t.speaker_cluster.startswith(f"lane:{LANE_A_KEY}:")),
        key=lambda t: t.start_time,
    )
    assert len(lane_a_segs) == 2
    first, second = lane_a_segs
    assert first.start_time == pytest.approx(5.0), "segment shifted +5s onto the master timeline"
    assert first.end_time == pytest.approx(6.0)
    assert first.speaker == "山森", "DOM vote matched because speaker_events were shifted -5s to lane-local time"
    assert second.start_time == pytest.approx(7.0)
    assert second.speaker == "Unknown"


@pytest.mark.asyncio
async def test_lane_fallback_with_meaningful_speakers_skips_instead_of_mixed():
    """BUG-011: lane STT fails after the pre-flight guard let the run
    proceed (because usable lane masters existed at that point). The mixed
    path must NOT run in this case — it would delete meaningful existing
    speaker labels and rewrite them mostly as "Unknown" with no
    speaker_events. Same skip as the pre-flight no-speaker-events guard."""
    meeting = _meeting_with_lanes()
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        MockResult(scalar_value=2),   # existing_count
        MockResult(scalar_value=1),   # _has_meaningful_existing_speakers
    ])
    db.commit = AsyncMock()
    db.add = MagicMock()
    mixed_stt = AsyncMock()

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio",
               new=AsyncMock(side_effect=RuntimeError("lane object missing"))), \
         patch("meeting_api.final_transcription._call_transcription_service", new=mixed_stt):
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace")

    mixed_stt.assert_not_awaited(), "mixed path must not run — it would destroy good speaker labels"
    db.add.assert_not_called()
    assert result.segment_count == 0
    state = meeting.data["final_transcription"]
    assert state["status"] == "skipped_no_speaker_events"
    assert state["skipped_reason"] == "no_speaker_events"


@pytest.mark.asyncio
async def test_lane_segments_stamped_with_own_lane_session_uid():
    """BUG-023: persisted rows for a lane segment carry that LANE's own
    recording session_uid, not the mixed source's — needed for multi-session
    (bot rejoin) meetings where lanes and the chosen mixed master could
    belong to different sessions."""
    meeting = _meeting_with_lanes()
    db = _db_for(meeting)
    added: list[Transcription] = []
    db.add = MagicMock(side_effect=added.append)

    async def fake_stt(audio, fmt, *, language):
        return {"language": "ja",
                "segments": [{"start": 0.0, "end": 1.0, "text": "こんにちは"}]}

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=AsyncMock(side_effect=fake_stt)), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    assert added, "expected lane segments to be stored"
    assert all(t.session_uid == "sess-1" for t in added)
    state = meeting.data["final_transcription"]
    assert sorted(state["source_lane_paths"]) == sorted([
        f"{BASE}/lane-{LANE_A_KEY}/master.wav",
        f"{BASE}/lane-{LANE_B_KEY}/master.wav",
    ])
