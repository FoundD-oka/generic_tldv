# Verification Contract — p3-dead-code-removal

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体: **CI** = GitHub Actions(Test Dashboard)/ **ゲート** = pr-ready-gate。
S のため Fable レビューなし。

## ベースライン取得手順(転記値の使用禁止)

着手時に `services/dashboard` で `npm install --no-audit --no-fund` 後、
`npm test` サマリ・`npx tsc --noEmit` の exit・lint ラチェットの current
errors/warnings を実測し `.hw/gates/p3-dead-code-removal/baseline-<commit>.txt`
へ保存する。**実測値は本契約の改訂履歴節へも記入する**(Fable は `.hw/gates/`
に到達できない)。あわせて削除対象13ファイルの参照0を再実測し結果を記録する。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-911 | 対象13ファイルが存在しない(参照が見つかり残したものは改訂履歴に列挙し、その分は免除) | `test ! -e` ×13 | ゲート | 実行ログ |
| AT-912 | 削除ファイルへの repo 内参照0: `git grep` でベース名検索(node_modules 除外)が自ファイル以外0件 | grep | ゲート | grep 出力 |
| AT-913 | `npm test`・`npx tsc --noEmit`・lint ラチェットが緑(workflow「Test Dashboard」実走) | CI | CI + ゲート | gh run list 出力 |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-911 | 変更ファイルが「削除対象13件+`services/dashboard/lint-baseline.json`」のみ: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` | diff | ゲート | diff 出力 |
| FP-912 | `lint-baseline.json` は errors/warnings とも減少方向のみ(着手時ベースライン errors/warnings と比較し、いずれも増えていない) | JSON 比較 | ゲート | 両値 |
| FP-913 | `npm test` の pass 数がベースラインから非退行 | サマリ比較 | CI + ゲート | 両サマリ |
| FP-914 | `docker-entrypoint.sh`・`eslint.config.mjs`・`tsconfig.json`・`vitest.config.ts`・`package.json` に差分なし | diff | ゲート | diff 出力 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p3-dead-code-removal/`)
- hash-bound approval required: no(S・機械検証のみ)
