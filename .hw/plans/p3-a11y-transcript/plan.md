---
generated_by: fable
task_id: p3-a11y-transcript
base-commit: 8bfb4476bcbc25b935004e6c555ea3d0425b1470
size: M
---

# a11y 底上げ: 文字起こし行のキーボード操作・aria-label・reduced-motion(UI-14 / UI-15)

## ゴール

(a) 文字起こし行の「この時刻から再生」をキーボード・タッチで操作可能にする、
(b) アイコンのみボタンの aria-label 欠落を全量解消する、
(c) `prefers-reduced-motion` でアニメーションを無効化する。

## How

1. UI-14(`src/components/transcript/transcript-segment.tsx`):
   - 「再生」表示(264-281行付近)の `<span>` を `<button type="button">` へ変更し、
     click で `onClickSegment` を発火(`stopPropagation` で行 div の onClick と
     二重発火させない)。既存の aria-label「この時刻から再生」・title・className は維持。
   - 行全体の div には role/tabIndex を**付けない**(行内に編集ボタン・チェックボックスが
     あり、入れ子 interactive を作らないため。キーボード到達は button で達成)。
   - タッチ/キーボード可視性: 既存 `group-focus-within:opacity-100` に加え、
     coarse pointer で常時表示(Tailwind v4 の `pointer-coarse:` variant。
     着手時にプロジェクトの Tailwind バージョンで利用可否を実測し、不可なら
     `@media (pointer: coarse)` を globals.css へ追加)。
   - showSpeakerHeader が false の行(タイムスタンプのみの行)は変更しない。
2. UI-15a(aria-label): 着手時に `src/app`(`docs/**` 除く)と `src/components` の
   アイコンのみボタン(`size="icon"` の Button、テキスト子要素を持たない button/クリック
   要素)を grep で全量列挙し、**一覧を契約の改訂履歴へ記入してから**日本語の
   aria-label を付与する。既に aria-label があるもの(実測20箇所)は変更しない。
3. UI-15b(reduced-motion): `globals.css` へ
   `@media (prefers-reduced-motion: reduce)` ブロックを追加し、`animate-*`
   ユーティリティ(173-188行の自前定義)と transition の実質無効化
   (duration を 0.01ms 級へ)を行う。`tw-animate-css` の import 済みアニメにも
   効くことを着手時に実測(効かなければ該当クラスを個別に上書き)。
4. テスト(vitest): 「再生 button が Enter/Space(click イベント)で
   onClickSegment を1回だけ発火する」「行 div クリックの既存挙動が不変」
   「編集ボタン・チェックボックス操作が再生を誤発火しない」。
5. tsc / eslint(両方)/ vitest 緑。DOM 構造変更は transcript-segment.tsx 内に限定。

## Why(実装者に渡さない)

- span→button 化はセマンティクス修正の最小手で、視覚は className 維持で不変。
  行 div へ role="button" を足す案は入れ子 interactive(編集・チェックボックス)で
  スクリーンリーダーの操作モデルを壊すため退けた。
- aria-label の「全量」は監査の46箇所という概数を信用せず実測列挙で確定する。
  契約が列挙表に束縛されることで「大半直した」という自己申告を排除する。
