"""Phase 1b — speaker bulk-correction API (issue #23).

Verification contract: P1-AC4 (rename/merge/reassign + undo baseline),
P1-AC5 (Redis live cache invalidated AFTER commit), P1-AC6 (done Drive
export re-queued), P1-AC11 (auth).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import TEST_MEETING_ID, MockResult, make_meeting


class UpdateResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


def _completed_meeting(**data_overrides):
    data = {"speaker_events": []}
    data.update(data_overrides)
    return make_meeting(id=TEST_MEETING_ID, status="completed", data=data)


def _patch_flags():
    return (
        patch("meeting_api.meetings.attributes.flag_modified", MagicMock()),
        patch("meeting_api.drive_export.attributes.flag_modified", MagicMock()),
    )


@pytest.mark.asyncio
async def test_rename_by_cluster_updates_rows_and_saves_correction(client, mock_db):
    meeting = _completed_meeting()
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),          # ownership select
        UpdateResult(rowcount=3),        # rename UPDATE
        MockResult([("田中",), ("Bob",)]),  # distinct speakers
    ])
    clear_cache = AsyncMock(return_value=True)

    p1, p2 = _patch_flags()
    with p1, p2, patch(
        "meeting_api.final_transcription._clear_live_transcript_cache", new=clear_cache
    ):
        resp = await client.patch(
            f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
            json={"rename": [{"from_cluster": "1", "to_name": "田中"}]},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == {"rename": 3, "merge": 0, "reassign": 0}
    assert body["speakers"] == ["Bob", "田中"]
    assert body["redis_cache_cleared"] is True
    clear_cache.assert_awaited_once_with(TEST_MEETING_ID)

    # Correction persisted for replace re-application (P1-AC7 input).
    assert meeting.data["speaker_corrections"]["clusters"] == {"1": "田中"}
    assert meeting.data["speaker_corrections"]["history"][0]["op"] == "rename"

    # The UPDATE preserves the undo baseline: speaker_auto = COALESCE(auto, speaker).
    update_stmt = mock_db.execute.await_args_list[1].args[0]
    compiled = str(update_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "coalesce" in compiled.lower()
    assert "speaker_cluster = '1'" in compiled


@pytest.mark.asyncio
async def test_rename_by_name_supports_legacy_rows_without_cluster(client, mock_db):
    meeting = _completed_meeting()
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        UpdateResult(rowcount=2),
        MockResult([("鈴木",)]),
    ])
    p1, p2 = _patch_flags()
    with p1, p2, patch(
        "meeting_api.final_transcription._clear_live_transcript_cache",
        new=AsyncMock(return_value=True),
    ):
        resp = await client.patch(
            f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
            json={"rename": [{"from_name": "Unknown", "to_name": "鈴木"}]},
        )
    assert resp.status_code == 200
    assert resp.json()["updated"]["rename"] == 2
    # Label-only rename must not pollute the cluster corrections map.
    assert meeting.data["speaker_corrections"]["clusters"] == {}


@pytest.mark.asyncio
async def test_merge_clusters_records_all_source_clusters(client, mock_db):
    meeting = _completed_meeting()
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        UpdateResult(rowcount=5),
        MockResult([("佐藤",)]),
    ])
    p1, p2 = _patch_flags()
    with p1, p2, patch(
        "meeting_api.final_transcription._clear_live_transcript_cache",
        new=AsyncMock(return_value=True),
    ):
        resp = await client.patch(
            f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
            json={"merge": [{"clusters": ["1", "3"], "to_name": "佐藤"}]},
        )
    assert resp.status_code == 200
    assert resp.json()["updated"]["merge"] == 5
    # Both source clusters map to the merged name so replace restores the merge.
    assert meeting.data["speaker_corrections"]["clusters"] == {"1": "佐藤", "3": "佐藤"}

    update_stmt = mock_db.execute.await_args_list[1].args[0]
    compiled = str(update_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "speaker_cluster IN ('1', '3')" in compiled
    assert "speaker_cluster='1'" in compiled.replace(" ", "")  # representative id


@pytest.mark.asyncio
async def test_reassign_targets_specific_segment_ids(client, mock_db):
    meeting = _completed_meeting()
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        UpdateResult(rowcount=2),
        MockResult([("山田",)]),
    ])
    p1, p2 = _patch_flags()
    with p1, p2, patch(
        "meeting_api.final_transcription._clear_live_transcript_cache",
        new=AsyncMock(return_value=True),
    ):
        resp = await client.patch(
            f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
            json={"reassign": [{
                "segment_ids": ["deferred:42:5:10.000", "deferred:42:6:12.000"],
                "to_name": "山田",
                "to_cluster": "2",
            }]},
        )
    assert resp.status_code == 200
    assert resp.json()["updated"]["reassign"] == 2
    update_stmt = mock_db.execute.await_args_list[1].args[0]
    compiled = str(update_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "segment_id IN" in compiled
    assert "deferred:42:5:10.000" in compiled


@pytest.mark.asyncio
async def test_redis_invalidation_happens_after_commit(client, mock_db):
    """P1-AC5 ordering: DB commit BEFORE dropping the Redis live-segments hash."""
    meeting = _completed_meeting()
    order = []
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        UpdateResult(rowcount=1),
        MockResult([("田中",)]),
    ])
    mock_db.commit = AsyncMock(side_effect=lambda: order.append("commit"))

    fake_redis = AsyncMock()
    fake_redis.delete = AsyncMock(side_effect=lambda key: order.append(("redis_delete", key)))

    p1, p2 = _patch_flags()
    with p1, p2, patch("meeting_api.meetings.get_redis", return_value=fake_redis, create=True):
        resp = await client.patch(
            f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
            json={"rename": [{"from_cluster": "1", "to_name": "田中"}]},
        )

    assert resp.status_code == 200
    assert order[0] == "commit"
    assert ("redis_delete", f"meeting:{TEST_MEETING_ID}:segments") in order


@pytest.mark.asyncio
async def test_done_drive_export_is_requeued_on_rename(client, mock_db):
    """P1-AC6: status==done does not block re-export; file_id is preserved."""
    meeting = _completed_meeting(
        drive_export={"status": "done", "file_id": "drive-file-1", "attempts": 1},
        drive_export_status="done",
    )
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        UpdateResult(rowcount=1),
        MockResult([("田中",)]),
    ])
    p1, p2 = _patch_flags()
    with p1, p2, patch(
        "meeting_api.final_transcription._clear_live_transcript_cache",
        new=AsyncMock(return_value=True),
    ):
        resp = await client.patch(
            f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
            json={"rename": [{"from_cluster": "1", "to_name": "田中"}]},
        )
    assert resp.status_code == 200
    assert resp.json()["drive_export_requeued"] is True
    drive_state = meeting.data["drive_export"]
    assert drive_state["status"] == "queued"
    assert drive_state["requeued_from"] == "done"
    assert drive_state["file_id"] == "drive-file-1"  # updated in place, not duplicated
    assert drive_state["attempts"] == 0


@pytest.mark.asyncio
async def test_empty_operation_payload_is_rejected(client, mock_db):
    meeting = _completed_meeting()
    mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
    resp = await client.patch(
        f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
        json={},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_other_users_meeting_returns_404(client, mock_db):
    mock_db.execute = AsyncMock(return_value=MockResult([]))  # ownership filter misses
    resp = await client.patch(
        f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
        json={"rename": [{"from_cluster": "1", "to_name": "田中"}]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unauthorized_patch_is_rejected(unauthed_client):
    """P1-AC11: no/invalid API key → 401/403, no DB mutation."""
    resp = await unauthed_client.patch(
        f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
        json={"rename": [{"from_cluster": "1", "to_name": "田中"}]},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_rename_requires_cluster_or_name(client, mock_db):
    meeting = _completed_meeting()
    mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
    resp = await client.patch(
        f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
        json={"rename": [{"to_name": "田中"}]},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Codex sidechain findings — regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_meeting_is_rejected_before_any_mutation(client, mock_db):
    """Blocker fix: on an active meeting the Redis segments hash is the LIVE
    store — the endpoint must 409 before updating rows or deleting the cache."""
    meeting = make_meeting(id=TEST_MEETING_ID, status="active", data={})
    mock_db.execute = AsyncMock(return_value=MockResult([meeting]))
    clear_cache = AsyncMock(return_value=True)

    with patch("meeting_api.final_transcription._clear_live_transcript_cache", new=clear_cache):
        resp = await client.patch(
            f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
            json={"rename": [{"from_cluster": "1", "to_name": "田中"}]},
        )

    assert resp.status_code == 409
    assert mock_db.execute.await_count == 1  # ownership select only, no UPDATE
    mock_db.commit.assert_not_awaited()
    clear_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_ownership_select_takes_row_lock(client, mock_db):
    """Concurrent PATCHes must serialize on the meeting row so the JSONB
    corrections read-modify-write cannot lose an update."""
    meeting = _completed_meeting()
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        UpdateResult(rowcount=1),
        MockResult([("田中",)]),
    ])
    p1, p2 = _patch_flags()
    with p1, p2, patch(
        "meeting_api.final_transcription._clear_live_transcript_cache",
        new=AsyncMock(return_value=True),
    ):
        resp = await client.patch(
            f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
            json={"rename": [{"from_cluster": "1", "to_name": "田中"}]},
        )
    assert resp.status_code == 200
    ownership_stmt = mock_db.execute.await_args_list[0].args[0]
    assert "FOR UPDATE" in str(ownership_stmt.compile()).upper()


@pytest.mark.asyncio
async def test_merge_records_aliases_for_later_renames(client, mock_db):
    meeting = _completed_meeting()
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        UpdateResult(rowcount=4),
        MockResult([("佐藤",)]),
    ])
    p1, p2 = _patch_flags()
    with p1, p2, patch(
        "meeting_api.final_transcription._clear_live_transcript_cache",
        new=AsyncMock(return_value=True),
    ):
        resp = await client.patch(
            f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
            json={"merge": [{"clusters": ["1", "3"], "to_name": "佐藤"}]},
        )
    assert resp.status_code == 200
    corrections = meeting.data["speaker_corrections"]
    assert corrections["clusters"] == {"1": "佐藤", "3": "佐藤"}
    assert corrections["aliases"] == {"3": "1"}


@pytest.mark.asyncio
async def test_rename_of_merged_representative_covers_source_clusters(client, mock_db):
    """Blocker fix: merge 1+3→佐藤, then rename representative 1→田中 must also
    move alias cluster 3 to 田中, or a later mode=replace resurrects 佐藤."""
    meeting = _completed_meeting(
        speaker_corrections={
            "clusters": {"1": "佐藤", "3": "佐藤"},
            "aliases": {"3": "1"},
        }
    )
    mock_db.execute = AsyncMock(side_effect=[
        MockResult([meeting]),
        UpdateResult(rowcount=4),
        MockResult([("田中",)]),
    ])
    p1, p2 = _patch_flags()
    with p1, p2, patch(
        "meeting_api.final_transcription._clear_live_transcript_cache",
        new=AsyncMock(return_value=True),
    ):
        resp = await client.patch(
            f"/meetings/{TEST_MEETING_ID}/transcripts/speakers",
            json={"rename": [{"from_cluster": "1", "to_name": "田中"}]},
        )
    assert resp.status_code == 200
    corrections = meeting.data["speaker_corrections"]
    assert corrections["clusters"] == {"1": "田中", "3": "田中"}
    assert corrections["aliases"] == {"3": "1"}  # merge group itself is kept
