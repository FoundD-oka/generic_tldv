# Sidechain Synthesis — speaker-attribution-voiceprint (Phase 0 + Phase 1)

Date: 2026-07-08. Reviewer: Codex rescue subagent (independent runtime,
adversarial diff review). Scope: working-tree diff for issues #21–#24.
All findings recorded in `.pipeline/rules/hd-findings.jsonl` (source=tribunal).

## Findings and resolutions

| # | Severity | Finding | Resolution | Regression test |
|---|---|---|---|---|
| 1 | blocker | PATCH speakers はactive会議でも実行でき、ライブRedisセグメントhash（collectorの未フラッシュ書込先）を削除しうる | ステータスゲート追加: completed/failed以外は409、UPDATE・cache削除前に拒否（meetings.py） | `test_active_meeting_is_rejected_before_any_mutation` |
| 2 | blocker | merge(1,3→佐藤)後に代表cluster 1を田中へrenameすると、corrections[3]が佐藤のまま残り、mode=replaceで3が佐藤として復活 | `speaker_corrections.aliases`（source→representative）を導入。renameは代表clusterのエイリアス全員へ伝播、連鎖mergeも再ポイント | `test_rename_of_merged_representative_covers_source_clusters`, `test_merge_records_aliases_for_later_renames` |
| 3 | major | Drive export running 中の話者更新が再キューされず、doneのまま古い名前のファイルが残る | running時は`rerun_requested`フラグを立て、exporterがdone確定前に行を再取得してフラグ検知→queuedへ自己再キュー（同一file_id維持） | `test_requeue_during_running_export_flags_rerun_instead_of_requeue`, `test_run_drive_export_requeues_when_content_changed_mid_export` |
| 4 | major | meeting.data のread-modify-writeが行ロックなしで、並行PATCHでcorrectionsが失われる | 所有権SELECTを`WITH FOR UPDATE`＋populate_existingに変更（並行PATCH直列化）。exporterのdone確定前再取得で export↔PATCH 間の上書きも解消 | `test_ownership_select_takes_row_lock` |
| 5 | major | モデルに追加したindexを起動時schema-syncが非CONCURRENTLYで作成しうる（migration未実行デプロイ時、507K行で書込ブロック） | Indexに`info={'online_only': True}`、schema-sync `_sync_indexes`がスキップ（新規テーブルのcreate_all経路は従来どおり） | `test_sync_indexes_skips_online_only_but_creates_normal_ones`, `test_cluster_index_is_marked_online_only` |
| 6 | minor | merge/rename成功後も旧話者名のフィルタが残り、改名後セグメントが隠れる | 更新成功時に`setSelectedSpeakers([])`でフィルタ解除（transcript-viewer.tsx） | UIロジック（build検証） |

## Test adequacy notes from reviewer (accepted / rationale)

- Soniox HTTPフローはモック段階（実API形状は運用検証時にゴールデン更新する前提、
  adapter manifestのvalidation checksに明記済み）。
- meeting-api APIテストはモックDBベース（リポジトリの確立された規約 conftest.py に従う）。
  ロック・順序性は compiled SQL / call-order アサーションで補強した。
- ダッシュボードはロジック層（speaker-edit.ts）をユニットテスト、コンポーネントは
  build検証（リポジトリにコンポーネントレンダリングのテスト基盤なし）。

## Post-fix verification

- meeting-api: **375 passed / 10 skipped**（全suite、collector込み）
- dashboard: **73 passed / 13 files**、`npm run build` 成功
- transcription-service: 11 passed（変更なし）
- compileall: meeting_api / schema-sync OK

Blocker count after fixes: **0**.
