"""Issue #25 (Phase 2 audio lanes) — upload-path tests for lane media.

Covers the verification contract items owned by recordings.py:

* lane-* webm chunks get ``audio/webm`` content type (not video/webm)
* N lane uploads produce N independent ``media_files`` entries with their
  own cumulative counters (no per-type collapse across lanes)
* lane identity metadata (lane_id / lane_label / lane_id_source) is stored
  on the entry and inherited by later chunks that omit it
* Pack U.7 late-chunk guard also protects ``/{lane-*}/master.*`` paths
* ``_public_recording_view`` strips lane entries from API responses while
  the raw JSONB (deletion/finalizer surface) keeps them
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api import recordings as recordings_module
from meeting_api.recordings import (
    _public_recording_view,
    is_audio_like_media_type,
    is_lane_media_type,
    media_content_type,
)

from .conftest import TEST_SESSION_UID, make_meeting, make_session
from .test_recordings_concurrent_chunks import _StatefulMockDB, _make_upload_call

LANE_A = "lane-aaaaaaaaaa"
LANE_B = "lane-bbbbbbbbbb"


def _lane_upload_call(lane_type: str, *, chunk_seq: int = 0, is_final: bool = False,
                      lane_meta: dict | None = None):
    call = _make_upload_call(lane_type, "webm")
    call["chunk_seq"] = chunk_seq
    call["is_final"] = is_final
    meta = {
        "session_uid": TEST_SESSION_UID,
        "media_type": lane_type,
        "format": "webm",
        "chunk_seq": chunk_seq,
        "is_final": is_final,
    }
    if lane_meta:
        meta.update(lane_meta)
    call["metadata"] = json.dumps(meta)
    return call


def test_lane_type_predicates():
    assert is_lane_media_type(LANE_A)
    assert not is_lane_media_type("audio")
    assert not is_lane_media_type("video")
    assert is_audio_like_media_type("audio")
    assert is_audio_like_media_type(LANE_A)
    assert not is_audio_like_media_type("video")


def test_lane_webm_is_served_as_audio_webm():
    assert media_content_type(LANE_A, "webm") == "audio/webm"
    # regression: existing behavior unchanged
    assert media_content_type("audio", "webm") == "audio/webm"
    assert media_content_type("video", "webm") == "video/webm"


@pytest.mark.asyncio
async def test_two_lanes_and_audio_do_not_collapse():
    """audio + 2 lanes → 3 independent media_files entries with own counters."""
    meeting = make_meeting(data={})
    session = make_session()
    mock_db = _StatefulMockDB(session=session, meeting=meeting)
    fake_storage = MagicMock()

    with patch.object(recordings_module, "get_storage_client", return_value=fake_storage), \
         patch.object(recordings_module.attributes, "flag_modified", new=MagicMock()):
        await recordings_module.internal_upload_recording(
            db=mock_db, **_make_upload_call("audio", "webm"))
        await recordings_module.internal_upload_recording(
            db=mock_db, **_lane_upload_call(LANE_A, lane_meta={
                "lane_id": "spaces/x/devices/1", "lane_label": "山森",
                "lane_id_source": "participant-id"}))
        await recordings_module.internal_upload_recording(
            db=mock_db, **_lane_upload_call(LANE_B, lane_meta={
                "lane_id": "gm-id-r4nd0m", "lane_label": "岡田健一",
                "lane_id_source": "generated"}))
        # second chunk on lane A WITHOUT lane metadata — identity must survive
        await recordings_module.internal_upload_recording(
            db=mock_db, **_lane_upload_call(LANE_A, chunk_seq=1))

    recs = (meeting.data or {}).get("recordings") or []
    assert len(recs) == 1
    media_files = {mf["type"]: mf for mf in recs[0]["media_files"]}
    assert sorted(media_files) == ["audio", LANE_A, LANE_B]

    lane_a = media_files[LANE_A]
    assert lane_a["chunk_count"] == 2, "lane A got two chunks"
    assert lane_a["file_size_bytes"] == 2048
    assert lane_a["lane"] == {
        "lane_id": "spaces/x/devices/1",
        "lane_label": "山森",
        "lane_id_source": "participant-id",
    }, "lane identity must be stored and inherited across metadata-less chunks"
    assert media_files[LANE_B]["lane"]["lane_id_source"] == "generated"
    assert media_files[LANE_B]["chunk_count"] == 1
    # storage paths are prefix-separated per type
    assert f"/{LANE_A}/" in lane_a["storage_path"]
    assert "/audio/" in media_files["audio"]["storage_path"]
    # lane chunks were uploaded with audio content type
    lane_upload_types = [
        kwargs.get("content_type")
        for args, kwargs in fake_storage.upload_file.call_args_list
        if f"/{LANE_A}/" in args[0] or f"/{LANE_B}/" in args[0]
    ]
    assert lane_upload_types and all(ct == "audio/webm" for ct in lane_upload_types)


@pytest.mark.asyncio
async def test_u7_guard_preserves_lane_master_path():
    """Late lane chunk after finalizer wrote lane master: path must not rewind."""
    lane_master = f"recordings/1523/999/{TEST_SESSION_UID}/{LANE_A}/master.webm"
    meeting = make_meeting(data={
        "recordings": [{
            "id": 999,
            "session_uid": TEST_SESSION_UID,
            "source": "bot",
            "status": "completed",
            "media_files": [{
                "id": 1,
                "type": LANE_A,
                "format": "webm",
                "storage_path": lane_master,
                # race window: finalizer wrote master path but is_final not yet True
                "is_final": False,
                "lane": {"lane_id": "p1", "lane_label": "山森",
                         "lane_id_source": "participant-id"},
            }],
        }],
    })
    session = make_session()
    mock_db = _StatefulMockDB(session=session, meeting=meeting)

    with patch.object(recordings_module, "get_storage_client", return_value=MagicMock()), \
         patch.object(recordings_module.attributes, "flag_modified", new=MagicMock()):
        await recordings_module.internal_upload_recording(
            db=mock_db, **_lane_upload_call(LANE_A, chunk_seq=7))

    mf = (meeting.data["recordings"][0]["media_files"])[0]
    assert mf["storage_path"] == lane_master, (
        "U.7 lane generalization: a late chunk must not rewind the lane "
        "master storage_path back to a chunk path"
    )
    assert mf["is_final"] is True


def test_public_recording_view_hides_lanes_but_raw_keeps_them():
    rec = {
        "id": 1,
        "media_files": [
            {"type": "audio", "storage_path": "a"},
            {"type": LANE_A, "storage_path": "l1", "lane": {"lane_label": "山森"}},
            {"type": "video", "storage_path": "v"},
        ],
    }
    view = _public_recording_view(rec)
    assert [mf["type"] for mf in view["media_files"]] == ["audio", "video"]
    # the raw dict (deletion/finalizer surface) is untouched
    assert len(rec["media_files"]) == 3


@pytest.mark.asyncio
async def test_lane_start_offset_ms_stored_and_inherited():
    """BUG-002 (server half): lane_start_offset_ms rides the same
    fresh/inherit merge as lane_id/lane_label/lane_id_source."""
    meeting = make_meeting(data={})
    session = make_session()
    mock_db = _StatefulMockDB(session=session, meeting=meeting)
    fake_storage = MagicMock()

    with patch.object(recordings_module, "get_storage_client", return_value=fake_storage), \
         patch.object(recordings_module.attributes, "flag_modified", new=MagicMock()):
        await recordings_module.internal_upload_recording(
            db=mock_db, **_lane_upload_call(LANE_A, lane_meta={
                "lane_id": "spaces/x/devices/1", "lane_label": "山森",
                "lane_id_source": "participant-id", "lane_start_offset_ms": 5000}))
        # second chunk omits lane_start_offset_ms — must inherit, not reset to None.
        await recordings_module.internal_upload_recording(
            db=mock_db, **_lane_upload_call(LANE_A, chunk_seq=1))

    recs = (meeting.data or {}).get("recordings") or []
    lane_a = next(mf for mf in recs[0]["media_files"] if mf["type"] == LANE_A)
    assert lane_a["lane"]["lane_start_offset_ms"] == 5000


@pytest.mark.asyncio
async def test_lane_final_chunk_never_flips_recording_status_or_fires_webhook():
    """BUG-003: a lane's is_final chunk must only finalize that lane's own
    media_files entry, never flip the whole recording to COMPLETED or fire
    recording.completed — a mid-meeting lane departure is not the meeting
    ending."""
    meeting = make_meeting(data={})
    session = make_session()
    mock_db = _StatefulMockDB(session=session, meeting=meeting)
    fake_storage = MagicMock()

    with patch.object(recordings_module, "get_storage_client", return_value=fake_storage), \
         patch.object(recordings_module.attributes, "flag_modified", new=MagicMock()), \
         patch.object(recordings_module, "send_event_webhook", new=AsyncMock()) as webhook:
        await recordings_module.internal_upload_recording(
            db=mock_db, **_lane_upload_call(LANE_A, is_final=True, lane_meta={
                "lane_id": "p1", "lane_label": "山森", "lane_id_source": "participant-id"}))

    recs = (meeting.data or {}).get("recordings") or []
    assert recs[0]["status"] != "completed", "lane is_final must not complete the whole recording"
    lane_a = next(mf for mf in recs[0]["media_files"] if mf["type"] == LANE_A)
    assert lane_a["is_final"] is True, "the lane's OWN media_files entry still finalizes"
    webhook.assert_not_called()


@pytest.mark.asyncio
async def test_delete_recording_removes_lane_files_too():
    """Lane filtering happens ONLY at the response boundary — the delete
    path must still see and remove lane storage objects."""
    rec = {
        "id": 424242,
        "media_files": [
            {"type": "audio", "storage_path": "r/audio/master.webm", "storage_backend": "minio"},
            {"type": LANE_A, "storage_path": f"r/{LANE_A}/master.webm", "storage_backend": "minio"},
        ],
    }
    meeting = make_meeting(data={"recordings": [rec]})
    db = AsyncMock()
    fake_storage = MagicMock()

    with patch.object(recordings_module, "_find_meeting_data_recording",
                      new=AsyncMock(return_value=(meeting, rec))), \
         patch.object(recordings_module, "get_storage_client_for", return_value=fake_storage), \
         patch.object(recordings_module.attributes, "flag_modified", new=MagicMock()):
        await recordings_module.delete_recording(
            recording_id=424242, auth=(None, MagicMock(id=1523)), db=db)

    deleted = [args[0] for args, _ in fake_storage.delete_file.call_args_list]
    assert f"r/{LANE_A}/master.webm" in deleted, "lane objects must be deleted"
    assert "r/audio/master.webm" in deleted
