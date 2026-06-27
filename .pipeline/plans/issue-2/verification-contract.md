# Verification Contract — Issue #2

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | Existing realtime transcript rows do not block automatic final transcript generation. | unit | `test_run_deferred_transcription_replace_replaces_existing_rows_after_success` |
| AT-002 | Existing transcript rows are not deleted if deferred transcription fails. | unit | `test_run_deferred_transcription_failure_keeps_existing_rows` |
| AT-003 | Final transcript uses finalized audio master only, not recording chunks. | unit | `test_find_final_transcription_source_requires_finalized_audio_master` |
| AT-004 | transcription-service receives deferred tier via form and header. | unit | `test_call_transcription_service_marks_deferred_tier` |
| AT-005 | `run_all_tasks` queues the final transcription job but does not execute heavy transcription inline. | unit | `test_queue_final_transcription_sets_queued_state` |
| AT-006 | Sweep processes queued final transcription jobs in replace mode. | unit | `test_sweep_final_transcription_jobs_runs_replace_mode` |
| AT-007 | Speaker events are preserved and re-applied to final segments. | unit | `test_run_deferred_transcription_replace_replaces_existing_rows_after_success` |
| AT-008 | A transcription-service response with `text` but no `segments` still stores a final transcript segment. | unit + existing-file E2E | `test_parse_segments_falls_back_to_text_only_response`; `.pipeline/evidence/issue-2/test-results.md` |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | Manual `POST /meetings/{id}/transcribe` still rejects existing transcripts unless `mode=replace`. | unit/API | `test_transcribe_meeting_default_mode_rejects_existing_transcript` |
| FP-002 | Transcript API should not merge stale realtime Redis rows after final replacement. | unit | Redis delete assertion in final transcription replace test |
| FP-003 | Master-not-ready jobs stay queued instead of being marked permanently failed. | unit | `test_run_deferred_transcription_missing_master_stays_queued` |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | Harness residency and doctor pass. | command | `.pipeline/evidence/issue-2/test-results.md` |
| NFT-002 | GitNexus detect changes run before completion. | command | `.pipeline/evidence/issue-2/test-results.md` |
| NFT-003 | No credentials or signed URLs are persisted. | inspection/unit | final state assertions |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: no
