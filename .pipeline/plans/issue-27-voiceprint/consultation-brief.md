# Fable Consultation Brief: issue-27-voiceprint

Generated: 2026-07-10T03:07:05.754009+00:00
Provider target: claude-fable-cli
Mode: final
Model: fable

## Safety And Boundaries

- You are an advisory reviewer, not the implementer.
- Use local file reads and read-only shell inspection only.
- Do not edit files, run write commands, commit, push, install dependencies, or change state.
- Keep the answer concise. Each finding should be at most two sentences plus evidence.
- Treat your answer as advisory review evidence. Local tests, source checks, and project evidence remain the source of truth.

## Required Context Summary

### 1. Original Task And Plan Step

Task id: issue-27-voiceprint
Plan step or checkpoint: Phase 4 (issue #27) voiceprint enrollment + suggest-only naming — final audit before PR readiness (biometric PII path)

Relevant plan artifacts:

```text
## plan.md

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

**hook配置**: `run_deferred_transcription`の最終transcript commit

[truncated; verify against the source artifact]

## verification-contract.md

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


## option-matrix.md

# Option Matrix — Issue #27 Phase 4: 声紋埋め込みモデル比較

関連: `.pipeline/plans/issue-27-voiceprint/research-brief.md`（仮説・証拠の詳細）。
本デプロイ前提: 単一テナント・セルフホスト（docker compose）・CPU前提（GPU保証なし）・
日本語会議音声・クラスタ単位（話者ダイアリゼーションのクラスタごと）に1埋め込み・
音声を第三者に送らないローカル処理が前提のプライバシー方針。

## 比較表

| モデル / サービス | ライセンス | ゲーティング | CPU実行性 | 品質シグナル | 運用負荷 | 日本語頑健性 | 推奨 |
|---|---|---|---|---|---|---|---|
| **SpeechBrain ECAPA-TDNN** (`speechbrain/spkrec-ecapa-voxceleb`) | Apache-2.0 | なし（即DL可） | 高（RTF<0.1報告、192次元埋め込み、バッチ処理に十分） | VoxCeleb1 EER 0.80–0.90%、192-dim、コサイン距離 | 低（pip install speechbrain、単一モデルDL、月間244万DL・活発な保守） | 未検証（VoxCeleb=英語クリーン音声が前提、モデルカードも他データセットでの性能を保証せず） | **一次候補** |
| pyannote/embedding | MIT | あり（HFトークン＋利用条件同意必須） | 高（x-vector/SincNetベース） | ECAPA世代と同等クラス（やや古めのアーキ、新パイプラインは商用側に重心） | 中（トークン管理・HF承認フロー依存・商用pyannoteAIへの誘導文言） | 同上の留保 | 不採用（ライセンスは問題ないが摩擦がSpeechBrainより明確に大きい） |
| **3D-Speaker CAM++**（Alibaba/ModelScope） | Apache-2.0 | なし | 非常に高（ECAPAの約半分のパラメータ/FLOPs、推論2倍以上高速） | 競争力のある精度、CN-Celeb等の多様/雑音条件コーパスでの学習実績あり | 中（ModelScopeツールチェーンへの依存、SpeechBrainほど英語ドキュメント/導入実績が枯れていない） | 未検証だが、多様な録音条件での学習実績は会議音声（雑音・反響あり）への汎化にプラスの可能性 | **フォールバック**（CPUレイテンシがボトルネック化した場合の第一候補） |
| WeSpeaker（ResNet/CAM++レシピ） | Apache-2.0 | なし | 高（CPU/GPU両対応、ONNX/JITエクスポート） | 研究・実運用向けに競争力のある精度 | 中（ツールキット導入の手間はSpeechBrainより高いが「使えない」レベルではない） | 未検証 | フォールバック（CAM++系を別ツールチェーンで運用したい場合の代替） |
| NVIDIA TitaNet（NeMoフレームワーク） | CC-BY-4.0 | なし（HFゲートなし） | 技術的には可（ONNX変換でCPU実行可）だが生態系はGPU志向 | 高精度（23Mパラメータ） | 高（Apex/Megatron Core/Transformer Engine等の重い依存、NVIDIA自身がコンテナ運用を推奨するほど導入が煩雑） | 未検証 | **除外**（本デプロイのCPU前提・単一テナント運用コストに見合わない） |
| Azure AI Speaker Recognition | N/A（提供終了） | N/A | N/A | N/A | N/A | N/A | **除外**（2025年9月30日付で完全retirement、以後API利用不可） |
| AWS（Amazon Connect Voice ID / Transcribe diarization） | N/A | N/A | N/A | Transcribeのdiarizationは匿名ラベルのみで本人識別不可 | N/A | N/A | **除外**（単独の話者ID APIが存在せず、Voice IDも2026年5月20日でサポート終了予定） |
| サードパーティ・ホスト型話者ID（例: AssemblyAI経由） | 各社規約 | 各社規約 | N/A（クラウド処理） | 高品質な場合あり | 低（自前運用不要）だがデータ主権を失う | N/A | **除外**（音声を第三者に送信する構成となり、承認済みPII方針の「ローカル処理前提」に抵触） |

## 推奨

- **一次候補（Primary）**: SpeechBrain ECAPA-TDNN — `speechbrain/spkrec-ecapa-voxceleb`
  - 理由: ライセンス摩擦ゼロ（Apache-2.0、ゲーティングなし）、2026年時点でも活発に保守、
    CPU実行が会議後バッチ処理の要件を十分満たす速度、コミュニティのしきい値運用実績
    （コサイン0.70-0.75）が豊富で運用ノウハウを転用しやすい。単一テナント・小規模の
    自社運用チームにとって「導入・保守コストが最小」であることが決め手。
- **フォールバック（Fallback）**: 3D-Speaker CAM++（Alibaba/ModelScope、Apache-2.0）
  - 採用条件: (a) 会議音数の増大でCPUレイテンシが実運用のボトルネックになった場合、
    または (b) 実測フェーズで誤マッチ率検証のためECAPA-TDNNと別アーキテクチャの
    クロスチェックが必要になった場合。ECAPAの約半分のパラメータ数で2倍以上の
    推論速度、かつ多様な録音条件（CN-Celebのようなノイジー/多ジャンル）での学習実績が
    会議音声への汎化に有利に働く可能性がある点で、単なる「劣化版の速い選択肢」ではなく
    実質的な代替になり得る。

## 初期コサイン類似度しきい値の提案

- **コミュニティ通例**: SpeechBrain `spkrec-ecapa-voxceleb` のコサイン距離スコアは
  一般に **0.70〜0.75**（[0,1]スケール）が同一話者/別話者の分岐点として使われている
  （出典: HuggingFace `speechbrain/spkrec-ecapa-voxceleb` Discussion #7、2026-07取得）。
  これはVoxCeleb（英語・クリーン音声）のトライアルで調整された経験則であり、
  本デプロイの音声条件（日本語・会議録音・クラスタ単位の短い/雑音混じりの発話区間）を
  代表するものではない。
- **提案する初期値**: 通例の上限に寄せた **0.75〜0.80** を初期値とし、
  かつ承認済みPII方針（`pii-policy-draft.md` §5「照合しきい値と人間レビュー」）が
  定める通り、この値は**未検証のまま自動適用（auto）へは使わない**。
  Phase 4実装時にサンプル会議のクラスタ埋め込みで実測（suggest→人間確認のログから
  ROC的に真陽性率/偽陽性率を算出）し、最終値を決定する。
  - 根拠: research-brief.md のH5検証で確認した「訓練データ言語・録音条件と運用時の
    言語・録音条件が異なる場合、コミュニティ通例のしきい値より劣化するリスクがある」
    という知見（VoxCeleb EER<1%に対しCN-Celebのような挑戦的コーパスでEER~8.8%という
    報告）を踏まえ、通例の下限（0.70）ではなく上限側（0.75〜0.80）から開始し、
    偽陽性（誤って別人にマッチ）よりも「要確認」に落ちる方を優先する保守的運用とする。
  - この初期値は**確定値ではなく実測前の作業仮説**であり、PII方針の「auto適用は
    誤マッチ率実測後に個別承認」という既存の承認事項の範囲内で運用する。

## 未解決事項（Phase 4実装計画に引き継ぐ）

- 日本語会議音声・クラスタ単位埋め込みでの実測EER/しきい値データが存在しないため、
  Phase 4実装の早期にサンプル会議での検証ジョブ（suggest結果のログ収集）を
  スケジュールする必要がある。
- CAM++をフォールバックとして正式採用する場合、adapter-contractは
  ECAPA-TDNNとCAM++の両方に対応できるよう「埋め込み次元・前処理・モデルIDを
  パラメータ化した抽象インターフェース」として設計するのが望ましい
  （将来のモデル切り替えコストを下げる）。


## research-brief.md

# Research Brief — Issue #27 Phase 4: 声紋抽出モデルの選定

日付: 2026-07-10。目的: Phase 4（声紋登録・声紋照合によるクラスタ自動命名）で使う
**話者埋め込み（speaker-embedding）抽出器**を、adapter-contract 化する外部ツールとして
選定するための調査。Soniox には声紋・会議横断本人識別機能がないことは既に確定済み
（`.pipeline/evidence/speaker-attribution-voiceprint/soniox-capability-research.md`）。
承認済み PII 方針（`.pipeline/plans/issue-27-voiceprint/pii-policy-draft.md`）は
「ローカル処理前提・未登録者は照合のみで即時破棄・24ヶ月保持・暗号化」を既に確定している。

## リフレーミング

文字どおりの依頼は「埋め込みモデルを1つ選ぶ」だが、それだけでは実際の意思決定を
支えられない。以下の理由で、決定事項を「モデル選定＋しきい値運用手順」の2点セットに
広げる：

1. ホスト型API（H4）は調査以前から方針上ローカル処理前提のため実質選択肢に入らない
   （調査は「本当に排除できるか」の確認作業になる）。
2. 日本語音声への頑健性（H5）は「モデルを選べば解決する」問題ではなく、
   **しきい値を実測で校正する運用**が必要という結論になりやすい（後述）。これは
   PII 方針が既に定めている「suggest→人間確認→auto」の段階移行方針と整合するため、
   モデル選定の結論は「初期しきい値は保守的に置き、実測してから調整する」という
   既存方針を補強する形になる。

以上を踏まえ、本ブリーフはモデル比較（H1-H4）と、しきい値運用への含意（H5）を
分けて記述する。

## 仮説（調査前に記述）

- **H1**: SpeechBrain ECAPA-TDNN（`speechbrain/spkrec-ecapa-voxceleb`）が2026年時点でも
  セルフホスト・CPU話者照合の実務的デフォルトである — 活発に保守され、ライセンスが
  寛容で、会議後バッチ処理として十分な速度でCPU動作する。
- **H2**: pyannote/embedding（または pyannote community モデル）は同等品質だが、
  ゲーティング/ライセンス摩擦（HFトークン、利用条件同意）が再現可能なデプロイに
  影響する。
- **H3**: WeSpeaker / NVIDIA TitaNet / 3D-Speaker は精度面で有利な代替だが、
  依存が重い（またはGPU志向）で、本デプロイの運用コストに見合わない。
- **H4**: ホスト型話者IDAPI（Azure Speaker Recognition、AWSなど）は退役/制限済み、
  または本デプロイのプライバシー方針と矛盾するため実質除外される。
- **H5**: 日本語音声はx-vector/ECAPA系埋め込みの性能を実質的に劣化させない
  （言語頑健）ため、日本語専用モデルは不要である。

## 検証結果

### H1 — SpeechBrain ECAPA-TDNN: 支持（確信度: 高）

- ライセンス: **Apache-2.0**。ゲーティングなし（HFトークン不要、即ダウンロード可）。
  （出典: https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb, 2026-07 取得）
- 保守状況: 2026年に入っても `speechbrain/speechbrain` 本体リポジトリはコミット
  活動が継続（2026-05-03更新確認）、関連リポジトリ（benchmarks, HyperPyYAML）も
  2026年に更新あり。モデル自体のダウンロード数は月間244万件、328 likes と
  実運用での採用実績が大きい。
  （出典: GitHub `speechbrain/speechbrain` activity, HF model page, 2026-07 取得）
- CPU性能: リアルタイム話者ダイアリゼーションでECAPA-TDNNを埋め込み抽出に使う
  参照実装が「CPUのみでRTF<0.1、定常レイテンシ約5.5秒」という報告あり
  （本タスクは会議後バッチ処理なのでリアルタイム制約はさらに緩い）。
  （出典: Springer JASMP 2024 論文, arXiv 2506.19875, 2026-07 取得検索）
- 精度: VoxCeleb1テストでEER 0.80–0.90%（s-norm有無）。192次元埋め込み、
  コサイン距離でスコアリング。
- **反証探索の結果（H1を弱める材料）**: 同じ調査で **CAM++**（3D-Speaker/ModelScope,
  Apache-2.0）がECAPA-TDNNの約半分のパラメータ数・FLOPsで2倍以上の推論速度、
  同等以上の精度という報告を確認した（arXiv 2303.00332 他）。これはH1の
  「実務的デフォルト」という位置づけそのものを覆すものではないが、**CPUレイテンシが
  ボトルネックになった場合の妥当な代替**として無視できない。ただしCAM++は
  SpeechBrainほど成熟した英語ドキュメント・単一パッケージ導入体験を持たず、
  ModelScope系ツールチェーンへの依存が増える点で運用コストはSpeechBrainより高い。
  → 結論は変えず、フォールバックとして記録（option-matrix参照）。
- モデルカードの明記事項: 「SpeechBrainチームはVoxCeleb以外のデータセットでの
  性能について保証しない」との免責記載あり。日本語会議音声はこの「保証外」領域に
  該当する（H5と関連）。

### H2 — pyannote/embedding: 部分的に支持（確信度: 高＝摩擦の存在、中＝実害の大きさ）

- ライセンス: **MIT**（技術的には商用利用も許可）。
- ゲーティング: 確認された。HuggingFace上で「利用条件への同意」が必須で、
  アクセストークンを発行してモデルロード時に渡す必要がある
  （出典: https://huggingface.co/pyannote/embedding, 2026-07 取得）。
- 商用誘導: モデルカード自体が「本番で使うなら pyannoteAI（商用サービス）への
  切り替えを検討してほしい」と明記し、企業ユーザーには寄付や商用相談を促す文言がある。
  ライセンス上のブロッカーではないが、**運用上の摩擦（トークン管理、HF承認フローへの
  依存、ベンダーからの商用誘導）は仮説どおり実在する**。
- 保守状況: pyannote.audio 2.1系列として現在も配布されているが、開発リソースは
  pyannoteAI（商用）側に重心が移っている兆候がある（`community-1`/`precision-2`という
  ティア分けの登場）。
- 判定: H2は支持。SpeechBrainがゼロ摩擦（トークン不要・単一Apache-2.0）である一方、
  pyannoteは技術的に使えるが小さな運用コストが常に乗る。単一テナント・小規模運用の
  本デプロイでは、この差が意思決定を動かすほどではないにせよ、SpeechBrainを選ぶ
  積極的な理由になる。

### H3 — WeSpeaker / TitaNet / 3D-Speaker: 部分的に支持、要修正（確信度: 中）

- **NVIDIA TitaNet（NeMo）**: ライセンスは**CC-BY-4.0**。推論自体はONNX変換で
  CPU実行が可能だが、NeMoフレームワーク全体は Apex / Megatron Core /
  Transformer Engine など重い依存を要求し、NVIDIA自身がコンテナ利用を推奨する
  ほど導入が煩雑（出典: NVIDIA NeMo公式ドキュメント, 2026-07 取得）。
  → **仮説どおり、GPU志向・運用コスト高で本デプロイには不向き**（支持）。
- **WeSpeaker**: ライセンス**Apache-2.0**。CPU/GPU両対応、ONNX/JITエクスポート対応で
  「研究・実運用向け」を明確に志向したツールキット。GPUはクラスタリング処理で
  CPU比約3倍高速化との報告があるが、埋め込み抽出自体はCPUでも動作する。
  SpeechBrainより導入の手間はあるが「使えない」ほどの重さではない。
- **3D-Speaker（Alibaba/ModelScope）**: ライセンス**Apache-2.0**。CAM++を含み、
  H1のセクションで述べた通り軽量・高速。
- **反証**: 「精度面で有利だが運用コストに見合わない」という当初仮説は
  TitaNet/NeMoには当たるが、WeSpeaker・3D-SpeakerのCAM++には当てはまらない
  ——これらは**軽量かつCPUフレンドリー**であり、単に「SpeechBrainほど枯れた
  単一パッケージ体験ではない」という程度の差。H3はTitaNetについては支持、
  WeSpeaker/3D-Speakerについては仮説を修正（除外ではなくフォールバック候補）。

### H4 — ホスト型話者IDAPI: 支持（確信度: 高）

- **Azure AI Speaker Recognition**: **2025年9月30日付で退役（提供終了）確定**。
  以後APIアクセス不可。Microsoft公式が pyannote/SpeechBrain等のOSSをリアルタイム
  ダイアリゼーションの代替として案内。
  （出典: Microsoft Q&A / Azalio retirement notice / picovoice blog, 2026-07 取得）
- **AWS**: 単独の話者識別APIは現在も存在しない。Amazon Transcribeのダイアリゼーションは
  匿名ラベル（Speaker 0, 1…）のみで本人識別・会議横断照合はできないと明記。
  さらに Amazon Connect Voice ID（話者照合機能）自体が**2026年5月20日でサポート終了**
  予定であることを確認 — ホスト型話者IDはAWS内でも縮小方向。
  （出典: AWS公式ドキュメント, 2026-07 取得）
- サードパーティ（AssemblyAI等）はAWS Marketplace経由で話者識別を提供するが、
  音声を第三者（サードパーティAPI事業者）に送信する構成になり、承認済みPII方針の
  「ローカル処理前提」と矛盾する。
- 判定: H4は完全に支持。退役/縮小という事実面と、方針上の排除の両方が確認できた。

### H5 — 日本語音声への頑健性: 支持されない・要修正（確信度: 中〜高、重要な発見）

これが最も重要な反証結果である。

- 「言語ミスマッチはクロスリンガル話者照合の性能劣化の主因である」という一次文献が
  複数存在する（enrollment言語とtest言語が異なるトライアルでの劣化）。本デプロイは
  enrollment・照合とも日本語音声で統一されるため、この「言語ミスマ

[truncated; verify against the source artifact]
```

### 2. Approaches Tried And Failure Reasons

```text
[none recorded for this consultation]
```

### 3. Current Hypothesis

[not specified]

### 4. Questions To Decide

1. Is there any remaining PII leak path (API, webhook, logs, audit detail, exports, MCP/telegram surfaces) or consent-invariant gap that should block PR creation?
2. Are the tribunal fixes for the two criticals (webhook redaction single-source; with_for_update key-merge in the post-commit follow-up) correct and complete against the actual code?
3. Does the implementation genuinely satisfy the verification contract's Must Pass items (match-then-discard, disabled mode, retention sweep, transcript never delayed/failed, tenant isolation)?

## Decision Or Result Under Review

Implemented per approved plan v2 + PII policy (8 items + proxy-consent risk accepted): new voiceprint-service (ECAPA-TDNN CPU, token auth, streaming size cap, no vector logging), meeting-api voiceprint tables (consent_id NOT NULL invariant), Fernet encryption with disabled mode, lane-offset-aware cluster slicing, post-commit matching (zero transcript latency, all failures audited skips), suggestions redacted from API responses AND all webhooks (single redaction source), suggest-only with 0.78 threshold, 24mo retention sweep, dashboard suggestion chips + consent-worded enroll offers. Tribunal confirmed 12/12 findings (2 critical: webhook redaction divergence, post-commit lost-update race) — all fixed and re-verified (meeting-api 492, voiceprint-service 20, dashboard 121, tsc clean). Plan: .pipeline/plans/issue-27-voiceprint/plan.md; evidence: .pipeline/evidence/issue-27-voiceprint/

## Extra Context

```text
[no extra source file provided]
```

## Current Git Status

```text
 M AGENTS.md
 M CLAUDE.md
?? .pipeline/evidence/issue-27-voiceprint/test-results.md
?? "\343\203\241\343\203\242.md"

```

## Current Diff Stat

```text
 AGENTS.md | 2 +-
 CLAUDE.md | 2 +-
 2 files changed, 2 insertions(+), 2 deletions(-)

```

## Current Diff Excerpt

```diff
diff --git a/AGENTS.md b/AGENTS.md
index 4f330bc..c1bcde9 100644
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -119,7 +119,7 @@ Use:
 <!-- gitnexus:start -->
 # GitNexus — Code Intelligence
 
-This project is indexed by GitNexus as **generic_tldv** (15887 symbols, 28249 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.
+This project is indexed by GitNexus as **generic_tldv** (15916 symbols, 28323 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.
 
 > Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).
 
diff --git a/CLAUDE.md b/CLAUDE.md
index 30445ec..e62e106 100644
--- a/CLAUDE.md
+++ b/CLAUDE.md
@@ -88,7 +88,7 @@ See `.ai/BUILD.md`.
 <!-- gitnexus:start -->
 # GitNexus — Code Intelligence
 
-This project is indexed by GitNexus as **generic_tldv** (15887 symbols, 28249 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.
+This project is indexed by GitNexus as **generic_tldv** (15916 symbols, 28323 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.
 
 > Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).
 

```

## Required JSON Output

Return only JSON matching this shape:

```json
{
  "type": "object",
  "additionalProperties": true,
  "required": [
    "verdict",
    "summary",
    "findings",
    "confidence"
  ],
  "properties": {
    "verdict": {
      "type": "string",
      "enum": [
        "MUST_FIX",
        "SHOULD_FIX",
        "SHIP"
      ]
    },
    "summary": {
      "type": "string"
    },
    "confidence": {
      "type": "string",
      "enum": [
        "low",
        "medium",
        "high"
      ]
    },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": true,
        "required": [
          "id",
          "severity",
          "title",
          "evidence",
          "recommendation"
        ],
        "properties": {
          "id": {
            "type": "string"
          },
          "severity": {
            "type": "string",
            "enum": [
              "MUST_FIX",
              "SHOULD_FIX",
              "NOTE"
            ]
          },
          "title": {
            "type": "string"
          },
          "evidence": {
            "type": "string"
          },
          "recommendation": {
            "type": "string"
          }
        }
      }
    },
    "local_verification": {
      "type": "array",
      "items": {
        "type": "string"
      }
    }
  }
}
```
