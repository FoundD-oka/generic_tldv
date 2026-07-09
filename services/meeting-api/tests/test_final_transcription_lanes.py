"""Issue #25 (Phase 2 audio lanes) / Issue #26 (Phase 3 shared-mic
detection) — deferred lane-STT tests.

Verification-contract items covered here:

* lane master discovery from JSONB (lane metadata carried through)
* solo lane → auto-confirm: speaker_cluster="lane:{laneKey}",
  speaker=speaker_auto=lane_label, segment_id carries the laneKey
* all-or-nothing: any lane failure → full fallback to the mixed-master
  path with zero lane segments in the output
* duration budget exceeded → same full fallback
* saved cluster corrections keyed "lane:{laneKey}" override the lane label
  (user corrections win; auto label preserved in speaker_auto)
* issue #26 P3-AC1/AC5: 2 stable clusters in one lane → shared mic,
  sub-cluster ids kept, speaker/speaker_auto forced None even where the
  DOM vote would have named the cluster, shared_mic_lanes recorded
* issue #26 P3-AC3/AC4: an unstable (too short/too few tokens) cluster
  never creates a false split — solo treatment, and never counted toward
  K_stable — while a genuine 2-stable-cluster lane keeps unstable
  clusters as their OWN needs_review sub-cluster instead of absorbing them
* issue #26 P3-AC2: a saved rename for "lane:{key}:{cluster}" still applies
  to only that sub-cluster after shared-mic processing
"""

from __future__ import annotations

import os
import subprocess
import sys
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


def _meeting_with_single_lane(*, lane_label: str | None = "山森", corrections: dict | None = None,
                               speaker_events: list | None = None):
    """Issue #26 — a ONE-lane meeting (no lane B), so a multi-cluster STT
    result unambiguously describes that single lane's own K_stable case
    without needing to filter out a second lane sharing the same STT mock
    return value (see _meeting_with_lanes' two-lane fixture)."""
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
                    "lane": {"lane_id": "t1", "lane_label": lane_label,
                             "lane_id_source": "participant-id"},
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
    lane-relative offset math still lines up.

    Issue #26 AC5 (behavior change from Phase 2, per approved plan v2):
    both clusters here are long enough (3s each, >= the default 2.0s
    stability threshold) to count as STABLE, so K_stable=2 — this is now a
    SHARED MIC lane. Where Phase 2 let the DOM vote name the first cluster
    "山森" (asserted below to confirm the vote WOULD still match — the DOM
    event overlaps only the first segment), Phase 3 discards that DOM name
    entirely for shared-mic sub-clusters: `speaker` must be None on BOTH
    segments. This test now covers BUG-002 offset-shifting plus the AC5
    DOM-discard guarantee in one fixture.
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
                # Lane-relative 0s-3s (stable: 3s >= 2.0s threshold) —
                # overlaps the shifted (lane-local) DOM event at 0s-1s once
                # offset is correctly subtracted, so the DOM vote WOULD name
                # this cluster "山森" if it were consulted.
                {"start": 0.0, "end": 3.0, "text": "こんにちは、今日はよろしくお願いします", "speaker": "spk0"},
                # Lane-relative 4s-7s (stable: 3s) — no DOM overlap either way.
                {"start": 4.0, "end": 7.0, "text": "別の発言者からの発話内容です", "speaker": "spk1"},
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
    assert first.end_time == pytest.approx(8.0)
    assert first.speaker is None, "AC5 — shared-mic sub-cluster speaker is None even though DOM vote would have matched 山森"
    assert first.speaker_auto is None, "speaker_auto must not resurrect the discarded DOM name either"
    assert second.start_time == pytest.approx(9.0)
    assert second.end_time == pytest.approx(12.0)
    assert second.speaker is None
    assert LANE_A_KEY in meeting.data["final_transcription"]["shared_mic_lanes"]


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
async def test_lane_in_other_session_does_not_bypass_no_speaker_events_guard():
    """F1 (Fable consultation): the BUG-011 re-guard used to require
    lane_fallback_reason to be set, but a session-asymmetric meeting never
    sets it. Here the finalized lane masters live ONLY in a DIFFERENT
    recording/session than the chosen mixed audio master: the meeting-wide
    pre-flight check (_lane_masters_available) sees the other session's
    lanes and lets the run proceed, but the session-scoped lane lookup used
    for the actual transcription finds nothing and raises no
    LaneTranscriptionFallback (lane_fallback_reason stays None). Without the
    fix, the mixed path would then run with empty speaker_events in
    mode="replace" and clobber meaningful existing speaker labels with
    "Unknown". The fixed guard must still skip."""
    other_lane_key = "cccccccccc"
    data = {
        "transcribe_enabled": True,
        "recording_enabled": True,
        "speaker_events": [],
        "recordings": [
            {
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
                ],
            },
            {
                "id": 1002,
                "session_uid": "sess-2",
                "status": "completed",
                "media_files": [
                    {
                        "id": 2002, "type": f"lane-{other_lane_key}", "format": "wav",
                        "storage_backend": "minio",
                        "storage_path": f"recordings/5/1002/sess-2/lane-{other_lane_key}/master.wav",
                        "finalized_by": "recording_finalizer.master",
                        "lane": {"lane_id": "t9", "lane_label": "他セッションの人",
                                 "lane_id_source": "participant-id"},
                    },
                ],
            },
        ],
    }
    meeting = make_meeting(id=TEST_MEETING_ID, status=MeetingStatus.COMPLETED.value, data=data)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),      # meeting select
        MockResult(scalar_value=2),  # existing_count
    ])
    db.commit = AsyncMock()
    db.add = MagicMock()
    mixed_stt = AsyncMock()

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._has_meaningful_existing_speakers",
               new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock()), \
         patch("meeting_api.final_transcription._call_transcription_service", new=mixed_stt):
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace")

    mixed_stt.assert_not_awaited(), (
        "mixed path must not run — the lane path was unavailable only "
        "because the finalized lanes belong to a different session, not "
        "because of a real lane_fallback_reason"
    )
    db.add.assert_not_called()
    assert result.segment_count == 0
    state = meeting.data["final_transcription"]
    assert state["status"] == "skipped_no_speaker_events"
    assert state["skipped_reason"] == "no_speaker_events"
    assert state.get("lane_fallback_reason") is None, (
        "sanity check: this bypass is exactly the case where no "
        "LaneTranscriptionFallback was ever raised"
    )


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


