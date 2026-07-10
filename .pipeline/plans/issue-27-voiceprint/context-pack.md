# コンテキストパック — Issue #27 Phase 4（声紋登録による自動命名）

対象: プランナー。読み取り専用調査のみ、コード変更なし。
参照: `gh issue view 27` / `.pipeline/plans/speaker-attribution-voiceprint/plan.md`（Phase 4, Preconditions #4）/
`.pipeline/plans/issue-27-voiceprint/pii-policy-draft.md`（2026-07-10 承認済み: 保持24ヶ月、未登録者は照合のみ即時破棄）。

## 1. 新規テーブルの導入パターン（DB/schema-sync）

- `services/meeting-api/meeting_api/models.py:17-156` に `Meeting`/`Transcription`/`MeetingSession`/`Recording`/`MediaFile` が並ぶ。新テーブルはここに `class` を追加するだけ。
- `libs/schema-sync/schema_sync/sync.py:70-165` `ensure_schema()`:
  - `_sync_tables()`（L70-72）= `base.metadata.create_all(conn, checkfirst=True)` → **起動時に新テーブルを自動作成**（既存行がないので即時・非ロッキング）。
  - `_sync_columns()`（L75-108）= 既存テーブルへの `ALTER TABLE ADD COLUMN`（既存行がある場合は安全なデフォルト値を付与）。
  - `_sync_indexes()`（L121-146）は `info={'online_only': True}` の索引をスキップ（大規模テーブル対策）。
- **結論**: `speaker_profiles` / `voiceprints` / 同意・監査ログテーブルは**全て新規テーブル**なので `create_all(checkfirst=True)` で安全に自動作成可能。`scripts/migrations/20260708_add_speaker_cluster.py` のオンライン移行手順（バッチbackfill・`CREATE INDEX CONCURRENTLY`）は「既存の大規模テーブル(`transcriptions`, 507K行)への列追加」専用の対策であり、**新規テーブル作成には不要**（models.pyに追加してschema-syncに任せれば良い）。索引を `info={'online_only': True}` にする必要があるのは、将来 `voiceprints` が巨大化した場合の追加索引時のみ。
- オープン: embedding列の型（`BYTEA`/`JSONB`配列/pgvector拡張）は要決定。pgvector拡張の導入有無で類似度検索の実装（アプリ側cosine計算 vs SQL側）が変わる。現行スタックにpgvector利用の形跡なし。

## 2. クラスタ命名/訂正の実装（暗黙登録のフック点）

- バックエンド: `PATCH /meetings/{id}/transcripts/speakers` → `services/meeting-api/meeting_api/meetings.py:2032-2205` `update_meeting_speakers()`。`rename`/`merge`/`reassign` を処理し、`meeting.data["speaker_corrections"]`（`clusters`/`aliases`/`history`）に保存（L2176-2183, 詳細は`final_transcription.py:935-940`の読込側）。コミット後にRedisライブキャッシュを破棄し、Drive再エクスポートをキュー（L2185-2196）。
- フロント: `services/dashboard/src/components/transcript/transcript-viewer.tsx:1173-1189` の `onSpeakerEdit` コールバックが `buildSpeakerRename`/`buildSegmentReassign`（`services/dashboard/src/lib/speaker-edit.ts:10-37`）でPATCHペイロードを組み立て `applySpeakerUpdate` を呼ぶ。ここが**唯一の命名確定ポイント**。
- **暗黙登録の自然な差し込み場所**: `onSpeakerEdit` の成功後（`applySpeakerUpdate` 完了後）に「この声を◯◯として登録」を提示するUI（トースト/インラインボタン）。バックエンドは既存PATCHの直後に新規エンドポイント（例 `POST /speaker-profiles/enroll`）を別呼び出しする形が自然（PATCH自体に埋め込みAPI呼び出しを混ぜない — PATCHは同期的にDB更新のみ）。ただしPATCHのレスポンスに `cluster_id`（登録対象を一意特定するため。現状レスポンスは`speakers: List[str]`のみで cluster id を返していない — 要追加）を含める必要あり。

## 3. クラスタ音声の切り出し（埋め込み抽出用）

