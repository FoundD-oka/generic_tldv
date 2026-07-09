# Plan — Issue #25: 参加者別音声レーンの録音・保存（Phase 2）

> エピック: `.pipeline/plans/speaker-attribution-voiceprint/plan.md`（Phase 2節）
> エピック規定により Phase 2 は独立サブプラン（専用の契約・承認）として扱う。
> Issue: https://github.com/FoundD-oka/generic_tldv/issues/25
> Codex Plan Critic: `.pipeline/evidence/issue-25-audio-lanes/codex-plan-critique.md`（v2で反映済み）
> Rev: v2（2026-07-10、批判反映＋Needs-Human 4項目のユーザー決定反映）

## Request

- Source: GitHub issue #25（エピック #28 のPhase 2）＋ user指示（2026-07-10、#25着手）
- 目的: Google Meet の参加者別音声ストリームを混合前にレーンとして保存し、
  deferred STT がレーン単位で処理できるようにする。単独タイルは自動確定（手作業ゼロ）。

## Context Pack（コード検証済み・批判で補強）

- 混合点: `createCombinedAudioStream`（`browser.ts:119`）→ `BrowserMediaRecorderPipeline` → chunk upload。
- Upload API（`recordings.py:331,417`）: `media_type` は書き込み経路では自由形式Form field。
  storage path = `recordings/{user}/{rec}/{session}/{media_type}/{seq:06d}.{fmt}`、
  JSONB `media_files` はtypeキーでエントリ独立。
- **ただし読み出しは自由形式ではない**（批判で確定）:
  - `MediaFileResponse.type` は enum `audio|video|screenshot` 固定（`schemas.py:1262-1269`）
    → laneエントリは `/recordings` の `model_validate()` を壊す。
  - finalizerは `mf_type not in ("audio","video")` をskip（`recording_finalizer.py:615-624`）
    → **lane masterは無償では生成されない**。
  - sweeps復旧は `audio|video` 以外を除外（`sweeps.py:335-359`）。
  - `media_content_type()`/`_media_content_type()` はwebmで非audioを `video/webm` にする。
  - Pack U.7 late-chunkガードは `/audio/master.*` 固定（`recordings.py:492-501`、`post_meeting.py:334-341`）。
- bot側: `RecordingService.uploadChunk()` metadataに `media_type`/lane情報の口がない
  （`recording.ts:225-236`）。既存per-speaker captureはPCM live用で流用不可、ただし
  stream.id重複回避・track `ended` 解除のパターンは再利用可能（`index.ts:2041-2164`）。
- presentation中にparticipant tileが消える既知挙動あり（`googlemeet/recording.ts:498-501,605-620`）。

## 設計（v2）: lane-id を media_type 経路に載せる＋audio-like明示対応

レーンは `media_type = "lane-{laneKey}"` としてアップロードする。書き込み経路の
prefix分離とJSONBエントリ独立は批判でも裏付け済み。読み出し・処理系は
**`is_audio_like_media_type()` 共通helperを導入して明示的に対応する**：

1. **prefix分離（構造的）**: lane chunkは `.../lane-{laneKey}/...`。masterの `.../audio/`
   prefixリスティングに混入し得ない（`_chunk_prefix` 検証済み）。混合master経路は不変。
2. **audio-like helper**: `is_audio_like_media_type(t) = (t == "audio" or t.startswith("lane-"))`
   を meeting-api に追加し、以下へ適用:
   - `recordings.media_content_type()` / `recording_finalizer._media_content_type()`
     → lane webmを `audio/webm` に
   - finalizer連結allowlist（`recording_finalizer.py:615-624`）→ lane masterを生成。
     **ただし `playback_url` 生成は従来通り audio|video のみ**（境界をテスト化）
   - sweeps `_parse_recording_chunk_key()` / `_recording_has_playback_url()`
     → lane chunkのJSONB復旧＋lane未finalize時のsweep継続
   - Pack U.7ガード＋post_meeting reconcilerのmaster判定
     → `/audio/master.*` 固定を「audio-like typeのmaster」へ一般化
3. **API非公開（ユーザー決定③）**: `/recordings` 系レスポンスのserialize前に
   `lane-*` エントリを除外する。`MediaFileType` enum・dashboard型・MCP契約は**不変**。
   deferred STTはJSONBを直接読むため影響なし。
4. **削除**: 既存の全media_files走査（`recordings.py:855-873`）がlaneも対象にする
   ことを確認（変更不要見込み、テストで担保）。

### lane-id の定義と安定性

- `laneKey = sha1(participantId).slice(0,10)`。participantIdは
  `getGoogleParticipantId()`（data-participant-id → jsinstance → `gm-id-*`）。
- lane metadata: `lane_id`（原文）、`lane_label`（DOM表示名スナップショット、
  speaker-identityのlock名優先）、`lane_id_source`（"participant-id"|"jsinstance"|"generated"）。
- **rejoin/DOM-recycle方針**: 「新しいparticipantId = 新しいレーン」。統合は
  Phase 1のmerge API（#23）の責務。
- **tile消失対策（批判採用）**: laneレコーダのkeyはDOM tileでなく
  `MediaStream.id`/`MediaStreamTrack.id` を併用。tileが消えても（presentation中）
  trackがaliveならレーン録音を継続、track `ended` で final chunk 送出。
