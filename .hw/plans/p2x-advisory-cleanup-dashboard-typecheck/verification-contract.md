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

## 改訂履歴

### 2026-08-10 実行役(Opus 5)

- base-commit を実測値へ更新: `0fe5ea5`(プラン記載・古い)→ `6fe1b23`(着手時 HEAD)。
  plan.md frontmatter と `base-commit` ファイルの両方を更新。
- 契約に Research Freshness Check の節はなし(該当なし)。代わりにベースライン取得手順を実測した。
- ベースライン実測(`6fe1b23`、`npm install --no-audit --no-fund` + `npm run sync-packages` 後):
  - `npx tsc --noEmit`: exit 2 / エラー1件。
    `tests/test_voiceprint_recording_ui.test.ts(84,7): error TS2769` —
    `VoiceprintPreparationGateProps` の必須 prop `children` 欠落。
    6件未満のため中断条件に該当せず着手。
  - `npm test`: exit 0 / Test Files 33 passed (33) / Tests 263 passed (263)。
  - lint ラチェット: current errors=61 warnings=87 = baseline errors=61 warnings=87。
  - 証跡: `.hw/gates/p2x-advisory-cleanup-dashboard-typecheck/baseline-tsc-6fe1b23.txt`,
    `baseline-test-6fe1b23.txt`(`.hw/gates/` は gitignore のため未コミット)。
- 実装後実測: `npx tsc --noEmit` exit 0 / `npm test` 33 files・263 tests passed(非退行)/
  lint ラチェット OK(errors=61 で増加なし・`lint-baseline.json` 不変)。
- CI 経路の再現確認: `npm ci --no-audit --no-fund`(sync-packages なし・workflow と同条件)後も
  `npx tsc --noEmit` exit 0、`npm test` 263 passed。
- ローカル sabotage 確認: `src/__sabotage_check.ts` に型エラーを注入すると
  `npx tsc --noEmit` が exit 2 で失敗し、削除後に exit 0 へ復帰(検出力あり・一時ファイルは削除済み)。
- 実装メモ: 当初 `createElement` の props に `children` を渡す修正を行ったが
  eslint `react/no-children-prop` が新規1件増え lint ラチェットが赤になったため、
  コンポーネント関数を直接呼ぶ形へ変更した(assertion とレンダリング結果は不変)。