- ダウンロード: `services/meeting-api/meeting_api/storage.py` 各バックエンドに `download_file`/`download_file_range(path, start, end)`（バイトレンジ、L36-37, 209-214, 369-378）あり。**時間指定のクリップ切り出しAPIは無い**（バイトオフセット限定、時間→バイト変換はしていない）。
- deferred経路: `final_transcription.py:492-609` `_transcribe_lanes()` がレーン音声を丸ごとダウンロード→`_convert_audio_to_wav`（同 L751-780、ffmpeg `-ar 16000 -ac 1 -f wav` に変換）→STT呼び出し。変換後は16kHzモノラルPCM WAVなので、**クラスタのstart/end（秒）→サンプルオフセット（×16000×2byte）でのバイトスライスが可能**（新規実装が必要、既存関数は無い）。
- クラスタ単位のセグメント時間範囲は `_parse_segments` が返す `segments[].start/end/speaker_cluster`（`final_transcription.py:710, 880`）から集計可能。同一クラスタが非連続区間に分散する場合は複数区間の連結が必要（ffmpeg再呼び出し or PCM結合）。
- **結論**: 「クラスタ音声を切り出して埋め込み器に渡す」処理は**新規実装**（既存の`_convert_audio_to_wav`のffmpeg subprocessパターンを再利用可能）。ffmpeg `-ss/-t` で元のlane/master音声から直接切り出す方が、変換後WAVをPythonでスライスするより単純（変換とクリップを1回のffmpeg呼び出しに統合できる）。

## 4. アダプタ/外部ツール契約パターン

- 参考実装: `.pipeline/adapters/soniox-stt.adapter.json` + `services/transcription-service/soniox_adapter.py` + `services/transcription-service/main.py`（モデル名`stt-async-*`でルーティング、`is_soniox_model()`）。マニフェストは `kind`, `entrypoints`, `contracts.{inputs,outputs,evidence}`, `safety.{boundaries,forbidden_claims}`, `validation.{checks,minimum_artifacts}` を必須（`.claude/skills/adapter-contract/SKILL.md`）。
- 埋め込み器の配置場所の選択肢:
  - (a) `transcription-service`に同居: 現行 `requirements.txt`（`services/transcription-service/requirements.txt`）は`faster-whisper`（CTranslate2、**PyTorch不使用**）のみ。SpeechBrain/ECAPA・pyannote はPyTorch依存が大きく、Dockerイメージ（`nvidia/cuda:12.3.2-cudnn9-runtime`ベース）とVRAM設計に影響（現行はINT8で~2.1GB VRAM狙い）。追加すると依存衝突/イメージ肥大のリスク。
  - (b) meeting-api内のライブラリとして実装: ネットワーク越しの音声転送が不要になるが、meeting-apiは軽量APIプロセス想定でGPU/重量MLライブラリを持たせるのは責務が混ざる。
  - (c) 新規サービス（hosted API または別コンテナ）: adapter-contractの前提（外部ツールとして境界を明示）に最も自然に合致。ベンダー選定（SpeechBrain自前ホスト vs pyannote vs 外部API）は本タスクの範囲外（プラン記載の通り）。
- **結論**: 新規サービス化 or 外部ホスト型APIが既存アーキテクチャとの摩擦が最小。transcription-serviceへの同居はDockerイメージ/依存の観点で要検討事項として明記。

## 5. 暗号化・鍵管理の既存実装

- コードベース全体を`encrypt|Fernet|pgcrypto|cryptography`等で検索した結果、**アプリケーションレベルの暗号化実装は存在しない**。見つかったのはS3/MinIOの`secret_key`（`services/meeting-api/meeting_api/storage.py:109`, `meetings.py:810,1140,1357`）のみで、これは転送先認証情報であり、DBカラム暗号化とは無関係。
- `cryptography`/`fernet`は`services/meeting-api/requirements.txt`に無し。
- **オープン（プランナー判断必須）**: (1) アプリ層暗号化（`cryptography.Fernet`, 環境変数の鍵管理）を新規導入するか、(2) Postgres `pgcrypto`拡張でDB側暗号化するか。鍵のローテーション/保管場所（既存のシークレット管理方式が明文化されていないため、これも合わせて要決定）。

## 6. 監査ログの既存パターン

