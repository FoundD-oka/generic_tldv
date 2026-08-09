---
generated_by: fable
task_id: p3-vexa-devtool-removal
base-commit: 8bfb4476bcbc25b935004e6c555ea3d0425b1470
size: M
---

# 開発者向け UI と Vexa 由来導線の除去(UI-8 / UI-9)

## ゴール

カボス単一ブランドのダッシュボードから、(a) API/curl を露出する開発者向け UI
(docs-mode 機構・apiView)と、(b) vexa.ai / docs.vexa.ai への導線・既定値を除去する。
`src/app/docs/**` ページ本体は削除しない(リファクタv2 RF-74I へ委譲済み)。

## How

1. 着手時実測(Research Freshness): `src/app/docs/**` から
   `docs-link.tsx` / `docs-mode-store.ts` / `lib/docs/webapp-url.ts` への import 有無を
   grep で確認する。docs ページが import している場合、そのファイルは削除せず
   docs 配下からのみ参照される状態(=RF-74I の削除に同乗)へ縮退させ、契約の
   改訂履歴に記録する。
2. docs-mode 機構の除去:
   - `src/components/docs/docs-link.tsx` と `src/stores/docs-mode-store.ts` を削除。
   - DocsLink を使用する8ファイルから import と使用箇所を除去:
     `app/admin/users/page.tsx`、`app/meetings/page.tsx`、`app/meetings/[id]/page.tsx`、
     `components/join/join-form.tsx`、`components/join/live-session.tsx`、
     `components/transcript/transcript-viewer.tsx`、`components/meetings/status-history.tsx`、
     `components/meetings/bot-status-indicator.tsx`(+着手時 grep で増減を確認)。
   - 使用箇所の除去は「DocsLink 要素と、それだけを包むラッパー」までに留め、
     周辺レイアウト・文言は変更しない。
3. apiView の除去(`app/meetings/[id]/page.tsx`):
   `apiViewOpen` state(145行)とその4使用箇所(1479, 1486-1533 の curl ターミナル
   モック, 1535, 2103)を削除。停止ダイアログの日本語文言・ボタンは不変。
4. vexa.ai 既定値の除去:
   - `sidebar.tsx:213` / `join-form.tsx:225`: `config?.webappUrl || "https://vexa.ai"` の
     フォールバックを除去し、`webappUrl` 未設定時はリンク自体を描画しない。
     isHosted 分岐と BillingStatus は変更しない。
   - `app/api/config/route.ts:72`: 既定 `https://vexa.ai` を空文字へ(未設定は未設定として返す)。
   - `app/robots.ts` / `app/sitemap.ts`: 既定 `https://dashboard.vexa.ai` を
     `http://localhost:3000` へ変更(env `NEXT_PUBLIC_BASE_URL` 優先は不変)。
   - `src/lib/docs/webapp-url.ts`: 手順1の実測で docs 配下以外から参照が無ければ削除。
5. `git grep -n "vexa\.ai" services/dashboard/src -- ':(exclude)services/dashboard/src/app/docs'`
   が0件になることを確認(next.config.ts の docs redirect 先 `docs.vexa.ai` は
   RF-74I の管轄につき対象外。同 grep は src/ のみが対象)。
6. tsc / eslint(両方)/ vitest を確認。既存テストが DocsLink や apiView に依存して
   いれば、該当 assert のみ削除・更新(テストの丸ごと削除は禁止)。

## Why(実装者に渡さない)

- docs-mode/apiView は上流 Vexa の開発者向けデモ機能で、localStorage / クエリの
  opt-in でしか出ないが、「素人っぽさ」評価の温床であり日本語限定方針とも矛盾する。
  機構ごと消すのが再発防止(コピー修正では復活する)。
- docs ページ本体を触らないのは、RF-74I に redirect 検証つきの削除契約が既にあり
  二重管理を避けるため。robots/sitemap の localhost 既定は「誤った他社ドメインを
  出さない」ことが目的で、正しい本番 URL は env で入る前提。
