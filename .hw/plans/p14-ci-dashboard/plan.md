---
generated_by: fable
task_id: p14-ci-dashboard
base-commit: 69d01b2f887a0765d2900f83545e1b58bcf8de0c
size: M
---

# ST-26(2/4): dashboard の単体テスト + lint ラチェット CI を追加する(P1-4 第2弾)

## ゴール

依頼の文字通りの内容: 「dashboard に CI が1本もない」(監査 ST-26)。

reframe(lint の扱い): `npm test`(vitest、32 files / 240 tests 全pass)は
そのまま必須化できるが、lint はベースから **61 errors / 87 warnings 規模の
既存負債**があり(概数。正確な値は着手時に実測)、exit 0 必須にすると CI が
常時赤 = 誰も見なくなり ST-27 の逆パターンに堕ちる。よって lint は
**閾値固定(ラチェット)方式**で CI に含める: ベースライン実測値を commit した
基準ファイルと比較し、**新規増加0** を強制する。負債を返したら基準値を下げる。
この方式は本リポジトリの `.hw/verify.sh` + `.hw/verify-baseline`(既知の失敗を
列挙し、新規回帰のみ block)と同じ設計思想であり、リポジトリ内に前例がある。

## 現状分析(現物確認済み、2026-08-09)

- dashboard を検証する CI は0。`deploy-dashboard-gcp.yml` は push main で
  テストなしに即デプロイする(advisory: 将来 test-dashboard 緑を前提条件に
  できるが本タスク外)。
- `services/dashboard/package-lock.json` あり → `npm ci` 可能。
- `npm test` = `vitest run`(tests/**/*.test.ts)。vitest はテスト0件のとき
  既定で非0 exit(passWithNoTests は未設定)なので「収集0で緑」にはならない。
- `@vexaai/transcript-rendering` は `file:../../packages/transcript-rendering`
  依存。**dist/ は commit 済み**(git ls-files で確認)なので npm ci だけで
  テスト実行可能。ただし transcript-rendering の変更は dashboard テストに
  影響し得るため path filter に含める。
- lint: `eslint.config.mjs`(flat config、eslint ^9 + eslint-config-next)。
  `npm run lint` = 引数なし `eslint`。決定性のため CI・ベースライン測定とも
  明示的に `npx eslint .` を使う。
- 既存 test-*.yml の流儀(SHA ピン、paths、permissions)と、pull_request paths
  に workflow 自身を含める必要性は p14-ci-transcription-service の plan と同じ。

## How

変更は4ファイル: `.github/workflows/test-dashboard.yml`(新規)、
`services/dashboard/lint-baseline.json`(新規)、
`services/dashboard/scripts/ci/lint-ratchet.mjs`(新規)、
`services/dashboard/vitest.config.ts`(TZ 固定の1項目追加のみ。§4)。

### 1. `services/dashboard/lint-baseline.json`(新規)

- 実装者が **base-commit の clean tree で実測**して作る(転記禁止):
  `cd services/dashboard && npm ci --no-audit --no-fund && npx eslint . --format json --output-file /tmp/eslint-base.json || true`
  → errorCount / warningCount の合計を集計し
  `{"errors": <実測合計>, "warnings": <実測合計>}` を書く。
- コメント不可の JSON のため、運用ルール(下げてよい・上げるの禁止)は
  lint-ratchet.mjs 冒頭コメントに書く。

### 2. `services/dashboard/scripts/ci/lint-ratchet.mjs`(新規)

- 入力: eslint `--format json` の出力ファイルパス + baseline パス(argv)。
- 集計: 全 results の errorCount / warningCount / fatalErrorCount を合計。
- 判定:
  - fatalErrorCount > 0 → 即 exit 1(パースエラー等は負債でなく故障)
  - errors > baseline.errors または warnings > baseline.warnings → exit 1
    (現在値と基準値、増分を明示してから)
  - errors / warnings が基準より減っている → 緑のまま
    「baseline を下げて commit せよ」のリマインダを出力(強制はしない)
- Node 標準モジュールのみで書く(依存追加なし、~40行)。

### 3. `.github/workflows/test-dashboard.yml`(新規)

