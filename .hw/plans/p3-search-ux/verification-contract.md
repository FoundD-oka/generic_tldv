# Verification Contract — p3-search-ux

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体: **Fable** / **CI** = GitHub Actions(Test Dashboard)/ **ゲート** = pr-ready-gate。

## ベースライン取得手順(転記値の使用禁止)

着手時に `services/dashboard` で `npm install --no-audit --no-fund` 後、`npm test`
サマリ・`npx tsc --noEmit` exit・lint ラチェット current 値を実測し
`.hw/gates/p3-search-ux/baseline-<commit>.txt` へ保存、**実測値を本契約の改訂履歴節へ
記入**する。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-931 | 更新ボタンで文字起こし検索が再実行される(unit test 名を改訂履歴に記載) | vitest | CI + Fable | テスト名+結果 |
| AT-932 | 新しい検索の開始で直前の in-flight リクエストが abort され、AbortError は error 表示にならない | vitest(fetch モックの signal 検証) | CI + Fable | テスト名+結果 |
| AT-933 | 4件以上ヒット時に3件+「他N件を表示」トグルが描画され、展開で全件表示 | vitest | CI + Fable | テスト名+結果 |
| AT-934 | 追加コピーが日本語で dashboard-copy(JA 辞書)経由 | diff + grep | Fable | diff |
| AT-935 | `npm test`・`npx tsc --noEmit`・lint ラチェットが緑(workflow「Test Dashboard」実走) | CI | CI + ゲート | gh run list |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-931 | 変更ファイルが `services/dashboard` 内の対象(page.tsx / transcript-search-results.tsx / transcript-search.ts / dashboard-copy.ts / テスト)に収まる: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` | diff | Fable + ゲート | diff |
| FP-932 | meeting-api / api-gateway / 認可境界に差分なし(検索 API 側を触らない) | 同上 | Fable + ゲート | diff |
| FP-933 | PR #68 の既存テスト(配線・ハイライト・2文字 idle)非退行。削除・skip 化なし | サマリ+diff | Fable + CI | 両サマリ |
| FP-934 | 世代ガードの削除・弱体化なし(abort を正しさの根拠に置き換えていない) | diff レビュー | Fable | diff |
| FP-935 | lint ラチェット errors/warnings とも増加なし | CI + 値比較 | CI + Fable | 両値 |

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-931 | 「世代ガードで stale 応答は破棄される」前提(triage 記録由来) | 着手時に page.tsx の実装を読み、実測結果を改訂履歴へ記録。崩れていたら差し戻し | 改訂履歴 |
| RF-932 | `fetchTranscriptSearch` の署名・呼び出し箇所が PR #68 時点のまま | 着手時 grep | 改訂履歴 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p3-search-ux/`)
- hash-bound approval required: yes(M・Fable レビュー必須)
