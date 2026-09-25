"""Shared list-view projection for meeting JSONB `data`.

Single source of truth for the slim `data` payload returned by the meeting
list endpoints (`GET /bots` in meetings.py and `GET /meetings` in
collector/endpoints.py).

It lives in its own module — not in meetings.py — because collector modules
must not import meetings.py (meetings.py imports collector.auth, so the
reverse direction would create an import cycle).
"""

from typing import Optional


def meeting_list_data_summary(d: Optional[dict]) -> dict:
    d = d or {}
    participants = d.get("participants") or []
    notes = d.get("notes")
    transitions = d.get("status_transition") or []
    calendar_event = d.get("calendar_event")
    calendar_title = (
        calendar_event.get("title")
        if isinstance(calendar_event, dict)
        else None
    )
    final_transcription = d.get("final_transcription")
    final_transcription_summary = (
        {"status": final_transcription.get("status")}
        if isinstance(final_transcription, dict) and final_transcription.get("status")
        else None
    )
    return {
        "name": d.get("name") or d.get("title"),
        "calendar_title": calendar_title,
        "final_transcription": final_transcription_summary,
        "final_transcription_status": d.get("final_transcription_status"),
        "completion_reason": d.get("completion_reason"),
        "participants": participants[:3],
        "participants_count": len(participants),
        "notes_preview": (notes[:120] if isinstance(notes, str) else None),
        "languages": d.get("languages"),
        "last_transition": transitions[-1] if transitions else None,
        "has_recording": bool(d.get("recordings")),
    }
