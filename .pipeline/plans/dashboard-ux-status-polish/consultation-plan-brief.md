# Fable Consultation Brief: dashboard-ux-status-polish

Generated: 2026-07-16T14:10:35.172447+00:00
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

Task id: dashboard-ux-status-polish
Plan step or checkpoint: [not specified]

Relevant plan artifacts:

```text
## plan.md

# 実装計画: Dashboard UX・再文字起こし状態改善

## 目的

辞書の有効状態、ナビゲーション名、声紋プレビュー準備、再文字起こし状態を、ユーザーが見誤らない表示と操作制御へ揃える。

## 実装範囲

1. 無効化した辞書行の背景と文字色をグレー化する。
2. サイドパネルの日本語ナビゲーション「会議」を「会議一覧」へ変更する。
3. 声紋録音停止から確認音声のメタデータ読込完了まで、全画面ローディングで操作を遮断する。Blob URL生成直後の `readyState` 確認と10秒タイムアウトを設け、解除不能を防ぐ。
4. 再文字起こしの `queued` / `running` を会議の共通表示で「処理中」、`failed` を「再処理失敗」と表示する。
5. 再文字起こし開始直後は詳細画面を楽観更新し、APIの会議データで再同期する。
6. 一覧APIの軽量データにも `final_transcription.status` を含め、処理中の会議がある間は一覧をサイレント更新して完了・失敗表示へ追従する。

## 対象ファイル

- `services/dashboard/src/app/dictionary/page.tsx`
- `services/dashboard/src/lib/dashboard-copy.ts`
- `services/dashboard/src/app/voiceprints/page.tsx`
- `services/dashboard/src/types/vexa.ts`
- `services/dashboard/src/components/transcript/transcript-viewer.tsx`
- `services/dashboard/src/app/meetings/[id]/page.tsx`
- `services/dashboard/src/app/meetings/page.tsx`
- `services/dashboard/src/stores/meetings-store.ts`
- `services/meeting-api/meeting_api/meetings.py`
- 関連するDashboard／Meeting APIテスト

## 完了条件

- 辞書をオフにすると行全体の背景と文字がグレーになる。
- サイドパネルに「会議一覧」と表示される。
- 声紋確認音声の準備中は他の操作ができず、読込完了・エラー・タイムアウトのいずれでも解除される。
- 再文字起こし開始後は一覧・詳細とも「処理中」、成功後は「完了」、失敗後は「再処理失敗」と表示される。
- 対象テスト、Dashboard全テスト、production build、Meeting API対象テスト、GitNexus検査が通る。

## スコープ外

- 辞書APIや声紋APIのデータ契約変更。
- 再文字起こしジョブ実行方式の変更。
- 英語ロケールのナビゲーション文言変更。


## verification-contract.md

# Verification Contract: dashboard-ux-status-polish

- size: M
- external consultation required: no
- external consultation provider: claude-fable-cli

## Required Commands
- `cd services/dashboard && npm test -- tests/test_transcription_dictionary_ui.test.ts tests/test_dashboard_brand.test.ts tests/test_voiceprint_recording_ui.test.ts tests/test_meeting_status_display.test.ts tests/test_transcript_reprocess_ui.test.ts`
- `cd services/dashboard && npm test`
- `cd services/dashboard && npm run build`
- `PYTHONPATH=services/meeting-api:libs/admin-models /Users/bonginkan-3-gouki/project/generic_tldv/.pipeline/worktrees/recording-range-streaming/checkout/.venv/bin/python -m pytest services/meeting-api/tests/test_meetings.py -q`
- `node /Users/bonginkan-3-gouki/project/generic_tldv/.gitnexus/run.cjs detect-changes --repo /Users/bonginkan-3-gouki/project/generic_tldv/.pipeline/worktrees/dashboard-ux-status-polish/checkout --scope compare --base-ref main`

## Evidence Rule
- Evidence Manifest must have no missing_evidence entries.


## option-matrix.md

# 選択肢比較

| 論点 | 選択肢 | 評価 | 採否 |
|---|---|---|---|
| 辞書無効表示 | 文字だけグレー | 背景との差が弱い | 不採用 |
| 辞書無効表示 | 行背景＋文字をグレー | 状態を一目で判別でき、Switchは残る | 採用 |
| 声紋準備中 | 各操作を個別disable | 操作追加時に漏れやすい | 不採用 |
| 声紋準備中 | 固定オーバーレイで全体遮断 | 待機理由が明確で漏れがない | 採用 |
| 声紋ローディング解除 | `loadedmetadata` だけを待つ | イベント欠落時に永久ロックし得る | 不採用 |
| 声紋ローディング解除 | `readyState`・イベント・タイムアウトを併用 | 表示可能状態を待ちつつ異常時も解除できる | 採用 |
| 再文字起こし表示 | ボタン内のローカル状態だけ | 一覧・詳細バッジと不一致になる | 不採用 |
| 再文字起こし表示 | API保存済み状態を共通表示へ接続 | 一覧・詳細・再読込後も一貫する | 採用 |
| 一覧の状態追従 | 通常ローディング付き再取得 | カード一覧が定期的にちらつく | 不採用 |
| 一覧の状態追従 | 処理中だけサイレント更新 | 操作を妨げず完了・失敗へ追従できる | 採用 |

## 採用理由

見た目だけでなく、APIの正しい状態と画面操作を一致させられる構成を優先した。


## research-brief.md

# 調査概要

## 問い

1. 無効な辞書項目を、削除済みと誤認させず視覚的に区別するにはどこを変えるか。
2. 声紋プレビュー準備中の操作競合を、個別ボタン制御と全体遮断のどちらで防ぐか。
3. 再文字起こし状態をローカルボタン状態ではなく、一覧・詳細で一貫して表示するには何を正とするか。

## 仮説と確認結果

- 仮説A: 辞書行は `item.enabled` を既に持つため、API変更なしで行コンテナへ無効スタイルを付けられる。コード確認で支持。
- 仮説B: 声紋ページは録音停止後にBlob URLを生成し、`audio` の読込完了を待っていない。`loadedmetadata` を主な解除条件にし、生成直後の `readyState` 確認と10秒タイムアウトを併用すれば、確認再生が表示可能になるまで遮断しつつ永久ロックを防げる。コード確認とFableレビューで支持。
- 仮説C: Meeting APIは再文字起こし受付時に `final_transcription.status=queued` をコミット済み。これを共通ステータス表示へ接続すれば、独自の状態を増やさず一覧・詳細を統一できる。コード確認で支持。
- 仮説D: 一覧は通常の初回ローディング表示を出さずに処理状態だけ再取得できれば、再文字起こし完了後も画面遷移なしで正しい状態へ追従できる。既存ストアへ後方互換なサイレント更新オプションを追加する方針で支持。

## 反証確認

- 会議本体の `status` を `completed` から別値へ変更すると会議ライフサイクル契約を壊すため不採用。
- 声紋ページの全入力を個別にdisableする方法は、将来追加される操作の漏れを作るため不採用。
- `loadedmetadata` のみを解除条件にするとブラウザイベント欠落時に永久ロックし得るため、単独利用は不採用。
- 一覧で楽観更新だけを行う方法は、別画面から開始した処理や失敗・完了への遷移を拾えないため不採用。
- 外部仕様や最新ライブラリには依存せず、現行リポジトリの状態契約だけで完結するためWeb調査は不要。

## 信頼度

高。対象状態と表示経路を現行ソース・既存テストで確認済み。覆る条件は、再文字起こしAPIが `final_transcription.status` を保存しない実装へ変更された場合。

```

