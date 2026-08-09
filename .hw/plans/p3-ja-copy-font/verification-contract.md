# Verification Contract — p3-ja-copy-font

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体: **Fable** / **CI** = GitHub Actions(Test Dashboard)/ **ゲート** = pr-ready-gate。

## ベースライン取得手順(転記値の使用禁止)

着手時に `services/dashboard` で `npm install --no-audit --no-fund` 後、`npm test`
サマリ・`npx tsc --noEmit` exit・lint ラチェット current 値を実測し
`.hw/gates/p3-ja-copy-font/baseline-<commit>.txt` へ保存、**実測値と英語残存の棚卸し
一覧を本契約の改訂履歴節へ記入**する。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-941 | `getDashboardCopy` が未知 locale / undefined / null で JA 辞書を返す(unit test) | vitest | CI + Fable | テスト名+結果 |
| AT-942 | 改訂履歴に列挙した英語残存文字列がすべて日本語化されている(列挙外の変更なし) | 一覧と diff の突合 | Fable | 一覧+diff |
| AT-943 | font-family チェーンに日本語フォントが明示されている(next/font 採用時は `--font-noto-sans-jp` 変数、システムスタック採用時は明示列挙) | grep | Fable + ゲート | grep 出力 |
| AT-944 | `npm run build`(または CI 相当のビルド)が成功する ※フォント取得を含む | ビルドログ + CI | CI + ゲート | ログ |
| AT-945 | `npm test`・`npx tsc --noEmit`・lint ラチェットが緑(workflow「Test Dashboard」実走) | CI | CI + ゲート | gh run list |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-941 | 変更ファイルが `services/dashboard` 内(dashboard-copy.ts / layout.tsx / globals.css / 棚卸し対象ファイル / テスト)に収まる: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` | diff | Fable + ゲート | diff |
| FP-942 | EN 辞書の削除なし。JA 辞書の既存キー削除なし | diff | Fable | diff |
| FP-943 | 既定 locale("ja")での表示文言は棚卸し一覧の範囲以外で不変 | diff レビュー | Fable | diff |
| FP-944 | lint ラチェット errors/warnings とも増加なし。`npm test` pass 数非退行(スナップショット更新はフォント名差分のみ・改訂履歴へ列挙) | CI + サマリ比較 | CI + Fable | 両値 |
| FP-945 | `src/app/docs/**` に差分なし | diff | ゲート | diff |

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-941 | next/font(Google Fonts 取得)が CI・Docker ビルドで成功するという仮説 | 着手時にビルド実測。失敗時は第二候補(明示システムスタック)へ切替え、採否と根拠を改訂履歴へ記録 | 改訂履歴+ビルドログ |
| RF-942 | 英語残存が「少数件」という監査時の想定 | 着手時 grep で全量列挙。20件を超えたらプラン層へ差し戻し(タスク分割を再検討) | 改訂履歴の一覧 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p3-ja-copy-font/`)
- hash-bound approval required: yes(M・Fable レビュー必須)
