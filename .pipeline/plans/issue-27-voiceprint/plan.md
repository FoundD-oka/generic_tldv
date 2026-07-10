# Plan — Issue #27 Phase 4: 声紋登録による自動命名（生体PII）

> エピック: `.pipeline/plans/speaker-attribution-voiceprint/plan.md`（Phase 4節）
> 前提: Phase 1〜3（issue #21-24, #25, #26）実装済み。PII方針承認済み:
> `.pipeline/plans/issue-27-voiceprint/pii-policy-draft.md`（保持24ヶ月／未登録者は
> 照合のみ即時破棄／auto移行基準は実測後別途承認）。
> 状態: **DRAFT（計画のみ、製品コード未変更）**
> Rev: v2（2026-07-10、Codex批判反映）。差分要点は末尾「Codex Plan Critique」節参照。

## Request

Phase 1〜3で「クラスタを1回名付ける」が実現した土台に、声紋を登録すれば**同一人物の声を
次回以降・会議横断で自動命名候補として提示**できるようにする。いきなり自動適用はせず
suggest→人間確認→auto の段階導入とする（issue本文AC1〜AC9）。

## Context Pack要約

詳細: `context-pack.md`。v2はCodex批判の事実訂正（末尾節）を反映済み——hookは
「訂正再適用直後」ではなく**最終transcript commit後**、切り出しはlane/mixed分岐が必要、
`_derive_speaker_mapping_status`は照合側で同等条件を再計算する（この関数内では未呼出）。

1. **DB**: 新4テーブルは起動時`schema_sync.ensure_schema()`の`create_all(checkfirst=True)`
   で自動作成（`main.py:261-267`→`sync.py:149-162`）。新規空テーブルなのでオンライン
   移行手順は不要。
2. **暗黙登録フック**: `PATCH /transcripts/speakers`（`meetings.py:2032-2205`）が唯一の
   命名確定点。rename/merge/reassignを同時に受けるため単数`cluster_id`では対象を一意
   特定できない（§8で`affected_clusters`に解決）。
3. **音声切り出し**: 時間範囲→クリップAPIは無い。ffmpegパターンは再利用可だが
   lane/mixedで切り出し元とtime baseが異なる（§2で分岐）。
4. **暗号化・監査ログ**: 前例なし。独立監査テーブルも無し。
5. **マッチングのフック点**: 最終`segments`確定＋訂正再適用後、かつlane
   all-or-nothing fallback確定後（`final_transcription.py:1151-1175`）でなければ、
   捨てられるlane結果へembeddingを作るリスクがある（§6で解決）。
6. **テナント**: `user_id`がテナント単位（組織階層は無い）。

## 設計

### 1. 埋め込みサービス — 新規 `services/voiceprint-service`

- モデル: **SpeechBrain ECAPA-TDNN**（Apache-2.0, CPU RTF<0.1, 192次元）一次採用。
  フォールバックCAM++は明記のみ（本フェーズ未実装）。ホスト型APIはPII方針の
  「ローカル処理前提」に抵触のため除外。
- エンドポイント: `POST /embed`（wavバイト→192次元embedding配列）、`GET /health`。
- **運用面（Blocker 6解決）**: transcription-service構造を鏡写し——`API_TOKEN`認証
  （`VOICEPRINT_SERVICE_TOKEN`、compose env配布）、モデルキャッシュvolume＋ビルド時
  ダウンロードをデフォルト（起動時fallback downloadは保険）、Dockerヘルスチェック＋
  compose `depends_on: condition: service_healthy`、`MAX_ACTIVE_REQUESTS=2`＋
  semaphoreでバックプレッシャ、request timeout、payload size cap、torch CPU wheel使用
  明記、メモリ制限約1.5g、startup warmup推論（`main.py:211-260`相当を鏡写し）。
- adapter-contract: `.pipeline/adapters/voiceprint-embedder.adapter.json`。
  `safety.boundaries`に「第三者送信なし」「未登録者embeddingの保存責務は呼び出し元」を
  明記。golden fixtureで決定性を保証。

### 2. クラスタ音声の切り出し（lane/mixed分岐＝ARC-3解決）