- 専用の`audit_log`テーブルやイベントログの仕組みは**存在しない**。最も近い前例は `drive_export` の状態遷移: `meeting.data["drive_export"]`（JSONB内）に`status`（`queued/running/done/failed/skipped`）を持たせ、`requeue_drive_export()`（`services/meeting-api/meeting_api/drive_export.py:101-146,274`）で更新するパターン、および`speaker_corrections["history"]`（`meetings.py:2179`、直近50件のリスト、`meeting.data`内JSONB）。
- いずれも**単一Meeting行のJSONB内に閉じたローカル履歴**であり、ポリシー文書が要求する`enroll/match/suggest/confirm/delete`の6種イベント×`subject_id`/`actor_user_id`/`timestamp`を横断的に追跡する**独立監査テーブルの前例はない**。Phase 4では専用`voiceprint_audit_log`テーブル（新規）が妥当と考えられる（drive_exportパターンのJSONB追記方式では、profile削除時のカスケード削除と矛盾しない永続性を保証しにくい — JSONBはMeeting行に紐づくが、音声PII削除後も監査ログは残す必要があるため、Meeting削除と結合しない独立テーブルが必須）。

## 7. マッチング・パイプラインのフック点

- `run_deferred_transcription`（`final_transcription.py:1044-`）はクラスタ命名（DOM投票 `_apply_lane_identity`等）→ 保存済み訂正の再適用（`speaker_corrections`読込 L935-940）を経て`Transcription`行を書き込む。声紋照合は**この後**（クラスタが確定した後、embedding抽出→照合）に挿入するのが自然 — 保存済み訂正（人間が既に名付けたクラスタ）を声紋サジェストで上書きしないため。
- `speaker_mapping_status`は**読み取り時に構造から導出される値**で、DBカラムではない（`collector/endpoints.py:237-253` `_derive_speaker_mapping_status`、現状は`None`か`"needs_review"`の2値のみ、レーンsub-cluster×未命名の構造条件で判定）。**声紋由来の`"suggested"`はこの関数の前提（純粋に構造から決まる）と合わない** — 声紋マッチ結果は状態（DB/JSONBに保存された事実）なので、`speaker_corrections`と同様に`meeting.data`内（例: `voiceprint_suggestions: {cluster_id: {profile_id, name, score}}`）に保存し、読み取り時に`_derive_speaker_mapping_status`相当のロジックへ**マージする一段追加**が必要（既存関数を拡張、または並列の第二導出ステップを追加）。
- Phase 1c/3で確立済み: `speaker_cluster`（`Transcription.speaker_cluster`, `models.py:75`）、レーンsub-cluster形式`lane:{laneKey}:{cluster}`（`final_transcription.py:432-449`）、`speaker_mapping_status`の値は現状`needs_review`のみ（`"suggested"`はPhase 4で新設）。

## 8. テナント/スコープモデル

- 「テナント」= **`user_id`**（`Meeting.user_id`, `models.py:21`、他テーブルも`user_id`列でスコープ）。組織/会社単位の階層は存在しない（`auth.py:15-21` `UserProxy`、`validate_request`はゲートウェイヘッダ`X-User-ID`かAPIキーで単一の`user_id`のみ返す。コードベース内`tenant`文字列はAzure AD関連のみで無関係）。
- 現行デプロイはカボス単一テナント運用（MEMORY: 日本語限定方針参照）だが、コード上のスコープ単位は`user_id`。`speaker_profiles`/`voiceprints`のテナント分離は**`user_id`によるフィルタ必須化**（DBクエリ・索引の両方、ポリシー文書§6と合致）として実装すればよい。

## プランナーへの主な未決事項

1. embedding格納の型/拡張（`pgvector`導入 or アプリ側cosine、`BYTEA`+暗号化 or `pgcrypto`）。
2. 暗号化方式の新規導入（既存に前例なし、ライブラリ追加が必要）。
3. 埋め込み抽出器の配置（新規サービス vs transcription-service同居 vs meeting-apiライブラリ）— Dockerイメージ/GPU/依存の影響を評価する必要。
4. クラスタ音声切り出し（時間範囲→PCMスライス or ffmpeg `-ss/-t`）は新規実装、既存の時間指定クリップAPIなし。
5. `voiceprint_audit_log`は独立テーブルが必要（既存はMeeting.data内JSONBローカル履歴のみで、Meeting/クラスタのライフサイクルと監査ログの保持要件が食い違う）。
6. `speaker_mapping_status="suggested"`は現状の「構造からの純粋導出」方式と非互換 — 状態保存＋マージの追加ステップが必要。
7. PATCH `/meetings/{id}/transcripts/speakers`のレスポンスに`cluster_id`が無いため、暗黙登録オファーのUIが対象クラスタを一意特定できない（追加が必要）。
