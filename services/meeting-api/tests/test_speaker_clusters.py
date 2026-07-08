"""Phase 1a — speaker cluster adoption + DOM-vote naming (issues #21/#22).

Verification contract: P1-AC1 (clusters survive DOM), P1-AC2 (online
migration shape), P1-AC3 (whole-cluster weighted vote beats per-segment
jitter), P1-AC7 (replace re-applies saved manual corrections).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api.final_transcription import (
    _parse_segments,
    _saved_cluster_corrections,
    map_speakers_to_segments,
    name_clusters_by_dom_vote,
    run_deferred_transcription,
)
from meeting_api.models import Transcription
from meeting_api.schemas import MeetingStatus

from .conftest import TEST_MEETING_ID, MockResult, make_meeting


def _events(*ranges):
    """ranges: (name, start_s, end_s) → DOM SPEAKER_START/END event pairs."""
    events = []
    for name, start, end in ranges:
        events.append({
            "participant_name": name,
            "event_type": "SPEAKER_START",
            "relative_timestamp_ms": int(start * 1000),
        })
        events.append({
            "participant_name": name,
            "event_type": "SPEAKER_END",
            "relative_timestamp_ms": int(end * 1000),
        })
    return events


# ---------------------------------------------------------------------------
# P1-AC1 — diarization clusters survive even when speaker_events exist
# ---------------------------------------------------------------------------

def test_parse_segments_keeps_cluster_ids_when_speaker_events_present():
    tx_result = {
        "language": "ja",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "こんにちは", "speaker": "1"},
            {"start": 2.0, "end": 4.0, "text": "どうも", "speaker": "2"},
            {"start": 4.0, "end": 6.0, "text": "続けます", "speaker": "1"},
        ],
    }
    speaker_events = _events(("Alice", 0.0, 2.0), ("Bob", 2.0, 4.0), ("Alice", 4.0, 6.0))

    segments, _ = _parse_segments(
        tx_result, language="ja", speaker_events=speaker_events,
    )

    assert [s["speaker_cluster"] for s in segments] == ["1", "2", "1"]
    assert [s["speaker"] for s in segments] == ["Alice", "Bob", "Alice"]


def test_parse_segments_falls_back_to_dom_mapping_without_clusters():
    tx_result = {
        "language": "ja",
        "segments": [{"start": 0.0, "end": 2.0, "text": "こんにちは"}],
    }
    speaker_events = _events(("Alice", 0.0, 2.0))
    segments, _ = _parse_segments(tx_result, language="ja", speaker_events=speaker_events)
    assert segments[0]["speaker"] == "Alice"
    assert segments[0].get("speaker_cluster") is None


def test_parse_segments_clusters_without_dom_events_stay_unknown_but_distinct():
    tx_result = {
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "a", "speaker": "1"},
            {"start": 1.0, "end": 2.0, "text": "b", "speaker": "2"},
        ],
    }
    segments, _ = _parse_segments(tx_result, language="ja", speaker_events=[])
    assert [s["speaker"] for s in segments] == ["Unknown", "Unknown"]
    assert [s["speaker_cluster"] for s in segments] == ["1", "2"]


# ---------------------------------------------------------------------------
# P1-AC3 — whole-cluster weighted vote is stable where per-segment flips
# ---------------------------------------------------------------------------

def test_cluster_vote_stable_where_per_segment_mapping_flips():
    # Cluster "1" = one voice. DOM jitter: Bob's tile briefly lights up over
    # the tail segment, so per-segment overlap flips that segment to Bob.
    segments = [
        {"start": 0.0, "end": 2.0, "text": "s1", "speaker_cluster": "1"},
        {"start": 2.0, "end": 4.0, "text": "s2", "speaker_cluster": "1"},
        {"start": 5.4, "end": 6.2, "text": "s3", "speaker_cluster": "1"},
    ]
    speaker_events = _events(("Alice", 0.0, 5.5), ("Bob", 5.3, 6.2))

    # Per-segment mapping (legacy) flips s3 to Bob…
    legacy = [dict(s) for s in segments]
    map_speakers_to_segments(speaker_events, legacy)
    assert legacy[2]["speaker"] == "Bob"

    # …but the whole-cluster vote stays Alice (4.1s vs 0.9s of overlap).
    names = name_clusters_by_dom_vote(segments, speaker_events)
    assert names == {"1": "Alice"}


def test_cluster_vote_below_overlap_ratio_returns_unknown():
    segments = [{"start": 0.0, "end": 10.0, "text": "long", "speaker_cluster": "1"}]
    speaker_events = _events(("Alice", 0.0, 1.0))  # 10% coverage < 20% threshold
    names = name_clusters_by_dom_vote(segments, speaker_events)
    assert names == {"1": "Unknown"}


def test_cluster_vote_tie_breaks_deterministically_by_name():
    segments = [{"start": 0.0, "end": 4.0, "text": "x", "speaker_cluster": "1"}]
    speaker_events = _events(("Zoe", 0.0, 2.0), ("Alice", 2.0, 4.0))
    names = name_clusters_by_dom_vote(segments, speaker_events)
    assert names == {"1": "Alice"}


def test_cluster_vote_never_names_a_cluster_unknown_by_vote():
    segments = [{"start": 0.0, "end": 4.0, "text": "x", "speaker_cluster": "1"}]
    speaker_events = _events(("Unknown", 0.0, 4.0))
    names = name_clusters_by_dom_vote(segments, speaker_events)
    assert names == {"1": "Unknown"}


# ---------------------------------------------------------------------------
# P1-AC7 (cluster side) — saved corrections re-applied on replace
# ---------------------------------------------------------------------------

def _meeting_with_audio_master(extra_data=None):
    data = {
        "transcribe_enabled": True,
        "recording_enabled": True,
        "speaker_events": _events(("Alice", 0.0, 2.0), ("Bob", 2.0, 4.0)),
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
    data.update(extra_data or {})
    return make_meeting(id=TEST_MEETING_ID, status=MeetingStatus.COMPLETED.value, data=data)


def test_saved_cluster_corrections_reads_meeting_data():
    meeting = _meeting_with_audio_master(
        {"speaker_corrections": {"clusters": {"1": "田中", "2": "  ", "3": 5}}}
    )
    assert _saved_cluster_corrections(meeting) == {"1": "田中"}


@pytest.mark.asyncio
async def test_replace_reapplies_manual_cluster_corrections():
    meeting = _meeting_with_audio_master(
        {"speaker_corrections": {"clusters": {"1": "田中"}}}
    )
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

    call_transcription = AsyncMock(return_value={
        "language": "ja",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": "こんにちは", "speaker": "1"},
            {"start": 2.0, "end": 4.0, "text": "どうも", "speaker": "2"},
        ],
    })

    with patch("meeting_api.final_transcription.attributes.flag_modified", new=MagicMock()), \
         patch("meeting_api.final_transcription._download_recording_audio", new=AsyncMock(return_value=b"wav")), \
         patch("meeting_api.final_transcription._convert_audio_to_wav", return_value=(b"wav", "wav")), \
         patch("meeting_api.final_transcription._call_transcription_service", new=call_transcription), \
         patch("meeting_api.final_transcription._clear_live_transcript_cache", new=AsyncMock(return_value=True)), \
         patch("meeting_api.final_transcription._publish_transcript_finalized", new=AsyncMock(return_value=True)):
        result = await run_deferred_transcription(TEST_MEETING_ID, db, mode="replace")

    assert result.segment_count == 2
    assert len(added) == 2
    assert all(isinstance(row, Transcription) for row in added)
    # Cluster "1": auto vote says Alice, manual correction 田中 wins; undo kept.
    assert added[0].speaker == "田中"
    assert added[0].speaker_auto == "Alice"
    assert added[0].speaker_cluster == "1"
    # Cluster "2": untouched by corrections.
    assert added[1].speaker == "Bob"
    assert added[1].speaker_auto == "Bob"
    assert added[1].speaker_cluster == "2"
    assert sorted(result.speakers) == ["Bob", "田中"]


# ---------------------------------------------------------------------------
# P1-AC2 — online migration script shape (no plain locking ALTER/INDEX)
# ---------------------------------------------------------------------------

def _load_migration_module():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts" / "migrations" / "20260708_add_speaker_cluster.py"
    )
    spec = importlib.util.spec_from_file_location("add_speaker_cluster_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_statements_are_online_safe():
    mig = _load_migration_module()
    # Nullable adds (metadata-only), no NOT NULL / DEFAULT rewrite.
    for stmt in mig.ADD_COLUMNS:
        assert "NOT NULL" not in stmt
        assert "DEFAULT" not in stmt
        assert "IF NOT EXISTS" in stmt
    # Index must be CONCURRENTLY, and rollback path exists (down).
    assert "CONCURRENTLY" in mig.CREATE_INDEX
    assert "CONCURRENTLY" in mig.DROP_INDEX
    assert any("DROP COLUMN" in stmt for stmt in mig.DROP_COLUMNS)
    # Backfill is scoped by id range (batched), never a full-table UPDATE.
    assert "id >= %s AND id < %s" in mig.BACKFILL


def test_migration_batch_ranges_cover_id_space_exactly():
    mig = _load_migration_module()
    ranges = list(mig.batch_ranges(1, 45, 20))
    assert ranges == [(1, 21), (21, 41), (41, 61)]
    assert list(mig.batch_ranges(5, 4, 10)) == []
