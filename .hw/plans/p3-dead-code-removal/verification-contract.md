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

## 改訂履歴

### 2026-08-10 実装時ベースライン実測(base-commit 更新込み)

`base-commit` はプラン確定後に plan コミット(02df7cf)を積んだためズレていた。
実測 HEAD に合わせ `plan.md` frontmatter と `base-commit` を
`8bfb4476bcbc25b935004e6c555ea3d0425b1470` → `02df7cffd0c502197c9ab53eb230b7c9bd8f68b1`
へ更新した(差分は plan コミット1本のみで、対象コードに変更なし)。

実測手順(`services/dashboard` にて。転記でなく本タスクで実行):
`npm install --no-audit --no-fund` → `npm run sync-packages` →
`npm run generate-release-version`(`src/lib/release-version.generated.json` を生成。
これが無いと `tsc --noEmit` が落ちる)。
環境: node v22.23.0 / eslint v9.39.1 / vitest v4.1.0。

| 項目 | 着手時ベースライン | 実装後 |
|---|---|---|
| `npm test` | 33 files / 263 tests passed, exit 0 | 33 files / 263 tests passed, exit 0 |
| `npx tsc --noEmit` | exit 0 | exit 0 |
| lint ratchet current | errors=61 warnings=87 fatalErrors=0 | errors=50 warnings=84 fatalErrors=0 |
| `lint-baseline.json` | `{"errors": 61, "warnings": 87}` | `{"errors": 50, "warnings": 84}`(errors -11 / warnings -3、減少方向のみ) |

測定コマンド:
`npx eslint . --format json --output-file eslint-report.json` →
`node scripts/ci/lint-ratchet.mjs eslint-report.json lint-baseline.json`。

### 2026-08-10 削除対象13件の参照0再実測(AT-912 / How ステップ1)

各ベース名について
`git grep -n -I -F "<base>" -- . ':(exclude)services/dashboard/node_modules'`
を実行(`validate` は語が広すぎるため `validate.sh` で実施)。
動的 import・文字列パス参照・`next/dynamic`・テストからの参照も併せて確認した
(`git grep -n -I -E "next/dynamic|import\("` は `services/dashboard/src` で0件、
`DecisionsPanel` シンボル参照は自ファイル内の定義のみ)。

**削除した12件(機能参照0)**

| ファイル | 参照0の確認方法と残存ヒットの性質 |
|---|---|
| `src/components/decisions/decisions-panel.tsx` | `decisions-panel` / `DecisionsPanel` / `components/decisions` の3語で grep。ヒットは (a) 自ファイル内の定義、(b) `.pipeline/` の旧リファクタ計画・preflight(旧 harness-init 資産で hw フローは参照しない)、(c) `scripts/build-clean.sh:213` のコミットグルーピング用パスパターン。(c) は `git diff --name-only … -- "$pattern" \|\| true` と `git show … >/dev/null 2>&1` でガードされ、パス消失時は no-op。import は repo 全体で0件 |
| `agent-flow.js` `agent-inspect.js` `auth-validate.js` `auth-validate2.js` `auth-validate3.js` `auth-validate-final.js` `check-pages.js` `deliver-validate.js` `deliver-validate.ts` `feature-validate.js` `test-agent-panel.mjs` | 各ベース名 grep のヒットは (a) 自ファイル内文字列(`agent-inspect.js` の `/tmp/screenshots/agent-inspect.png` のみ)、(b) `.cleanignore`、(c) `.gitguardian.yml`、(d) `.pipeline/` 旧計画、(e) `.hw/plans/` の本件プラン。`package.json` scripts・`Dockerfile`・`.github/workflows/`・`Makefile`・`tests3/`・`README` からの参照は0件。(b) の消費者 `scripts/build-clean.sh` / `scripts/sync-clean.sh` は `git ls-files --cached "$pattern"` の結果が空なら skip する実装で、存在しないパスは no-op(除外リストであって依存ではない)。(c) は ggshield の secret scan 除外パターンで、対象消滅時も無害 |

**削除しなかった1件(How ステップ1により保留・AT-911 の免除対象)**

| ファイル | 見つかった参照 |
|---|---|
| `services/dashboard/validate.sh` | `tests3/tests/static/dashboard-config-ssot.sh:18` が `"$ROOT_DIR/services/dashboard/validate.sh"` を `SEARCH_PATHS` 配列に入れ、`rg` の探索対象として実行時に渡している。稼働中の静的テストからの実参照のため How ステップ1「1件でも見つかったものは削除せず残して報告」に従い残置。削除対象は13→12件(10本以上のため差し戻し条件には該当せず) |