@pytest.mark.asyncio
async def test_shared_mic_two_stable_clusters_speaker_none_and_recorded():
    """Issue #26 P3-AC1/AC5: a lane with 2 stable clusters (each long enough
    to clear LANE_SHARED_MIC_MIN_CLUSTER_DURATION_S) is a shared-mic lane.
    Both sub-clusters keep their own namespaced id; `speaker`/`speaker_auto`
    are None on ALL of the lane's segments — including the one the DOM vote
    WOULD have named "山森" — because AC5 forbids a DOM guess from ever
    surfacing as a sub-speaker's identity. `shared_mic_lanes` records the
    lane in the success state for downstream consumers."""
    speaker_events = [
        {"participant_name": "山森", "event_type": "SPEAKER_START", "relative_timestamp_ms": 1000},
        {"participant_name": "山森", "event_type": "SPEAKER_END", "relative_timestamp_ms": 2000},
    ]
    meeting = _meeting_with_single_lane(speaker_events=speaker_events)
    db = _db_for(meeting)
    added: list[Transcription] = []
    db.add = MagicMock(side_effect=added.append)

    async def fake_stt(audio, fmt, *, language):
        return {
            "language": "ja",
            "segments": [
                # 3s — stable, and overlaps the 1s-2s DOM event above, so the
                # DOM vote WOULD name this cluster "山森" if consulted.
                {"start": 0.0, "end": 3.0, "text": "こんにちは、今日はよろしくお願いします", "speaker": "spk0"},
                # 3s — stable, no DOM overlap.
                {"start": 4.0, "end": 7.0, "text": "別の発言者からの発話内容です", "speaker": "spk1"},
            ],
        }

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=AsyncMock(side_effect=fake_stt)), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    assert {t.speaker_cluster for t in added} == {
        f"lane:{LANE_A_KEY}:spk0", f"lane:{LANE_A_KEY}:spk1",
    }
    assert all(t.speaker is None for t in added), "AC5 — DOM name discarded on every shared-mic segment"
    assert all(t.speaker_auto is None for t in added), "speaker_auto must not resurrect the discarded DOM name"
    state = meeting.data["final_transcription"]
    assert state["shared_mic_lanes"] == [LANE_A_KEY]


