# Plan — Speaker Attribution Correction + Voiceprint Enrollment

## Request

- Source: user (conversation, 2026-07-07)
- Issue or task: 会議後の文字起こしで「音声と話者名」がズレる問題を、手作業を最小化して直せるようにする。加えて (a) 同室で1マイクを複数人が共有するケース、(b) 声紋登録による自動命名まで視野に入れる。
- Requester: user (oxygami.jpn@gmail.com / カボス運用)

## Context Pack

- GitNexus used: no (stale-safe manual scout; run `impact`/`detect_changes` at implementation time per CLAUDE.md)
- Context scout used: yes (read-only)
- Key files (verified in this repo; corrected after Codex Plan Critic pass):
  - `services/meeting-api/meeting_api/final_transcription.py` — deferred transcript. `_parse_segments` keeps `dict(segment)` from the tx result, but `map_speakers_to_segments()` **overwrites `seg["speaker"]` whenever `speaker_events` exist** (DOM overlap), so any Soniox diarization label is clobbered on that path; storage then uses `seg.get("speaker")`. Net effect today = diarization is effectively unused whenever DOM events are present. (`:334-351`, `:210-240`, `:625-634`)
  - `services/meeting-api/meeting_api/collector/speaker_mapper.py` — realtime speaker mapping from DOM `speaker_events` (SPEAKER_START/END, `participant_name`/`participant_id_meet`), longest-overlap wins.
  - `services/meeting-api/meeting_api/models.py` — `Transcription.speaker` (nullable `String(255)`) holds display labels fine; what is missing is a stable **cluster/profile identity** dimension. `MediaFile` has no participant/lane dimension, and the upload API keys `media_files` by `media_type` so multiple audio lanes would collapse into one entry (`recordings.py:429-431,475-490`).
  - `services/meeting-api/meeting_api/recording_finalizer.py` — builds a **single mixed** `master.webm/.wav` from one chunk lane.
  - `services/vexa-bot/core/src/services/speaker-identity.ts`, `core/*/browser-utils*` — Meet exposes **N per-participant streams**, but `createCombinedAudioStream` **mixes them into one** MediaRecorder stream before storage.
  - `services/dashboard/src/components/transcript/transcript-viewer.tsx`, `transcript-segment.tsx` — transcript UI is **read-only** (no edit/rename/merge).
- External facts:
  - Post-meeting STT is served via `TRANSCRIPTION_SERVICE_URL` (env default = Whisper `large-v3-turbo`). User runs **Soniox** behind/alongside this; Soniox provides acoustic **diarization** (anonymous speaker clusters), not cross-session named identity.
- Unknowns / to validate at implementation:
  - Whether Soniox response schema exposes per-token/segment speaker labels through the current `transcription-service` adapter (may need adapter change). Track via `.claude/skills/adapter-contract`.
  - Speaker-embedding model choice for voiceprints (Soniox has no cross-session speaker ID → needs a dedicated embedder). Candidates: SpeechBrain/ECAPA-TDNN, pyannote, or a hosted speaker-ID API. External tool → adapter contract required.

## Problem Decomposition (two structurally different failure modes)

1. **Remote solo participants, deferred transcript mislabeled.** Cause: deferred re-transcribes the *mixed* master and re-derives speakers by jittery DOM overlap, discarding both realtime per-tile identity and any tx diarization. Fixable by plumbing — no manual work required.
2. **Same-room shared mic (N people, 1 Meet tile, 1 stream, 1 DOM name).** DOM and per-stream identity **cannot** separate these people — only acoustic diarization can. DOM has no names for the sub-speakers, so they must be named once (manually, or automatically via a matched voiceprint).

## Proposed Change (phased; each phase ships value and isolates regression)

Speaker identity is modeled as a **two-level key**: `tile identity (DOM name) × acoustic cluster (voice)`, plus an optional `speaker_profile` (voiceprint-backed, cross-meeting).

