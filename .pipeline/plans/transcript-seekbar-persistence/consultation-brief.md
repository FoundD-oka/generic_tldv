# Fable Consultation Brief: transcript-seekbar-persistence

Generated: 2026-07-17T06:33:41.684455+00:00
Provider target: claude-fable-cli
Mode: plan
Model: fable

## Safety And Boundaries

- You are an advisory reviewer, not the implementer.
- Use local file reads and read-only shell inspection only.
- Do not edit files, run write commands, commit, push, install dependencies, or change state.
- Keep the answer concise. Each finding should be at most two sentences plus evidence.
- Treat your answer as advisory review evidence. Local tests, source checks, and project evidence remain the source of truth.

## Required Context Summary

### 1. Original Task And Plan Step

Task id: transcript-seekbar-persistence
Plan step or checkpoint: 文字起こし再生時の追従スクロール修正

Relevant plan artifacts:

```text
## plan.md

# 実装計画: 文字起こし再生時のシークバー表示維持

## 目的

文字起こし内の発話を選択して再生位置へ移動しても、会議全体のシークバーを画面外へ押し出さず、文字起こし欄だけを追従スクロールさせる。

## 原因

再生中の発話へ追従する処理が `scrollIntoView()` を使用しており、文字起こし欄だけでなく外側のページスクロールも動かしている。そのため、選択した発話を中央へ移動すると上部の全体シークバーが画面外へ出る。

## 実装範囲

1. 再生中発話の位置を文字起こしコンテナ基準で計算する。
2. 追従スクロールは文字起こしコンテナ自身の `scrollTo()` だけで行う。
3. 中央寄せ座標と上端クランプをユニットテストする。

## 対象ファイル

- `services/dashboard/src/components/transcript/transcript-viewer.tsx`
- `services/dashboard/src/lib/transcript-scroll.ts`
- `services/dashboard/tests/test_transcript_scroll.test.ts`

## 完了条件

- 発話をクリックして再生しても、会議全体のシークバーが上部に残る。
- 再生中の発話は文字起こし欄の中央付近へ追従する。
- 外側ページのスクロール位置を変更しない。
- 対象テスト、Dashboardの型・Lint・ビルド、GitNexus差分検査が通る。

## スコープ外

- 音声プレイヤーの再生・シーク計算そのもの。
- ライブ文字起こしの新着行追従。
- 会議詳細ページ全体のレイアウト変更。


## verification-contract.md

# Verification Contract: transcript-seekbar-persistence

- size: S
- external consultation required: no
- external consultation provider: claude-fable-cli

## Required Commands
- none recorded

## Evidence Rule
- Evidence Manifest must have no missing_evidence entries.

```

### 2. Approaches Tried And Failure Reasons

```text
[none recorded for this consultation]
```

### 3. Current Hypothesis

[not specified]

### 4. Questions To Decide

1. 原因と修正方針に見落としがないか
2. 回帰テストの観点は十分か

## Decision Or Result Under Review

外側ページを動かさず文字起こしコンテナだけをscrollToする

## Context Coverage

```json
complete for configured brief limits
```

## Extra Context

```text
[no extra source file provided]
```

## Current Git Status

```text
 M services/dashboard/src/components/transcript/transcript-viewer.tsx
?? .pipeline/gates/transcript-seekbar-persistence/
?? .pipeline/plans/transcript-seekbar-persistence/
?? services/dashboard/src/lib/transcript-scroll.ts
?? services/dashboard/tests/test_transcript_scroll.test.ts

```

## Current Diff Stat

```text
 .../src/components/transcript/transcript-viewer.tsx         | 13 +++++++++----
 1 file changed, 9 insertions(+), 4 deletions(-)

```

## Current Diff Excerpt

```diff
diff --git a/services/dashboard/src/components/transcript/transcript-viewer.tsx b/services/dashboard/src/components/transcript/transcript-viewer.tsx
index e93ddcc..0991ca5 100644
--- a/services/dashboard/src/components/transcript/transcript-viewer.tsx
+++ b/services/dashboard/src/components/transcript/transcript-viewer.tsx
@@ -48,6 +48,7 @@ import { format } from "date-fns";
 import { ja } from "date-fns/locale";
 import { normalizeVoiceprintSelectionTiming } from "@/lib/voiceprint-selection";
 import { isRetranscriptionInProgress } from "@/lib/retranscription-status";
+import { scrollTranscriptItemToCenter } from "@/lib/transcript-scroll";

 // Linkify URLs in chat message text — splits text into plain strings and clickable <a> elements
 const URL_REGEX = /(https?:\/\/[^\s<>"')\]]+)/gi;
@@ -737,10 +738,14 @@ export function TranscriptViewer({

     // Use a small delay to let the DOM update
     requestAnimationFrame(() => {
-      activeSegmentRef.current?.scrollIntoView({
-        behavior: "smooth",
-        block: "center",
-      });
+      const container = scrollRef.current;
+      const activeSegment = activeSegmentRef.current;
+      if (!container || !activeSegment) return;
+
+      // Keep playback follow-up inside the transcript pane. scrollIntoView()
+      // also scrolls outer ancestors and can push the meeting-wide seek bar
+      // out of the viewport when a transcript segment is selected.
+      scrollTranscriptItemToCenter(container, activeSegment);
     });
   }, [activePlaybackIndex, isPlaybackActive]);


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