@pytest.mark.asyncio
async def test_shared_mic_lane_namespaces_clusterless_segment_instead_of_blanking_it():
    """BUG-001 regression: a lane with 2 stable diarized clusters (shared
    mic) can ALSO contain a segment Soniox could not diarize at all (no
    `speaker` tag from the STT backend). Before the fix, the unconditional
    `seg["speaker"] = None` in the shared-mic branch collapsed that
    segment's identity to speaker_cluster=None/speaker=None — the same
    blank "" identity key used meeting-wide for every other unattributed
    segment, with no needs_review affordance to fix it. After the fix it
    must get its OWN namespaced "lane:{laneKey}:unclustered" sub-cluster id
    (still with speaker=None, per AC5) so it stays distinct and reviewable."""
    meeting = _meeting_with_single_lane()
    db = _db_for(meeting)
    added: list[Transcription] = []
    db.add = MagicMock(side_effect=added.append)

    async def fake_stt(audio, fmt, *, language):
        return {
            "language": "ja",
            "segments": [
                # 2 stable diarized clusters — this makes the lane shared-mic.
                {"start": 0.0, "end": 3.0, "text": "こんにちは、今日はよろしくお願いします", "speaker": "spk0"},
                {"start": 4.0, "end": 7.0, "text": "別の発言者からの発話内容です", "speaker": "spk1"},
                # No `speaker` key at all — Soniox could not diarize this
                # one. has_clusters stays True (spk0/spk1 carry the tag),
                # so this segment comes out of _parse_segments with
                # speaker_cluster=None, speaker="Unknown".
                {"start": 8.0, "end": 9.0, "text": "聞き取れない発話"},
            ],
        }

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=AsyncMock(side_effect=fake_stt)), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    assert len(added) == 3
    by_cluster = {t.speaker_cluster: t for t in added}
    assert set(by_cluster) == {
        f"lane:{LANE_A_KEY}:spk0",
        f"lane:{LANE_A_KEY}:spk1",
        f"lane:{LANE_A_KEY}:unclustered",
    }
    unclustered = by_cluster[f"lane:{LANE_A_KEY}:unclustered"]
    assert unclustered.speaker is None, "AC5 — still no guessed identity, just like a named sub-cluster"
    assert unclustered.speaker_auto is None
    # The other two segments are unaffected by this fix.
    assert all(t.speaker is None for t in added)
    state = meeting.data["final_transcription"]
    assert state["shared_mic_lanes"] == [LANE_A_KEY]


@pytest.mark.asyncio
async def test_solo_lane_with_tiny_noise_cluster_does_not_split():
    """Issue #26 P3-AC3 (false-split guard): a lane with one genuine
    (stable) cluster plus a tiny one-word interjection (0.3s, well under
    the 2.0s duration threshold) must NOT become a shared-mic lane —
    K_stable=1 because the tiny cluster never counts. ALL segments,
    including the tiny-cluster one, take the lane's single auto-confirmed
    identity, exactly like the Phase 2 solo path."""
    meeting = _meeting_with_single_lane(lane_label="山森")
    db = _db_for(meeting)
    added: list[Transcription] = []
    db.add = MagicMock(side_effect=added.append)

    async def fake_stt(audio, fmt, *, language):
        return {
            "language": "ja",
            "segments": [
                {"start": 0.0, "end": 5.0, "text": "本当に長い発言です、テスト用の十分な発話内容です", "speaker": "spk0"},
                # Stray one-word interjection — far under the duration bar.
                {"start": 5.0, "end": 5.3, "text": "うん", "speaker": "spk1"},
            ],
        }

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=AsyncMock(side_effect=fake_stt)), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    assert len(added) == 2
    assert all(t.speaker_cluster == f"lane:{LANE_A_KEY}" for t in added), (
        "no sub-cluster split — the tiny cluster is never counted toward K_stable, "
        "and is never silently absorbed into spk0 either (B-2): it just rides the solo lane"
    )
    assert all(t.speaker == "山森" for t in added)
    state = meeting.data["final_transcription"]
    assert state.get("shared_mic_lanes") == []


