"""Issue #27 Phase 4 — voiceprint matching: lane-offset slicing math,
mixed/lane audio source resolution, clip selection, and the post-commit
`run_voiceprint_matching_followup` orchestration (never raises, discards
unmatched embeddings, stale-clears on replace, respects the total budget).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api import voiceprint_matching as vm
from meeting_api.models import Voiceprint, VoiceprintAuditLog
from meeting_api.voiceprint_crypto import VoiceprintCrypto

from .conftest import MockResult, make_meeting


@dataclass(frozen=True)
class _FakeMixedSource:
    storage_path: str = "recordings/5/1001/sess-1/audio/master.wav"
    media_format: str = "wav"
    storage_backend: Optional[str] = "minio"


@dataclass(frozen=True)
class _FakeLaneSource:
    lane_key: str
    storage_path: str
    media_format: str = "wav"
    storage_backend: Optional[str] = "minio"
    start_offset_seconds: float = 0.0


# ---------------------------------------------------------------------------
# cluster_local_time_ranges / resolve_cluster_audio_source
# ---------------------------------------------------------------------------


def test_cluster_local_time_ranges_mixed_cluster_is_passthrough():
    segments = [
        {"speaker_cluster": "mixed-1", "start": 10.0, "end": 13.0},
        {"speaker_cluster": "mixed-1", "start": 20.0, "end": 21.5},
        {"speaker_cluster": "other", "start": 0.0, "end": 1.0},
    ]
    ranges = vm.cluster_local_time_ranges("mixed-1", segments, offset_seconds=0.0)
    assert ranges == [(10.0, 13.0), (20.0, 21.5)]


def test_cluster_local_time_ranges_subtracts_lane_offset():
    """BUG-002/critique FC-6: segments carry MIXED-timeline times (already
    shifted +offset by final_transcription._shift_segment_times); recovering
    the lane master's own local time means subtracting that offset again."""
    segments = [
        {"speaker_cluster": "lane:aaaaaaaaaa:spk0", "start": 15.0, "end": 18.0},
    ]
    ranges = vm.cluster_local_time_ranges(
        "lane:aaaaaaaaaa:spk0", segments, offset_seconds=5.0,
    )
    assert ranges == [(10.0, 13.0)]


def test_cluster_local_time_ranges_clamps_negative_start_to_zero():
    """A segment landing before the lane's own t=0 after subtracting the
    offset (shouldn't normally happen, but must never go negative into
    ffmpeg) is clamped."""
    segments = [{"speaker_cluster": "lane:aaaaaaaaaa:spk0", "start": 2.0, "end": 6.0}]
    ranges = vm.cluster_local_time_ranges(
        "lane:aaaaaaaaaa:spk0", segments, offset_seconds=5.0,
    )
    assert ranges == [(0.0, 1.0)]


def test_cluster_local_time_ranges_skips_non_matching_cluster():
    segments = [{"speaker_cluster": "lane:aaaaaaaaaa:spk1", "start": 0.0, "end": 5.0}]
    ranges = vm.cluster_local_time_ranges("lane:aaaaaaaaaa:spk0", segments, offset_seconds=0.0)
    assert ranges == []


def test_resolve_cluster_audio_source_mixed_cluster_uses_mixed_master():
    mixed = _FakeMixedSource()
    source = vm.resolve_cluster_audio_source("mixed-1", mixed_source=mixed, lane_sources=[])
    assert source is mixed


def test_resolve_cluster_audio_source_lane_cluster_finds_matching_lane():
    lane_a = _FakeLaneSource(lane_key="aaaaaaaaaa", storage_path="recordings/5/1/sess-1/lane-aaaaaaaaaa/master.wav")
    lane_b = _FakeLaneSource(lane_key="bbbbbbbbbb", storage_path="recordings/5/1/sess-1/lane-bbbbbbbbbb/master.wav")
    source = vm.resolve_cluster_audio_source(
        "lane:bbbbbbbbbb:spk0", mixed_source=_FakeMixedSource(), lane_sources=[lane_a, lane_b],
    )
    assert source is lane_b


