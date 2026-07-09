# Fable Consultation Brief: issue-25-audio-lanes

Generated: 2026-07-09T20:44:06.135237+00:00
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

Task id: issue-25-audio-lanes
Plan step or checkpoint: Phase 2 (issue #25) per-participant audio lane recording — final audit before PR readiness

Relevant plan artifacts:

```text
## plan.md

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
     混合master経路へ全面フォールバッ

[truncated; verify against the source artifact]

## verification-contract.md

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

```

### 2. Approaches Tried And Failure Reasons

```text
[none recorded for this consultation]
```

### 3. Current Hypothesis

[not specified]

### 4. Questions To Decide

1. Is there any remaining correctness or safety gap in the lane design or the tribunal-fix set that should block PR creation?
2. Is the all-or-nothing lane fallback policy correctly implemented given the unfinalized-lane and budget cases?
3. Any risk that the mixed-master byte-invariance guarantee is violated by the fixes?

## Decision Or Result Under Review

Implemented lanes as media_type lane-{laneKey} (prefix separation + independent JSONB entries), lane-first deferred STT with all-or-nothing mixed-master fallback, opt-in flag default off with cost caps. Three-stage tribunal confirmed 16/23 findings (2 critical: missing bundle export; missing lane start-offset); all 16 fixed and re-verified (meeting-api 401 passed, bot tsc/build/bundle verified, dashboard 73 passed). Key evidence: .pipeline/evidence/issue-25-audio-lanes/tribunal-report.json, test-results.md; plan .pipeline/plans/issue-25-audio-lanes/plan.md

## Extra Context

```text
[no extra source file provided]
```

## Current Git Status

```text
 M .pipeline/evidence/issue-25-audio-lanes/approvals.jsonl
 M .pipeline/gates/issue-25-audio-lanes/preflight.json
 M scripts/harness/external-consultation.sh
?? .pipeline/evidence/issue-25-audio-lanes/external-consultation/
?? .pipeline/gates/issue-25-audio-lanes/adapters.json
?? .pipeline/gates/issue-25-audio-lanes/backcast.json
?? .pipeline/gates/issue-25-audio-lanes/doc-staleness.json
?? .pipeline/gates/issue-25-audio-lanes/external-consultation.json
?? .pipeline/gates/issue-25-audio-lanes/feedback-prune.json
?? .pipeline/gates/issue-25-audio-lanes/hd.json
?? .pipeline/gates/issue-25-audio-lanes/pr-ready.json
?? .pipeline/gates/issue-25-audio-lanes/residency.json
?? .pipeline/plans/issue-25-audio-lanes/consultation-brief.md
?? "\343\203\241\343\203\242.md"

```

## Current Diff Stat

```text
 scripts/harness/external-consultation.sh | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)

```

## Current Diff Excerpt

```diff
diff --git a/scripts/harness/external-consultation.sh b/scripts/harness/external-consultation.sh
index c7d13a4..e73e866 100755
--- a/scripts/harness/external-consultation.sh
+++ b/scripts/harness/external-consultation.sh
@@ -565,7 +565,7 @@ def run_fable() -> None:
         "--max-budget-usd", str(args.max_budget_usd),
         "--tools", "Read,Bash",
         "--allowedTools", READ_ONLY_BASH,
-        "--disallowedTools", "Edit,Write,MultiEdit,NotebookEdit",
+        "--disallowedTools", "Edit,Write,NotebookEdit",
         "--permission-mode", "dontAsk",
     ]
     if args.bare:

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
