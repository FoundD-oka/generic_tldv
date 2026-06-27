# Plan — Issue #2: post-meeting final transcript replacement

## Request

- Source: GitHub issue #2
- Issue or task: 会議後に録音音声から正式版文字起こしを自動生成して置き換える
- Requester: user

## Context Pack

- GitNexus used: yes
- Context scout used: yes
- Key files:
  - `services/meeting-api/meeting_api/meetings.py`
  - `services/meeting-api/meeting_api/post_meeting.py`
  - `services/meeting-api/meeting_api/sweeps.py`
  - `services/meeting-api/meeting_api/collector/endpoints.py`
  - `services/transcription-service/main.py`
- Unknowns:
  - Live transcription-service capacity and model runtime are deployment-specific.
  - Codex polish worker is described in the issue comments but is a later stage; this plan implements the durable final-transcript base.

## Proposed Change

Implement a durable final transcription flow:

1. Extract deferred transcription into `meeting_api.final_transcription`.
2. Keep manual API compatibility:
   - default mode: reject when existing transcript rows exist
   - replace mode: generate final transcript from finalized recording master and atomically replace existing rows only after transcription succeeds
3. Queue final transcription in `run_all_tasks` without doing heavy work inline.
4. Add a sweep that processes queued or retryable failed final transcription jobs.
5. Use only finalized audio master media (`recording_finalizer.master`, `master.webm` or `master.wav`) as input.
6. Send transcription requests with deferred tier in both form data and header.
7. Preserve `meeting.data.speaker_events` and re-map speakers onto final segments.
8. Clear live Redis transcript cache after successful replacement so transcript APIs return the final transcript only.

## Non Goals

- Docker-internal Codex CLI polishing.
- UI polish for "formal transcript" labels.
- Streaming/chunked long-meeting transcription.
- Schema migration for transcript generations.

## Verification Contract

Link: `.pipeline/plans/issue-2/verification-contract.md`

## S/M/L Decision

| Field | Value |
|---|---|
| size | M |
| reason | Changes touch API, post-meeting task state, sweep execution, DB rows, Redis cache, and storage-backed media reads, but avoid new infrastructure or migrations. |
| remaining uncertainty | Long recording timeout/memory behavior remains deployment-dependent and is documented as follow-up. |
| human gate required | no |

## Implementation Notes

- GitNexus impact:
  - `transcribe_meeting`: LOW, no upstream dependents reported.
  - `run_all_tasks`: LOW, no upstream dependents reported.
  - `start_sweeps`: LOW, direct caller `startup`.
  - `_map_speakers_to_segments`: LOW, direct caller `transcribe_meeting`.
  - `_get_full_transcript_segments`: LOW, direct callers are transcript fetch endpoints; relevant because Redis cache must be cleared on replace.
- Store job state in `meeting.data.final_transcription` and mirror current state to `meeting.data.final_transcription_status`.
- Do not delete existing transcript rows before transcription-service success.

## Risks

- Long recordings still use whole-file download/convert/transcribe.
- If `speaker_events` are missing or time-shifted, speakers may still map to `Unknown`.
- If Redis is unavailable during successful replacement, DB replacement still succeeds and a warning is logged; a stale live cache may persist until Redis expires or is cleared later.

## Codex Plan Critique

- critique file: n/a
- adopted: preserve manual API compatibility; avoid inline heavy post-meeting work; clear Redis live segments.
- rejected: direct Codex CLI polish worker in this change.
- needs human: none
