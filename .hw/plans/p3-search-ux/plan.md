---
generated_by: fable
task_id: p3-search-ux
base-commit: 8bfb4476bcbc25b935004e6c555ea3d0425b1470
size: M
---

# 会議一覧の文字起こし検索 UX 改善(advisory #15/#16/#17)

## ゴール

P2-3 Stage2(PR #68)で入った「文字起こしに一致」セクションの3つの残指摘を解消する:
(a) 更新ボタンで再検索されない、(b) 検索結果が会議一覧を押し下げる、
(c) 検索リクエストに中断(AbortController)が無い。

## How

対象: `services/dashboard/src/app/meetings/page.tsx`、
`src/components/meetings/transcript-search-results.tsx`、`src/lib/transcript-search.ts`、
`src/lib/dashboard-copy.ts`(新コピーが必要な場合)、対応するテスト。

1. 着手時実測(Research Freshness): `fetchTranscriptSearch`(lib/transcript-search.ts)の
   署名と、page.tsx の世代ガード実装(stale 応答の破棄方法)を読む。
   **世代ガードによる結果の正しさは既に担保済み**という前提(triage 記録)を確認し、
   崩れていたらプラン層へ差し戻す。
2. (a) 更新ボタン: `handleRefresh`(page.tsx:188)で `applyFilters` に加えて
   `runTranscriptSearch(searchQuery)` を呼ぶ。2文字未満クエリは既存の
   normalize 経由で idle のまま(挙動不変)。
3. (c) AbortController: `fetchTranscriptSearch` に `signal?: AbortSignal` を追加し、
   `runTranscriptSearch` は新検索の開始時と unmount 時に直前の in-flight を abort する。
   abort 起因の reject は error 状態にしない(握り潰して世代ガードに委ねる)。
   既存の世代ガードは削除しない(abort はネットワーク節約であり正しさの根拠にしない)。
4. (b) 押し下げ解消: 検索セクションは現在位置(一覧の上)のまま、既定表示を
   上位3件+「他N件を表示」トグル(展開で現行の全件表示)にする。
   ヒット0件時と idle 時は現行どおり非表示。コピーは日本語で dashboard-copy 経由。
5. テスト(vitest): 「更新で再検索が走る」「新検索開始で前リクエストが abort される」
   「abort は error 表示にならない」「4件以上ヒット時に3件+トグルが描画され、
   展開で全件になる」を追加。PR #68 の既存配線テスト・ハイライトのテストは
   非退行(assert の書き換えは折りたたみ UI 起因の範囲のみ)。

## Why(実装者に渡さない)

- 3件とも advisory 一掃 triage で「Phase 3 へ送る」と明記した残件。(b) の解は
  「セクションを一覧の下へ移す」ではなく件数制限+トグルにする。検索意図がある
  ときヒットは最重要情報であり、下へ移すと (b) の不満が「検索結果が見えない」に
  置き換わるだけのため。3件は 50K 行実測時の平均ヒット表示として十分。
- (c) を握り潰し設計にするのは、AbortError を error 表示へ流すと正常な連続入力で
  エラートーストが出る逆退行を作るため。
