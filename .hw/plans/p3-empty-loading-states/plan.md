---
generated_by: fable
task_id: p3-empty-loading-states
base-commit: 8bfb4476bcbc25b935004e6c555ea3d0425b1470
size: M
---

# 空状態・ローディング・エラー状態の統一(UI-13、対象4画面に限定)

## ゴール

未使用の `EmptyState` コンポーネントを正とし、**対象4画面に限って**空状態を統一、
mcp / settings にエラー状態と再試行導線を追加する。全画面の統一はしない
(Phase 3 は磨き込みであり、対象を価値の高い導線に絞る)。

## How

対象4画面: 会議一覧(`app/meetings/page.tsx` の空状態)、文字起こし検索結果
(`components/meetings/transcript-search-results.tsx` の 0件/エラー表示)、
`app/mcp/page.tsx`、`app/settings/page.tsx`。

1. 着手時実測: `components/ui/empty-state.tsx` の props 契約と、対象4画面の
   現行の空/ローディング/エラー表現を読み、置換マップ(現行マークアップ→EmptyState)
   を契約の改訂履歴へ記入する。p3-search-ux 適用後の検索セクション構造を前提にする
   (本タスクは p3-search-ux の後に着手)。
2. 会議一覧: 「条件に一致する会議がない」「まだ会議がない」の2状態を EmptyState へ
   置換。既存の導線(参加ボタン等)は EmptyState の action として維持。
3. 検索結果: エラー時の表現を EmptyState 系へ寄せ、再試行(再検索)導線を付ける。
4. mcp: config 取得失敗時に toast だけでなく画面内エラー状態(EmptyState +
   再読み込みボタン)を描画する。ローディング中はスケルトンまたはスピナー1種に統一。
5. settings: 同様にエラー状態と再試行導線を追加。
6. コピーはすべて日本語で dashboard-copy(JA 辞書)経由。
7. テスト(vitest): 「mcp が fetch 失敗時にエラー状態と再試行ボタンを描画し、
   再試行で再 fetch する」「会議一覧の2空状態が EmptyState で描画される」
   「検索エラー状態から再試行できる」。
8. tsc / eslint(両方)/ vitest 緑。

## Why(実装者に渡さない)

- 「3系統併存」の全面統一は視覚回帰リスクと工数が磨き込みの価値に見合わないため、
  ユーザーが毎日通る会議一覧・検索と、エラー状態が存在しない mcp/settings に絞った。
  EmptyState は監査時から存在するのに未使用で、「作ったのに使っていない」の解消は
  P0-5 と同じ思想(配線が最小コストで最大効果)。
