# Test Evidence — speaker-attribution-voiceprint (Phase 0 + Phase 1)

Date: 2026-07-08. Scope: issues #21 (Phase 0), #22 (1a), #23 (1b), #24 (1c).
Phases 2–4 (#25–#27) are NOT in this delivery.

## Commands and results

| Command | Result |
|---|---|
| `python -m pytest services/transcription-service/tests/test_soniox_adapter.py -q` (py3.11 venv) | **11 passed** — token folding, golden replay deterministic, backward compat |
| `cd services/meeting-api && PYTHONPATH=.:../../libs/admin-models python -m pytest tests -q --ignore=tests/test_integration_live.py --ignore=tests/collector` | **297 passed, 10 skipped** (includes new test_speaker_clusters.py 11, test_speaker_update_api.py 10) |
| `cd services/meeting-api && python -m pytest tests/collector -q` | **67 passed** |
| `python3 -m compileall services/transcription-service` | OK |
| `python3 -m compileall services/meeting-api/meeting_api` | OK |
| `cd services/dashboard && npm test` | **73 passed / 13 files** (includes new test_speaker_edit.test.ts 12) |
| `cd services/dashboard && VEXA_API_URL=http://localhost:8056 npm run build` | build success |
| `bash .claude/hooks/adapter-validate.sh` | pass (incl. new soniox-stt.adapter.json) |
| `node .gitnexus/run.cjs detect-changes -r generic_tldv` | 15 files / 34 symbols; risk "high" driven by additive changes to shared dashboard api.ts/vexa.ts modules (new optional fields + one new method) |

Note: venv at `<scratchpad>/venv` built from `~/.local/bin/python3.11`
(pytest, pytest-asyncio, httpx, fastapi, sqlalchemy, pydantic[email], redis,
psycopg2-binary, asyncpg).

## AC coverage map (verification-contract.md)

| AC | Evidence |
|---|---|
| P0-AC1 STT契約にspeaker/cluster | `contracts/stt/v1/README.md` diarization節、`soniox-stt.adapter.json` adapter gate pass、golden-2フィクスチャ |
| P0 交互2話者トークン折りたたみ | `test_fold_alternating_speakers_splits_on_speaker_boundary`、`test_golden_fixture_replay_is_deterministic` |
| P0 後方互換 | `test_fold_without_speaker_omits_field_for_backward_compat`; 既存 stt.v1 消費者無変更（golden-1不変） |
| P1-AC1 clusterがDOMに上書きされない | `test_parse_segments_keeps_cluster_ids_when_speaker_events_present` |
| P1-AC2 online migration | `scripts/migrations/20260708_add_speaker_cluster.py`（nullable add / batched backfill / CREATE INDEX CONCURRENTLY / down）、`test_migration_statements_are_online_safe`、`test_migration_batch_ranges_cover_id_space_exactly` |
| P1-AC3 クラスタ多数決の安定性 | `test_cluster_vote_stable_where_per_segment_mapping_flips`、Unknown閾値・タイブレークテスト |
| P1-AC4 rename/merge/reassign + undo | `test_rename_by_cluster_*`、`test_merge_clusters_*`、`test_reassign_*`（speaker_auto COALESCE保持をSQLコンパイルで検証） |
| P1-AC5 Redis無効化（commit後） | `test_redis_invalidation_happens_after_commit`（順序をside effectで検証） |
| P1-AC6 done Drive再キュー | `test_done_drive_export_is_requeued_on_rename`（file_id維持=重複作成なし、Drive PATCH更新実装） |
| P1-AC7 replaceで手動修正保持 | `test_replace_reapplies_manual_cluster_corrections` |
| P1-AC8 speaker_clusterのschema/型伝播 | `schemas.py TranscriptionSegment`、collector両経路(PG/Redis)、`api.ts RawSegment`、`types/vexa.ts`、build成功 |
| P1-AC9 export/Drive/AIへの反映 | DBが正: export txt/json・AIコンテキストはDB/REST経由（AC5で新名検証）; Drive mdは再キューで再生成 |
| P1-AC10 インラインrename+マージ+範囲付替UI | `transcript-segment.tsx`（クリック編集・一括/発話のみ）、`transcript-viewer.tsx`（統合入力・範囲選択ツールバー）、ロジックは `speaker-edit.ts` 12テスト、build成功 |
| P1-AC11 認可 | `test_unauthorized_patch_is_rejected`（401/403）、`test_other_users_meeting_returns_404` |

## GitNexus impact (pre-edit, CLAUDE.md mandate)

- `map_speakers_to_segments` upstream: LOW (5 impacted)
- `run_deferred_transcription` upstream: LOW (4)
- `_parse_segments` upstream: LOW (4)
- `Transcription` (model) upstream: **HIGH (20 impacted / 15 direct)** — mitigated: change is additive nullable columns only; no existing column/behavior modified; full suite green.
- `transcribe_audio` upstream: LOW (0)

## Fix round after Codex sidechain review (2026-07-08)

2 blockers + 3 majors + 1 minor — all fixed with regression tests
(details: `sidechain-synthesis.md` / `sidechain/synthesis.json`). Post-fix:
meeting-api **375 passed / 10 skipped** (full suite incl. collector),
dashboard **73 passed** + build success, transcription-service 11 passed.
Findings recorded via hd-record.sh (source=tribunal). Final diff hash:
`sha256:75f30469dbe6e6127afcc651ff1a51078e2abf576368125534a27a92e08b8f37`.
Independent QA judgment: `qa-judgment.json` → **needs_human**（全AC pass、
L方針の人間承認のみ残）。

## Deployment note (P1-AC2)

Run `scripts/migrations/20260708_add_speaker_cluster.py up` against prod
BEFORE deploying meeting-api; startup schema-sync then no-ops (column/index
already present, index matched by name). Rollback: `... down`.
Soniox routing: set `DEFERRED_TRANSCRIPTION_MODEL=stt-async-v5` +
`SONIOX_API_KEY` on transcription-service to enable diarization for the
deferred path; without them Whisper continues unchanged (DOM fallback).
