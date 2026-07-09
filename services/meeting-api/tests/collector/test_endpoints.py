"""Issue #26 — needs_review read-time derivation for PG-persisted lane
sub-cluster segments.

`speaker_mapping_status` is NOT a DB column (ARC-3: no migration) — it is
derived at read time in collector/endpoints._get_full_transcript_segments'
PG branch from `speaker_cluster` (lane SUB-cluster shape, i.e.
"lane:{laneKey}:{cluster}") plus an empty `speaker`. This mirrors the
K_stable>=2 shared-mic branch of final_transcription._apply_lane_identity,
which forces speaker=None on every shared-mic sub-cluster segment.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from meeting_api.collector.endpoints import (
    _derive_speaker_mapping_status,
    _get_full_transcript_segments,
)


class _Result:
    """Minimal stand-in for a SQLAlchemy result — just enough for the
    `.scalars().all()` chain used by _get_full_transcript_segments."""

    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


def _pg_segment(*, speaker_cluster, speaker, segment_id):
    return SimpleNamespace(
        segment_id=segment_id,
        session_uid=None,
        start_time=0.0,
        end_time=1.0,
        text="テスト発話内容",
        language="ja",
        speaker=speaker,
        created_at=None,
        speaker_cluster=speaker_cluster,
        speaker_auto=speaker,
    )


def test_derive_speaker_mapping_status_needs_review_for_unnamed_sub_cluster():
    assert _derive_speaker_mapping_status("lane:aaaaaaaaaa:spk0", None) == "needs_review"
    assert _derive_speaker_mapping_status("lane:aaaaaaaaaa:spk0", "") == "needs_review"
    assert _derive_speaker_mapping_status("lane:aaaaaaaaaa:spk0", "   ") == "needs_review"


def test_derive_speaker_mapping_status_none_for_named_sub_cluster():
    """Once a human renames the sub-cluster (via the correction API),
    `speaker` is populated and the segment stops being flagged."""
    assert _derive_speaker_mapping_status("lane:aaaaaaaaaa:spk0", "花子") is None


def test_derive_speaker_mapping_status_none_for_solo_lane_cluster():
    """"lane:{laneKey}" (no second colon) is a SOLO lane identity, not a
    shared-mic sub-cluster — must never be flagged even with an empty
    speaker (e.g. a lane with no lane_label at all)."""
    assert _derive_speaker_mapping_status("lane:aaaaaaaaaa", None) is None


def test_derive_speaker_mapping_status_none_for_non_lane_cluster():
    assert _derive_speaker_mapping_status("mixed-cluster-1", None) is None
    assert _derive_speaker_mapping_status(None, None) is None


@pytest.mark.asyncio
async def test_pg_segment_construction_sets_needs_review_for_unnamed_sub_cluster():
    """`_get_full_transcript_segments`'s PG branch must actually thread the
    derivation through to the returned TranscriptionSegment — the field
    already exists on the schema (confirmed separately), this confirms the
    read path populates it for an unnamed lane sub-cluster and leaves it
    unset for a named one."""
    db_segments = [
        _pg_segment(speaker_cluster="lane:aaaaaaaaaa:spk0", speaker=None, segment_id="seg-1"),
        _pg_segment(speaker_cluster="lane:aaaaaaaaaa:spk1", speaker="花子", segment_id="seg-2"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Result([]),           # MeetingSession query
        _Result(db_segments),  # Transcription query
    ])
    redis_c = AsyncMock()
    redis_c.hgetall = AsyncMock(return_value={})

    segments = await _get_full_transcript_segments(1, db, redis_c)

    by_id = {s.segment_id: s for s in segments}
    assert by_id["seg-1"].speaker_mapping_status == "needs_review"
    assert by_id["seg-2"].speaker_mapping_status is None