def test_resolve_cluster_audio_source_lane_cluster_with_no_matching_lane_returns_none():
    source = vm.resolve_cluster_audio_source(
        "lane:zzzzzzzzzz:spk0", mixed_source=_FakeMixedSource(), lane_sources=[],
    )
    assert source is None


# ---------------------------------------------------------------------------
# _select_clip_ranges — min/max clip policy
# ---------------------------------------------------------------------------


def test_select_clip_ranges_below_minimum_returns_none():
    ranges = [(0.0, 2.0)]  # 2s total < 5s minimum
    assert vm._select_clip_ranges(ranges, min_seconds=5.0, max_seconds=30.0) is None


def test_select_clip_ranges_caps_total_at_maximum():
    ranges = [(0.0, 20.0), (30.0, 50.0)]  # 40s total > 30s cap
    selected = vm._select_clip_ranges(ranges, min_seconds=5.0, max_seconds=30.0)
    total = sum(end - start for start, end in selected)
    assert total == pytest.approx(30.0)


def test_select_clip_ranges_prefers_longest_segments_first():
    ranges = [(0.0, 3.0), (10.0, 12.0), (20.0, 30.0)]  # durations 3, 2, 10
    selected = vm._select_clip_ranges(ranges, min_seconds=5.0, max_seconds=10.0)
    # Only the 10s segment (longest) is needed to reach the 10s cap.
    assert selected == [(20.0, 30.0)]


# ---------------------------------------------------------------------------
# _needs_review_clusters
# ---------------------------------------------------------------------------


def test_needs_review_clusters_only_groups_unnamed_lane_sub_clusters():
    segments = [
        {"speaker_cluster": "lane:aaaaaaaaaa:spk0", "speaker": None, "start": 0.0, "end": 3.0},
        {"speaker_cluster": "lane:aaaaaaaaaa:spk0", "speaker": None, "start": 4.0, "end": 6.0},
        {"speaker_cluster": "lane:aaaaaaaaaa:spk1", "speaker": "田中", "start": 0.0, "end": 3.0},
        {"speaker_cluster": "lane:aaaaaaaaaa", "speaker": "山森", "start": 0.0, "end": 3.0},
        {"speaker_cluster": "mixed-1", "speaker": None, "start": 0.0, "end": 3.0},
    ]
    grouped = vm._needs_review_clusters(segments)
    assert list(grouped.keys()) == ["lane:aaaaaaaaaa:spk0"]
    assert len(grouped["lane:aaaaaaaaaa:spk0"]) == 2


# ---------------------------------------------------------------------------
# _download_master_to_tempfile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_master_to_tempfile_unlinks_on_download_failure():
    """BUG-007 regression: tempfile.mkstemp creates the file on disk
    immediately, before the download call. If storage.download_file_to_path
    raises (network/credential/missing-object error), the function used to
    propagate without ever returning `path` — so embed_clip_from_ranges's
    `finally: os.unlink(src_path)` never ran (it never got a src_path value)
    and the empty temp file leaked on disk for the container's lifetime."""
    created_paths: list[str] = []

    class _FailingStorage:
        def download_file_to_path(self, storage_path, path):
            assert os.path.exists(path)  # mkstemp already created it
            created_paths.append(path)
            raise RuntimeError("simulated storage backend failure")

    with patch(
        "meeting_api.voiceprint_matching.create_storage_client",
        return_value=_FailingStorage(),
    ):
        with pytest.raises(RuntimeError):
            await vm._download_master_to_tempfile("minio", "recordings/x/master.wav", "wav")

    assert created_paths, "download_file_to_path was never called"
    assert not os.path.exists(created_paths[0]), "temp file leaked after download failure"


# ---------------------------------------------------------------------------
# run_voiceprint_matching_followup — orchestration
# ---------------------------------------------------------------------------


