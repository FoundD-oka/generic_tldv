# Plan — Issues #4-#19: Kabosu hardening and Japanese-only consolidation

## Request

- Source: GitHub issues #4 through #19
- Requester: user
- Required workflow: harness-init

## Context Pack

- GitHub issues inspected: #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14, #15, #16, #17, #18, #19
- Harness installed: yes; `harness-doctor` passed with 0 warnings after re-running harness-init.
- GitNexus used: yes
- High-risk impact warnings:
  - `withPostMeetingAutoStop`: CRITICAL, direct join/pending/Zoom bot creation flows.
  - `CreateBotRequest`: CRITICAL, broad dashboard request contract.
- Scope control:
  - Keep request/response shapes compatible unless the issue explicitly requires a safer behavior.
  - Preserve explicit user choices such as `voice_agent_enabled=false`.
  - Avoid runtime event-name changes that would break existing wake consumers.

## Work Phases

1. Stop-the-bleed fixes:
   - #4 redact LLM contexts across dashboard AI and agent context.
   - #5 resolve final transcription language from explicit request, meeting data, or Japanese default.
   - #6 skip destructive final transcript replacement when speaker attribution would regress.
   - #7 unify dashboard bot-name defaults on Kabosu/config.
   - #8 default API-created bots to voice-agent enabled and improve timeout diagnostics.
2. Kabosu/Japanese consolidation:
   - #9 add persona source file and sync tests; enforce Japanese response rule.
   - #10 default transcription language to Japanese across dashboard creation and display fallback.
   - #11 translate listed user-visible dashboard English strings.
   - #12 replace listed user-visible Vexa branding with Kabosu.
   - #18 add shared assistant-context endpoint and use it for dashboard/agent context where practical.
   - #19 update the Japanese-only design document and brand tests.
3. Operational consistency:
   - #13 split wake-stt/tts compose profiles and clarify wake env checks.
   - #14 harden wake meeting resolution around early events and ID aliases.
   - #15 publish and consume `transcript.finalized`.
   - #16 clean `chat_messages` and make ambiguous delete safe.
   - #17 align WS docs/types around runtime `transcript` event.

## Non Goals

- Multi-user wake discovery with an admin key.
- Replacing the runtime producer event name from `transcript` to `transcript.mutable`.
- Full locale framework; this product is Japanese-only.
- Live production deploy or GitHub issue closure unless requested after verification.

## Verification Contract

Link: `.pipeline/plans/issues-4-19/verification-contract.md`

## S/M/L Decision

| Field | Value |
|---|---|
| size | L |
| reason | Sixteen issues span security, data preservation, API behavior, dashboard UX, websocket contracts, wake runtime, compose profiles, and documentation. |
| human gate required | not for local implementation; issue closure/deploy remains separate |

## Impact Summary

- LOW: `_get_meeting_context`, `run_deferred_transcription`, `_sweep_final_transcription_jobs`, `_call_transcription_service`, `build_kabosu_meet_system_prompt`, `useLiveTranscripts`, `useVexaWebSocket`, `missing_required`, wake `VexaClient`, `VexaTranscriptSubscriber`, `WakeOrchestrator`, collector `delete_meeting`.
- CRITICAL: `withPostMeetingAutoStop`, dashboard `CreateBotRequest`; changes must remain additive and covered by dashboard tests.
