# Sidechain Synthesis — Calendar Kabosu Drive

## Review Focus

- Avoided editing `MeetingCreate` after GitNexus reported HIGH blast radius. Calendar metadata is attached by calendar-service directly to `Meeting.data` after bot creation instead.
- Kept Drive upload outside final transcription execution; final transcription only queues the job, and the sweep performs retryable external I/O.
- Kept `calendar-service` profile-gated instead of default-on to avoid changing local default deployment behavior.

## Residual Risks

- Real Google OAuth consent, Drive folder access, and Meet guest admission require live account setup and cannot be fully proven by unit tests.
- Existing CalendarEvent schema has no attendee JSON field, so exported participants are derived from transcript speaker labels unless future calendar metadata enrichment is added.
- If the configured API token and `KABOSU_BOT_OWNER_USER_ID` do not represent the same Vexa user, wake replies may still fail by owner scope.