def _needs_review_segments():
    return [
        {"speaker_cluster": "lane:aaaaaaaaaa:spk0", "speaker": None, "start": 5.0, "end": 20.0},
    ]


def _mock_db(execute_results=None):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute_results or [])
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
async def test_followup_is_a_noop_with_no_needs_review_clusters():
    """The common case: nothing to match, and — critically — no audit noise
    written for the vast majority of meetings that have no lane shared-mic
    sub-clusters at all."""
    meeting = make_meeting(id=1, user_id=5, data={})
    db = _mock_db()

    await vm.run_voiceprint_matching_followup(
        meeting, db,
        segments=[{"speaker_cluster": "mixed-1", "speaker": "Bob", "start": 0.0, "end": 1.0}],
        mixed_source=_FakeMixedSource(),
        lane_sources=[],
        mode="reject_if_exists",
    )
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_followup_skips_and_audits_when_encryption_disabled():
    meeting = make_meeting(id=1, user_id=5, data={})
    db = _mock_db()

    with patch("meeting_api.voiceprint_matching.get_voiceprint_crypto",
               return_value=VoiceprintCrypto(key=None)):
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            segments=_needs_review_segments(),
            mixed_source=_FakeMixedSource(),
            lane_sources=[_FakeLaneSource(lane_key="aaaaaaaaaa", storage_path="x")],
            mode="reject_if_exists",
        )

    db.add.assert_called_once()
    audit = db.add.call_args[0][0]
    assert isinstance(audit, VoiceprintAuditLog)
    assert audit.event == "skip"
    assert audit.detail["reason"] == "encryption_disabled"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_followup_skips_and_audits_when_service_not_configured():
    meeting = make_meeting(id=1, user_id=5, data={})
    db = _mock_db()
    enabled_crypto = VoiceprintCrypto(key=None)
    enabled_crypto._fernet = MagicMock()  # force is_enabled() True without a real key

    with patch("meeting_api.voiceprint_matching.get_voiceprint_crypto", return_value=enabled_crypto), \
         patch("meeting_api.voiceprint_matching.VOICEPRINT_SERVICE_URL", ""):
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            segments=_needs_review_segments(),
            mixed_source=_FakeMixedSource(),
            lane_sources=[_FakeLaneSource(lane_key="aaaaaaaaaa", storage_path="x")],
            mode="reject_if_exists",
        )

    audit = db.add.call_args[0][0]
    assert audit.detail["reason"] == "service_not_configured"


@pytest.mark.asyncio
async def test_followup_never_raises_on_internal_error():
    """Plan §6 / critique FC-4/5/20: a bug inside matching must degrade to
    a skip audit, never propagate — the caller (final_transcription.py)
    relies on this to keep transcript success untouched."""
    meeting = make_meeting(id=1, user_id=5, data={})
    db = _mock_db()

    with patch("meeting_api.voiceprint_matching._needs_review_clusters", side_effect=RuntimeError("boom")):
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            segments=_needs_review_segments(),
            mixed_source=_FakeMixedSource(),
            lane_sources=[],
            mode="reject_if_exists",
        )  # must not raise

    audit = db.add.call_args[0][0]
    assert audit.event == "skip"
    assert audit.detail["reason"] == "matching_error"


@pytest.mark.asyncio
async def test_followup_times_out_and_audits_budget_exceeded():
    meeting = make_meeting(id=1, user_id=5, data={})
    db = _mock_db()

    async def _hang(*args, **kwargs):
        import asyncio
        await asyncio.sleep(10)

    with patch("meeting_api.voiceprint_matching._run_matching", new=_hang), \
         patch("meeting_api.voiceprint_matching.VOICEPRINT_MATCH_TOTAL_BUDGET_S", 0.01):
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            segments=_needs_review_segments(),
            mixed_source=_FakeMixedSource(),
            lane_sources=[],
            mode="reject_if_exists",
        )

    audit = db.add.call_args[0][0]
    assert audit.detail["reason"] == "budget_exceeded"


