# Verification Contract — Speaker Attribution Correction + Voiceprint Enrollment

Scope note: acceptance criteria are grouped by phase. Each phase is a separately
mergeable L sub-deliverable; its ACs must pass before that phase's PR readiness.

## Phase 0 (precondition) — Soniox STT adapter contract

| # | Criterion | Verification |
|---|---|---|
| P0-AC1 | STT adapter contract exposes per-segment `speaker`/cluster id | Adapter manifest + fixture validated by `.claude/skills/adapter-contract`; contract doc updated from today's `start/end/text`-only `contracts/stt/v1`. |

## Phase 1 — Cluster-aware correction (mixed master)

| # | Criterion | Verification |
|---|---|---|
| P1-AC1 | Deferred pipeline keeps `tx_result.segments[].speaker` as the cluster source and does NOT let DOM overwrite it | Unit test: tx payload with diarization + present `speaker_events` → cluster ids survive (regression against current `map_speakers_to_segments` overwrite). |
| P1-AC2 | `Transcription.speaker_cluster` added via online migration without data loss | Migration test: nullable column, batched backfill, `CREATE INDEX CONCURRENTLY`, up/down; existing rows retain `speaker`. |
| P1-AC3 | Cluster → name uses overlap-seconds-weighted whole-cluster DOM vote (no per-segment jitter) | Unit test: synthetic overlaps where per-segment mapping flips names but cluster vote stays stable; covers Unknown-ratio + tie-break. |
| P1-AC4 | `PATCH /meetings/{id}/transcripts/speakers` supports rename / merge / reassign | API tests for each op; original auto-label preserved for undo. |
| P1-AC5 | Rename invalidates/updates the Redis live-segments cache | Test: after PATCH, transcript REST (Postgres+Redis merge) returns the new name, not the Redis-cached old one. |
| P1-AC6 | Rename requeues the Drive export even when status was `done` | Test: `drive_export.status == done` + rename → export re-queued and re-rendered with new name. |
| P1-AC7 | `mode="replace"` re-transcription preserves manual corrections | Test: correct a cluster, run replace, assert saved cluster→name is re-applied (not silently dropped). |
| P1-AC8 | `speaker_cluster` is exposed through API schema and dashboard types | Schema/type test + dashboard build; segment payload carries cluster id. |
| P1-AC9 | Correction is durable and flows to export/share/AI | Test: after rename, export txt/json + Drive markdown + AI-context reflect the new name. |
| P1-AC10 | Dashboard viewer supports inline rename + merge + range reassign | Dashboard component/unit test; build passes. |
| P1-AC11 | Auth: only authorized users can mutate a meeting's speakers | API test asserts 401/403 on unauthorized PATCH. |

## Phase 2 — Per-participant lanes preserved (capture/storage change)

| # | Criterion | Verification |
|---|---|---|
| P2-AC1 | Mixed `master.webm/.wav` playback path is byte-for-byte unchanged | Regression test on finalizer output for a fixed chunk fixture. |
| P2-AC2 | Lanes stored under a SEPARATE prefix; finalizer never concatenates lane audio into the master | Test: finalizer prefix-list excludes lanes; master fixture unchanged with lanes present. |
| P2-AC3 | Lane id propagates through upload metadata → storage path → JSONB `media_files` → callback | Integration test asserts lane id present at each hop; N audio lanes do NOT collapse under `media_type`. |
| P2-AC4 | Deferred transcribes per lane; solo tile → one cluster auto-named from DOM | Test: single-voice lane yields exactly one cluster mapped to the tile name, zero manual step. |
| P2-AC5 | Lane/DOM-name instability handled (random DOM id, rename, rejoin, DOM-recycle) | Test matrix over rename/rejoin/fallback-id → lanes remain attributable, no cross-person leakage. |
| P2-AC6 | `media_files` participant/lane-dimension migration is reversible | Migration up/down test. |

## Phase 3 — Room-share detection + sub-speaker naming

| # | Criterion | Verification |
|---|---|---|
| P3-AC1 | Lane-scoped diarization detects K>1 voices in one tile as shared-mic | Test on a fixture lane containing two voices → two stable clusters flagged "要確認". |
| P3-AC2 | Naming a sub-cluster once applies to all its segments | Test: single rename updates every segment of that cluster; other clusters untouched. |
| P3-AC3 | Solo lanes are never split into spurious sub-speakers (false-split guard) | Test: single-voice fixture → no false "要確認". |
| P3-AC4 | Short-utterance and overlapping-speech inputs do not corrupt attribution | Test on fixtures with <1s turns and cross-talk; assert bounded, documented degradation, no wrong-name silently applied. |
| P3-AC5 | DOM is treated as tile-level only, never as sub-speaker ground truth | Test asserts sub-clusters within one tile are not auto-named from the tile DOM name. |

## Phase 4 — Voiceprint enrollment (biometric PII)

| # | Criterion | Verification |
|---|---|---|
| P4-AC1 | `speaker_profiles` + `voiceprints` are tenant-scoped and isolated | Test: profile from tenant A never matches tenant B. |
| P4-AC2 | Cluster embedding matches enrolled profile above threshold → auto-name | Test with fixture embeddings: match ≥ threshold auto-names; below → "要確認". |
| P4-AC3 | Implicit enrollment: naming a cluster can register that voice | Test: correcting a cluster creates a voiceprint; next meeting auto-names the same voice. |
| P4-AC4 | Consent required before any voiceprint is stored | Test: enrollment blocked without recorded consent flag. |
| P4-AC5 | Delete/opt-out API removes profile + all voiceprints | Test: delete cascades; subsequent match falls back to "要確認". |
| P4-AC6 | Voiceprints encrypted at rest | Test/inspection asserts embeddings are not stored in plaintext. |
| P4-AC7 | Embedding extractor conforms to adapter contract | `.claude/skills/adapter-contract` manifest validated by adapter gate. |
| P4-AC8 | Auto-naming is staged suggest → human-confirm → auto (no immediate auto-apply) | Test: a threshold match produces a suggestion requiring confirmation until the rollout flag is enabled. |
| P4-AC9 | Consent capture is per-subject with purpose limitation + audit log | Test: no voiceprint persists without a per-subject consent record; audit entry written on enroll/match/delete. |

## Test Commands (baseline; extend per phase)

- `cd services/meeting-api && PYTHONPATH=. python -m pytest tests -q`
- `python -m compileall services/meeting-api/meeting_api`
- `cd services/dashboard && npm test`
- `cd services/dashboard && VEXA_API_URL=http://localhost:8056 npm run build`
- migration up/down check (alembic or project migration runner) for each schema phase
- `node .gitnexus/run.cjs detect-changes -r generic_tldv`
- `bash .claude/hooks/pr-ready-gate.sh speaker-attribution-voiceprint`

## Out of Contract

- Live/real-time correction.
- Vendor selection for the embedding model (settled in Phase 4 adapter contract).
- Non-dashboard correction surfaces.
