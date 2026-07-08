# Plan — Calendar Kabosu Auto-Join and Drive Export

## Goal

Invite `e@bonginkan.ai` to a Google Calendar event and have Kabosu join near the start time, then export the completed transcript Markdown to a configured Google Drive folder.

## Implementation Scope

- Keep legacy per-user calendar OAuth mode.
- Add `KABOSU_CALENDAR_MODE=single_account` for the dedicated Kabosu Google account.
- Schedule calendar-created bots with `bot_name=カボス`, `language=ja`, and `voice_agent_enabled=true`.
- Store `meeting.data.calendar_event` after bot creation so downstream jobs can distinguish calendar-origin meetings.
- Queue Drive export when final transcription succeeds or falls back via `skipped_no_speaker_events`.
- Add a retrying Drive export sweep using `meeting.data.drive_export`.
- Enable `calendar-service` through a Compose `calendar` profile and document required env.
- Add a no-dependency OAuth helper script for refresh-token acquisition.

## Non-Scope

- User-facing OAuth UI.
- Google Workspace domain-wide delegation.
- Drive folder permission automation.
- Summary generation or minutes formatting beyond speaker-labeled full transcript Markdown.
- Guaranteed Zoom/Teams E2E.