@pytest.mark.asyncio
async def test_all_tiny_clusters_fall_back_to_solo():
    """Issue #26 P3-AC4 (no-stable-cluster edge case): when EVERY cluster in
    the lane is under threshold, K_stable=0, which is still <= 1 → solo
    lane treatment (never an unresolved/ambiguous state, never a guessed
    name)."""
    meeting = _meeting_with_single_lane(lane_label="山森")
    db = _db_for(meeting)
    added: list[Transcription] = []
    db.add = MagicMock(side_effect=added.append)

    async def fake_stt(audio, fmt, *, language):
        return {
            "language": "ja",
            "segments": [
                {"start": 0.0, "end": 0.5, "text": "うん", "speaker": "spk0"},
                {"start": 0.6, "end": 1.0, "text": "はい", "speaker": "spk1"},
            ],
        }

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=AsyncMock(side_effect=fake_stt)), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    assert len(added) == 2
    assert all(t.speaker_cluster == f"lane:{LANE_A_KEY}" for t in added)
    assert all(t.speaker == "山森" for t in added)
    assert all(t.speaker is not None for t in added), "AC4 — never leaves speaker as a guessed value, but also never as an ambiguous unset here"


@pytest.mark.asyncio
async def test_saved_correction_for_subcluster_applies_after_shared_mic_processing():
    """Issue #26 P3-AC2 (rename path): a saved rename keyed
    "lane:{laneKey}:{cluster}" must still apply to ONLY that sub-cluster
    after shared-mic processing forced speaker=None on every segment of the
    lane — the correction re-apply loop runs after _apply_lane_identity, so
    it overwrites the None for the renamed sub-cluster while the other
    sub-cluster (uncorrected) stays None."""
    meeting = _meeting_with_single_lane(corrections={f"lane:{LANE_A_KEY}:spk0": "花子"})
    db = _db_for(meeting)
    added: list[Transcription] = []
    db.add = MagicMock(side_effect=added.append)

    async def fake_stt(audio, fmt, *, language):
        return {
            "language": "ja",
            "segments": [
                {"start": 0.0, "end": 3.0, "text": "こんにちは、今日はよろしくお願いします", "speaker": "spk0"},
                {"start": 4.0, "end": 7.0, "text": "別の発言者からの発話内容です", "speaker": "spk1"},
            ],
        }

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=AsyncMock(side_effect=fake_stt)), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock()):
        await run_deferred_transcription(TEST_MEETING_ID, db, mode="reject_if_exists")

    by_cluster = {t.speaker_cluster: t for t in added}
    renamed = by_cluster[f"lane:{LANE_A_KEY}:spk0"]
    other = by_cluster[f"lane:{LANE_A_KEY}:spk1"]
    assert renamed.speaker == "花子", "saved rename still targets only its own sub-cluster"
    assert renamed.speaker_auto is None, "auto baseline stays the discarded (None) DOM name, not the correction"
    assert other.speaker is None, "the OTHER sub-cluster is untouched by a rename scoped to spk0 only"


def test_lane_shared_mic_min_cluster_tokens_accepts_decimal_env_value():
    """BUG-004 regression: LANE_SHARED_MIC_MIN_CLUSTER_TOKENS is parsed at
    module IMPORT time, so this must be a fresh-process import (a
    monkeypatched env var + importlib.reload in-process would mutate the
    shared module namespace other test modules already hold references
    into). A plausible-looking decimal value (by analogy with the
    float-typed LANE_SHARED_MIC_MIN_CLUSTER_DURATION_S sibling) must not
    crash the whole process with ValueError at startup."""
    env = dict(os.environ, LANE_SHARED_MIC_MIN_CLUSTER_TOKENS="5.0")
    result = subprocess.run(
        [
            sys.executable, "-c",
            "from meeting_api.final_transcription import LANE_SHARED_MIC_MIN_CLUSTER_TOKENS as v; print(v)",
        ],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "5"
