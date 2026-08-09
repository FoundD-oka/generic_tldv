---
generated_by: fable
task_id: p3-triage
base-commit: 8bfb4476bcbc25b935004e6c555ea3d0425b1470
size: S
---

# Phase 3(磨き込み)の選別記録(実装なし・PR なし・記録のみ)

## ゴール

依頼の文字通りの内容: 計画本体の Phase 3 記載(UI-12〜18、UI-8/9/19/20、FT-10、
FT-2 デッドコード扱い、page.tsx 分割)+ advisory 棄却時に Phase 3 へ送った3件を実施する。

reframe: Phase 3 は「継続」と位置づけられた磨き込みで出荷ブロッカーではない。
全件消化ではなく、**2026-08-10 の現物確認で「まだ残っている」と確定した項目のうち
価値の高いものを選び、残りは理由つきで棄却・委譲する**ことが本当の成果。
監査(2026-08-08、HEAD e4cc7be)以降に Phase 0/1/2 で UI は大きく変わっており、
監査の記載をそのまま実装すると解消済み項目の再実装や誤削除を起こす。

## 現物確認の結果(2026-08-10、HEAD 8bfb447 で全項目実測)

| ID | 監査の記載 | 実測結果 |
|---|---|---|
| UI-8 | 停止ダイアログに curl+APIキーのターミナルモック | **残存**(page.tsx:1486-1533)。ただし `?apiView=1` クエリでのみ表示(既定不可視) |
| UI-9 | docs.vexa.ai / vexa.ai 導線・デッドコード | **残存(場所は移動)**。docs-link.tsx は `src/components/docs/` に存在し8ファイルから使用(localStorage `vexa-docs-mode` 有効時のみ表示)。webapp-url.ts、sidebar/join-form の vexa.ai/account フォールバック(isHosted 時)、robots/sitemap の dashboard.vexa.ai 既定値、api/config の vexa.ai 既定値が残存。docs/ ページ本体は恒久 redirect 済みで実質不可視 |
| UI-12 | EN辞書フォールバック方向が逆+英語残存5件 | **残存(実害縮小)**。`getDashboardCopy` は locale!=="ja" で EN 辞書(dashboard-copy.ts:373)。既定 locale は "ja"(dashboard-brand.ts:18)なので通常は日本語表示。env 誤設定時のみ全英語化。英語残存の全量は未列挙(実装時実測) |
| UI-13 | EmptyState 未使用・mcp/settings にエラー状態なし | **残存**。EmptyState の import は0件。mcp はロード失敗で toast のみ |
| UI-14 | 文字起こし行がキーボード操作不能・「再生」が span | **残存(部分改善)**。「再生」は aria-label/title 付きになったが依然 span。行 div に tabIndex/role/onKeyDown なし |
| UI-15 | aria-label 欠落広範・reduced-motion ゼロ | **残存**。aria-label 20箇所のみ、`prefers-reduced-motion` 0件 |
| UI-16 | 日本語フォント未指定 | **残存**。layout.tsx は Geist latin のみ |
| UI-17 | MutationObserver でタブタイトル全ページ固定上書き | **大半解消済み**。layout.tsx で metadata title 実装済み(計画本体の「metadata化」は達成済み)。DocumentTitle は runtime config の brand 名反映用として残り、全ページ同一タイトルなのは事実 |
| UI-18 | 色トークン混在・text-[9〜11px] 63箇所 | **残存**(実測65箇所) |
| UI-19 | 検証スクリプト13本が dashboard 直下 | **残存**(実測12本: agent-flow/agent-inspect/auth-validate×4/check-pages/deliver-validate.js+.ts/feature-validate/test-agent-panel.mjs/validate.sh)。package.json/CI/tests3/README/Dockerfile からの参照0を実測 |
| UI-20 | meetings/[id]/page.tsx 2452行 | **残存**(実測2502行)。リファクタv2 RF-70/71 が分割を詳細計画済み(RF- コミット0件=v2未着手) |
| FT-2 | decisions-panel.tsx 637行が未 import | **残存(場所は移動)**。`src/components/decisions/decisions-panel.tsx` 637行、import 0件を実測。デッドコード確定 |
| FT-10 | agent-api/telegram-bot NO-SHIP、vexa-agent 実体なし、tracker スタブ | **大半整理済み**。compose では agent-api/telegram-bot ともコメントアウト済み(NO-SHIP 明記)。tracker は dashboard の /tracker ルートとして実装があり `NEXT_PUBLIC_TRACKER_ENABLED=true` でのみサイドバー表示(既定不可視)。vexa-agent は Dockerfile+bin のみだが meeting-api の profiles.yaml から参照あり |
| adv#15 | 検索の AbortController 未配線 | **残存**(meetings/page.tsx に AbortController なし) |
| adv#16 | 検索結果が会議一覧を押し下げる | **残存**(検索セクション:282行目が一覧より上) |
| adv#17 | 更新ボタンで再検索しない | **残存**(handleRefresh:188 は applyFilters のみ) |

監査が誤っていた/古くなっていた点: UI-17 の metadata 化は済み。FT-10 の compose 整理は済み。
tracker は「スタブ」ではなく env ゲート付きの実装。decisions-panel/docs-link はパス移動済み。
UI-8/9 の開発者 UI は opt-in(クエリ/localStorage)で既定不可視のため深刻度は監査時より低い。

## 選別の物差し(この順で判定)