def _enabled_crypto():
    crypto = VoiceprintCrypto(key=None)
    crypto._fernet = MagicMock()
    return crypto


@pytest.mark.asyncio
async def test_followup_writes_suggestion_when_similarity_above_threshold():
    meeting = make_meeting(id=1, user_id=5, data={})
    db = _mock_db(execute_results=[
        MockResult([(_FakeVoiceprintRow(profile_id=7), "田中")]),  # _load_user_voiceprints
        MockResult([meeting]),  # BUG-002 fix: re-SELECT immediately before the write
    ])
    crypto = _enabled_crypto()
    crypto.decrypt_embedding = MagicMock(return_value=[1.0, 0.0, 0.0])

    with patch("meeting_api.voiceprint_matching.get_voiceprint_crypto", return_value=crypto), \
         patch("meeting_api.voiceprint_matching.VOICEPRINT_SERVICE_URL", "http://voiceprint-service"), \
         patch("meeting_api.voiceprint_matching.embed_clip_from_ranges",
               new=AsyncMock(return_value=[1.0, 0.0, 0.0])), \
         patch("meeting_api.voiceprint_matching.attributes.flag_modified", new=MagicMock()):
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            segments=_needs_review_segments(),
            mixed_source=_FakeMixedSource(),
            lane_sources=[_FakeLaneSource(lane_key="aaaaaaaaaa", storage_path="x")],
            mode="reject_if_exists",
        )

    suggestion = meeting.data["speaker_suggestions"]["lane:aaaaaaaaaa:spk0"]
    assert suggestion["candidate_display_name"] == "田中"
    assert suggestion["profile_id"] == 7
    assert suggestion["status"] == "suggested"
    assert suggestion["similarity"] == pytest.approx(1.0)

    events = [call.args[0].event for call in db.add.call_args_list]
    assert "match_attempt" in events
    assert "suggest" in events
    # The embedding itself must never appear in an audit row's detail.
    for call in db.add.call_args_list:
        detail = call.args[0].detail
        assert "embedding" not in detail
        assert "vector" not in detail


@pytest.mark.asyncio
async def test_followup_discards_embedding_when_below_threshold():
    """PII policy §2 OPEN DECISION B, 案A — an unmatched/below-threshold
    cluster's embedding must be discarded, never persisted: no Voiceprint
    row is ever added, and no suggestion is written."""
    meeting = make_meeting(id=1, user_id=5, data={})
    db = _mock_db(execute_results=[
        MockResult([(_FakeVoiceprintRow(profile_id=7), "田中")]),
    ])
    crypto = _enabled_crypto()
    crypto.decrypt_embedding = MagicMock(return_value=[1.0, 0.0, 0.0])

    with patch("meeting_api.voiceprint_matching.get_voiceprint_crypto", return_value=crypto), \
         patch("meeting_api.voiceprint_matching.VOICEPRINT_SERVICE_URL", "http://voiceprint-service"), \
         patch("meeting_api.voiceprint_matching.embed_clip_from_ranges",
               new=AsyncMock(return_value=[0.0, 1.0, 0.0])):  # orthogonal — similarity 0.0
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            segments=_needs_review_segments(),
            mixed_source=_FakeMixedSource(),
            lane_sources=[_FakeLaneSource(lane_key="aaaaaaaaaa", storage_path="x")],
            mode="reject_if_exists",
        )

    assert "speaker_suggestions" not in meeting.data or meeting.data["speaker_suggestions"] == {}
    added_types = [type(call.args[0]).__name__ for call in db.add.call_args_list]
    assert "Voiceprint" not in added_types
    events = [call.args[0].event for call in db.add.call_args_list]
    assert "match_attempt" in events
    assert "suggest" not in events


