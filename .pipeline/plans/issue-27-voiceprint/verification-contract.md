# Verification Contract — issue-27-voiceprint

- size: L（sml-decision.json 参照）
- external consultation: claude-fable-cli（optional、L required_for_l consultation対象）
- tribunal: required（L policy）
- human gate: required（PII方針§7の8項目＋v2追加「運用リスク受容」項目、ハッシュ束縛承認）
- Rev: v1（2026-07-10、plan v2＝Codex批判反映と同時に確定）

## Must Pass（deterministic）

### 0. 批判由来の追加基準（plan v2 Blocker/NH解決に対応）

- **consent不変条件（Blocker 1）**: `voiceprints.consent_id`が`NOT NULL` FKであることを
  スキーマから確認し、`voiceprint_consents`に対応する行を作らずに`voiceprints`へINSERTする
  テストが**トランザクション失敗**（IntegrityError/FK違反）で終わることをアサートする
  （同一トランザクション内でconsent行を後から作っても不可であることも確認）。
- **cascade整合性**: `speaker_profiles`削除で`voiceprints`と`voiceprint_consents`の両方が
  削除され、`voiceprint_audit_log.subject_profile_id`は`NULL`化されつつ行自体は残存する
  fixtureテスト。`voiceprints.consent_id`にも`ON DELETE CASCADE`が付与されていることを
  スキーマレベルで確認（片方向cascadeのみでのFK違反再発を防ぐ回帰）。
- **鍵欠落disabledモード（Blocker 2/NH-4）**: `VOICEPRINT_ENCRYPTION_KEY`未設定/不正な値で
  起動した状態で、(a) `POST /voiceprints/enroll-from-cluster`が503を返す、(b) matching
  ステップがskipされ`voiceprint_audit_log`に`skip`イベント（reason=key_missing相当）が
  記録される、(c) 既存`voiceprints`行への復号アクセスが発生しないことをモックで確認する
  fixtureテスト。
- **retention sweep（Blocker 3）**: `last_matched_at`（未マッチは`created_at`）が
  `VOICEPRINT_RETENTION_MONTHS=24`ヶ月超過した`voiceprints`が`_sweep_voiceprint_retention`
  で削除され`voiceprint_audit_log`に`delete`（`detail.reason="retention"`）が記録される
  fixtureテスト。未超過行が削除されないことの否定的テストを併記。
- **suggestions非露出（Blocker 5/NH-2）**: `speaker_suggestions`を含む`meeting.data`を持つ
  fixture会議で、`GET /meetings/{id}`（`MeetingResponse`）と`GET
  /meetings/{id}/transcript`のトップレベル`data`のいずれにも`speaker_suggestions`キーが
  現れないことをアサートする。segment単位応答には`speaker_suggestion:
  {candidate_display_name, similarity, status}`のみが含まれ、**`profile_id`が一切
  含まれない**ことを併せてアサートする。
- **match-then-discard（未登録者embedding即時破棄）**: 未登録クラスタ（`voiceprints`に
  一致なし）の照合を行うfixtureで、照合後に(a) 当該embeddingがDBの`voiceprints`または
  他のいかなるテーブルにも書き込まれていないこと、(b) ログ出力（stdout/logger呼び出し）に
  embeddingベクトルの生値が一切出力されないこと（構造化ログのフィールド走査で確認）を
  アサートする。
- **suggested overlayの経路別保持（ARC-4）**: 同一cluster_idに対しPG由来の
  `speaker_mapping_status`とRedis由来のライブ値が異なるfixtureで、
  `_get_full_transcript_segments`のPG/Redis merge後にoverlayが適用され、
  Redis側の値が最終的に勝つ（Redis-wins semanticsが保持される）ことを確認する。
  REST経由（`api.ts`マッパー）まで`speaker_suggestion`payloadが届くfixtureテストも含む。
- **replaceによるstale suggestion一掃**: `mode="replace"`で同一会議を再実行したとき、
  前回runの`speaker_suggestions`が新runの結果に上書きされ、旧runのサジェストが
  `completed_at`の異なるレコードとして残留しないことをアサートする。
- **transcript非遅延/非失敗（FC-20/latency budget）**: voiceprint-serviceを
  unavailable/timeoutにモックしたfixtureで、`run_deferred_transcription`の
  transcript本体commitは成功し、`final_transcription.status`が`failed`にならず、
  matchingステップの例外が呼び出し元に伝播しないことをアサートする（matchingは別コミット、
  per-embed timeout 15秒／全体budget 120秒を超えた場合はskip+audit）。
- **lane-offset切り出し正しさ（ARC-3）**: lane構成のfixtureで、lane clusterの切り出しが
  `segment.start/end - lane.start_offset_seconds`でlane masterから正しい区間を取得すること
  （mixed timelineへのshift後の値をそのまま使うと誤った区間になる回帰を防ぐ）。mixed
  clusterはsegment時間をそのまま使うことも併せて確認する。
- **affected_clusters echo（ARC-6/NH-3）**: merge操作を含むPATCHで、応答の
  `affected_clusters`が複数エントリ（各`{cluster_id, display_name, operation}`）を持つ
  こと。暗黙登録オファーは`operation="rename"`のエントリにのみ提示され、merge/reassignの
  エントリには提示されないことをアサートする。
- **audit_logイベント網羅**: `enroll`/`match_attempt`/`suggest`/`confirm`/`delete`/`skip`
  の6種別それぞれについて、対応する操作（登録・照合実行・サジェスト提示・人間確認確定・
  削除・鍵欠落等によるskip）で最低1件記録されることをfixtureで確認する。

## Required Commands

- `cd services/meeting-api && PYTHONPATH=. python -m pytest tests -q`（全緑、特に
  `test_voiceprint_matching` / `test_final_transcription` / `test_speaker_clusters`）
- `cd services/voiceprint-service && python -m pytest -q`（`/embed`決定性・golden
  fixture一致テスト、鍵欠落disabledモード相当のヘルスチェック/401テスト含む）
- `cd services/dashboard && npx vitest run`（サジェストバッジ・承諾/棄却・
  `affected_clusters`ハンドリング・REST mapperの`speaker_suggestion`伝搬テスト）
- `cd services/dashboard && npx tsc --noEmit`（型変更の整合確認）
- `node .gitnexus/run.cjs detect-changes -r generic_tldv`（想定シンボルのみ:
  `run_deferred_transcription` / `_get_full_transcript_segments` / `_sweep_voiceprint_retention`
  / `MeetingResponse`serializer / dashboard mapper・viewer関連）
- GitNexus `impact`: `run_deferred_transcription` / `_get_full_transcript_segments` /
  `MeetingResponse` / `start_sweeps`のupstream影響を実装前に記録

## Evidence Rule

- Evidence は `.pipeline/evidence/issue-27-voiceprint/` に格納。
- L要件: tribunal report または sidechain synthesis 必須（consent不変条件のDB保証、
  post-commit hookがtranscript成功判定に影響しないこと、削除cascade完全性、
  suggestions露出制御、鍵欠落disabledモードを重点レビュー）。
- PII human gate: PII方針§7の8項目チェックリスト＋plan v2追加「運用リスク受容」項目
  （代理登録の技術的非防止を明示受容）の**個別承認**をdiff hash束縛で記録する
  （approvals.jsonl）。8項目・運用リスク受容項目のいずれかが未承認のままPR readyにしない。
- 人間承認なしにPR readyにしない（AC1〜AC9＋本契約の追加基準の説明を承認材料に含める）。
- 実装エージェントの自己申告をevidenceにしない（テストは独立再実行で記録）。