### Phase 1 — Cluster-aware correction on the existing mixed master (no capture change)
Precondition (BLOCKER): lock a formal **Soniox STT adapter contract + fixture** exposing `segments[].speaker`/cluster id — the current `contracts/stt/v1` is `start/end/text` only. Do this via `.claude/skills/adapter-contract` before backend work.
- Stop DOM-overwriting diarization: when the tx result carries speaker/cluster labels, keep them as the cluster source; DOM is used only to *name* clusters, not to relabel segments.
- Add stable `Transcription.speaker_cluster` (online migration — see Preconditions). Map cluster → display name by **overlap-seconds-weighted DOM vote over the whole cluster** (with Unknown-ratio + confidence threshold + deterministic tie-break), not per-segment overlap → kills jitter for mode ①.
- Propagate `speaker_cluster` through the **API schema and dashboard types**, not just the DB (`schemas.py`, `dashboard/src/lib/api.ts`, `types/vexa.ts`).
- New endpoint `PATCH /meetings/{id}/transcripts/speakers`: `rename` (whole cluster/label), `merge`, `reassign` (segment_ids). Preserve original auto label for undo. DB is source of truth, and on mutation it MUST also:
  - **invalidate/update the Redis live-segments cache** (REST merges Postgres+Redis by `segment_id` with Redis winning — a DB-only write shows stale names). `collector/endpoints.py:231-345`
  - **requeue the Drive export** (Drive won't re-run once `drive_export.status == done`). `drive_export.py:161-169,249-255`
- **Protect manual corrections from `mode="replace"`**: today replace deletes all `Transcription` rows and rebuilds from deferred segments (`final_transcription.py:611-634`), silently discarding edits. Add a preserve/warn/reapply policy (re-map saved cluster→name after rebuild).
- Dashboard: inline click-to-rename per cluster + merge; range-select reassign. Read-only viewer becomes editable.
- Sub-slice for tighter verification: **1a** adapter+schema+cluster storage, **1b** correction API+dashboard, **1c** export/Drive/AI propagation.

### Phase 2 — Preserve per-participant audio lanes to the deferred stage (the accepted "保存変更")
This is a **new storage pipeline**, not reuse of existing scaffolding (the current per-speaker stream is a live-STT `ScriptProcessor`, not a storage `MediaRecorder`: `audio.ts:318-324`, `index.ts:2041-2096`).
Preconditions (BLOCKERS): (a) design a **lane-id that propagates** through upload metadata → storage path → JSONB `meeting.data.recordings[].media_files` → finalizer exclusion → bot callback payload (nothing carries a lane id today); (b) decide **full prefix separation** for lanes vs the mixed master.
- **Do not replace the mixed master** (playback/download stays intact). *Add* per-participant lanes alongside it.
- vexa-bot: also record per-element (per-tile) streams to separate lanes before the mix.
- Store lanes under a **separate prefix** (NOT under the master's chunk prefix — the finalizer recursively lists the prefix and concatenates every non-master object, so co-locating lanes would contaminate the master: `recording_finalizer.py:451-452`, `storage.py:527-558`).
- Extend the upload API so N audio lanes do not collapse (today `media_files` is keyed by `media_type`: `recordings.py:429-431`). Treat `meeting.data.recordings[].media_files` as canonical, not `MediaFile` alone.
- Handle **lane/DOM-name instability**: Meet falls back to a random DOM id when the participant id is absent, and speaker-identity locks track→name (`googlemeet/recording.ts:210-223`, `speaker-identity.ts:54-80`); define rename/rejoin/DOM-recycle behavior.
- Deferred: transcribe per lane. **Solo tile → exactly one cluster → auto-named from the tile’s DOM name (zero manual work).**

### Phase 3 — Room-share detection + sub-speaker naming
- Run diarization **scoped inside each lane** (constrained by that tile’s DOM speaking intervals). One lane yielding K>1 stable clusters ⇒ shared mic detected.
- Solo → auto-confirm. Shared → K sub-clusters surfaced as “要確認”; user names each **once**, applied to all their segments (stable cluster ⇒ no jitter).

### Phase 4 — Voiceprint enrollment (auto-naming, cross-meeting)
- New store: `speaker_profiles` (tenant-scoped person, display_name) + `voiceprints` (profile_id, embedding vector, source, quality). Vector similarity (cosine) match with threshold.
- Speaker-embedding extractor as an **adapter** (external tool contract). Extract one embedding per cluster.
- At transcription: cluster embedding → nearest enrolled profile above threshold → **suggest**, then (staged rollout) human-confirm before it becomes auto-apply. Below threshold → “要確認”. Immediate auto-apply is deferred until false-match rates are measured.
- Enrollment flows: (a) explicit enroll (reference audio); (b) **implicit** — when a user names/corrects a cluster, offer “この声を◯◯として登録” so the same voice (incl. room-sharers) is auto-named next time and across meetings.
- **PII/biometric governance (mandatory):** voiceprints are biometric PII (California Civil Code §1798.140 treats voiceprint-extractable recordings as biometric information requiring specific/informed/affirmative consent). Require per-tenant isolation, encryption at rest, per-subject consent capture, purpose limitation, audit log, retention policy, a delete/opt-out API that cascades to all voiceprints, and a calibrated match threshold with human review. This triggers preflight PII flagging and human approval.

## Preconditions / Blockers (from Codex Plan Critic — must clear before the named phase)

1. **Phase 1:** formal Soniox STT adapter contract + fixture returning `segments[].speaker`/cluster id (current `contracts/stt/v1` has no speaker field). — **RESOLVED / feasible** (research `.pipeline/evidence/speaker-attribution-voiceprint/soniox-capability-research.md`): Soniox **async** (`stt-async-v5`) with `enable_speaker_diarization: true` returns per-**token** `speaker` (numeric, ≤15 speakers), and async has materially better diarization accuracy than realtime — ideal for the deferred path. Remaining adapter work: Soniox returns token-level, NOT OpenAI `verbose_json`, so the adapter must fold tokens→segments carrying a cluster id. Well-scoped, not a research risk.
2. **Phase 1 (DB):** approved large-table **online-migration** procedure for `transcriptions` (~507K rows in practice). Startup schema-sync does a plain `ALTER TABLE ADD COLUMN` + `index.create()` (`database.py:90-97`, `schema-sync/sync.py:151-164`); use nullable column, batched backfill, `CREATE INDEX CONCURRENTLY`, and a rollback path instead.
3. **Phase 2:** lane-id propagation design across upload metadata → storage path → JSONB `media_files` → finalizer exclusion → callback payload; and full lane/master prefix separation.
4. **Phase 4:** approved biometric consent / retention / delete / threshold / human-review spec before any voiceprint persistence model is built. Note: **Soniox provides no enrollment/voiceprint/cross-session identity** (confirmed) — a separate speaker-embedding adapter (SpeechBrain/ECAPA, pyannote, or hosted) is required; Soniox clusters only feed it per-cluster.

Evidence: `.pipeline/evidence/speaker-attribution-voiceprint/codex-plan-critique.md`.

## Non Goals

- Replacing or changing the existing mixed `master.webm` playback/download path.
- Real-time (live) speaker correction — this plan targets the post-meeting deferred transcript.
- Cross-tenant/global voiceprint sharing. Profiles are tenant-scoped only.
- Choosing the final embedding vendor in this document (decided under adapter-contract during Phase 4 planning).
- Telegram/MCP/other surfaces beyond dashboard for the correction UI (later).

## Verification Contract

Link: `.pipeline/plans/speaker-attribution-voiceprint/verification-contract.md`

## S/M/L Decision

| Field | Value |
|---|---|
| size | L |
| reason | Cross-service (vexa-bot capture, meeting-api pipeline, dashboard), **schema migrations** (speaker_cluster, MediaFile participant dim, speaker_profiles/voiceprints), touches the regression-scarred recording/finalizer path, and introduces **biometric PII** (voiceprints). Playback-correctness and privacy sensitive. |
| remaining uncertainty | Soniox diarization exposure through the current adapter; embedding-model choice/hosting; consent + retention UX. |
| human gate required | yes (L policy + PII path + hash-bound approval) |
| tribunal required | yes (config `tribunal: required_for_l`) |

## Implementation Notes

- Ship Phase 1 first: it already makes “same voice = one-click fix” reliable on the current mixed master and delivers most of mode ①’s improvement with the smallest blast radius.
- Run GitNexus `impact` on `map_speakers_to_segments`, `run_deferred_transcription`, `_parse_segments`, `recording_finalizer` builders, and `MediaFile`/`Transcription` models before editing; report HIGH/CRITICAL before proceeding.
- Keep original auto-assigned labels in a sidecar column/mapping so all manual/auto changes are reversible.
- Each phase is independently mergeable; treat Phases 2–4 as separate L sub-plans with their own contracts/approvals.

## Risks

- Recording pipeline is fragile (Pack U/M/G regression history around chunk order/flush/master assembly); Phase 2 must add lanes without perturbing the mixed-master path.
- Per-participant lanes increase storage and STT cost roughly ×(active participants).
- Diarization quality on short/overlapping utterances may still require manual touch-up.
- Voiceprints: biometric PII → legal/consent exposure; embedding drift and false-match risk require a conservative threshold + human confirmation before auto-applying a name.
- Migrations on `transcriptions` (large table) need online/backfill strategy.

## Codex Plan Critique

- critique file: `.pipeline/evidence/speaker-attribution-voiceprint/codex-plan-critique.md` (Codex/codex-rescue, independent runtime).
- factual corrections adopted: diarization is clobbered only when DOM events exist (not "always discarded"); `Transcription.speaker` holds labels fine — cluster/profile identity is what's missing.
- adopted into refine: Soniox adapter contract as Phase-1 precondition; Redis live-cache invalidation + Drive requeue on PATCH; `speaker_cluster` propagation to API schema + dashboard types; `mode="replace"` must preserve manual corrections; lane-id propagation + prefix separation + lane/DOM-name stability for Phase 2; tile-level-only DOM caveat + false-split/short-utterance/overlap ACs for Phase 3; CA Civil Code §1798.140 consent specifics + staged suggest→confirm→auto for Phase 4; online-migration procedure for the 507K-row `transcriptions` table; Phase 1 sub-split 1a/1b/1c.
- rejected (critic counter-points I keep as-is): Phase 1 needs no capture change (already scoped so); public share links re-fetch fresh (only the Redis transcript cache is the staleness — handled); speaker column type is adequate.
- needs human: yes — PII/biometric consent + retention decision, and final approval per L policy.