- **mixed経路**: `source.storage_path`＋segmentの`start/end`をそのまま使用。
- **lane経路**: `lane_sources`を`lane_key -> source`にマップし、`lane.start_offset_seconds`
  で**lane-local時間へ戻して**（`start/end - offset`）lane masterから切る。根拠:
  lane音声はlane-localタイムラインで、mixed timelineへのshiftは`_shift_segment_times`
  （`:482-489`）が事後適用するため。
- 非連続segmentはffmpeg concatで結合、**最大30秒**（`VOICEPRINT_MAX_CLIP_SECONDS`）まで、
  合計5秒未満（`VOICEPRINT_MIN_CLIP_SECONDS`）はスキップし「要確認」に留める。
  **未登録クラスタの埋め込みは照合後即時破棄**（ディスク/DBに残さない）。
- AC追加（critique FC-13）: 5秒/15秒/30秒クリップの類似度スコア分布をaudit_logへ記録し
  比較可能にする（auto移行判断の実測基盤。本フェーズはログ収集のみ）。

### 3. データモデル（新規4テーブル、Blocker 1解決）

- `speaker_profiles`(`id`,`user_id` idx,`display_name`,`created_at`,`updated_at`)
- `voiceprints`(`id`,`profile_id` FK `ON DELETE CASCADE`,`consent_id` **NOT NULL** FK
  `voiceprint_consents(id)` `ON DELETE CASCADE`,`key_id`,`embedding_encrypted` BYTEA,
  `embedding_dim`,`embedding_model`,`source`,`quality`,`source_meeting_id`,
  `created_at`,`last_matched_at`)
- `voiceprint_consents`(`id`,`subject_profile_id` FK `speaker_profiles(id)`
  `ON DELETE CASCADE`,`scope`固定値,`method`(explicit_enroll|implicit_suggest_accept),
  `consented_at`,`consented_by`,`revoked_at`)
- `voiceprint_audit_log`（独立テーブル、delete後も生存）(`id`,
  `event`(enroll/match_attempt/suggest/confirm/delete/skip),`actor_user_id`,
  `subject_profile_id` FK `ON DELETE SET NULL`,`meeting_id` nullable,`detail` JSONB,
  `created_at`)

**同意不変条件（AC）**: `voiceprints.consent_id NOT NULL` FKにより、consent行なしの
voiceprint insertはDBレベルで不可能（テスト追加）。`voiceprints.consent_id`にも
`ON DELETE CASCADE`を付与——profile削除時、`profile_id`経路と`subject_profile_id`経路の
2系統のcascadeがPostgres内部順序に依存せず整合する（片方向のみでは発生し得るFK違反を
回避）。全テーブル`user_id`必須+idx、照合クエリは`WHERE user_id=:current_user`が先頭条件。
`DELETE /speaker-profiles/{id}`はこのFK設計により`profile→voiceprints→consents`が順序
非依存でカスケードし、`voiceprint_audit_log`に`delete`記録（独立テーブルのため残存）。
SLA 72時間は運用目標値。

### 4. 暗号化・鍵管理（Blocker 2/NH-4解決）

`cryptography.Fernet`。単一鍵`VOICEPRINT_ENCRYPTION_KEY`（env）＋将来のkey-ring
rotationに備えた`voiceprints.key_id`列（本フェーズは常に固定値）。**鍵欠落/無効時**:
起動時検証し「voiceprint機能disabledモード」に落とす——enrollは503、matchingは
skip+audit記録、既存行は復号を試みず不変。**ローテーション運用手順**（ops notes、
手順のみ）: 新鍵生成→旧鍵で全voiceprintsをdecrypt→新鍵でre-encrypt→`key_id`をbump→
旧鍵破棄。単一テナント規模のためバッチ実行想定、オンラインrotationは対象外。

### 5. 保持（Retention、Blocker 3解決・PII方針§3対応）

