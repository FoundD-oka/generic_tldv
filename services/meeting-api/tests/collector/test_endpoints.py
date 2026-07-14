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

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from meeting_api.collector.endpoints import (
    _derive_speaker_mapping_status,
    _get_full_transcript_segments,
    _overlay_speaker_suggestions,
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


@pytest.mark.asyncio
async def test_redis_segment_derives_needs_review_status_as_fallback():
    """BUG-005 — the Redis (live) merge branch must not just trust
    `d.get("speaker_mapping_status")`; it must derive the same way the PG
    branch does, so an unnamed lane sub-cluster id that lands in Redis is
    still flagged needs_review even though nothing wrote the wire field
    (today; the derivation must not silently stop working if that changes)."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Result([]),  # MeetingSession query
        _Result([]),  # Transcription query
    ])
    redis_c = AsyncMock()
    redis_c.hgetall = AsyncMock(return_value={
        "seg-r1": json.dumps({
            "segment_id": "seg-r1",
            "text": "テスト発話内容",
            "start_time": 0.0,
            "end_time": 1.0,
            "speaker": None,
            "speaker_cluster": "lane:aaaaaaaaaa:spk0",
        }),
    })

    segments = await _get_full_transcript_segments(1, db, redis_c)

    by_id = {s.segment_id: s for s in segments}
    assert by_id["seg-r1"].speaker_mapping_status == "needs_review"


@pytest.mark.asyncio
async def test_redis_segment_keeps_explicit_wire_status_when_derivation_is_none():
    """An explicitly-set wire value must survive when derivation itself has
    nothing to say (e.g. a non-lane speaker_cluster) — derive-first,
    trust-the-wire-as-fallback, not derive-only."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _Result([]),
        _Result([]),
    ])
    redis_c = AsyncMock()
    redis_c.hgetall = AsyncMock(return_value={
        "seg-r2": json.dumps({
            "segment_id": "seg-r2",
            "text": "テスト発話内容2",
            "start_time": 0.0,
            "end_time": 1.0,
            "speaker": "山田",
            "speaker_cluster": "mixed-cluster-1",
            "speaker_mapping_status": "some_future_status",
        }),
    })

    segments = await _get_full_transcript_segments(1, db, redis_c)

    by_id = {s.segment_id: s for s in segments}
    assert by_id["seg-r2"].speaker_mapping_status == "some_future_status"


# ---------------------------------------------------------------------------
# Issue #27 Phase 4 — voiceprint suggestion overlay
# ---------------------------------------------------------------------------


def _suggestion_payload(**overrides):
    payload = {
        "candidate_display_name": "田中",
        "profile_id": 42,
        "similarity": 0.83,
        "status": "suggested",
        "run_completed_at": "2026-07-10T00:00:00",
    }
    payload.update(overrides)
    return payload


def test_overlay_adds_minimal_payload_without_profile_id():
    """plan §6 露出制御 — profile_id must never reach the segment-level
    payload, only candidate_display_name/similarity/status."""
    from meeting_api.schemas import TranscriptionSegment

    seg = TranscriptionSegment(
        start=0.0, end=1.0, text="hi", language="ja",
        speaker=None, speaker_cluster="lane:aaaaaaaaaa:spk0",
        speaker_mapping_status="needs_review",
    )
    _overlay_speaker_suggestions([seg], {"lane:aaaaaaaaaa:spk0": _suggestion_payload()})

    assert seg.speaker_suggestion == {
        "candidate_display_name": "田中",
        "similarity": 0.83,
        "status": "suggested",
    }
    assert "profile_id" not in seg.speaker_suggestion


def test_overlay_adds_suggestion_to_unconfirmed_gemini_cluster():
    from meeting_api.schemas import TranscriptionSegment

    seg = TranscriptionSegment(
        start=0.0, end=1.0, text="hi", language="ja",
        speaker="Unknown", speaker_cluster="g:78225710:s1",
    )
    _overlay_speaker_suggestions([seg], {"g:78225710:s1": _suggestion_payload()})

    assert seg.speaker_suggestion == {
        "candidate_display_name": "田中",
        "similarity": 0.83,
        "status": "suggested",
    }


def test_overlay_skips_named_or_malformed_gemini_cluster():
    from meeting_api.schemas import TranscriptionSegment

    named = TranscriptionSegment(
        start=0.0, end=1.0, text="hi", language="ja",
        speaker="田中", speaker_cluster="g:78225710:s1",
    )
    malformed = TranscriptionSegment(
        start=1.0, end=2.0, text="hi", language="ja",
        speaker="Unknown", speaker_cluster="g:not-hex:s2",
    )
    _overlay_speaker_suggestions(
        [named, malformed],
        {
            "g:78225710:s1": _suggestion_payload(),
            "g:not-hex:s2": _suggestion_payload(),
        },
    )

    assert named.speaker_suggestion is None
    assert malformed.speaker_suggestion is None


