"""v0.10.5 — URL handling + Pack X dry_run unit tests.

Two architectural shifts:

1. URL handling — `(URL + platform)` trust model. Parser is best-effort
   metadata extraction; failing to recognize a URL shape is NOT a
   validation gate. White-label / enterprise / never-seen-before URLs
   (LFX, AWS, Bloomberg, etc.) work via the (URL + platform) path
   without requiring per-vendor parser entries.

2. Pack X — `dry_run=true` flag on /bots POST skips runtime-api bot
   launch. Test driver controls full lifecycle via callback endpoints.
   No real Playwright bot, no contamination from real-bot callbacks.

Per project-owner principle 2026-04-27: "we will have endless [white-
label URLs]; cannot create a parser for every one. Allow users to
supply (URL + platform) and trust them."
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from meeting_api.schemas import (
    MANUAL_MEETING_TITLE_MAX_LENGTH,
    parse_meeting_url,
    MeetingCreate,
)


# ===================================================================
# parse_meeting_url — canonical shapes (regression locks)
# ===================================================================


class TestParserCanonicalUrls:
    """Canonical zoom.us / meet.google.com / teams.microsoft.com shapes
    must continue to extract metadata as before. These are tight
    contracts — the parser SHOULD recognize them."""

    def test_zoom_us_j_path(self):
        result = parse_meeting_url("https://zoom.us/j/96088138284?pwd=abc123")
        assert result["platform"] == "zoom"
        assert result["native_meeting_id"] == "96088138284"
        assert result["passcode"] == "abc123"

    def test_zoomgov_path(self):
        result = parse_meeting_url("https://zoomgov.com/j/89234567890")
        assert result["platform"] == "zoom"
        assert result["native_meeting_id"] == "89234567890"


# ===================================================================
# parse_meeting_url — fails silently on unrecognized shapes
# ===================================================================


class TestParserBestEffort:
    """When the parser doesn't recognize a URL shape, it raises
    ValueError. The model_validator catches it silently and lets
    downstream validation handle the (URL + platform) trust path.
    This test asserts the parser raises (caller is responsible for
    treating it as best-effort)."""

    def test_lfx_url_unrecognized_raises(self):
        with pytest.raises(ValueError):
            parse_meeting_url(
                "https://zoom-lfx.platform.linuxfoundation.org/meeting/96088138284"
                "?password=c9e528a8-3852-4b82-89c2-96d6f22526ad"
            )

    def test_arbitrary_url_unrecognized_raises(self):
        with pytest.raises(ValueError):
            parse_meeting_url("https://example.com/meeting/12345")


# ===================================================================
# MeetingCreate — Path 3: (platform + meeting_url) trust model
# ===================================================================


class TestUrlPlusPlatformTrustModel:
    """v0.10.5 Path 3 (the architectural shift): user supplies
    `(meeting_url + platform)`. Parser may or may not recognize the
    URL — schema accepts the request either way. No per-vendor parser
    entries proliferating.

    Test cases cover canonical (parser succeeds), white-label
    (parser fails, trust path kicks in), and edge cases."""

    def test_canonical_zoom_url_plus_platform(self):
        """Canonical Zoom URL — parser extracts metadata, request validates."""
        m = MeetingCreate(
            meeting_url="https://zoom.us/j/96088138284?pwd=abc",
            platform="zoom",
        )
        assert m.platform.value == "zoom"
        assert m.native_meeting_id == "96088138284"

    def test_lfx_url_plus_platform_zoom(self):
        """LFX URL (white-label) + platform=zoom — parser fails,
        trust-path accepts. native_meeting_id stays None at validation
        time; handler synthesizes one from URL hash."""
        m = MeetingCreate(
            meeting_url="https://zoom-lfx.platform.linuxfoundation.org/meeting/96088138284?password=secret",
            platform="zoom",
        )
        assert m.platform.value == "zoom"
        assert m.meeting_url is not None

    def test_arbitrary_url_plus_platform(self):
        """Arbitrary URL with platform supplied — accepted via trust
        model. The bot will navigate the URL directly; if it's a real
        Zoom-managed URL, server-side redirect resolves it."""
        m = MeetingCreate(
            meeting_url="https://my-corp.example.com/meet/abc-defg-hij",
            platform="google_meet",
        )
        assert m.platform.value == "google_meet"

    def test_url_only_no_platform_no_id_rejected(self):
        """Pure URL-only (no platform, parser fails) — request is
        rejected with a clear actionable error. The user can either
        (a) tell us the platform, or (b) supply native_meeting_id, or
        (c) use a recognized URL shape."""
        with pytest.raises(ValueError, match="Either provide"):
            MeetingCreate(meeting_url="https://my-corp.example.com/meet/abc")

    def test_native_id_only_still_works(self):
        """Path 1 (existing) still works."""
        m = MeetingCreate(platform="google_meet", native_meeting_id="abc-defg-hij")
        assert m.native_meeting_id == "abc-defg-hij"


# ===================================================================
# Pack X dry_run flag — schema field
# ===================================================================


class TestDryRunSchemaField:
    """Pack X dry_run is a first-class schema field. Production gate
    (VEXA_ENV != 'production') enforced at request_bot handler;
    schema-level test verifies the field exists and round-trips."""

    def test_dry_run_default_false(self):
        m = MeetingCreate(platform="google_meet", native_meeting_id="abc-defg-hij")
        assert m.dry_run is False

    def test_dry_run_explicit_true(self):
        m = MeetingCreate(
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            dry_run=True,
        )
        assert m.dry_run is True

    def test_dry_run_with_url_plus_platform(self):
        """dry_run composes with the (URL + platform) trust model."""
        m = MeetingCreate(
            meeting_url="https://zoom-lfx.platform.linuxfoundation.org/meeting/96088138284?password=x",
            platform="zoom",
            dry_run=True,
        )
        assert m.dry_run is True
        assert m.platform.value == "zoom"


# ===================================================================
# Video recording receive path — schema field
# ===================================================================


class TestVideoReceiveSchemaField:
    """video_receive_enabled is the explicit participant-video receive knob."""

    def test_video_receive_default_unset(self):
        m = MeetingCreate(platform="google_meet", native_meeting_id="abc-defg-hij")
        assert m.video_receive_enabled is None

    def test_video_receive_explicit_true(self):
        m = MeetingCreate(
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            video_receive_enabled=True,
        )
        assert m.video_receive_enabled is True


# ===================================================================
# Path 3 — empty native_meeting_id normalization
# ===================================================================


class TestPath3EmptyNativeIdNormalization:
    """Clients on the (URL + platform) path have no ID to send for
    white-label URLs. Sending `native_meeting_id=""` used to 422 on
    "cannot be empty" even though Shape B (platform + meeting_url) is
    documented as valid. An empty/blank ID alongside (meeting_url +
    platform) is normalized to None; the rejection boundaries for
    requests that lack meeting_url or platform stay put."""

    def test_white_label_url_with_empty_native_id(self):
        m = MeetingCreate(
            meeting_url="https://zoom-lfx.platform.linuxfoundation.org/meeting/96088138284?password=secret",
            platform="zoom",
            native_meeting_id="",
        )
        assert m.platform.value == "zoom"
        assert m.native_meeting_id is None
        assert m.meeting_url is not None

    def test_white_label_url_with_blank_native_id(self):
        m = MeetingCreate(
            meeting_url="https://my-corp.example.com/meet/room-42",
            platform="google_meet",
            native_meeting_id="   ",
        )
        assert m.platform.value == "google_meet"
        assert m.native_meeting_id is None

    def test_canonical_zoom_url_with_empty_native_id_backfills(self):
        """Normalization runs before the parser, so a canonical URL still
        backfills the ID instead of leaving it None."""
        m = MeetingCreate(
            meeting_url="https://zoom.us/j/96088138284?pwd=abc",
            platform="zoom",
            native_meeting_id="",
        )
        assert m.native_meeting_id == "96088138284"

    def test_canonical_meet_url_with_blank_native_id_backfills(self):
        m = MeetingCreate(
            meeting_url="https://meet.google.com/abc-defg-hij",
            platform="google_meet",
            native_meeting_id="   ",
        )
        assert m.native_meeting_id == "abc-defg-hij"

    def test_empty_native_id_without_meeting_url_still_rejected(self):
        """No URL to fall back on — the empty ID is the whole request."""
        with pytest.raises(ValueError, match="cannot be empty"):
            MeetingCreate(platform="google_meet", native_meeting_id="")

    def test_empty_native_id_without_platform_still_rejected(self):
        """Unknown URL shape + no platform — nothing identifies the meeting."""
        with pytest.raises(ValueError):
            MeetingCreate(
                meeting_url="https://my-corp.example.com/meet/abc",
                native_meeting_id="",
            )


class TestManualMeetingTitle:
    def test_omitted_defaults_to_none(self):
        m = MeetingCreate(platform="google_meet", native_meeting_id="abc-defg-hij")
        assert m.meeting_title is None

    def test_surrounding_whitespace_stripped(self):
        m = MeetingCreate(
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            meeting_title="  週次定例  ",
        )
        assert m.meeting_title == "週次定例"

    @pytest.mark.parametrize("raw", ["", "   \t\n  "])
    def test_empty_value_becomes_none(self, raw):
        m = MeetingCreate(
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            meeting_title=raw,
        )
        assert m.meeting_title is None

    def test_exactly_max_length_accepted(self):
        title = "x" * MANUAL_MEETING_TITLE_MAX_LENGTH
        m = MeetingCreate(
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            meeting_title=title,
        )
        assert m.meeting_title == title

    def test_max_length_with_padding_accepted_after_strip(self):
        title = "y" * MANUAL_MEETING_TITLE_MAX_LENGTH
        m = MeetingCreate(
            platform="google_meet",
            native_meeting_id="abc-defg-hij",
            meeting_title=f"   {title}   ",
        )
        assert m.meeting_title == title

    def test_over_max_length_rejected(self):
        with pytest.raises(ValidationError, match="200 characters or fewer"):
            MeetingCreate(
                platform="google_meet",
                native_meeting_id="abc-defg-hij",
                meeting_title="z" * (MANUAL_MEETING_TITLE_MAX_LENGTH + 1),
            )

    def test_non_string_rejected(self):
        with pytest.raises(ValidationError):
            MeetingCreate(
                platform="google_meet",
                native_meeting_id="abc-defg-hij",
                meeting_title=123,
            )
