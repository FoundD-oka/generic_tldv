"""Issue #27 Phase 4 plan §6 露出制御 — `meeting.data["speaker_suggestions"]`
must never reach a generic API response (MeetingResponse.data or the
transcript endpoint's hand-built `data` dict), mirroring the existing
webhook_secret exclusion precedent."""
from __future__ import annotations

from datetime import datetime

from meeting_api.schemas import MeetingResponse, redact_meeting_data


def test_redact_meeting_data_strips_speaker_suggestions_and_webhook_secret():
    data = {
        "name": "weekly sync",
        "webhook_secret": "shh",
        "speaker_suggestions": {"lane:aaaaaaaaaa:spk0": {"candidate_display_name": "田中"}},
    }
    redacted = redact_meeting_data(data)
    assert redacted == {"name": "weekly sync"}


def test_redact_meeting_data_none_passthrough():
    assert redact_meeting_data(None) is None


def test_meeting_response_serializer_excludes_speaker_suggestions():
    now = datetime.utcnow()
    response = MeetingResponse(
        id=1, user_id=5, platform="google_meet", native_meeting_id="abc",
        status="completed", bot_container_id=None, start_time=None, end_time=None,
        data={
            "webhook_secret": "shh",
            "speaker_suggestions": {"lane:x:spk0": {"candidate_display_name": "田中"}},
            "notes": "keep me",
        },
        created_at=now, updated_at=now,
    )
    dumped = response.model_dump()
    assert dumped["data"] == {"notes": "keep me"}