def test_overlay_skips_segment_not_needing_review():
    from meeting_api.schemas import TranscriptionSegment

    seg = TranscriptionSegment(
        start=0.0, end=1.0, text="hi", language="ja",
        speaker="花子", speaker_cluster="lane:aaaaaaaaaa:spk0",
        speaker_mapping_status=None,
    )
    _overlay_speaker_suggestions([seg], {"lane:aaaaaaaaaa:spk0": _suggestion_payload()})
    assert seg.speaker_suggestion is None


def test_overlay_skips_rejected_or_confirmed_entries():
    from meeting_api.schemas import TranscriptionSegment

    seg = TranscriptionSegment(
        start=0.0, end=1.0, text="hi", language="ja",
        speaker=None, speaker_cluster="lane:aaaaaaaaaa:spk0",
        speaker_mapping_status="needs_review",
    )
    _overlay_speaker_suggestions(
        [seg], {"lane:aaaaaaaaaa:spk0": _suggestion_payload(status="rejected")},
    )
    assert seg.speaker_suggestion is None


@pytest.mark.asyncio
async def test_overlay_applies_through_pg_only_path():
    """Overlay runs AFTER the PG/Redis merge, so it must apply to a
    PG-persisted segment when Redis has nothing live for it."""
    db_segments = [
        _pg_segment(speaker_cluster="lane:aaaaaaaaaa:spk0", speaker=None, segment_id="seg-1"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_Result([]), _Result(db_segments)])
    redis_c = AsyncMock()
    redis_c.hgetall = AsyncMock(return_value={})

    meeting = SimpleNamespace(data={
        "speaker_suggestions": {"lane:aaaaaaaaaa:spk0": _suggestion_payload()},
    })

    segments = await _get_full_transcript_segments(1, db, redis_c, meeting=meeting)
    by_id = {s.segment_id: s for s in segments}
    assert by_id["seg-1"].speaker_suggestion["candidate_display_name"] == "田中"


@pytest.mark.asyncio
async def test_overlay_applies_to_gemini_cluster_through_pg_path():
    db_segments = [
        _pg_segment(speaker_cluster="g:78225710:s1", speaker="Unknown", segment_id="seg-g1"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_Result([]), _Result(db_segments)])
    redis_c = AsyncMock()
    redis_c.hgetall = AsyncMock(return_value={})
    meeting = SimpleNamespace(data={
        "speaker_suggestions": {"g:78225710:s1": _suggestion_payload()},
    })

    segments = await _get_full_transcript_segments(1, db, redis_c, meeting=meeting)

    assert segments[0].speaker_mapping_status is None
    assert segments[0].speaker_suggestion["candidate_display_name"] == "田中"


@pytest.mark.asyncio
async def test_overlay_survives_redis_wins_merge():
    """Codex critique FC-8: Redis wins over PG on the same segment_id — the
    overlay must still apply to whatever the FINAL merged segment is, not
    just the PG branch."""
    db_segments = [
        _pg_segment(speaker_cluster="lane:aaaaaaaaaa:spk0", speaker=None, segment_id="seg-1"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_Result([]), _Result(db_segments)])
    redis_c = AsyncMock()
    redis_c.hgetall = AsyncMock(return_value={
        "seg-1": json.dumps({
            "segment_id": "seg-1",
            "text": "ライブ更新テキスト",
            "start_time": 0.0,
            "end_time": 1.0,
            "speaker": None,
            "speaker_cluster": "lane:aaaaaaaaaa:spk0",
        }),
    })

    meeting = SimpleNamespace(data={
        "speaker_suggestions": {"lane:aaaaaaaaaa:spk0": _suggestion_payload()},
    })

    segments = await _get_full_transcript_segments(1, db, redis_c, meeting=meeting)
    by_id = {s.segment_id: s for s in segments}
    # Redis-wins: the text is the live one, but the suggestion still overlays.
    assert by_id["seg-1"].text == "ライブ更新テキスト"
    assert by_id["seg-1"].speaker_suggestion["candidate_display_name"] == "田中"


@pytest.mark.asyncio
async def test_overlay_is_noop_without_meeting_argument():
    """Backward compatibility: callers that don't pass `meeting` (none
    currently, but the parameter is optional) get the old behavior."""
    db_segments = [
        _pg_segment(speaker_cluster="lane:aaaaaaaaaa:spk0", speaker=None, segment_id="seg-1"),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_Result([]), _Result(db_segments)])
    redis_c = AsyncMock()
    redis_c.hgetall = AsyncMock(return_value={})

    segments = await _get_full_transcript_segments(1, db, redis_c)
    by_id = {s.segment_id: s for s in segments}
    assert by_id["seg-1"].speaker_suggestion is None