`sweeps.py`のsweepパターン（`_sweep_aggregation_retry`等と同形）を再利用し、新規
`_sweep_voiceprint_retention`を追加。日次相当の周期チェックで`last_matched_at`
（未マッチなら`created_at`）が`VOICEPRINT_RETENTION_MONTHS=24`ヶ月超過した
`voiceprints`を削除＋audit_logに`delete`（`detail.reason="retention"`）記録。
`start_sweeps()`のループに追加登録。AC: 超過fixtureで削除・audit記録、未超過は非削除。

### 6. マッチングフロー（hook配置=FC-4/5/ARC-2解決、露出制御=Blocker5/NH-2解決）

**hook配置**: `run_deferred_transcription`の最終transcript commit（`:1318-1354`相当）
**成功後**に、独立したフォローアップステップとして実行し、**別コミット**で
`meeting.data["speaker_suggestions"]`のみ更新する。例外はここで完全に捕捉し呼び出し元へ
再raiseしない——**transcriptの成功/失敗判定を一切遅延・変更しない**（critique FC-20への
回答: matchingはtranscript完了に対しゼロレイテンシ）。per-embed timeout 15秒、matching
全体budget 120秒、voiceprint-service unavailableはskip+audit。`mode=replace`再実行時は
新runの書き込み前に**stale suggestionをクリア**し、今回runの`completed_at`と結びつける。

**対象**: 最終segments確定後に`needs_review`相当（`speaker`空／lane sub-cluster形式含む）
のクラスタのみ。切り出し→embed→そのユーザーの`voiceprints`全件（復号済み）とコサイン
比較→最大類似度が`VOICEPRINT_SUGGEST_THRESHOLD`（初期値**0.78**、env調整可、
**option-matrixの提案初期レンジ(0.75-0.80)内**——NH-5指摘により「community通例の上限側」
という誤った根拠付けを訂正）以上でサジェスト生成。suggest専用のためFMR/FRRはaudit_logの
スコア件数から実測し、auto移行判断は本フェーズでは行わない。

保存先: `meeting.data["speaker_suggestions"]`（DBカラム追加ではない）。**露出制御**:
`meeting.data`はtranscript endpoint（`:481-488`）と`MeetingResponse`serializer
（`webhook_secret`のみ現状除外）を経由して広く返るため、同じ除外パターンで
**`speaker_suggestions`も汎用API応答から必ずストリップ**する。transcript segment単位の
応答には代わりに最小payload`speaker_suggestion: {candidate_display_name, similarity,
status}`のみ追加し、**`profile_id`はtranscript応答に一切含めない**。

**読み取りoverlay（ARC-4解決）**: `_get_full_transcript_segments`の**PG/Redis merge後**に
`speaker_cluster`単位でoverlayする。条件: 現status がneeds_review相当、`speaker`が空、
`speaker_suggestions[cluster].status=="suggested"`。Redis-wins semanticsは保持（merge後の
最終結果にoverlayするため）。`_derive_speaker_mapping_status`関数自体は変更しない。
**`Transcription.speaker`はsuggestionによって一切書き換えられない**。dashboard `api.ts`
REST mapperにも`speaker_suggestion`payloadを通す。

### 7. 同意・登録フロー

(a) **明示登録**: `POST /speaker-profiles/enroll`（API-only、UIは次フェーズ）。
(b) **暗黙登録**（主経路）: PATCH成功後、dashboardが「この声を◯◯として登録」を
デフォルト非選択で提示。受諾時`POST /voiceprints/enroll-from-cluster`が切り出し→embed→
`voiceprints`保存＋`voiceprint_consents`記録を**同一トランザクション**で実行（§3の
DB不変条件と一致）。**代理登録防止（Blocker 4解決）**: 技術的機構は持たず運用判断に
委ねる（変更なし）が、人間承認チェックリストに**「運用リスク受容」項目**を追加——
承認者は「同意の本人性はDB制約ではなく運用手順でのみ担保される」ことを明示的に受け入れる
（PII方針§7とは別枠でハッシュ束縛承認）。各経路でaudit_logに該当イベントを記録。

### 8. ダッシュボード＋PATCH応答（ARC-6/NH-3解決）

