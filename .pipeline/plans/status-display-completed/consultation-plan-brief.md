# Fable Consultation Brief: status-display-completed

Generated: 2026-07-14T02:51:08.168342+00:00
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

Task id: status-display-completed
Plan step or checkpoint: 完了表示の統一

Relevant plan artifacts:

```text
## request.md

# 依頼

会議の終了ステータスについて、手動停止と自動終了を区別せず、終了処理が完了したものはすべて「完了」と表示する。



## plan.md

# 実装プラン: 会議ステータスの完了表示統一

## 目的

会議の終了理由をステータスラベルへ反映せず、内部状態が `completed` の会議は一覧・カード・詳細のすべてで「完了」と表示する。

## 変更範囲

1. 共通表示関数 `getDetailedStatus` で、`completed` の終了理由別ラベル分岐を廃止する。
2. `completion_reason` は監査・履歴用データとして保持し、API状態遷移や保存形式は変更しない。
3. 代表的な終了理由がすべて「完了」になる回帰テストを追加する。
4. 最終化処理中の `stopping` は「停止中」のまま維持する。

## 対象外

- バックエンドの状態遷移および終了理由の分類
- 録音、文字起こし、声紋登録の利用条件
- ステータス履歴に保存される終了理由

## 検証

- `npm test -- tests/test_meeting_status_display.test.ts`
- `npx eslint src/types/vexa.ts tests/test_meeting_status_display.test.ts`
- `npm test`
- GitNexusの変更影響確認で、一覧・カード・詳細以外への意図しない波及がないことを確認する。

## リスクと戻し方

- 共通表示関数は3画面で利用されるため表示影響は広いが、API契約やデータは変えない。
- 問題があれば `completed` の共通分岐と追加テストのみを差し戻せる。

## 実行予算

- 変更対象: 実装1ファイル、テスト1ファイル
- 外部Web調査: なし
- サブエージェント: なし
- 外部相談: プラン確認1回まで
- 打ち切り条件: API状態遷移または保存データの変更が必要と判明した場合



## verification-contract.md

# Verification Contract: status-display-completed

- size: S
- external consultation required: no
- external consultation provider: not needed

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

1. 終了理由を保持したまま表示だけ統一する計画に抜け漏れがないか

## Decision Or Result Under Review

completedは終了理由に関係なく完了と表示し、stoppingのみ停止中を維持する

## Extra Context

```text
[no extra source file provided]
```

## Current Git Status

```text
 M .pipeline/rules/hd-log.tsv
 M services/dashboard/src/types/vexa.ts
?? .pipeline/gates/status-display-completed/
?? .pipeline/plans/status-display-completed/
?? services/dashboard/tests/test_meeting_status_display.test.ts
?? "\343\203\241\343\203\242.md"

```

## Current Diff Stat

```text
 services/dashboard/src/types/vexa.ts | 48 ++++++------------------------------
 1 file changed, 8 insertions(+), 40 deletions(-)

```

## Current Diff Excerpt

```diff
diff --git a/services/dashboard/src/types/vexa.ts b/services/dashboard/src/types/vexa.ts
index 23001db..5c0ed56 100644
--- a/services/dashboard/src/types/vexa.ts
+++ b/services/dashboard/src/types/vexa.ts
@@ -365,46 +365,14 @@ export function getDetailedStatus(status: MeetingStatus, data?: MeetingData): De
     description: "状態を確認できません"
   };

-  // For completed meetings, check completion reason
-  if (status === "completed" && data?.completion_reason) {
-    switch (data.completion_reason) {
-      case "stopped":
-        return {
-          label: "停止済み",
-          color: "text-gray-600 dark:text-gray-400",
-          bgColor: "bg-gray-100 dark:bg-gray-800/50",
-          description: "ユーザーが手動で停止しました",
-        };
-      case "meeting_ended":
-        return {
-          label: "終了",
-          color: "text-green-600 dark:text-green-400",
-          bgColor: "bg-green-100 dark:bg-green-950/50",
-          description: "会議が終了しました",
-        };
-      case "kicked":
-      case "removed":
-        return {
-          label: "退出済み",
-          color: "text-orange-600 dark:text-orange-400",
-          bgColor: "bg-orange-100 dark:bg-orange-950/50",
-          description: "ボットが会議から退出しました",
-        };
-      case "awaiting_admission_rejected":
-        return {
-          label: "入室拒否",
-          color: "text-red-600 dark:text-red-400",
-          bgColor: "bg-red-100 dark:bg-red-950/50",
-          description: "ボットの入室が許可されませんでした",
-        };
-      default:
-        return {
-          ...(baseConfig || fallbackConfig),
-          color: "text-green-600 dark:text-green-400",
-          bgColor: "bg-green-100 dark:bg-green-950/50",
-          description: "文字起こしが完了しました"
-        };
-    }
+  // The status communicates processing state, not how the meeting ended.
+  // Keep completion_reason in the data for audit/history, but render every
+  // successfully finalized meeting consistently as completed.
+  if (status === "completed") {
+    return {
+      ...(baseConfig || fallbackConfig),
+      description: "文字起こしが完了しました",
+    };
   }

   // For failed meetings, add description based on error

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
