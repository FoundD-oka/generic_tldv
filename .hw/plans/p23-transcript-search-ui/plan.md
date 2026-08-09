---
generated_by: fable
task_id: p23-transcript-search-ui
base-commit: 7c3c6555f63950eaf5a496da07b6a84da62fd0e8
size: M
parent: p23-transcript-fulltext-search
---

# P2-3 Stage 2: 横断全文検索のダッシュボード統合

親プラン `.hw/plans/p23-transcript-fulltext-search/plan.md` の Stage 2 を独立レビュー
可能な形で切り出したもの。Why・スコープの線引きは親プランを正とする。

**base-commit は暫定値**(親プラン作成時の HEAD)。Stage 1
(`p23-transcript-search-api`)の PR マージ後、着手時にコーディネータが
`git rev-parse HEAD` で frontmatter と `base-commit` ファイルの両方を更新すること。
Fable レビューはその commit から実装 HEAD までを対象にする。
着手前提: Stage 1 の `GET /transcripts/search` が main にマージ済みであること。

## ゴール

- 会議一覧ページの検索が、タイトルだけでなく文字起こし本文の横断ヒットを
  「文字起こしに一致」セクションとして表示し、会議詳細へ到達できる(FT-3 の
  ユーザー体験側の解消)。
- 新設文言はすべて日本語(カボス単一ブランド・日本語限定方針)。
- 既存のタイトル検索・絞り込み・一覧表示は一切変えない。

## How

変更ファイル: `services/dashboard/` 配下のみ(契約 FP-313。`.hw/plans/` 配下は
規約準拠のため別途許容)。想定: `src/app/meetings/page.tsx`、
`src/lib/dashboard-copy.ts`、新規コンポーネント(例:
`src/components/meetings/transcript-search-results.tsx`)、新規 lib ユーティリティ、
`tests/`(vitest)。

### 1. 検索の配線

- 会議一覧ページの既存検索ボックス(300ms デバウンス済み、`applyFilters` 経由)を
  拡張し、strip 後2文字以上の入力で既存タイトル検索(`/bots?search=`)に**加えて**
  `/api/vexa/transcripts/search?q=...` を呼ぶ(既存の汎用プロキシ経由。
  プロキシが新パスを転送することは実装時に現物確認 → 契約 RF-311)。
- 2文字未満・空のときは transcript 検索を発行せずセクションを出さない。
- 既存の一覧絞り込み挙動(タイトル検索・status/platform フィルタ)は変えない。

### 2. 結果表示

- 「文字起こしに一致」セクション: 会議タイトル(既存の詳細ページ導線へリンク)+
  マッチ発言スニペット(API が返す最大3件)+ 総マッチ数(match_count)。
- クエリ文字列のリテラル一致部分を `<mark>` で強調。強調ユーティリティは
  正規表現メタ文字を含むクエリでも安全(エスケープ)に実装する。
- ローディング・0件・エラーの各状態を日本語コピーで表示。文言はすべて
  `dashboard-copy.ts` 経由で追加(英語文言の新設は不可)。

### 3. テスト(vitest)

- レスポンス整形・スニペット強調ユーティリティのユニットテスト
  (正規表現メタ文字ケースを含む)。
- 検索呼び出しロジック(2文字閾値・デバウンス連動)のテスト。
- lint はベースライン比較方式(`lint-baseline.json`)を悪化させない。

### 4. 手動確認(ゲート証跡)

実スタックで日本語クエリを1回検索し、セクション表示のスクリーンショットを
`.hw/gates/p23-transcript-search-ui/` へ保存する(AT-311 の証跡)。