### 2. Approaches Tried And Failure Reasons

```text
[none recorded for this consultation]
```

### 3. Current Hypothesis

[not specified]

### 4. Questions To Decide

1. Does this plan faithfully capture the user's intent and unstated constraints?
2. What important alternative or edge case is missing before implementation starts?
3. Is the verification contract strong enough to prove the intended outcome?

## Decision Or Result Under Review

[review the current diff and plan evidence]

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
 M AGENTS.md
 M CLAUDE.md
?? .pipeline/evidence/dashboard-ux-status-polish/
?? .pipeline/gates/dashboard-ux-status-polish/
?? .pipeline/plans/dashboard-ux-status-polish/

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
index b956b1e..102f0ca 100644
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -119,7 +119,7 @@ Use:
 <!-- gitnexus:start -->
 # GitNexus — Code Intelligence

-This project is indexed by GitNexus as **generic_tldv** (16878 symbols, 30951 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.
+This project is indexed by GitNexus as **generic_tldv** (16868 symbols, 30948 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

 > Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

diff --git a/CLAUDE.md b/CLAUDE.md
index 2489528..c17b1a5 100644
--- a/CLAUDE.md
+++ b/CLAUDE.md
@@ -88,7 +88,7 @@ See `.ai/BUILD.md`.
 <!-- gitnexus:start -->
 # GitNexus — Code Intelligence

-This project is indexed by GitNexus as **generic_tldv** (16878 symbols, 30951 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.
+This project is indexed by GitNexus as **generic_tldv** (16868 symbols, 30948 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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
