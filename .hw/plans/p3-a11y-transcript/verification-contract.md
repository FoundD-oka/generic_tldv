# Verification Contract — p3-a11y-transcript

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体: **Fable** / **CI** = GitHub Actions(Test Dashboard)/ **ゲート** = pr-ready-gate。

## ベースライン取得手順(転記値の使用禁止)

着手時に `services/dashboard` で `npm install --no-audit --no-fund` 後、`npm test`
サマリ・`npx tsc --noEmit` exit・lint ラチェット current 値を実測し
`.hw/gates/p3-a11y-transcript/baseline-<commit>.txt` へ保存、**実測値とアイコンのみ
ボタンの列挙一覧を本契約の改訂履歴節へ記入**する。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-951 | 「この時刻から再生」が `<button type="button">` であり、click で onClickSegment が1回だけ発火(行クリックとの二重発火なし) | vitest | CI + Fable | テスト名+結果 |
| AT-952 | 編集ボタン・範囲選択チェックボックスの操作が再生を誤発火しない | vitest | CI + Fable | テスト名+結果 |
| AT-953 | 改訂履歴に列挙したアイコンのみボタン全件に日本語 aria-label が付与されている(残欠落0件) | 一覧と grep の突合 | Fable | 一覧+grep 出力 |
| AT-954 | `globals.css` に `@media (prefers-reduced-motion: reduce)` ブロックが存在し、自前 `animate-*` 定義が無効化対象に含まれる | grep + diff | Fable + ゲート | grep 出力 |
| AT-955 | `npm test`・`npx tsc --noEmit`・lint ラチェットが緑(workflow「Test Dashboard」実走) | CI | CI + ゲート | gh run list |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-951 | DOM 構造の変更(要素タグの変更・追加)が `transcript-segment.tsx` と `globals.css` 以外に無い(他ファイルは aria-label 属性の追加のみ) | diff レビュー | Fable | diff |
| FP-952 | 変更ファイルが `services/dashboard` 内に収まる: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` | diff | Fable + ゲート | diff |
| FP-953 | 既存 aria-label(実測20箇所)の文言変更なし | diff | Fable | diff |
| FP-954 | `npm test` pass 数非退行。lint ラチェット errors/warnings とも増加なし | CI + 比較 | CI + Fable | 両値 |
| FP-955 | 「再生」の className・表示条件(hover/focus-within/activePlayback)の削除なし(追加のみ可) | diff | Fable | diff |

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-951 | Tailwind `pointer-coarse:` variant が本プロジェクトの Tailwind バージョンで使える | 着手時に実測。不可なら `@media (pointer: coarse)` を globals.css へ(採否を改訂履歴へ) | 改訂履歴 |
| RF-952 | reduced-motion の一括無効化が `tw-animate-css` のアニメにも効く | 着手時にブラウザ/ビルド成果物で実測 | 改訂履歴 |
| RF-953 | アイコンのみボタンの全量(監査概数46箇所) | 着手時 grep で列挙。60件を超えたらプラン層へ差し戻し | 改訂履歴の一覧 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p3-a11y-transcript/`)
- hash-bound approval required: yes(M・Fable レビュー必須)
