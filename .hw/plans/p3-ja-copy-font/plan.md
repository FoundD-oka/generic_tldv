---
generated_by: fable
task_id: p3-ja-copy-font
base-commit: 8bfb4476bcbc25b935004e6c555ea3d0425b1470
size: M
---

# 日本語限定方針の仕上げ: 辞書フォールバック反転・英語残存・日本語フォント(UI-12 / UI-16)

## ゴール

env 誤設定でも UI が英語化しないこと(フォールバックを日本語へ反転)、ユーザー可視の
英語残存文字列の日本語化、日本語フォントの明示指定。カボス単一ブランド・日本語限定方針。

## How

コミットは2つに分ける(1: 辞書+コピー、2: フォント。視覚全域に効くフォントを独立 revert
可能にするため)。

1. フォールバック反転: `src/lib/dashboard-copy.ts:373` の `getDashboardCopy` を
   「`locale === "en"` のときだけ EN、それ以外(未知含む)は JA」へ反転する。
   EN 辞書自体は削除しない。unit test: 未知 locale / undefined / null → JA 辞書。
2. 英語残存の棚卸し(着手時実測): `src/app`(`docs/**` 除く)と `src/components` の
   JSX 内テキスト・toast・placeholder・aria-label を対象に、ユーザー可視の英語文字列を
   grep で列挙し、**一覧を契約の改訂履歴へ記入してから**日本語化する。
   監査は「5件」と言うが正本は実測。対象外: ログ・コメント・コード識別子・
   API パス表記(`GET /meetings` 等の技術表記)・`src/app/docs/**`。
   ハードコード和訳ではなく可能な範囲で dashboard-copy(JA 辞書)へ寄せる。
3. 日本語フォント(`src/app/layout.tsx:11-19`、`globals.css`):
   - 第一候補: `next/font/google` の Noto Sans JP(`subsets` 指定なしの
     variable font)を追加し、`--font-noto-sans-jp` を Geist の後段フォールバックとして
     font-family チェーンへ組み込む(latin は Geist のまま)。
   - 着手時実測(Research Freshness): CI(GitHub Actions)と Docker ビルドが
     next/font の Google Fonts 取得を伴って成功するかを確認する。ビルドが
     ネットワーク遮断等で失敗する場合は第二候補へ切替: フォント取得を追加せず
     font-family へ明示システムスタック
     (`"Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic UI", Meiryo`)
     を追記する(監査の指摘「未指定で OS 依存」は指定順の明示で解消)。
     採った側と根拠を改訂履歴へ記録する。
4. tsc / eslint(両方)/ vitest 緑。フォント変更で既存のスナップショット系テストが
   落ちる場合は、フォント名差分のみの更新に限って許容(改訂履歴へ列挙)。

## Why(実装者に渡さない)

- フォールバック反転は「日本語限定」の機械的保証。既定 locale が "ja" のため通常系は
  不変で、変わるのは誤設定時の挙動のみ。EN 辞書を消さないのは diff 最小化と
  上流追従の余地のため(契約は最低合格ライン)。
- フォント2段構えは、next/font がビルド時に fonts.googleapis.com へアクセスする
  制約による。CI/Docker のネットワーク事情は実測でしか分からず、推測実装を禁じた
  ハーネス方針(実測3回逆転の経緯)に従う。