- **gm-id-* フォールバックも自動確定する（ユーザー決定②）**: 同一人物のレーンが
  DOM要素再生成で分断し得るが、分断レーンも同じ lane_label を持つため命名は
  一貫する。`lane_id_source: "generated"` をmedia_files/segmentメタに記録し、
  誤命名時の監査・一括修正（#23 merge/rename）を可能にしておく。

## 変更対象

### vexa-bot（capture）
- `services/recording.ts`: `uploadChunk()` にoptions引数を追加し、metadataへ
  `media_type` / `lane_id` / `lane_label` / `lane_id_source` を載せる
  （既存呼び出しはdefault "audio" で不変）。
- **lane registry を新設**（批判採用: 既存 `__vexaSaveRecordingChunk` /
  `UnifiedRecordingPipeline` の流用ではなく分離）: per-element streamごとに
  `BrowserMediaRecorderPipeline` を多重化し、lane専用callbackでupload。
- `platforms/googlemeet/recording.ts`: `createCombinedAudioStream` への入力と同じ
  element群からlane streamを取得。stream.id重複バインド回避＋track ended解除は
  `index.ts:2073-2107` のパターンを踏襲。
- フラグ/上限（ユーザー決定④）: `RECORD_PARTICIPANT_LANES`（default **false**）、
  `MAX_RECORDING_LANES=8`（超過は録らず警告ログ＋skipped metadata記録）。

### meeting-api（storage / finalize / deferred）
- `recordings.py`: lane metadata → media_files `lane` サブオブジェクト保存、
  audio-like helper適用、`/recordings` serializerでlane除外、U.7ガード一般化。
- `recording_finalizer.py` / `sweeps.py` / `post_meeting.py`: audio-like適用
  （連結対象化・復旧対象化・reconciler判定）。playback_url境界テスト。
- `final_transcription.py` — **deferredの状態モデル（ユーザー決定①: all-or-nothing）**:
  1. JSONB から finalized lane master 一覧を取得（`lane` メタ付き）。
  2. lane が1本以上あれば全laneをSTT（`LANE_STT_CONCURRENCY=2`、
     会議あたりレーン合計上限=4時間相当。超過時はlane経路を放棄しmixedへ）。
  3. **全lane成功時のみ** lane transcript を採用。1本でも失敗したら
     混合master経路へ全面フォールバック（重複・混在を構造的に排除）。
  4. lane採用時: レーン内クラスタ=1なら `speaker_cluster = "lane:{laneKey}"`、
     `speaker = speaker_auto = lane_label` を明示設定（generic DOM voteに任せない。
     批判指摘: `_parse_segments()` はlane_labelを見ない）。
     `segment_id` に laneKey を含め、複数laneの idx/start 衝突を回避。
  5. その後、保存済みcluster corrections（`final_transcription.py:740-747`）を
     `speaker_cluster` キーで適用（`lane:{laneKey}` キーがそのままmerge/rename APIで
     扱えることをテストで担保）。
  6. レーン内クラスタ>1（共有マイク兆候）はPhase 2では**単独扱いにせず**
     従来のcluster命名（DOM vote）に委ねる（Phase 3で本対応）。
- lane STTの再実行（mode=replace）もall-or-nothingを維持。

### dashboard
- **変更なし**（lane非公開のため型・UIとも不変。批判の指摘はAPI公開時のみ該当）。

## Out of Scope

- レーン内diarization・共有マイク検出（Phase 3 / #26）
- 声紋（Phase 4 / #27）
- Zoom/Teams、混合master経路の変更、lane再生UI・MP3変換（laneは内部データ）

## 検証契約（verification-contract.md v2 参照）

批判採用のacceptance criteria: lane有効時の `/recordings` レスポンスvalidation
（lane除外で全pass）、lane webm content-type=`audio/webm`、finalizerが
`lane-*/master.webm` を生成しつつ audio master の入力キー集合がバイト同一、
sweepsがJSONB欠損laneを復旧、lane master後のlate-chunkでstorage_path不戻り、
**フラグoff時にbotのupload metadataとmaster bytesが現行と完全一致**、
all-or-nothingフォールバック、cost cap実装のevidence（env読取り＋skipped記録）。

## S/M/L

| Field | Value |
|---|---|
| size | **L**（エピック規定・回帰歴のあるrecording経路・クロスサービス） |
| human gate | yes（L policy、ハッシュ束縛承認） |
| tribunal | yes（`required_for_l`） |

## Risks（v2）

- recording pipeline回帰歴 → 混合master不触＋不変テスト＋フラグdefault off。
- gm-id分断レーン（ユーザー決定で自動確定）→ lane_label一貫性で命名は保たれるが、
  誤ラベル時は複数レーンに波及。`lane_id_source` 記録＋#23一括修正で回収。
- **実機PoC必須（批判Needs-Human⑤）**: `data-participant-id`/`jsinstance` の
  layout change・screen share・rejoin時のchurn率はコードから読めない。
  実装後、フラグonの実会議で lane数・分断率・自動確定精度を計測してから常用判断。
- audio-like helperの適用漏れ → grepベースの網羅チェックをQAに含める。

## 実装順

1. meeting-api: audio-like helper＋lane metadata受入＋serializer除外＋U.7一般化＋テスト
2. finalizer/sweeps/post_meeting: lane連結・復旧・reconciler＋master不変テスト
3. vexa-bot: uploadChunk options＋lane registry＋フラグ/上限
4. final_transcription: 状態モデル（all-or-nothing）＋lane自動確定＋corrections互換
5. 統合fixture＋GitNexus impact/detect-changes＋tribunal＋evidence pack
