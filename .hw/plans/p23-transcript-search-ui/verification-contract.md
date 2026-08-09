# Verification Contract — p23-transcript-search-ui(Stage 2)

親契約 `.hw/plans/p23-transcript-fulltext-search/verification-contract.md` の
Stage 2 の表を、合格ライン不変のまま抽出したもの(項目 ID は親と同一)。
対象: `base-commit..HEAD` の差分。**base-commit は Stage 1 マージ後に
コーディネータが着手時 HEAD へ更新する**(plan.md frontmatter と `base-commit`
ファイルの両方)。検証は commit 済み clean tree に対して実行する。
Stage 1(API)の項目は本契約に含まない(`p23-transcript-search-api` で判定済み)。

判定主体の凡例: **Fable** = base-commit..HEAD の差分と契約本文のみで判定
(gitignore 領域 `.hw/gates/` には到達できない。実測値は契約本文の改訂履歴に記入させる)/
**CI** = GitHub Actions(最終権威)/ **ゲート** = pr-ready-gate 実行者が
`.hw/gates/p23-transcript-search-ui/` の証跡を確認。

## 監査 ID → 検証項目の対応

| 監査 ID | 要求 | 検証先 |
|---|---|---|
| FT-3 | 会議横断の全文検索のユーザー体験側(ダッシュボードからの利用) | AT-311〜313 |
| UI-11 系方針 | 日本語限定(新設文言に英語を入れない) | AT-313 |

## ベースライン取得手順(転記値の使用禁止)

