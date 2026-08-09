# Verification Contract — p3-empty-loading-states

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体: **Fable** / **CI** = GitHub Actions(Test Dashboard)/ **ゲート** = pr-ready-gate。
前提: p3-search-ux マージ後に着手(検索セクションの構造が変わるため)。
着手時に base-commit を実測 HEAD へ更新してよい(frontmatter と base-commit ファイル両方)。

## ベースライン取得手順(転記値の使用禁止)

着手時に `services/dashboard` で `npm install --no-audit --no-fund` 後、`npm test`
サマリ・`npx tsc --noEmit` exit・lint ラチェット current 値を実測し
`.hw/gates/p3-empty-loading-states/baseline-<commit>.txt` へ保存、**実測値と置換マップ
(現行マークアップ→EmptyState の対応表)を本契約の改訂履歴節へ記入**する。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-961 | 会議一覧の2空状態(条件不一致/会議なし)が `EmptyState` で描画され、既存導線(参加ボタン等)が維持される | vitest | CI + Fable | テスト名+結果 |
| AT-962 | mcp が config 取得失敗時に画面内エラー状態+再試行ボタンを描画し、再試行で再 fetch する | vitest | CI + Fable | テスト名+結果 |
| AT-963 | settings にエラー状態+再試行導線がある | vitest または diff | CI + Fable | テスト名+結果 |
| AT-964 | 検索結果のエラー状態から再検索できる | vitest | CI + Fable | テスト名+結果 |
| AT-965 | 追加コピーがすべて日本語で dashboard-copy(JA 辞書)経由 | diff + grep | Fable | diff |
| AT-966 | `npm test`・`npx tsc --noEmit`・lint ラチェットが緑(workflow「Test Dashboard」実走) | CI | CI + ゲート | gh run list |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-961 | 変更ファイルが対象4画面+empty-state.tsx+dashboard-copy.ts+テストに収まる: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` | diff | Fable + ゲート | diff |
| FP-962 | 対象4画面の正常系(データあり)の描画・導線に差分なし(空/エラー/ローディング分岐のみ変更) | diff レビュー + 既存テスト | Fable + CI | diff+サマリ |
| FP-963 | p3-search-ux で入ったテスト(再検索・abort・トグル)非退行 | サマリ比較 | CI + Fable | 両サマリ |
| FP-964 | lint ラチェット errors/warnings とも増加なし。`npm test` pass 数非退行 | CI + 比較 | CI + Fable | 両値 |
| FP-965 | `EmptyState` 自体の既存 props 契約を壊す変更なし(必要なら追加のみ) | diff | Fable | diff |

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-961 | 対象4画面の空/エラー表現の現状(p3-search-ux 等の先行タスクで変わる) | 着手時に再読して置換マップを作成・改訂履歴へ記入 | 改訂履歴 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p3-empty-loading-states/`)
- hash-bound approval required: yes(M・Fable レビュー必須)
