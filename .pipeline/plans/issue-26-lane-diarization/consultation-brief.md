# Fable Consultation Brief: issue-26-lane-diarization

Generated: 2026-07-09T23:10:50.373837+00:00
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

Task id: issue-26-lane-diarization
Plan step or checkpoint: Phase 3 (issue #26) shared-mic detection + needs-review sub-speaker naming — final audit before PR readiness

Relevant plan artifacts:

```text
## plan.md

# Plan — Issue #26: 同室共有マイクの検出とサブ話者命名（Phase 3）

> エピック: `.pipeline/plans/speaker-attribution-voiceprint/plan.md`（Phase 3節）
> 前提: Phase 2（issue #25, PR #33）がmainにmerge済み。本ブランチはそのmainから分岐。
> Codex Plan Critic: `.pipeline/evidence/issue-26-lane-diarization/codex-plan-critique.md`（v2で反映済み）
> Issue: https://github.com/FoundD-oka/generic_tldv/issues/26
> 状態: **DRAFT（計画のみ、製品コード未変更）**
> Rev: v2（2026-07-10、Codex批判反映）

## Request

- Source: GitHub issue #26（エピックPhase 3）
- 目的: レーン単位のdeferred STTが手に入った状態（Phase 2）を前提に、1レーン内に複数の
  声（=同室で1マイクを共有する複数人）が混在するケースを検出し、DOMでは分離できないその
  サブ話者を「要確認」として提示、1回の命名で全発話に反映できるようにする。

## Context Pack

詳細: `.pipeline/plans/issue-26-lane-diarization/context-pack.md`（批判のFC-1/3/5/11で事実確認済み）。

- `_apply_lane_identity`（`final_transcription.py:314-343`）は既にマルチクラスタレーンを
  `lane:{laneKey}:{cluster}` にnamespaceするが、命名は`_parse_segments`内の
  `name_clusters_by_dom_vote`（DOM重複秒投票、共通コード）に委ねたまま——issueのAC5（DOMを
  サブ話者の正解に使わない）と矛盾する、Phase 2からの意図的な挙動変更点。
- 安定性フィルタが皆無（1トークンの誤爆でK>1化）——AC3のfalse-split guardが未実装。
- 訂正API（PATCH `/transcripts/speakers`）のrenameは`speaker_cluster`の文字列値に汎用的に
  動作し、`lane:{key}:{cluster}`形式にも変更不要（AC2はrenameに限定すれば満たせる）。ただし
  merge/reassignの`mode=replace`再実行後semanticsは現行APIでは保証できない（後述）。
- `speaker_mapping_status`はAPIスキーマ・ダッシュボード型に既に存在するが、deferred/DB経路の
  読み出し（`collector/endpoints.py`）でもREST mapper（`dashboard/src/lib/api.ts`）でも
  伝搬していない——両方を直さないとUIに届かない。
- ダッシュボードのグルーピングは「複数segmentの結合」ではなく、`seg.speaker || ""`が空文字に
  縮退することで起きる**identityの潰れ**（filter/色/連続header判定/話者一覧が同じ空keyに集約）。
- DOM発話区間でのdiarization区間制約はサーバ側で信頼できる形で実装不可（tile↔レーンのid空間
  無関係、`lane_label`はBUG-020で頻繁にnull）——issue本文の実装指示①は不採用、無制約の
  全レーンdiarizeを継続する。

## 設計

### 方針: 挙動変更点を明示した最小差分

Phase 2の共通コード（`_parse_segments`, `name_clusters_by_dom_vote`, Soniox adapter呼び出し）は
変更しない。変更点は`_apply_lane_identity`前後のレーン後処理に閉じる。**mixed-master経路
（レーン非使用の混合音声diarization）のDOM投票は無変更**（FC-2）。

1. **DOM区間制約は不採用**（設計判断、TODOではない）。DOMは`lane_label`（Phase 2の単独レーン
   自動命名）としてのみ残す。サブ話者の正解には一切使わない（AC5）。
2. **安定性フィルタ（AC3 false-split guard）**: Soniox adapterに非破壊的フィールド
   `token_count`（fold時のトークン件数、`speaker`追加と同じ拡張パターン）を追加。契約変更面は
   golden fixture（`contracts/stt/v1/examples/golden-2-diarization.response.json`）、
   `test_golden_fixture_replay_is_deterministic`、`contracts/stt/v1/README.md`（optional
   additive field記載）、`.pipeline/adapters/soniox-stt.adapter.json`（`validation.checks`）の
   4点セット——これが変更面から欠けていた（批判FC-4/ARC-2）。`_stable_lane_clusters(segments,
   min_duration_s, min_tokens)`を新設し、クラスタごとの総発話時間・総トークン数
   （`token_count`欠損時は語数近似）を集計、閾値（`LANE_SHARED_MIC_MIN_CLUSTER_DURATION_S`
   既定2.0秒、`LANE_SHARED_MIC_MIN_CLUSTER_TOKENS`既定5）以上を「安定クラスタ」とする。
3. **不安定クラスタの扱い（B-2の明確化、AC4の境界劣化の解決）**: 「最も時間重複の大きい安定
   クラスタへ吸収」という未定義の吸収ロジックは採用しない。**K = 安定クラスタの数のみ**で数え、
   不安定クラスタは決して他クラスタへ黙って吸収しない：
   - K_stable <= 1（0または1個、全クラスタが閾値未満の場合を含む）: **単独レーン扱い**。
     不安定クラスタの発話も含め全segmentがレーンラベル（`speaker_cluster="lane:{laneKey}"`、
     `lane_label`があれば`speaker`もそれで上書き）を取る——Phase 2 solo挙動と一致、
     リスクは閾値でバウンドされる。
   - K_stable >= 2（共有マイク）: 各安定クラスタを`lane:{laneKey}:{cluster}`にnamespace。
     不安定クラスタも**吸収せず自分のサブクラスタidを維持**し、同様に`needs_review`対象とする
     （未定義の吸収先選択ロジックを排除）。
4. **命名破棄はK>1（共有マイク）分岐でのみ**（ARC-1、FC-2）: `_apply_lane_identity`の
   multi-cluster branchで`seg["speaker"]`を**保存処理に渡す前・`speaker_auto`キャプチャの前に**
   `None`へ上書きする。`speaker_auto = seg.get("speaker")`は`_apply_lane_identity`後に走る
   （`final_transcription.py:1122-1129`）ため、この順序でDOM投票名が`speaker_auto`にも
   残らない。**mixed-master path（レーン非使用時）のDOM投票結果は無変更**。単独レーン
   （K_stable<=1）分岐はDOM投票を破棄せず`lane_label`のみ使う（Phase 2と同じ）。
5. **要確認フラグ（マイグレーション無し、B-1で変更面を訂正）**: (a) `collector/endpoints.py:
   _get_full_transcript_segments`のPGセグメント構築分岐に、`speaker_cluster`が
   `^lane:[^:]+:.+$`（サブクラスタ形式）かつ`speaker`が空/Unknownの場合に
   `speaker_mapping_status="needs_review"`を読み出し時に導出。(b) **これだけではUIに届かない**
   （FC-8）——`services/dashboard/src/lib/api.ts`のREST mapper（`RawSegment`→
   `TranscriptSegment`）に`speaker_mapping_status`を追加で通す。websocket経路は既に伝搬済みの
   ため対象外。新規DBカラムは追加しない。
6. **命名反映（AC2はrenameに限定、B-3/FC-6）**: 既存のPhase 1b`PATCH /transcripts/speakers`の
   `rename`（`from_cluster=lane:{key}:{cluster}`）をそのまま使う。バックエンド変更ゼロ。
   **merge/reassignの`mode=replace`再実行後semantics（代表clusterへの再集約、segment単位の
   再適用）は現行APIの構造的限界でAC2の対象外**——将来issueで扱う（Out of Scope参照）。
7. **ダッシュボード（B-4/ARC-4で識別キーと表示ラベルを分離）**:
   - identity/groupingキー: `seg.speaker || seg.speaker_cluster || ""`にフォールバック
     （filter・色・連続speaker header判定・話者一覧の全箇所で同じキーを使い、identityの
     潰れ（FC-9/RC-4: segment結合ではなく空keyへの縮退）を解消）。
   - **表示ラベルはidentityキーと別**（B-4）: `needs_review`のサブクラスタは生の
     `lane:{key}:{cluster}`を絶対に表示しない。日本語ラベル「要確認の話者」＋クラスタ出現順の
     短い添字（A/B…）を表示名として使う。
   - `speaker_mapping_status === "needs_review"`のグループに「要確認」バッジを表示し、
     既存の話者リネームUI（`buildSpeakerRename`）をそのまま起動できるようにする。
8. **不変条件の維持（ARC-6）**: 既存のall-or-nothingフォールバック（`LaneTranscriptionFallback`）
   とBUG-002対応のoffset順序（speaker_eventsをlane-localにずらして`_parse_segments`→
   `_apply_lane_identity`→`_shift_segment_times`でmaster timelineへ戻す順）は変更しない。
   安定性フィルタはこの順序

[truncated; verify against the source artifact]

## verification-contract.md

# Verification Contract — issue-26-lane-diarization

- size: L（sml-decision.json 参照）
- external consultation: claude-fable-cli（optional）
- tribunal: required（L policy）
- Rev: v1（2026-07-10、plan v2＝Codex批判反映と同時に確定）

## Must Pass（deterministic）

### 0. 批判由来の追加基準
- golden fixture: `contracts/stt/v1/examples/golden-2-diarization.response.json`に
  `token_count`を追加した状態で`test_golden_fixture_replay_is_deterministic`が完全一致で
  pass（`build_verbose_json_response()`出力とgolden responseのバイト単位一致）。
- `contracts/stt/v1/README.md`が`token_count`をoptional additive fieldとして記載し、
  `.pipeline/adapters/soniox-stt.adapter.json`の`validation.checks`が更新後golden fixtureと
  整合すること（adapter-contract gateでの検証）。
- REST mapper: `services/dashboard/src/lib/api.ts`の`RawSegment`→`TranscriptSegment`
  マッピングが`speaker_mapping_status`を保持する（deferred/PG経由の会議データで
  `needs_review`がdashboard型まで届くfixtureテスト）。
- **生のcluster idが話者名として画面に出ないこと**: `needs_review`の全経路
  （grouping/header/speaker-list/badge/`transcript-segment.tsx`描画）で
  `lane:{key}:{cluster}`形式の文字列がそのまま表示されないことをスナップショット/DOM
  アサートで確認。
- K計数は安定クラスタのみ: `_stable_lane_clusters`が閾値未満のクラスタを`K`から除外し、
  かつ「最も時間重複の大きい安定クラスタへの吸収」を行わない（不安定クラスタが独自の
  sub-cluster idを保持し続けるfixtureテスト）。K_stable<=1（全クラスタ閾値未満を含む）は
  単独レーン扱い、K_stable>=2は不安定クラスタも`needs_review`対象として維持。
- mixed-master（レーン非使用）経路のDOM投票が無変更: multi-cluster lane分岐の
  `speaker=None`上書きロジックが`_apply_lane_identity`のlane専用コードパスにのみ効き、
  mixed-master diarization結果のfixtureはPhase 2から出力が変わらないことの回帰テスト。
- all-or-nothing＋offset順序の不変条件回帰: 1レーンSTT失敗fixtureで
  `LaneTranscriptionFallback`が発火し混合master経路に全面フォールバックすること、および
  安定性フィルタ挿入後もspeaker_eventsのlane-localシフト→`_parse_segments`→
  `_apply_lane_identity`→`_shift_segment_times`の順序・例外非伝播が保たれること
  （既存`test_final_transcription.py`／`test_final_transcription_lanes.py`suite全緑）。

### 1. P3-AC1（共有マイク検出）
- 2声混在の合成fixture（1レーンに2安定クラスタ、閾値以上の発話時間・トークン数）で
  K_stable=2と判定され、両クラスタの`speaker_cluster`が`lane:{laneKey}:{cluster}`に
  namespaceされ、`speaker`/`speaker_auto`が`None`、読み出し時に
  `speaker_mapping_status="needs_review"`が導出されること。

### 2. P3-AC2（命名の一括反映、renameに限定）
- `from_cluster="lane:{key}:A"`のrenameでクラスタAの全発話にのみ反映され、クラスタBは
  不変であること。merge/reassignの`mode=replace`再実行後semanticsはこの契約のアサート
  対象外（Out of Scopeとして明記済み、将来issue）。

### 3. P3-AC3（false-split guard）
- 短い相槌1発話だけの誤爆クラスタを含むfixtureでK_stable<=1に収束し、単独レーン判定
  （`lane_label`があれば全segmentがそれを`speaker`として持つ）を維持すること。誤爆クラスタが
  新規サブクラスタとして数えられないこと。

### 4. P3-AC4（境界のバウンドされた劣化、B-2解決の検証）
- `LANE_SHARED_MIC_MIN_CLUSTER_DURATION_S`/`_TOKENS`ちょうどの境界値fixtureで、
  speakerが推測名を出さず絶対に`None`以外にならないこと（否定的テスト）。
- 全クラスタが閾値未満（no-stable-cluster edge）のfixtureで単独レーン扱いに帰着すること。
- K_stable>=2かつ不安定クラスタが存在するfixtureで、不安定クラスタが安定クラスタへ
  吸収されず自分のsub-cluster idを保持し、`needs_review`対象になること。

### 5. P3-AC5（DOM非依存の回帰）
- マルチクラスタfixtureでDOM speaker_eventsの内容を変えても`speaker`/`speaker_auto`が
  変化しないこと。
- mixed-master（レーン非使用）fixtureではDOM投票結果がPhase 2と同一であること
  （lane専用分岐に閉じていることの回帰確認）。

## Required Commands

- `cd services/meeting-api && PYTHONPATH=. python -m pytest tests -q`（全緑、特に
  `test_final_transcription` / `test_final_transcription_lanes` / `test_speaker_clusters`）
- `cd services/transcription-service && python -m pytest -q`（`test_soniox_adapter`の
  golden fixture一致テスト含む）
- `cd services/dashboard && npx vitest run`（fallback key・identity/表示ラベル分離・
  REST mapperのstatus伝搬テスト）
- `cd services/dashboard && npx tsc --noEmit`（型変更の整合確認）
- `node .gitnexus/run.cjs detect-changes -r generic_tldv`（想定シンボルのみ:
  `_apply_lane_identity` / `_stable_lane_clusters` / `_get_full_transcript_segments` /
  `fold_tokens_to_segments` / dashboard mapper・viewer関連）
- GitNexus `impact`: `_apply_lane_identity` / `name_clusters_by_dom_vote` /
  `fold_tokens_to_segments` / `_get_full_transcript_segments`のupstream影響を実装前に記録

## Evidence Rule

- Evidence は `.pipeline/evidence/issue-26-lane-diarization/` に格納。
- L要件: tribunal report または sidechain synthesis 必須
  （AC3/AC4/AC5＋B-2不安定クラスタ非吸収セマンティクスを重点レビュー）。
- 人間承認（approvals.jsonl、diff hash束縛）なしにPR readyにしない
  （AC5挙動変更の説明を承認材料に含める）。
- 実装エージェントの自己申告をevidenceにしない（テストは独立再実行で記録）。

```

### 2. Approaches Tried And Failure Reasons

```text
[none recorded for this consultation]
```

### 3. Current Hypothesis

[not specified]

### 4. Questions To Decide

1. Is there any remaining correctness or safety gap in the shared-mic detection design or the tribunal-fix set that should block PR creation?
2. Are the P3 acceptance criteria (AC1-AC5) genuinely satisfied by the implementation and tests, including the invariants (all-or-nothing fallback, offset ordering, mixed-master DOM vote unchanged)?
3. Any risk in the needs_review derivation asymmetry between deferred and realtime paths after the BUG-005 fix?

## Decision Or Result Under Review

Implemented on top of the Phase 2 lane pipeline: token_count additive STT contract field (golden fixture updated from real fold), K_stable stability filter (2.0s/5tokens env-tunable) deciding solo vs shared-mic, DOM-vote names discarded on shared-mic sub-clusters (AC5, user-approved behavioral change), needs_review derived read-time (PG+Redis derive-first, no migration), dashboard identity/display-label separation rendering 要確認の話者A/B (raw lane ids never rendered). Tribunal confirmed 5/6 findings (0 critical), all fixed: clusterless segments namespaced lane:{key}:unclustered, buildSpeakerMerge identity-key matching, decimal env parse, Redis derivation. Tests: meeting-api 415, transcription-service 42, dashboard 89, tsc clean. Plan: .pipeline/plans/issue-26-lane-diarization/plan.md; evidence: .pipeline/evidence/issue-26-lane-diarization/

## Extra Context

```text
[no extra source file provided]
```

## Current Git Status

```text
 M AGENTS.md
 M CLAUDE.md
?? .pipeline/evidence/issue-26-lane-diarization/test-results.md
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
index a534b0a..a8104b7 100644
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -119,7 +119,7 @@ Use:
 <!-- gitnexus:start -->
 # GitNexus — Code Intelligence
 
-This project is indexed by GitNexus as **generic_tldv** (15564 symbols, 27575 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.
+This project is indexed by GitNexus as **generic_tldv** (15567 symbols, 27589 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.
 
 > Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).
 
diff --git a/CLAUDE.md b/CLAUDE.md
index 40edf30..4ade8f9 100644
--- a/CLAUDE.md
+++ b/CLAUDE.md
@@ -88,7 +88,7 @@ See `.ai/BUILD.md`.
 <!-- gitnexus:start -->
 # GitNexus — Code Intelligence
 
-This project is indexed by GitNexus as **generic_tldv** (15564 symbols, 27575 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.
+This project is indexed by GitNexus as **generic_tldv** (15567 symbols, 27589 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.
 
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