着手時 base-commit の clean tree で
`cd services/dashboard && npm install --no-audit --no-fund && npm test` を実行し、
サマリ全文を `.hw/gates/p23-transcript-search-ui/vitest-baseline-<commit>.txt` へ保存。
lint ベースライン比較(`lint-baseline.json`、#63 の仕組み)の結果も併せて保存し、
数値を本契約の改訂履歴へ記入して commit する。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-311 | 会議一覧ページの検索入力(strip後2文字以上、既存300msデバウンス)で `/api/vexa/transcripts/search` が呼ばれ、「文字起こしに一致」セクションに会議タイトル(詳細への既存導線)+スニペット(最大3件)+総マッチ数が表示される。2文字未満では発行しない | vitest(整形・呼び出しロジック)+ 手動確認1回 | CI + ゲート(スクリーンショット) | テストログ + 証跡 |
| AT-312 | マッチ部分がリテラル一致で `<mark>` 強調される(正規表現メタ文字を含むクエリでも安全) | 強調ユーティリティのユニットテスト(メタ文字ケース含む) | CI + Fable | テストログ |
| AT-313 | ローディング・0件・エラー状態が日本語コピーで表示される。新設文言はすべて日本語(dashboard-copy.ts 経由) | vitest + diff(新設文字列レビュー) | Fable + CI | diff |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-311 | 既存のタイトル検索・status/platform フィルタ・一覧表示の挙動に差分なし(検索ボックスは共用だが一覧の絞り込み結果は従来どおり) | vitest 既存テスト + diff | CI + Fable | テストログ |
| FP-312 | dashboard テスト非退行 + lint ベースライン比較(lint-baseline.json)を悪化させない(eslint exit≥2 は即 fail) | CI(#63 の仕組み) | CI | CI ログ |
| FP-313 | 変更ファイルが `services/dashboard/` 配下のみ。判定コマンドは `git diff --name-only <base>..HEAD -- ':(exclude).hw/plans'`(`.hw/plans/` は許容。それ以外の `.hw/`(hooks / rules / verify.sh 等)は除外しない) | diff | Fable | diff |

## Non-Functional Checks

なし(表示上限は API 側 NFT-302 で有界。UI 固有の NFT は設けない)。

## KPI Checks

なし。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p23-transcript-search-ui/` へ)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no(親プランのリサーチ節に記録済み)
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks(実測結果は本契約の改訂履歴に記入すること)

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-311 | dashboard の汎用プロキシ(`/api/vexa/[...path]` 相当)が新パス `/transcripts/search` をクエリ文字列・認証情報付きで api-gateway へ転送すること(プラン時は前提扱いで現物未確認) | 実装時にプロキシ実装を読み、実スタックで1回実測(AT-311 の手動確認と兼ねてよい) | 契約改訂履歴に確認結果 + `.hw/gates/` に証跡 |

## 改訂履歴

### 分割(2026-08-09、planner 実施)

親契約 `p23-transcript-fulltext-search` から Stage 2 の項目(AT-311〜313 /
FP-311〜313)を項目 ID・合格ライン不変のまま抽出。追加は RF-311(dashboard
プロキシの新パス転送の現物確認。プラン時未検証の前提を Research Freshness へ
明示化したもので、合格ラインの緩和ではない)のみ。

### 着手時ベースラインと実測結果(2026-08-09、実装者実施)

**base-commit 更新**: 暫定値 `7c3c6555f63950eaf5a496da07b6a84da62fd0e8` を
着手時 HEAD の実測値 `7febd39e78e0b468fb5e1fa707f2f88d4a129f41`(Stage 1
マージ後の main)へ更新。plan.md frontmatter と `base-commit` ファイルの両方。

**ベースライン実測**(base-commit の clean tree、`cd services/dashboard &&
npm install --no-audit --no-fund && npm test`。Node v22.23.0 / eslint v9.39.1)

- vitest: **32 files / 243 tests passed**(証跡
  `.hw/gates/p23-transcript-search-ui/vitest-baseline-7febd39.txt`)。
- lint ベースライン比較: `npx eslint . --format json` の実測が
  **errors=61 / warnings=87 / fatalErrors=0**。`lint-baseline.json` の値
  (errors 61 / warnings 87)と一致し `lint-ratchet: OK`(証跡
  `lint-ratchet-baseline-7febd39.txt`)。

**実装後の実測**(同コマンド、同環境)

- vitest: **33 files / 263 tests passed**(既存 243 は全て維持し、新規
  `tests/test_transcript_search_ui.test.ts` の 20 件を追加。証跡
  `vitest-after.txt`)。FP-311(既存タイトル検索・status/platform
  フィルタ・一覧表示の非退行)/ FP-312(テスト非退行)に対応。
- lint: **errors=61 / warnings=87 / fatalErrors=0** で不変、
  `lint-ratchet: OK (新規増加なし)`。`lint-baseline.json` は変更していない
  (上げていない)。証跡 `lint-ratchet-after.txt`。
- FP-313: `git diff --name-only 7febd39..HEAD -- ':(exclude).hw/plans'` は
  `services/dashboard/` 配下の5ファイルのみ(page.tsx / dashboard-copy.ts /
  transcript-search.ts / transcript-search-results.tsx / テスト1本)。

**Research Freshness 実測結果**

- **RF-311(PASS)**: dashboard の汎用プロキシ
  `src/app/api/vexa/[...path]/route.ts` は、`meetings` の GET 特例を除く全パスを
  `${VEXA_API_URL}/${path.join("/")}` へそのまま転送し、`request.nextUrl.searchParams`
  を(`proxy` パラメータのみ除去して)クエリ文字列として引き継ぎ、認証クッキー
  (`vexa-token`)の値を `X-API-Key` ヘッダに載せる。新パス用の分岐追加は不要。
  実スタックで実測(現行 main のソースからビルドした meeting-api / api-gateway を
  同一 docker ネットワークへ一時起動し、dashboard を `VEXA_API_URL` 経由で接続):
  - `GET /api/vexa/transcripts/search?q=エージェント&limit=2` +
    認証クッキー → **200**、`{"query":"エージェント","results":[...]}` を取得
    (`limit` も転送されている)。
  - 認証クッキーなし → プロキシが **401**(上流へ出さない)。
  - `q=あ`(1文字)→ meeting-api の 422
    `{"detail":"検索キーワードは2文字以上で指定してください"}` が透過。
    クエリ文字列がそのまま届いている証拠。
  - 証跡: `.hw/gates/p23-transcript-search-ui/rf-311-proxy-live.txt`。
- **AT-311 手動確認(PASS)**: 同スタックの会議一覧で日本語クエリ
  「エージェント」を1回検索し、「文字起こしに一致」セクションに会議タイトル
  (`/meetings/{id}` への既存導線)+ 会議あたり最大3件のスニペット + 総マッチ数
  (「5件一致」等)が表示されることを確認。`<mark>` 強調が18箇所。
  証跡: `at-311-transcript-search-section.png`。
- **AT-313 手動確認(PASS)**: 0件クエリで
  「文字起こしに一致する会議はありません」を表示。証跡: `at-313-empty-state.png`。
