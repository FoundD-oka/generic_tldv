"""Issue #27 Phase 4 plan §6 露出制御 — `meeting.data["speaker_suggestions"]`
must never reach a generic API response (MeetingResponse.data or the
transcript endpoint's hand-built `data` dict), mirroring the existing
webhook_secret exclusion precedent."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from meeting_api.schemas import MeetingResponse, redact_meeting_data
from meeting_api.webhook_delivery import build_envelope, clean_meeting_data
from meeting_api.webhooks import _build_meeting_event_data


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


def test_webhook_delivery_clean_meeting_data_strips_speaker_suggestions_and_secret():
    """BUG-001 regression: webhook_delivery.clean_meeting_data() must reuse
    schemas.MEETING_DATA_REDACTED_KEYS as its single source of truth, so a
    populated speaker_suggestions key (and webhook_secret) never survives
    into a webhook envelope — not even via a third exit path that the
    generic-response redaction test above cannot exercise."""
    data = {
        "notes": "keep me",
        "webhook_secret": "shh",
        "speaker_suggestions": {
            "lane:aaaaaaaaaa:spk0": {
                "candidate_display_name": "田中",
                "profile_id": 42,
                "similarity": 0.91,
                "status": "suggested",
            }
        },
    }
    cleaned = clean_meeting_data(data)
    assert "speaker_suggestions" not in cleaned
    assert "webhook_secret" not in cleaned
    assert cleaned == {"notes": "keep me"}


def test_build_meeting_event_data_never_leaks_speaker_suggestions_via_webhook_envelope():
    """BUG-001 regression: build a real webhook envelope (as
    send_completion_webhook / send_status_webhook do) for a meeting whose
    data contains speaker_suggestions + webhook_secret, and assert both are
    absent from the final payload sent to the user-configured webhook_url."""
    now = datetime.utcnow()
    meeting = SimpleNamespace(
        id=1,
        user_id=5,
        platform="google_meet",
        native_meeting_id="abc",
        constructed_meeting_url=None,
        status="completed",
        start_time=now,
        end_time=now,
        created_at=now,
        updated_at=now,
        data={
            "webhook_url": "https://example.com/hook",
            "webhook_secret": "shh",
            "speaker_suggestions": {
                "lane:aaaaaaaaaa:spk0": {
                    "candidate_display_name": "田中",
                    "profile_id": 42,
                    "similarity": 0.91,
                    "status": "suggested",
                }
            },
        },
    )

    envelope = build_envelope("meeting.completed", {"meeting": _build_meeting_event_data(meeting)})

    meeting_data = envelope["data"]["meeting"]["data"]
    assert "speaker_suggestions" not in meeting_data
    assert "webhook_secret" not in meeting_data
    assert "webhook_url" not in meeting_data