@pytest.mark.asyncio
async def test_followup_treats_all_nonfinite_scores_as_embed_failed():
    """BUG-011 regression: NaN/inf similarity scores must never win max()'s
    left-to-right fold (NaN comparisons are always False, so an
    order-dependent NaN entry could otherwise beat a legitimately higher
    real score). When EVERY candidate's score for a cluster is non-finite
    (e.g. a corrupted stored embedding, or a NaN slipping through
    _embed_clip's response parsing), the cluster must be treated as
    embed_failed — an audited skip — never a silent drop, and never a
    max()-on-empty-sequence crash."""
    meeting = make_meeting(id=1, user_id=5, data={})
    db = _mock_db(execute_results=[
        MockResult([
            (_FakeVoiceprintRow(profile_id=7, id=99), "田中"),
            (_FakeVoiceprintRow(profile_id=9, id=100), "鈴木"),
        ]),
    ])
    crypto = _enabled_crypto()
    crypto.decrypt_embedding = MagicMock(return_value=[1.0, 0.0, 0.0])

    with patch("meeting_api.voiceprint_matching.get_voiceprint_crypto", return_value=crypto), \
         patch("meeting_api.voiceprint_matching.VOICEPRINT_SERVICE_URL", "http://voiceprint-service"), \
         patch("meeting_api.voiceprint_matching.embed_clip_from_ranges",
               new=AsyncMock(return_value=[1.0, 0.0, 0.0])), \
         patch("meeting_api.voiceprint_matching._cosine_similarity", return_value=float("nan")):
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            segments=_needs_review_segments(),
            mixed_source=_FakeMixedSource(),
            lane_sources=[_FakeLaneSource(lane_key="aaaaaaaaaa", storage_path="x")],
            mode="reject_if_exists",
        )

    assert "speaker_suggestions" not in meeting.data or meeting.data["speaker_suggestions"] == {}
    events = [call.args[0].event for call in db.add.call_args_list]
    assert events == ["skip"]
    reasons = [call.args[0].detail.get("reason") for call in db.add.call_args_list]
    assert reasons == ["embed_failed"]


@pytest.mark.asyncio
async def test_followup_replace_clears_stale_suggestions_before_new_run():
    """plan §6: mode='replace' must clear a PRIOR run's suggestions in its
    own commit before writing this run's results — even when this run finds
    nothing new to suggest, the stale entry must not survive."""
    meeting = make_meeting(id=1, user_id=5, data={
        "speaker_suggestions": {
            "lane:aaaaaaaaaa:spk0": {
                "candidate_display_name": "旧候補", "profile_id": 1,
                "similarity": 0.9, "status": "suggested", "run_completed_at": "old",
            },
        },
    })
    db = _mock_db(execute_results=[
        MockResult([meeting]),  # BUG-002 fix: re-SELECT for the stale-clear write
    ])

    with patch("meeting_api.voiceprint_matching.attributes.flag_modified", new=MagicMock()):
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            # No needs-review clusters in the NEW run's segments — the old
            # cluster was presumably renamed since.
            segments=[{"speaker_cluster": "lane:aaaaaaaaaa:spk0", "speaker": "確定済み", "start": 0.0, "end": 1.0}],
            mixed_source=_FakeMixedSource(),
            lane_sources=[],
            mode="replace",
        )

    assert meeting.data["speaker_suggestions"] == {}
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_followup_write_preserves_concurrent_edit_via_fresh_reselect():
    """BUG-002 regression: a concurrent PATCH (e.g. update_meeting_speakers /
    reject_speaker_suggestion) can commit new data (speaker_corrections) via
    a DIFFERENT db session/ORM object while this long-running matching
    follow-up is still in flight on its own stale `meeting` object
    (database.py's expire_on_commit=False never refreshes it, and this run
    can take up to VOICEPRINT_MATCH_TOTAL_BUDGET_S doing network/ffmpeg
    work). The final write must re-SELECT the row immediately before
    writing and merge ONLY the speaker_suggestions key, so the concurrent
    edit survives rather than being clobbered by a full-dict overwrite
    sourced from the stale object captured at function entry."""
    meeting = make_meeting(id=1, user_id=5, data={})  # stale snapshot: no speaker_corrections
    # Simulates the row as a fresh SELECT would see it: a concurrent rename
    # landed speaker_corrections (via a different session) after `meeting`
    # was first loaded but before this run's final write.
    fresh_row = make_meeting(id=1, user_id=5, data={
        "speaker_corrections": {"lane:zzzzzzzzzz:spk0": "concurrent-rename"},
    })

    db = _mock_db(execute_results=[
        MockResult([(_FakeVoiceprintRow(profile_id=7), "田中")]),  # _load_user_voiceprints
        MockResult([fresh_row]),  # the re-SELECT immediately before the write
    ])
    crypto = _enabled_crypto()
    crypto.decrypt_embedding = MagicMock(return_value=[1.0, 0.0, 0.0])

    with patch("meeting_api.voiceprint_matching.get_voiceprint_crypto", return_value=crypto), \
         patch("meeting_api.voiceprint_matching.VOICEPRINT_SERVICE_URL", "http://voiceprint-service"), \
         patch("meeting_api.voiceprint_matching.embed_clip_from_ranges",
               new=AsyncMock(return_value=[1.0, 0.0, 0.0])), \
         patch("meeting_api.voiceprint_matching.attributes.flag_modified", new=MagicMock()):
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            segments=_needs_review_segments(),
            mixed_source=_FakeMixedSource(),
            lane_sources=[_FakeLaneSource(lane_key="aaaaaaaaaa", storage_path="x")],
            mode="reject_if_exists",
        )

    # The concurrent edit must survive the write ...
    assert fresh_row.data["speaker_corrections"] == {"lane:zzzzzzzzzz:spk0": "concurrent-rename"}
    # ... AND this run's suggestion must land, merged into the SAME fresh row.
    suggestion = fresh_row.data["speaker_suggestions"]["lane:aaaaaaaaaa:spk0"]
    assert suggestion["candidate_display_name"] == "田中"
    # The stale object captured at function entry must never be the thing
    # that gets written back.
    assert "speaker_corrections" not in (meeting.data or {})