サジェストバッジ「候補: ◯◯ 87%」（§6の最小payloadを表示、`profile_id`は使わない）＋
承諾/棄却。PATCH応答は単数`cluster_id`ではなく**`affected_clusters:
[{cluster_id, display_name, operation}]`**（merge/reassignの複数cluster同時反映に対応）。
**暗黙登録オファーは`operation=rename`のクラスタに限定**（merge/reassignは対象クラスタの
一意性が保証できないため本フェーズ対象外）。既存rename UIに追加するのみ。

### 9. 日本語劣化リスク対応（H5）

実測ベンチマークが存在しないため、本フェーズは「保守的初期値+suggest専用+実測ログ収集」
までを実装する。auto移行の数値基準（PII方針OPEN DECISION C）は本フェーズでは確定しない。

## 変更対象

- `models.py`（新4テーブル、`consent_id`/`key_id`列）／`requirements.txt`
  （`cryptography`, `numpy`追加）
- `final_transcription.py`（lane/mixed分岐切り出し、post-commit follow-upの照合ステップ、
  `speaker_suggestions`永続化＋stale clear）
- `collector/endpoints.py`（PG/Redis merge後段overlay、transcript応答からの除外）
- `schemas.py`（`MeetingResponse`serializerに除外追加、segment応答に最小payload追加）
- `meetings.py`（PATCH応答`affected_clusters`、enroll/delete/承諾棄却エンドポイント）
- `sweeps.py`（`_sweep_voiceprint_retention`新規、`start_sweeps()`登録）
- 新規 `services/voiceprint-service/`（`main.py`,`requirements.txt`,`Dockerfile`,
  healthcheck, model cache volume）
- `.pipeline/adapters/voiceprint-embedder.adapter.json`（新規、golden fixture含む）
- `deploy/compose/docker-compose.yml`（`voiceprint-service`追加、token/healthcheck配線）
- `dashboard/src/lib/api.ts`（`speaker_suggestion`payload伝搬）
- `dashboard/.../transcript-viewer.tsx`/`transcript-segment.tsx`（バッジ、承諾/棄却、
  rename限定の登録オファー）
- テスト: `test_voiceprint_matching.py`（新規、consent不変条件・retention・redaction・
  overlay・lane offset含む）、`voiceprint-service/tests/`、dashboardバッジ/オファーテスト

## Out of Scope

- **auto適用**（移行基準は別途承認、PII方針OPEN DECISION C）
- クロステナント/組織横断のプロファイル共有
- 明示登録の専用ダッシュボードUI（次フェーズ）
- 埋め込みモデルのファインチューニング/日本語専用モデルの新規学習
- pgvector導入（規模的に不要）
- **代理登録に対する技術的防止機構**（運用リスク受容として人間承認、§7参照）
- 削除SLA 72時間の自動監視/エスカレーション
- CAM++への実際の切替実装、key-ring複数鍵の実運用（key_id列のみ予約）

## 検証契約要点

詳細は`verification-contract.md`。P4-AC1〜AC9に加え、v2で以下を追加: consent不変条件の
DBレベルテスト、鍵欠落時disabledモード、retention sweep、`speaker_suggestions`の汎用API
非露出、Redis-wins下でのoverlay保持、lane-offset切り出し正しさ、matchingがtranscript
成功判定を遅延/変更しないこと。

## S/M/L

| Field | Value |
|---|---|
| size | **L** |
| reason | 生体PIIパス（新4テーブル・暗号化・同意・監査・retention）、新規サービス、既存read pathへの状態merge、新規外部依存、UI変更 |
| human gate | yes（PII方針§7の8項目＋v2追加の「運用リスク受容」項目、ハッシュ束縛） |
| tribunal | yes（`required_for_l`。consent不変条件のDB保証、post-commit hookがtranscript成功判定に影響しないこと、削除cascade完全性、露出制御を重点検証） |

## Risks

- 初期しきい値0.78は実測前の作業仮説（対策: suggest専用＋audit_logへの全スコア記録）
- 新規サービス追加による運用複雑性（対策: transcription-service相当の運用装備を用意）
- 鍵ローテーションは手順のみ、実装は次フェーズ（disabledモードで安全側に倒す）
- 代理登録防止は運用判断依存（人間承認で明示リスク受容、単一テナント前提内で許容）

