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

(着手時のベースライン実測をここへ追記して commit すること)
