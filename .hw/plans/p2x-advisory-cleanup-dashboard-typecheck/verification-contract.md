# Verification Contract — p2x-advisory-cleanup-dashboard-typecheck

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体: **CI** = GitHub Actions(Test Dashboard)/ **ゲート** = pr-ready-gate
実行者が `.hw/gates/p2x-advisory-cleanup-dashboard-typecheck/` を確認。S のため
Fable レビューなし。

## ベースライン取得手順(転記値の使用禁止)

着手時に `npm ci` 後の `npx tsc --noEmit` のエラー全文と `npm test` のサマリを
`.hw/gates/p2x-advisory-cleanup-dashboard-typecheck/baseline-<commit>.txt` へ保存する。
tsc エラーが6件以上ならタスクを中断してプラン層へ差し戻す。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-701 | `npx tsc --noEmit` が exit 0 | ローカル実行ログ + CI | CI + ゲート | 実行ログ |
| AT-702 | test-dashboard.yml に Typecheck ステップ(`npx tsc --noEmit`)が存在し、本 PR で workflow「Test Dashboard」が実走して緑 | `gh run list --branch <branch>`(workflow 名で確認) | CI + ゲート | gh run list 出力 |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-701 | `npm test` 非退行(ベースラインの pass 数を維持) | サマリ比較 + CI | CI + ゲート | 両サマリ |
| FP-702 | 変更ファイルが型エラー該当ファイル(想定: `services/dashboard/tests/test_voiceprint_recording_ui.test.ts`)と `.github/workflows/test-dashboard.yml` のみ: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` | diff | ゲート | diff 出力 |
| FP-703 | `services/dashboard/tsconfig.json` に差分なし(設定緩和でエラーを消していない) | diff | ゲート | diff 出力 |
| FP-704 | test-dashboard.yml の差分が Typecheck ステップ追加のみ(既存ステップ・トリガー節に差分なし) | diff ハンク確認 | ゲート | diff 出力 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p2x-advisory-cleanup-dashboard-typecheck/`)
- hash-bound approval required: no(S・機械検証のみ)
