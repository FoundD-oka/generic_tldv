# Verification Contract — p07-08-brand-nav

前提: base = 5cae3a05550e8679b156e88652b0dfa2193d30f1。
dashboard テストのベースライン実測 = 28 files / 199 tests 全 pass(base 時点)。
p05-detail-wiring 等のマージで母数が増えた場合は「本タスク以外由来の既存テストが
全 pass +本タスク新規テストが全 pass」と読み替える(数の固定値比較はしない)。
検証は commit 済み clean tree に対して行う。

## Acceptance Tests

| ID | Requirement(最低合格ライン) | Method | Evidence |
|---|---|---|---|
| AT-001 | UI-6a: 既定ロゴが kabosu.svg。`grep -rn "vexadark\|vexalight" services/dashboard/src` の出力が空 | unit + command | 新規テスト pass + grep 出力空のログ |
| AT-002 | UI-6b: `services/dashboard/src/app/icon.svg` が存在し `cmp services/dashboard/src/app/icon.svg services/dashboard/public/icons/kabosu.svg` が一致。`test ! -f services/dashboard/src/app/favicon.ico` が真 | command | cmp / test コマンドの終了コード 0 |
| AT-003 | UI-7: version-chip.tsx に `Vexa-ai` 文字列・`href=` 属性・全角「ａ」が存在しない。`grep -n "Vexa-ai\|href\|ａ" services/dashboard/src/components/version-chip.tsx` が空 | unit + command | 新規テスト pass + grep 出力空のログ |
| AT-004 | UI-11: callback page に固定英語 UI 文言(Connecting Google Calendar / Finalizing / Calendar Connected / Calendar Connection Failed / Back to Meetings / Please wait / Redirecting / Meeting Transcription)が存在せず、日本語文言が存在する | unit | 新規テスト pass |
| AT-005 | UI-10: sidebar.tsx の navigation に `/profile` `/webhooks` `/mcp` `/settings` の4 href があり、ラベルは copy.nav.profile / copy.nav.webhooks / copy.nav.mcpSetup / copy.nav.settings を参照する(文言ハードコードなし) | unit | 新規テスト pass |
| AT-006 | 新規テストファイル `tests/test_brand_and_nav_ui.test.ts` が追加され全ケース pass | unit | `npm test` 出力(テストファイル数が base 比 +1) |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 既存 dashboard テスト全 pass(base 実測 28 files / 199 tests。母数変動時は前文の読み替え規則を適用) | `cd services/dashboard && npm install --no-audit --no-fund && npm test` | vitest サマリ(failed 0) |
| FP-002 | 変更ファイルが許可リスト8件(+ `.hw/plans/p07-08-brand-nav/` 配下)のみ(plan.md「変更対象」参照)。特に Codex dirty 4ファイル・st9〜st14/p05 保護ファイルに差分がない | `git diff --name-only 5cae3a0..HEAD` | 出力が許可リストの部分集合であるログ |
| FP-003 | hw 静的検証に新規回帰なし | `bash .hw/verify.sh` | 終了コード 0(または baseline 既知失敗のみ) |
| FP-004 | Logo コンポーネントの props 互換維持(header.tsx / login/page.tsx / callback page からの呼び出しシグネチャ不変)。VersionChip の props(variant/look/className/brandName)不変 | source check | logo.tsx / version-chip.tsx の interface 差分なし(diff で確認) |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | env でカスタムロゴ(DASHBOARD_BRAND_LOGO_*)を指定した場合の分岐(hasConfiguredLogo 側)を壊さない | source check | logo.tsx の configured 分岐が残存していることの diff 確認 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: yes
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-001 | Next.js App Router で file-based metadata(app/favicon.ico / app/icon.svg)が config-based metadata.icons を上書きするという前提(プランの favicon 削除方式の根拠) | 実装時に `npm run dev` または build 後の HTML `<link rel="icon">` を目視で1回確認。覆っていた場合(config が勝つ場合)は favicon.ico 削除のみで足りるので方式を縮小してよい | 確認メモを PR 本文に1行 |