@pytest.mark.asyncio
async def test_followup_write_does_not_resurrect_concurrently_rejected_entry():
    """Fable F1 (BUG-002 follow-up): the original re-SELECT fix still wrote
    the ENTIRE speaker_suggestions key from a value seeded at run-start —
    a stale snapshot plus this run's new entries. A concurrent DELETE
    /meetings/{id}/speaker-suggestions/{cluster_id} (reject) committed by a
    different session DURING the up-to-120s matching window pops its entry
    from the DB row; the old wholesale-write fix would resurrect it as
    "suggested" anyway. The fix must be entry-level: only THIS run's
    cluster_id -> suggestion entries are written; a rejected OLD entry
    (for a cluster this run never touches) must stay gone, while this run's
    own new suggestion still lands."""
    stale_old_entry = {
        "candidate_display_name": "旧候補", "profile_id": 1,
        "similarity": 0.9, "status": "suggested", "run_completed_at": "old",
    }
    # `meeting` is the stale object `_run_matching` was handed at entry —
    # it still sees the old, not-yet-rejected suggestion.
    meeting = make_meeting(id=1, user_id=5, data={
        "speaker_suggestions": {"lane:bbbbbbbbbb:spk0": stale_old_entry},
    })
    # `fresh_row` simulates what the re-SELECT sees: a concurrent reject
    # (different session) already popped the old cluster's entry.
    fresh_row = make_meeting(id=1, user_id=5, data={"speaker_suggestions": {}})

    db = _mock_db(execute_results=[
        MockResult([(_FakeVoiceprintRow(profile_id=7), "田中")]),  # _load_user_voiceprints
        MockResult([fresh_row]),  # the re-SELECT immediately before the final write
    ])
    crypto = _enabled_crypto()
    crypto.decrypt_embedding = MagicMock(return_value=[1.0, 0.0, 0.0])

    with patch("meeting_api.voiceprint_matching.get_voiceprint_crypto", return_value=crypto), \
         patch("meeting_api.voiceprint_matching.VOICEPRINT_SERVICE_URL", "http://voiceprint-service"), \
         patch("meeting_api.voiceprint_matching.embed_clip_from_ranges",
               new=AsyncMock(return_value=[1.0, 0.0, 0.0])), \
         patch("meeting_api.voiceprint_matching.attributes.flag_modified", new=MagicMock()):
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            # This run only reviews a DIFFERENT cluster than the rejected one.
            segments=_needs_review_segments(),
            mixed_source=_FakeMixedSource(),
            lane_sources=[_FakeLaneSource(lane_key="aaaaaaaaaa", storage_path="x")],
            mode="reject_if_exists",
        )

    final_suggestions = fresh_row.data["speaker_suggestions"]
    # The concurrently-rejected entry must NOT be resurrected.
    assert "lane:bbbbbbbbbb:spk0" not in final_suggestions
    # This run's own new suggestion must still land.
    assert final_suggestions["lane:aaaaaaaaaa:spk0"]["candidate_display_name"] == "田中"


