# Evidence — Issue #2

## Harness Init

- `bash /Users/bonginkan-3-gouki/project/claude-dotfiles/skills/harness-init/scripts/install_harness.sh generic_tldv`
- Result: passed; harness doctor reported 0 warnings.

## GitNexus Impact Before Editing

- `transcribe_meeting`: LOW, no upstream dependents reported.
- `run_all_tasks`: LOW, no upstream dependents reported.
- `start_sweeps`: LOW, direct caller `startup`.
- `_map_speakers_to_segments`: LOW, direct caller `transcribe_meeting`.
- `_get_full_transcript_segments`: LOW, transcript fetch endpoints affected; used to justify Redis live-cache cleanup.
- `aggregate_transcription`: LOW, `run_all_tasks` and aggregation retry affected.

## Verification Commands

- `python3 -m py_compile services/meeting-api/meeting_api/final_transcription.py services/meeting-api/meeting_api/meetings.py services/meeting-api/meeting_api/post_meeting.py services/meeting-api/meeting_api/sweeps.py services/meeting-api/tests/test_final_transcription.py services/meeting-api/tests/test_meetings.py`
  - Result: pass.
- `cd services/meeting-api && /tmp/generic_tldv_issue2_venv/bin/python -m pytest tests/test_final_transcription.py tests/test_meetings.py::TestDeferredTranscription -q`
  - Result: 9 passed.
- `cd services/meeting-api && /tmp/generic_tldv_issue2_venv/bin/python -m pytest tests/test_sweeps_unfinalized_recordings.py tests/test_post_meeting_idempotency.py -q`
  - Result: 8 passed.
- `cd services/meeting-api && /tmp/generic_tldv_issue2_venv/bin/python -m pytest tests/ -q --ignore=tests/test_integration_live.py --ignore=tests/collector/`
  - Result: 256 passed, 10 skipped.
- `git diff --check`
  - Result: pass.

## Existing-File E2E

- Existing audio fixture: `tests3/testdata/test-speech-en.wav`.
- Real infrastructure used: local Compose Postgres on `localhost:5458`, MinIO on `localhost:9000`, and configured transcription service on `localhost:8091`.
- Flow executed without adding a new test file:
  1. Uploaded the existing WAV as a finalized `audio/master.wav` object.
  2. Inserted one completed meeting and two existing realtime `transcriptions` rows.
  3. Ran `run_deferred_transcription(..., mode="replace")` against the real DB/storage/STT path.
  4. Verified old `live:*` rows were removed, one `deferred:*` row was stored, `replaced_realtime_count=2`, speaker mapped to `Alice`, and `final_transcription.status=succeeded`.
  5. Removed the temporary DB rows and MinIO object.
- Result: pass.
- Observed issue and fix: the configured transcription service returned `text` only, without `segments`; final transcription now falls back to a single segment using audio duration, and `test_parse_segments_falls_back_to_text_only_response` covers this compatibility path.
- Cleanup verification: `codex-e2e` meetings/transcriptions count `0`; `recordings/codex-e2e/` MinIO object count `0`.
- Note: `redis_cache_cleared=False` in this host-side E2E because Redis is not published to the host and `meeting_api.meetings.get_redis()` depends on service DB env. The replacement DB transaction still completed successfully.

## Gates

- `bash .claude/hooks/harness-residency.sh`
  - Result: pass.
- `HARNESS_TASK_ID=issue-2 bash .claude/hooks/preflight.sh --full`
  - Result: pass; diff hash `sha256:e66c4fe7838e9a905893b965166580d1d0b94e34cc507f0d5d11b867b6fe24bb`.
- `bash .claude/hooks/adapter-validate.sh`
  - Result: pass.
- `bash .claude/hooks/hd-gate.sh`
  - Result: pass.
- `bash .claude/hooks/doc-staleness.sh`
  - Result: pass after `.ai/DOCS.md` and `.ai/META.md` refresh.
- `bash .claude/hooks/feedback-prune.sh`
  - Result: pass.
- `scripts/harness/validate-runtime-profile.sh --quiet`
  - Result: pass.

## GitNexus Detect Changes

- `node .gitnexus/run.cjs detect-changes --repo generic_tldv --scope staged`
- Result: HIGH risk.
- Reason: `run_all_tasks` affects seven existing post-meeting execution-flow summaries, and `transcribe_meeting` affects its validation flow. This is expected for issue #2 because final transcription is queued from post-meeting completion and processed by sweeps.
- Mitigation: existing-file E2E passed; full meeting-api unit suite passed; post-meeting and sweep regression tests passed; heavy transcription is not run inline in `run_all_tasks`.

## Notes

- Test environment used a temporary venv at `/tmp/generic_tldv_issue2_venv` because the system and bundled Python did not include meeting-api test dependencies.
- Warnings were existing deprecation warnings from FastAPI/Pydantic and `datetime.utcnow`; no new failing warnings were introduced.
