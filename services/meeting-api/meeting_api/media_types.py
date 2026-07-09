"""Media-type predicates shared by upload, finalizer and sweep paths.

Issue #25 (Phase 2 audio lanes): per-participant lanes ride the chunk-upload
machinery with ``media_type = "lane-{laneKey}"``. The type keys both the
storage path (prefix separation from the mixed master) and the media_files
JSONB entry (no collapse across lanes). This module exists so that
recordings.py, recording_finalizer.py, sweeps.py and post_meeting.py agree
on what counts as a lane / audio-like type without importing each other.
"""


def is_lane_media_type(media_type: str) -> bool:
    """True for per-participant lane types ("lane-{laneKey}")."""
    return str(media_type or "").lower().startswith("lane-")


def is_audio_like_media_type(media_type: str) -> bool:
    """audio plus lane-* — types whose payload is audio content."""
    typ = str(media_type or "").lower()
    return typ == "audio" or typ.startswith("lane-")
