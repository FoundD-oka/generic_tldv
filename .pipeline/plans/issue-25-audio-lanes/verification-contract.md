# Verification Contract — issue-25-audio-lanes

- size: L（sml-decision.json 参照）
- external consultation: claude-fable-cli（optional）
- tribunal: required（L policy）
- Rev: v2（Codex批判＋ユーザー決定4項目反映）

## Must Pass（deterministic）

### 0. 批判由来の追加基準（v2）
- lane有効時に `/recordings`・`/recordings/{id}` の `RecordingResponse.model_validate()`
  が全pass（serializerがlane-*エントリを除外していること）。
- `lane-*` webm chunkのcontent-typeが `audio/webm`（upload・finalizer両方）。
- finalizerが `lane-{key}/master.webm` を生成し、かつ `playback_url` は
  audio|video のみ生成される（境界テスト）。
- sweepsがJSONB欠損のlane chunkを復旧し、audio playback_urlが有る会議でも
  lane未finalizeならsweep対象に残る。
- lane master生成後のlate-chunkで `storage_path` が巻き戻らない
  （U.7ガードのaudio-like一般化）。
- **フラグoff（default）時: botのupload metadata・混合master bytesが現行実装と
  完全一致**（バイト比較テスト）。
- all-or-nothing: 1レーンSTT失敗のfixtureで混合master経路の出力に全面フォール
  バックし、lane由来segmentが一切混入しない。
- cost cap実装evidence: `RECORD_PARTICIPANT_LANES` / `MAX_RECORDING_LANES` /
  `LANE_STT_CONCURRENCY` / レーン合計上限のenv読取りコードと、上限超過時の
  skipped metadata記録のテスト。
- `lane:{laneKey}` 形式の `speaker_cluster` が #23 merge/rename APIで
  操作可能（キー空間互換テスト）。
- `lane_id_source` がmedia_files/segmentメタに記録される（gm-id監査用）。

### 1. 混合master不変（最重要・回帰封じ）
- 単体: finalizerのprefixリスティング入力に `lane-*` キーが混入しないこと。
  `_chunk_prefix("recordings/u/r/s/audio/000000.webm")` 配下のリスティングに
  `recordings/u/r/s/lane-abc123/000000.webm` が含まれない検証
  （storage fake で `list_objects_bounded` の返却集合を直接assert）。
- 回帰: レーンupload有無の2条件で `finalize_recording_master` が生成する
  master連結対象キー列が同一。

### 2. media_files 潰れ解消
- 同一recordingへ `media_type=audio` ×1、`lane-aaaaaaaaaa` ×2、`lane-bbbbbbbbbb` ×2 を
  upload → `meeting.data.recordings[].media_files` が3エントリ
  （audio/lane-a/lane-b）で、それぞれ独立の cumulative bytes / chunk_count を持つ。
- Pack U.7回帰: lane masterがfinalized後のlate-chunkで `storage_path` が
  巻き戻らない（type単位の既存ロジックがlaneにも適用されること）。

### 3. lane-id 一気通貫伝播
- upload metadata（lane_id / lane_label / lane_id_source）→
  media_files エントリの `lane` サブオブジェクト → レーンmasterパス →
  deferred採用、のfixtureテスト。

### 4. 単独タイル自動確定
- レーンmaster 1本・レーン内クラスタ1 のfixtureで、当該レーンの全セグメントの
  `speaker == lane_label`、`speaker_cluster == "lane:{laneKey}"`。

### 5. フォールバック不変
- lane media_files が存在しない会議で、deferred（mode=replace含む）の出力が
  現行実装と同一（既存 `test_final_transcription.py` suite全緑で担保）。

### 6. bot側
- レーンrecorderのlifecycle: タイル出現→録音開始、消滅→final chunk送出。
- `RECORD_PARTICIPANT_LANES=false`（default）でレーン関連コードが完全不活性。
- `MAX_RECORDING_LANES` 超過時に新規レーンを録らず警告ログ。

## Required Commands

- `pytest services/meeting-api/tests/ -q`（全緑、特に test_final_transcription /
  test_speaker_clusters / test_sweeps_unfinalized_recordings / recordings系）
- `cd services/vexa-bot/core && npm test`（存在するsuiteの全緑）
- `cd services/dashboard && npx vitest run`（回帰確認のみ）
- `node .gitnexus/run.cjs detect-changes --repo generic_tldv`（想定シンボルのみ）
- GitNexus `impact`: `finalize_recording_master` / `upload_recording_chunk` /
  `run_deferred_transcription` / `startGoogleRecording` のupstream影響を実装前に記録

## Evidence Rule

- Evidence は `.pipeline/evidence/issue-25-audio-lanes/` に格納。
- L要件: tribunal report または sidechain synthesis 必須。
- 人間承認（approvals.jsonl、diff hash束縛）なしにPR readyにしない。
- 実装エージェントの自己申告をevidenceにしない（テストは独立再実行で記録）。
