# Codex Plan Critique — speaker-attribution-voiceprint

Runtime: Codex (codex-rescue), independent Plan Critic pass in Plan Relay.
Date: 2026-07-07. Verdict source: adversarial review of plan.md + verification-contract.md against actual code.

## ADOPT (fold into refined plan)

- Diarization is NOT "always discarded": `_parse_segments` keeps `dict(segment)`; `map_speakers_to_segments()` overwrites `seg["speaker"]` only when `speaker_events` exist; storage uses `seg.get("speaker")`. Fix phrasing. `final_transcription.py:334-351,210-240,625-634`
- Promote the **Soniox STT adapter contract to a Phase-1 precondition**: current STT contract is `start/end/text` only; no impl returns `speaker`/cluster. `transcription-service/main.py:461-483,521-527`; `contracts/stt/v1/README.md:13-14`
- PATCH must handle the **Redis live cache**: REST merges Postgres+Redis by `segment_id` with **Redis winning** → DB-only rename shows stale speaker. `collector/endpoints.py:231-239,247-345`
- **Drive export requeue**: Drive reads DB speaker but won't re-queue once `drive_export.status == done`; rename must invalidate/requeue. `drive_export.py:82-84,161-169,249-255`
- `speaker_cluster` must propagate to **API schema + dashboard types**, not just DB. `schemas.py:1044-1060`; `dashboard/src/lib/api.ts:171-181`; `dashboard/src/types/vexa.ts:58-76`
- `mode="replace"` **deletes all Transcription rows** and rebuilds → silently discards manual corrections. Need preserve/warn/reapply policy. `final_transcription.py:611-617,625-634`
- Phase 2 canonical recording metadata is `meeting.data.recordings[].media_files` (JSONB), not `MediaFile` alone. `recording_finalizer.py:551-555`; `recordings.py:427-435`
- Upload API **collapses lanes**: `media_files` keyed/replaced by `media_type`; N audio lanes merge into one. `recordings.py:429-431,475-490,508-535`
- Lane storage under `<prefix>/lanes/...` **contaminates master**: finalizer recursively lists prefix and concats all non-master objects → need separate prefix. `recording_finalizer.py:451-452`; `storage.py:251-285,444-457,527-558`
- Lane/DOM-name stability risk: Meet falls back to random DOM id; speaker-identity locks track→name; rename/rejoin/DOM-recycle undefined. `googlemeet/recording.ts:210-223`; `speaker-identity.ts:4-18,54-80`
- Phase 3 DOM events are **tile-level only**, not ground truth for co-located speakers; add false-split/merge, short-utterance, overlapping-speech ACs. `googlemeet/recording.ts:315-330`
- Phase 4 biometric consent underspecified: add per-subject specific/informed/affirmative consent, purpose limitation, audit, deletion, retention, vendor contract, threshold calibration (California Civil Code §1798.140).
- Migration is an **online-migration problem**: `transcriptions` ~507K rows; startup schema-sync does plain `ALTER TABLE ADD COLUMN` + `index.create()`. Add nullable col, batch backfill, `CONCURRENTLY` index, rollback. `deploy/helm/README.md:119`; `database.py:90-97`; `schema-sync/sync.py:106-108,122-127`

## CONSIDER

- Split Phase 1 → 1a (adapter+schema+backend cluster storage), 1b (correction API+dashboard), 1c (export/Drive/AI propagation).
- Specify whole-cluster vote precisely: overlap-seconds weighting, Unknown-ratio, confidence threshold, tie-break.
- Document Phase 2 as a NEW storage pipeline — existing per-speaker stream is a live-STT `ScriptProcessor`, not a storage `MediaRecorder`. `audio.ts:318-324`; `index.ts:2041-2096`
- Voiceprint auto-naming: staged **suggest → human confirm → auto**, not immediate auto-apply.
- Phase 4 could apply to mixed-master clusters right after Phase 1 (shared-mic accuracy improves after 2/3).

## REJECT (critic's counter-points; my plan already consistent)

- Phase 1 needing capture changes is unfounded — deferred runs on the finalized master; consuming cluster data touches no capture/finalizer. (Plan already scopes Phase 1 as no-capture-change; keep explicit.)
- Share links do NOT go stale as a blanket claim: api-gateway stores only share metadata and re-fetches fresh transcript; the separate Redis transcript cache is the real staleness. `api-gateway/main.py:967-977,1018-1029,1056-1074`
- `Transcription.speaker` (nullable String(255)) holds display labels fine; what's missing is cluster/profile identity. (Clarify in plan.)

## BLOCKERS (resolve before implementation)

- Before Phase 1: lock a formal **Soniox adapter contract/fixture** returning `segments[].speaker` or cluster id. `contracts/stt/v1/README.md:13-14`
- Before Phase 2: design **lane id propagation** through upload metadata → storage path → JSONB `media_files` → finalizer exclusion → callback payload (none carry a lane id today). `vexa-bot/.../browser.ts:340-345`; `recording.ts:226-236`
- Before Phase 2: decide **full separation** of mixed-master prefix vs lane prefix. `recording_finalizer.py:451-452`
- Before Phase 4: approved **biometric consent/retention/delete/threshold/human-review spec** (no profile/voiceprint model exists). `models.py:17-144`
- Before any DB work: approved **large-table online-migration procedure**, not startup schema-sync. `database.py:90-97`; `schema-sync/sync.py:151-164`
