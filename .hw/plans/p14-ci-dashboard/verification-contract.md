# Verification Contract — p14-ci-dashboard

対象: `base-commit..HEAD` の差分(base-commit はコーディネータが着手時に
plan.md frontmatter へ書き込んだ値)。検証は commit 済み clean tree に対して行う。

## ベースライン取得手順(転記値の使用禁止)

1. 実装着手前に base-commit の clean tree で:
   `cd services/dashboard && npm ci --no-audit --no-fund`
   - `npm test` → サマリ(files / tests 数)を
     `.hw/gates/p14-ci-dashboard/vitest-baseline-<commit>.txt` に保存。
   - `npx eslint . --format json --output-file /tmp/eslint-base.json`(exit 1 許容)
     → errorCount / warningCount 合計を集計し
     `.hw/gates/p14-ci-dashboard/eslint-baseline-<commit>.txt` に保存。
2. **この実測値が本契約のベースライン**であり、`lint-baseline.json` に commit
   する値もこの実測値。handoff の概数(61/87)の転記は無効。
3. ベースラインで vitest に fail がある場合は着手前に報告。

## 検証の権威分担

- **ローカルで確認するもの**: workflow 構文・禁止パターン不在・vitest 緑・
  lint-ratchet.mjs の判定境界(合成 report JSON で 同数=緑 / +1=赤 / fatal=赤)。
- **CI 実行結果に委ねるもの(最終権威)**: Linux での npm ci 成功・テスト緑・
  ラチェット緑・2種 sabotage の赤・キャッシュ効果・実行時間。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | workflow が有効な YAML で、trigger paths が `services/dashboard/**` + `packages/transcript-rendering/**` + workflow 自身(push / pull_request 両方)に限定されている | pyyaml でのパース + 目視レビュー | 出力 + レビュー verdict |
| AT-002 | **CI 実測緑(本質要求)**: PR 上で `Test Dashboard` が npm ci → vitest → lint ラチェットまで全ステップ成功。vitest のテスト数がベースラインと同数 | `gh run list --workflow=test-dashboard.yml` + `gh run view <id> --log` | run URL とログ抜粋を `.hw/gates/p14-ci-dashboard/ci-green.txt` に保存 |
| AT-003 | **sabotage 検証(テスト)**: テスト期待値を壊す一時 commit で「テストステップの失敗で」赤 → revert で緑 | 一時 commit → `gh run view --log-failed` → revert | 赤/緑 run URL・ログ抜粋を `.hw/gates/.../ci-sabotage-test.txt` に保存 |
| AT-004 | **sabotage 検証(lint ラチェット)**: 新規 lint error を1件足す一時 commit で「ラチェットステップの失敗で」赤(現在値>基準値の出力を含む)→ revert で緑 | 同上 | `.hw/gates/.../ci-sabotage-lint.txt` に保存 |
| AT-005 | lint-ratchet.mjs の判定境界: 合成 report で (a) errors/warnings がベースライン同数 → exit 0、(b) errors +1 → exit 1、(c) warnings +1 → exit 1、(d) fatalErrorCount ≥1 → exit 1 | ローカルで合成 JSON を与えて実行し exit code 確認 | 実行ログを `.hw/gates/.../ratchet-boundary.txt` に保存 |
| AT-006 | `lint-baseline.json` の値がベースライン取得手順の実測値と一致する | evidence の eslint-baseline と commit された JSON の突合 | 突合結果 |
| AT-007 | eslint クラッシュ(exit ≥2)がラチェットに吸収されず job fail になる分岐が workflow に存在する | workflow の該当ステップのレビュー | レビュー verdict |
| AT-008 | npm キャッシュが効く: 2回目以降の run ログに cache restore が記録され、job 実行時間が 10 分以内 | revert 後 run のログ + `gh run view --json jobs` | ログ・JSON 抜粋 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 握り潰しなし: workflow に `continue-on-error` / `\|\| true` がない。`set +e` は eslint 呼び出し1行の exit code 捕捉のみで、直後に `set -e` 復帰と ec 判定がある | grep + 該当ステップのレビュー | grep 出力 + verdict |
| FP-002 | 変更ファイルが `.github/workflows/test-dashboard.yml`・`services/dashboard/lint-baseline.json`・`services/dashboard/scripts/ci/lint-ratchet.mjs` の3つのみ(src / tests / package.json / eslint.config.mjs / vitest.config は無変更) | `git diff --name-only base-commit..HEAD` | diff 出力 |
| FP-003 | vitest 設定の弱化なし: passWithNoTests の有効化・include の縮小がない(FP-002 で vitest.config 無変更なら自動成立) | FP-002 の diff | diff 出力 |
| FP-004 | ローカル `npm test` の結果がベースラインと同数(本タスクはテストを変更しない) | 同一コマンドで HEAD を再実行しサマリ比較 | 両サマリ全文 |
| FP-005 | 依存追加なし: package.json / package-lock.json 無変更(lint-ratchet.mjs は Node 標準モジュールのみ) | FP-002 の diff + import レビュー | diff + verdict |
| FP-006 | 既存 workflow 10本が無変更 | `git diff base-commit..HEAD -- .github/workflows/ ':!.github/workflows/test-dashboard.yml'` が空 | diff 出力 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | actions は既存 test-*.yml と同一の SHA ピンを使用 | uses: 行レビュー | レビュー verdict |
| NFT-002 | secrets を要求しない(contents: read のみ) | workflow レビュー | レビュー verdict |
| NFT-003 | lint-ratchet.mjs に baseline を「上げる」抜け道の自動化がない(基準値変更は人間の commit のみ) | ソースレビュー | レビュー verdict |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p14-ci-dashboard/` へ)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

- eslint v9 flat config の `--format json` 出力スキーマ(results[].errorCount /
  warningCount / fatalErrorCount)は安定 API。vitest の「テスト0件で非0 exit」
  既定は v4 系で不変。いずれも実装時にローカル実行で確認できるため、
  外部リサーチへの依存なし。