- `name: Test Dashboard`
- trigger(push / pull_request とも同一 paths):
  - push: branches [main, feature/*], paths: `services/dashboard/**`,
    `packages/transcript-rendering/**`,
    `.github/workflows/test-dashboard.yml`
  - pull_request: paths 同上
- `permissions: contents: read`
- job `test`: ubuntu-latest, `timeout-minutes: 15`,
  `defaults.run.working-directory: services/dashboard`
- steps:
  1. checkout(既存 SHA ピン、persist-credentials: false)
  2. setup-node(test-packages.yml と同じ SHA ピン)`node-version: '20'`,
     `cache: 'npm'`, `cache-dependency-path: services/dashboard/package-lock.json`
  3. `npm ci --no-audit --no-fund`
  4. Run tests: `npm test`(vitest run)
  5. Lint ratchet:
     ```
     set +e
     npx eslint . --format json --output-file eslint-report.json
     ec=$?
     set -e
     if [ "$ec" -ge 2 ]; then echo "eslint crashed (exit $ec)"; exit "$ec"; fi
     node scripts/ci/lint-ratchet.mjs eslint-report.json lint-baseline.json
     ```
     eslint の exit 1(lint エラーあり)は既知負債の可能性があるため一旦許容し、
     ラチェット判定に委ねる。exit 2 以上(クラッシュ)は握り潰さず即 fail。
     `set +e` の適用範囲はこの eslint 呼び出し1行のみに限定する。
- 禁止事項: `continue-on-error` / `|| true` は使わない(上記 `set +e` ブロックが
  唯一の例外で、exit code を変数捕捉して明示判定するためマスクではない)。

### 4. テストのタイムゾーン固定(PR #63 CI 赤対応。改訂2で追加)

- **事実**: `tests/test_export_and_bot_defaults.test.ts > exportToTxt > uses a
  Japanese text export template` が JST 前提のリテラル期待値
  (`日時: 2026年6月25日 19:00`)を持ち、UTC の CI ランナーで赤になった。
  実測: 当該ファイルは TZ=UTC で 1 failed / TZ=Asia/Tokyo で 6 passed。
  スイート全体を TZ=UTC / America/New_York / Pacific/Kiritimati で実行した
  結果、失敗は全ゾーンともこの1件のみ(1 failed | 242 passed (243))。
  **他のテストに同種の TZ 依存はない**。
- **製品バグではない**: `exportToTxt`(`src/lib/export.ts`)は date-fns +
  ja ロケールで整形し、呼び出し元 `src/app/meetings/[id]/page.tsx` は
  `"use client"` = ブラウザ実行。閲覧者ローカル TZ での整形は意図どおりの
  挙動。欠陥は「テストが実行環境の TZ に暗黙依存」していること。
- **修正**: `vitest.config.ts` の `test` に `env: { TZ: "Asia/Tokyo" }` を
  追加し、**テストプロセスの TZ を Asia/Tokyo に固定**する
  (Node v22 は POSIX で実行中の `process.env.TZ` 変更を即時反映することを
  実測確認済み。vitest は worker 起動時=テスト import 前に test.env を
  process.env へ注入する)。テストファイル自体・製品コードは変更しない。
  万一 `test.env` で固定が効かない場合の代替は setupFiles 先頭での
  `process.env.TZ = "Asia/Tokyo"`(同等の効果。実装者判断で選択可)。
- **workflow には TZ を設定しない**: CI ランナーは UTC のままにする。
  これにより「CI 緑」自体が TZ 固定が効いていることの機械検証になる
  (workflow に TZ を足すと修理の実効性を CI で検証できなくなる)。

### 5. 検証手順(PR での実証)

0. ローカルで `TZ=UTC npm test` と `TZ=Asia/Tokyo npm test` の両方が
   全pass(同数)になることを確認(TZ 固定の実証)。
1. PR 作成 → `Test Dashboard` 緑(全 tests pass + ラチェット緑)を確認。
   ランナーは UTC なので、この緑が TZ 固定の CI 側実証を兼ねる。
2. **sabotage 検証(テスト)**: 既存テスト1件の期待値を壊す一時 commit →
   テストステップで赤 → revert → 緑。
3. **sabotage 検証(lint ラチェット)**: 新規 lint error を1件足す一時 commit
   (例: src 配下に未使用変数)→ ラチェットステップで赤(「errors N+1 >
   baseline N」の出力)→ revert → 緑。ローカルでも合成 report JSON で
   lint-ratchet.mjs の境界(同数=緑 / +1=赤 / fatal=赤)を確認する。
4. revert 後 run で npm キャッシュ restore を確認。

## 変更しないもの

- dashboard の src / tests / package.json / eslint.config.mjs(lint 負債の返済は
  本タスクのスコープ外。ラチェットで凍結するだけ)。vitest.config.ts は
  §4 の TZ env 追加**のみ**(include・passWithNoTests 等の判定に関わる設定は
  変更しない)。テストファイル(tests/**)も無変更(TZ 固定で通るため)。
- packages/transcript-rendering(path filter に含めるだけで変更しない)。
- deploy-dashboard-gcp.yml(テストゲート化は advisory)。

## Why(実装者に渡さない)

- lint を CI から外す選択肢を退けた理由: ST-27/28/29 の委譲先
  (full-repo-refactoring-v2 RF-31〜37/43)は rung/gates/tests3 の話で、
  dashboard lint 負債を拾う担当はどこにもない。外すと新規 lint error が
  無限に積もり、いつか lint を必須化する日のコストが単調増加する。
  ラチェットは追加コスト ~30秒/run で増加だけを止められる。
- 総数比較(ファイル別・ルール別でなく)の粗さは認識済み: 「1件直して1件
  壊す」は通る。ただし per-rule ベースラインは実装・保守コストが跳ね、
  P1-4 の最小合格ライン(新規負債の流入停止)には総数で足りる。
  精緻化は負債返済タスク(将来)の側で行う。
- warnings もラチェット対象にする理由: errors だけだと「error を warning に
  格下げして通す」抜け道が残る。両方凍結すれば設定改変での回避も総数に出る。
- vitest の passWithNoTests 既定(false)に依存して「収集0で緑」を防いでいる。
  設定で有効化された場合はテスト削除が緑で通るようになるため、レビューで
  vitest 設定変更を見る(FP に含めた)。

### 追記(改訂2): TZ 方針の判断理由

- (A)「workflow に `TZ: Asia/Tokyo` を設定」を退けた理由: CI だけ直り
  「JST のマシンでしか通らない」欠陥が残る。他 TZ の開発者がローカルで
  踏み続け、かつ CI 側からは欠陥が見えなくなる(隠蔽に近い)。
- (B) のうち「期待値を実行環境から導出」を退けた理由: 実装と同じ date-fns
  呼び出しで期待値を作るとトートロジー化し、リテラル断言
  (`日時: 2026年6月25日 19:00`)の可読性と検出力を失う。
- 採用した「テストプロセスの TZ を Asia/Tokyo に固定」は、(B) の本質
  (誰の環境でも同一結果)を満たしつつ、日本語限定プロダクト(カボス方針)の
  実運用表示と期待値リテラルを一致させたまま保てる。CI ランナーを UTC の
  ままにすることで、固定が外れた退行は即 CI 赤で検出される。