@pytest.mark.asyncio
async def test_followup_write_does_not_resurrect_concurrently_confirmed_entry():
    """Fable F1 companion case: a concurrent confirm (the rename/merge PATCH
    path in meetings.py, which pops a pending suggestion with
    status='suggested' out of speaker_suggestions once the human accepts it
    by renaming that cluster) must also stay popped — not just reject."""
    stale_old_entry = {
        "candidate_display_name": "旧候補", "profile_id": 1,
        "similarity": 0.9, "status": "suggested", "run_completed_at": "old",
    }
    meeting = make_meeting(id=1, user_id=5, data={
        "speaker_suggestions": {"lane:bbbbbbbbbb:spk0": stale_old_entry},
    })
    # Concurrent confirm (rename) popped the entry from speaker_suggestions
    # AND recorded the accepted name elsewhere in `data` (speaker_corrections)
    # — that unrelated key must also survive the merge untouched.
    fresh_row = make_meeting(id=1, user_id=5, data={
        "speaker_suggestions": {},
        "speaker_corrections": {"clusters": {"lane:bbbbbbbbbb:spk0": "確定済み太郎"}},
    })

    db = _mock_db(execute_results=[
        MockResult([(_FakeVoiceprintRow(profile_id=7), "田中")]),
        MockResult([fresh_row]),
    ])
    crypto = _enabled_crypto()
    crypto.decrypt_embedding = MagicMock(return_value=[1.0, 0.0, 0.0])

    with patch("meeting_api.voiceprint_matching.get_voiceprint_crypto", return_value=crypto), \
         patch("meeting_api.voiceprint_matching.VOICEPRINT_SERVICE_URL", "http://voiceprint-service"), \
         patch("meeting_api.voiceprint_matching.embed_clip_from_ranges",
               new=AsyncMock(return_value=[1.0, 0.0, 0.0])), \
         patch("meeting_api.voiceprint_matching.attributes.flag_modified", new=MagicMock()):
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            segments=_needs_review_segments(),
            mixed_source=_FakeMixedSource(),
            lane_sources=[_FakeLaneSource(lane_key="aaaaaaaaaa", storage_path="x")],
            mode="reject_if_exists",
        )

    final_suggestions = fresh_row.data["speaker_suggestions"]
    assert "lane:bbbbbbbbbb:spk0" not in final_suggestions
    assert final_suggestions["lane:aaaaaaaaaa:spk0"]["candidate_display_name"] == "田中"
    # The confirm's own record elsewhere in `data` is untouched by the
    # speaker_suggestions-only merge.
    assert fresh_row.data["speaker_corrections"] == {
        "clusters": {"lane:bbbbbbbbbb:spk0": "確定済み太郎"},
    }