1. **ユーザー可視の品質か**: 日本語限定・カボスブランド・操作可能性(a11y の実利)に直結するものを優先。
2. **削除は参照0が機械確認できるものだけ**: 「未配線に見えるが使われている」を掴まない。
   env/クエリでゲートされた機能(tracker、hostedMode)は「不可視=出荷要件達成」とみなし削除しない。
3. **既存プラン(リファクタv2)と重複しない**: 契約維持型の大規模分割・視覚回帰を要するものは v2 へ委譲。
4. **検証可能性**: grep/tsc/eslint/vitest で機械判定できる粒度に切れないものは棄却。

## 採否一覧

| 対象 | 採否 | 行き先 / 理由 |
|---|---|---|
| FT-2 decisions-panel(637行・参照0) | 採用 | p3-dead-code-removal。純削除 |
| UI-19 検証スクリプト12本(参照0) | 採用 | p3-dead-code-removal。**リファクタv2 RF-75A は9本を .mjs 化して残す計画だが、削除が先行すれば RF-75A は対象消滅で縮退する(lint 負債 no-require-imports 9件も同時に消える)。v2 側プランの改訂が必要な旨を本記録で通知** |
| UI-8 apiView ターミナルモック | 採用 | p3-vexa-devtool-removal。開発者 UI の除去 |
| UI-9 docs-mode/DocsLink/webapp-url/vexa.ai フォールバック | 採用 | p3-vexa-devtool-removal。ただし `src/app/docs/**` ページ本体は RF-74I へ委譲(redirect 済みで不可視・削除契約が v2 に既にある) |
| adv#15/#16/#17 検索 UX 3件 | 採用 | p3-search-ux |
| UI-12 ENフォールバック反転+英語残存 | 採用 | p3-ja-copy-font。EN 辞書自体の削除はしない(最低合格ラインを超える) |
| UI-16 日本語フォント | 採用 | p3-ja-copy-font |
| UI-14 文字起こし行のキーボード操作 | 採用 | p3-a11y-transcript |
| UI-15 aria-label / reduced-motion | 採用 | p3-a11y-transcript(アイコンのみボタンの全量は着手時実測で列挙) |
| UI-13 空状態/ローディング/エラー状態 | 採用 | p3-empty-loading-states(対象を会議一覧・検索結果・mcp・settings に限定) |
| UI-17 残件(ページ別タブタイトル) | 棄却 | metadata 化は済み。残るは「全ページ同一タイトル」のみで、SPA クライアント遷移でのページ別タイトル導入は DocumentTitle 機構の作り直しに対して価値小 |
| UI-18 色トークン統一・text-[9〜11px] 65箇所 | 棄却 | 全域の視覚変更で機能価値ゼロ。視覚回帰の検証手段が本計画になく、リファクタv2 の視覚 baseline 機構(RF-00C)の上で行うのが安全 |
| UI-20 page.tsx 分割 | 委譲 | リファクタv2 RF-70/71 が hook 抽出・1,200行以下・視覚回帰まで詳細計画済み。Phase 3 での実装は重複・衝突(依頼の「委譲するか本計画でやるか」への回答=委譲) |
| FT-10 compose の NO-SHIP 整理 | 解消済み | コメントアウト+NO-SHIP 注記が既に入っている。作業なし |
| FT-10 tracker | 棄却 | env ゲートで既定不可視=「出荷物から見えない」要件は達成済み。実装ありのためスタブ削除は誤り |
| FT-10 agent-api/telegram-bot/vexa-agent のソース削除 | 保留(ユーザー判断) | AI 層は別プロジェクト担当でありソースの帰属未確認。vexa-agent は meeting-api/config/profiles.yaml から参照あり。api-gateway の AGENT_API_URL 転送と dashboard の agent UI(agent-chat/workspace)は配線済みの AI 層受け皿につき触らない |
| src/app/docs/** ページ削除 | 委譲 | RF-74I(redirect 検証つき削除契約が既にある) |

## タスク一覧と着手順(全て dashboard を触るため直列)

| 順 | task-id | 対象 | S/M/L | R軸 | レビュー |
|---|---|---|---|---|---|
| 1 | p3-dead-code-removal | FT-2, UI-19 | S | inline | 機械検証のみ |
| 2 | p3-vexa-devtool-removal | UI-8, UI-9 | M | inline | Fable |
| 3 | p3-search-ux | adv#15/#16/#17 | M | inline | Fable |
| 4 | p3-ja-copy-font | UI-12, UI-16 | M | inline | Fable |
| 5 | p3-a11y-transcript | UI-14, UI-15 | M | inline | Fable |
| 6 | p3-empty-loading-states | UI-13 | M | inline | Fable |

直列の根拠: meetings/[id]/page.tsx(2と5)、meetings/page.tsx(3と6)、
dashboard-copy.ts(3・4・6)が重なる。1を先頭に置くのは、削除で後続タスクの
diff と lint 負債(baseline errors 61/warnings 87)を先に減らすため。
Codex 並行セッションがあるため、各タスクはコミット前に帰属確認を行うこと。

## Why(実装者に渡さない)

- 本タスク自体はコード変更なし。p3-dead-code-removal の最初のコミットに
  `plan(hw):` として同乗させる(untracked プランは clean-tree ゲートを塞ぐ)。
- FT-10 の残件(3サービスのソース削除)と UI-18/UI-20 の扱いはユーザーへ報告済み。
  採用が変わったらこの記録を改訂してからタスクを起こす。
