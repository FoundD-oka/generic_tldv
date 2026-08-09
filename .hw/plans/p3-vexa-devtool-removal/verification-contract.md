# Verification Contract — p3-vexa-devtool-removal

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体: **Fable** = 契約レビュー / **CI** = GitHub Actions(Test Dashboard)/
**ゲート** = pr-ready-gate。

## ベースライン取得手順(転記値の使用禁止)

着手時に `services/dashboard` で `npm install --no-audit --no-fund` 後、`npm test`
サマリ・`npx tsc --noEmit` exit・lint ラチェット current 値を実測し
`.hw/gates/p3-vexa-devtool-removal/baseline-<commit>.txt` へ保存、**実測値を本契約の
改訂履歴節へ記入**する(Fable は `.hw/gates/` に到達できない)。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-921 | `git grep -n "vexa\.ai" services/dashboard/src -- ':(exclude)services/dashboard/src/app/docs'` が0件 | grep | Fable + ゲート | grep 出力 |
| AT-922 | `DocsLink`・`docs-mode`・`apiView` の参照が `src/app/docs/**` 以外で0件 | grep | Fable + ゲート | grep 出力 |
| AT-923 | 停止ダイアログの日本語文言(「文字起こしを停止しますか?」等)と停止ボタンが差分で失われていない | diff 目視 | Fable | diff |
| AT-924 | `robots.ts`/`sitemap.ts`/`api/config/route.ts` に vexa.ai 系既定値がない(env 優先ロジックは不変) | diff + grep | Fable | diff |
| AT-925 | `npm test`・`npx tsc --noEmit`・lint ラチェットが緑(workflow「Test Dashboard」実走) | CI | CI + ゲート | gh run list |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-921 | `src/app/docs/**` と `next.config.ts` に差分なし(RF-74I 委譲領域) | `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` | Fable + ゲート | diff |
| FP-922 | 変更ファイルが plan.md 記載の対象(削除2〜3件+使用側8ファイル+robots/sitemap/config route+必要なテスト)に収まる | 同上 | Fable | diff |
| FP-923 | lint ラチェット errors/warnings とも増加なし(tsc 修正が eslint を赤にする前科に注意) | CI + 値比較 | CI + Fable | 両値 |
| FP-924 | `npm test` pass 数非退行。テストの丸ごと削除・skip 化なし(DocsLink/apiView 依存 assert の除去のみ可、改訂履歴へ列挙) | サマリ+diff | Fable + CI | 両サマリ |
| FP-925 | isHosted 分岐・BillingStatus・課金表示ロジックに挙動変更なし(フォールバック URL 除去のみ) | diff | Fable | diff |

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-921 | 「docs 配下は DocsLink/webapp-url を import していない」という前提 | 着手時に grep で実測し、import があれば webapp-url.ts 等を削除対象から外して改訂履歴へ記録 | 改訂履歴の実測記録 |
| RF-922 | DocsLink 使用ファイルが8件という前提(Codex 並行編集で増減し得る) | 着手時に `git grep -l "DocsLink"` を再実測 | 同上 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p3-vexa-devtool-removal/`)
- hash-bound approval required: yes(M・Fable レビュー必須)