@pytest.mark.asyncio
async def test_followup_write_preserves_untouched_old_entry_when_no_concurrent_change():
    """Normal-path regression: when NOTHING concurrent happens, an old
    suggestion for a cluster this run doesn't touch must still be preserved
    by the entry-level merge (it is not itself a "new entry" this run
    produced, but it is present in the freshly re-read row, which the merge
    starts from) — the F1 fix must not turn into "drop everything not
    produced by this run."""
    stale_old_entry = {
        "candidate_display_name": "旧候補", "profile_id": 1,
        "similarity": 0.9, "status": "suggested", "run_completed_at": "old",
    }
    meeting = make_meeting(id=1, user_id=5, data={
        "speaker_suggestions": {"lane:bbbbbbbbbb:spk0": stale_old_entry},
    })
    # No concurrent writer — the fresh re-SELECT sees exactly what `meeting`
    # saw at entry.
    fresh_row = make_meeting(id=1, user_id=5, data={
        "speaker_suggestions": {"lane:bbbbbbbbbb:spk0": dict(stale_old_entry)},
    })

    db = _mock_db(execute_results=[
        MockResult([(_FakeVoiceprintRow(profile_id=7), "田中")]),
        MockResult([fresh_row]),
    ])
    crypto = _enabled_crypto()
    crypto.decrypt_embedding = MagicMock(return_value=[1.0, 0.0, 0.0])

    with patch("meeting_api.voiceprint_matching.get_voiceprint_crypto", return_value=crypto), \
         patch("meeting_api.voiceprint_matching.VOICEPRINT_SERVICE_URL", "http://voiceprint-service"), \
         patch("meeting_api.voiceprint_matching.embed_clip_from_ranges",
               new=AsyncMock(return_value=[1.0, 0.0, 0.0])), \
         patch("meeting_api.voiceprint_matching.attributes.flag_modified", new=MagicMock()):
        await vm.run_voiceprint_matching_followup(
            meeting, db,
            segments=_needs_review_segments(),
            mixed_source=_FakeMixedSource(),
            lane_sources=[_FakeLaneSource(lane_key="aaaaaaaaaa", storage_path="x")],
            mode="reject_if_exists",
        )

    final_suggestions = fresh_row.data["speaker_suggestions"]
    # Old, untouched entry survives ...
    assert final_suggestions["lane:bbbbbbbbbb:spk0"] == stale_old_entry
    # ... alongside this run's new entry.
    assert final_suggestions["lane:aaaaaaaaaa:spk0"]["candidate_display_name"] == "田中"


def test_voiceprint_matching_reads_prefixed_budget_and_timeout_env_vars():
    """BUG-003 regression: docker-compose.yml / deploy/env-example only set
    and document VOICEPRINT_MATCH_TOTAL_BUDGET_S / VOICEPRINT_EMBED_TIMEOUT_S
    — an unprefixed os.getenv read silently ignores any operator override.
    Import the module fresh in a subprocess under a controlled env to prove
    the prefixed names are what's actually read (not just present unused
    elsewhere). A subprocess — not importlib.reload — is deliberate: reload
    would rebind every class/exception this module defines (e.g.
    VoiceprintServiceUnavailable) to a NEW object, silently breaking
    `except VoiceprintServiceUnavailable` in voiceprints.py (which imported
    the ORIGINAL class at its own module-load time) for the rest of the test
    session."""
    import subprocess
    import sys

    script = (
        "import meeting_api.voiceprint_matching as vm; "
        "print(vm.VOICEPRINT_MATCH_TOTAL_BUDGET_S, vm.VOICEPRINT_EMBED_TIMEOUT_S)"
    )
    env = dict(os.environ, VOICEPRINT_MATCH_TOTAL_BUDGET_S="77", VOICEPRINT_EMBED_TIMEOUT_S="9")
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["77.0", "9.0"]


class _FakeVoiceprintRow:
    """Minimal Voiceprint-shaped stand-in for the (Voiceprint, display_name)
    join row consumed by _load_user_voiceprints."""

    def __init__(self, *, profile_id, embedding_dim=3, id=99):
        self.profile_id = profile_id
        self.embedding_dim = embedding_dim
        self.id = id
        self.embedding_encrypted = b"cipher"
