---
generated_by: fable
task_id: p14-ci-vexa-bot
base-commit: 704e7380e8ab45ca957777bb4997e4386297a232
size: M
---

# ST-26(3/4): vexa-bot core の単体テスト CI を追加する(P1-4 第3弾)

## ゴール

依頼の文字通りの内容: 「vexa-bot に CI が1本もない」(監査 ST-26)。

reframe(対象の限定): CI 対象は **`services/vexa-bot/core/` のみ**とする。
親ディレクトリ `services/vexa-bot/`(binding.gyp = Zoom native SDK / Qt5・
Linux 前提の node-gyp ビルド)は、単体テストを持たず、ビルド検証には SDK
実体と専用イメージが必要で「単体CI」の範囲を超える(advisory として報告)。
core の `npm test` は tsc build + 手書きテスト7本(join / participant-roster /
googlemeet-roster-dom / chat-roster-lock / unified-callback / admission /
admission-classifier)で、ブラウザ起動なしの純ロジックテスト
(join.test.ts に playwright 実 import なし。現物確認済み)。

## 現状分析(現物確認済み、2026-08-09)

- vexa-bot を検証する CI は0(ST-26 未解消)。
- `core/package.json` の `npm test` = `npm run build --silent && tsx <5本> &&
  node dist/...roster-dom.test.js && ...`。`&&` 連結なのでどれか1本の失敗・
  ファイル消失(module not found)で非0 = 「収集0で緑」問題は構造的に起きない。
- **core に package-lock.json が無い** → `npm ci` 不可。lockfile 生成が必要。
- macOS では親 workspace の `npm install` が node-gyp rebuild で失敗する
  (handoff 記載)。ただし **core 単体は親と独立した package**(親の deps は
  node-addon-api のみ)で、core の deps に native ビルド必須のものはない
  (onnxruntime-node は prebuilt バイナリ、playwright / puppeteer は
  ブラウザダウンロードを env でスキップ可能)。
- ローカル(macOS)では core の node_modules が親 `services/vexa-bot/
  node_modules` への解決に依存して動いている(handoff)。**CI では core 単体の
  `npm ci` で完結させる必要があり、これが本タスク最大の未知**(Linux での
  install 成否、core/package.json の deps だけでテストが完結するか)。
  src の bare import 一覧(grep 実測): playwright / playwright-core /
  playwright-extra / redis / zod / onnxruntime-node + Node 組み込みのみ。
  playwright-core は playwright の依存として解決される見込み。
  puppeteer 系は src から直接 import されていない(deps には残っている)。
- 既存 test-*.yml の流儀(SHA ピン、paths、permissions)と pull_request paths
  に workflow 自身を含める必要性は p14-ci-transcription-service の plan と同じ。

## How

変更は2ファイル: `services/vexa-bot/core/package-lock.json`(新規)、
`.github/workflows/test-vexa-bot.yml`(新規)。

### 1. `services/vexa-bot/core/package-lock.json` の生成・commit

- `cd services/vexa-bot/core && npm install --package-lock-only --ignore-scripts`
  で lockfile のみ生成(macOS でも scripts なしなら安全に完了する見込み)。
- 生成後、lockfile の `packages` に package.json の全 deps が解決されている
  ことを確認して commit。

### 2. `.github/workflows/test-vexa-bot.yml`(新規)

- `name: Test Vexa Bot`
- trigger(push / pull_request とも同一 paths):
  - push: branches [main, feature/*], paths: `services/vexa-bot/core/**`,
    `.github/workflows/test-vexa-bot.yml`
  - pull_request: paths 同上
  - 親ディレクトリ直下(binding.gyp 等)は対象外(reframe 参照)
- `permissions: contents: read`
- job `test`: ubuntu-latest, `timeout-minutes: 15`,
  `defaults.run.working-directory: services/vexa-bot/core`
- job レベル env(install の重量削減。テストはブラウザ・DL バイナリ不要):
  - `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: "1"`
  - `PUPPETEER_SKIP_DOWNLOAD: "1"`
  - `PUPPETEER_SKIP_CHROMIUM_DOWNLOAD: "1"`(旧名の保険)
- steps:
  1. checkout(既存 SHA ピン、persist-credentials: false)
  2. setup-node(既存 SHA ピン)`node-version: '20'`, `cache: 'npm'`,
     `cache-dependency-path: services/vexa-bot/core/package-lock.json`
  3. `npm ci --no-audit --no-fund`
  4. `npm test`
- 禁止事項: `continue-on-error` / `|| true` を使わない。

### 3. CI で install / テストが赤になった場合の対応方針(実装者向け)

想定される失敗と許容される対処(いずれも PR に理由を明記):
- onnxruntime-node 等の postinstall 失敗 → バージョン確認の上、まず素直に
  解決を試みる。**最後の手段**として `npm ci --ignore-scripts` に切り替える
  場合は、テスト7本が native binding を実行時に require しないことを CI の
  緑で確認し、その旨を workflow コメントに残す。
- core 単体の node_modules で module not found(親 node_modules に隠れた依存)
  → 不足パッケージを core/package.json の devDependencies に追加し lockfile
  再生成(これは「core が独立してテスト可能になる」正しい方向の修正)。
- 上記で解決しない構造的な問題(例: テストが Linux で本質的に動かない)は
  握り潰さず停止して報告(コーディネータ判断へ)。

### 4. 検証手順

1. ローカル(macOS): base-commit で従来手順(親 node_modules 共有)により
   `cd services/vexa-bot/core && npm test` が緑であることを実測(ベースライン)。
2. PR 作成 → `Test Vexa Bot` が npm ci → npm test まで緑を確認(**Linux での
   install 成否はここが最初で最終の権威**)。
3. **sabotage 検証**: テスト1本の期待値を壊す一時 commit → テストステップで
   赤 → revert → 緑。
4. revert 後 run で npm キャッシュ restore と job 時間(10分以内目安)を確認。

## 変更しないもの

- core の src / tsconfig / package.json(隠れ依存の devDependencies 追加のみ
  例外として許容。§3 参照)。
- 親 `services/vexa-bot/`(binding.gyp / Makefile / Dockerfile)。
- 既存 workflow 10本。

## Why(実装者に渡さない)

- 親ディレクトリを外す判断: Zoom native SDK のビルドは SDK 配布物と Linux/Qt5
  環境を要求し、GitHub ホストランナーでの再現は「単体CI追加」の重さを超える。
  ST-26 の本体はテスト資産(core の7本)が回っていないことなので、core 限定で
  監査意図の大部分を回収できる。native ビルドの CI 化は Dockerfile ビルド検証
  (別axis)として advisory に落とす。
- ブラウザダウンロードのスキップを job env で固定する理由: テストは純ロジック
  で、playwright/puppeteer の postinstall は合計数百MBのダウンロード。スキップ
  しないと CI 時間とコストが3〜5倍になる。将来ブラウザ必須のテストを足す人は
  env を外す必要があり、その時点で意識的な判断が強制される(暗黙に遅くなる
  より良い)。
- lockfile を commit する理由: `npm ci` の再現性が「常に緑/常に赤」の切り分け
  の前提。lockfile なしの `npm install` は日々解決が変わり、CI の赤が「自分の
  変更のせいか依存のせいか」判別不能になる。
- --ignore-scripts を最後の手段に限定する理由: scripts スキップは依存の
  postinstall 全部に効く鈍器で、将来 native binding をテストで使い始めたとき
  「install は緑・実行時に初めて壊れる」を作る。使うなら明示コメント必須。