## 実装順

1. **人間承認（先に・ブロッカー）**: PII方針§7の8項目＋v2追加「運用リスク受容」項目を
   個別承認（ハッシュ束縛）。
2. **データモデル**: 4テーブル＋`consent_id`/`key_id`列、schema-sync自動作成確認、
   `cryptography`/`numpy`導入、暗号化/復号ユーティリティ＋鍵欠落disabledモードの単体テスト。
3. **voiceprint-service**: `/embed`,`/health`、token認証、healthcheck、model volume、
   compose定義、adapter manifest+golden fixture。
4. **クラスタ音声切り出し**: lane/mixed分岐、ffmpeg複数区間結合（30秒キャップ、
   5秒未満スキップ、5/15/30秒比較ログ）。
5. **照合ステップ**: post-commit follow-upとして追加、`speaker_suggestions`永続化＋
   stale clear、後段overlay、汎用API応答からの除外、`api.ts`伝搬確認。
6. **retentionスイープ**: `_sweep_voiceprint_retention`実装＋`start_sweeps()`登録。
7. **登録/同意/削除API**: `enroll-from-cluster`（同一トランザクション同意記録）、
   `speaker-profiles/enroll`、`DELETE`カスケード、PATCH`affected_clusters`、audit書き込み。
8. **dashboard**: サジェストバッジ+承諾/棄却、rename限定の登録オファートースト。
9. **統合テスト+GitNexus**: 全fixture（テナント分離／段階ロールアウト不変条件／
   consent不変条件／削除カスケード／暗号化＋disabledモード／retention／露出制御／
   lane offset切り出し／Redis-wins overlay保持／adapter準拠）、`impact`/`detect_changes`実行。
10. **evidence pack + tribunal + human承認**（AC1〜AC9＋v2追加AC証跡、ハッシュ束縛承認）。

## Codex Plan Critique

出所: `.pipeline/evidence/issue-27-voiceprint/codex-plan-critique.md`（Codexサンドボックスが
read-onlyでファイル書き込み不可のため、オーケストレータが最終メッセージ全文を転記した provenance注記あり）。

- **Factual Corrections（1-20）**: 全件反映済み。主要点: hook配置(#4,5,20)、offset(#6)、
  shared-mic衝突回避(#7)、Redis-wins(#8)、型は文字列で落ちない(#9)、meeting.data広範
  露出(#10)、PATCH単数不可(#11)、依存欠落(#14)、鍵管理未定(#15)、service運用未整備(#16)、
  しきい値言い回し(#17)、retention/代理同意ギャップ(#18)、DB保証不足(#19)。
- **Adopted-Recommended Changes（1-10）**: 全件採用——FK cascade明文化、hook配置と
  stale clear、lane/mixed分岐切り出し、overlay後段merge、最小payload追加、
  `affected_clusters`、鍵検証+disabledモード、voiceprint-service運用装備、露出最小化、
  latency/stale回帰AC。
- **Blocker解決（オーケストレータ決定、最終ユーザー承認待ち）**: 1. consent不変条件→DB制約
  （§3）／2. 鍵管理→単一鍵+key_id予約+disabledモード（§4）／3. retention sweep→§5＋実装順6
  ／4. 代理同意→技術対応せず「運用リスク受容」を人間承認チェックリストに追加（§7）／
  5. 露出制御→serializer除外+最小payload（§6）／6. voiceprint-service運用→§1。
- **Needs-Human dispositions**: NH-1（PII方針§7承認＋運用リスク受容項目）は人間承認
  チェックリストへ編入。NH-2〜NH-6はBlocker 5/2/ARC-6/NH-5/latencyの解決内容として上記に
  決定済みだが**すべて最終ユーザー承認待ち**（decided-in-refine、hash束縛承認前は未確定）。
- **Rejected-Counterpoints（1-5）**: Codex自身が不採用と判定した反論（新規空テーブルの
  オンライン移行不要／status enum懸念不成立／ffmpeg feasibility懸念不成立／pgvector必須論は
  弱い／needs_review競合はoverlay設計で解消）は本plan v2でも不採用のまま維持。
