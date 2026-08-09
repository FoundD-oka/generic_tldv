# Verification Contract — p14-ci-vexa-bot

対象: `base-commit..HEAD` の差分(base-commit はコーディネータが着手時に
plan.md frontmatter へ書き込んだ値)。検証は commit 済み clean tree に対して行う。

## ベースライン取得手順(転記値の使用禁止)

1. 実装着手前に base-commit の clean tree で、macOS ローカルの従来手順
   (既存 `services/vexa-bot/node_modules` を共有)により
   `cd services/vexa-bot/core && npm test` を実行し、出力全文を
   `.hw/gates/p14-ci-vexa-bot/npmtest-baseline-<commit>.txt` に保存する。
2. **この実測(テスト7本すべての実行と成功)が本契約のベースライン**。
   ベースラインで失敗がある場合は着手前に報告。
3. Linux での install / テスト成否はローカルで測定不能のため、ベースラインは
   「テストロジック自体は緑」の確認に限る(Linux は CI 実測に委ねる)。

## 検証の権威分担

- **ローカルで確認するもの**: workflow 構文・禁止パターン不在・macOS での
  npm test 緑(ベースライン)・lockfile が package.json の全 deps を解決して
  いること。
- **CI 実行結果に委ねるもの(最終権威)**: **Linux での `npm ci` 成功**(本タスク
  最大の未知)・npm test 緑・sabotage 赤・キャッシュ効果・実行時間。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | workflow が有効な YAML で、trigger paths が `services/vexa-bot/core/**` + workflow 自身(push / pull_request 両方)に限定され、親ディレクトリ直下を含まない | pyyaml でのパース + 目視レビュー | 出力 + レビュー verdict |
| AT-002 | `services/vexa-bot/core/package-lock.json` が commit され、lockfileVersion ≥ 2 で package.json の dependencies / devDependencies 全項目が解決されている | `node -e` で lockfile と package.json の突合、または `npm ci --dry-run`(macOS では --ignore-scripts 併用可) | 突合出力 |
| AT-003 | **CI 実測緑(本質要求)**: PR 上で `Test Vexa Bot` が npm ci → npm test(build + テスト7本)まで成功する | `gh run list --workflow=test-vexa-bot.yml` + `gh run view <id> --log` でテスト7本の実行行を確認 | run URL とログ抜粋を `.hw/gates/p14-ci-vexa-bot/ci-green.txt` に保存 |
| AT-004 | **sabotage 検証(常に緑でないことの実証)**: テスト期待値を壊す一時 commit で「テストステップの失敗で」赤 → revert で緑 | 一時 commit → `gh run view --log-failed` → revert | 赤/緑 run URL・ログ抜粋を `.hw/gates/.../ci-sabotage.txt` に保存 |
| AT-005 | ブラウザダウンロードのスキップ: job env に PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD / PUPPETEER_SKIP_DOWNLOAD が設定され、install ログにブラウザ DL が現れない | workflow grep + 緑 run の install ステップログ確認 | grep 出力 + ログ抜粋 |
| AT-006 | npm キャッシュが効き、緑 run の job 実行時間が 10 分以内 | revert 後 run のログで cache restore 確認 + `gh run view --json jobs` | ログ・JSON 抜粋 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 握り潰しなし: workflow に `continue-on-error` / `\|\| true` / 失敗マスクがない | grep が空 | grep 出力 |
| FP-002 | 変更ファイルが `.github/workflows/test-vexa-bot.yml`・`services/vexa-bot/core/package-lock.json` のみ。例外は plan §3 の範囲(core/package.json への devDependencies 追加、--ignore-scripts 化)に限り、理由が PR 本文と workflow コメントに明記されていること | `git diff --name-only base-commit..HEAD` + 例外時はハンクレビュー | diff 出力 + verdict |
| FP-003 | テストコード・src の無変更: `services/vexa-bot/core/src/**` に差分なし(sabotage commit は revert 済みであること) | `git diff base-commit..HEAD -- services/vexa-bot/core/src/` が空 | diff 出力 |
| FP-004 | `npm test` スクリプト定義の弱化なし: package.json の test スクリプトからテストファイルが削除されていない(7本の `&&` 連結が維持) | package.json の diff レビュー | diff + verdict |
| FP-005 | 既存 workflow 10本が無変更 | `git diff base-commit..HEAD -- .github/workflows/ ':!.github/workflows/test-vexa-bot.yml'` が空 | diff 出力 |
| FP-006 | 親 `services/vexa-bot/`(core 以外)に差分なし | `git diff base-commit..HEAD -- services/vexa-bot/ ':!services/vexa-bot/core/'` が空 | diff 出力 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | actions は既存 test-*.yml と同一の SHA ピンを使用 | uses: 行レビュー | レビュー verdict |
| NFT-002 | secrets を要求しない(contents: read のみ) | workflow レビュー | レビュー verdict |
| NFT-003 | `--ignore-scripts` を使った場合(最後の手段)、その理由と「テストが native binding を実行時に使わない」ことの確認が workflow コメントに記録されている | workflow レビュー | レビュー verdict |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p14-ci-vexa-bot/` へ)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

- PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD / PUPPETEER_SKIP_DOWNLOAD は両ライブラリの
  長期安定 env。効いていない場合は AT-005 の install ログ確認で検出できる
  (リサーチではなく実測で裏取りする設計)。
- onnxruntime-node 1.24 系は Linux x64 の prebuilt を npm 配布しており
  node-gyp ビルド不要の見込み。外れた場合は plan §3 の対応方針に従う。
