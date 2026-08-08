---
generated_by: fable
task_id: p07-08-brand-nav
base-commit: 5cae3a05550e8679b156e88652b0dfa2193d30f1
size: M
---

# P0-7 ブランド即応 + P0-8 サイドバー導線

## 依頼の文字通りの内容と再設計後のゴール

- 文字通り: P0-7「Vexa ロゴ → kabosu.svg」、P0-8「サイドバー導線欠落の解消」。
- 再設計: P0-7 の実体は監査 UI-6/UI-7/UI-11 の3点セット
  (ロゴ+ファビコン / Vexa 外部リンクと全角suffix / カレンダーコールバック英語)。
  「ロゴ差し替え」だけではカボス単一ブランド・日本語限定方針を満たさないため、
  この3点を P0-7 の完了条件とする。P0-8 は UI-10(4ページへの導線追加)。
  クライアント合意済み方針(カボス単一ブランド・日本語限定)自体は reframe しない。
- 2件は同一ファイル群・同一検証手段のため1タスクに統合する。

## 変更対象(この8ファイル以外に触れない)

1. `services/dashboard/src/components/ui/logo.tsx`(変更)
2. `services/dashboard/src/app/layout.tsx`(変更)
3. `services/dashboard/src/app/icon.svg`(新規)
4. `services/dashboard/src/app/favicon.ico`(削除)
5. `services/dashboard/src/components/version-chip.tsx`(変更)
6. `services/dashboard/src/app/auth/google-calendar/callback/page.tsx`(変更)
7. `services/dashboard/src/components/layout/sidebar.tsx`(変更)
8. `services/dashboard/tests/test_brand_and_nav_ui.test.ts`(新規)

## How

### Step 1: UI-6 ロゴ・ファビコンを kabosu.svg へ

- `logo.tsx:48-49`: `defaultLightLogoSrc` / `defaultDarkLogoSrc` の両方を
  `withBasePath("/icons/kabosu.svg")` に変更(単一ブランドなので light/dark 同一。
  同一の場合の分岐は既存の hasConfiguredLogo 側パターンに合わせて簡素化してよいが、
  hydration 安定性のコメント(47行付近)の意図は壊さない)。
- `layout.tsx:22`: フォールバックを `withBasePath("/icons/kabosu.svg")` に変更。
- `public/icons/kabosu.svg` を `src/app/icon.svg` としてバイト同一コピーする
  (Next.js の file-based metadata が config-based より優先されるため、
  `src/app/favicon.ico` を残すと Vexa アイコンが勝ち続ける)。
- `src/app/favicon.ico` を削除する。
- `public/icons/vexadark.svg` / `vexalight.svg` のファイル自体は削除しない
  (デッドアセット整理は Phase 3 UI-9 系の後続に委譲)。

### Step 2: UI-7 Vexa 外部リンク撤去・全角 suffix 除去

- `version-chip.tsx`: ルート要素を `<a href={releaseUrl(...)}>` から非リンクの
  `<span>` に変更(`target` / `rel` / `href` / title 内「リリースノートを開く」を除去。
  hover 系 class は残してよいが cursor は既定に)。
- `VERSION_SUFFIX = "ａ"` を廃止し、表示は `RELEASE.version` そのままにする。
- `releaseUrl` の import を除去。`release-version.ts` 本体は触らない
  (releaseUrl のデッドコード化は Phase 3 で整理)。

### Step 3: UI-11 カレンダーコールバック日本語化

- `auth/google-calendar/callback/page.tsx` の固定英語文言を全て日本語へ。対訳例
  (自然な日本語なら細部の言い回しは実装者裁量、ただし英語固定文言を残さない):
  - Connecting Google Calendar... → Googleカレンダーを接続しています…
  - Finalizing your authorization → 認証を完了しています
  - Calendar Connected → カレンダーを接続しました
  - Redirecting... → 画面を移動しています…
  - Calendar Connection Failed → カレンダー接続に失敗しました
  - Unknown error → 不明なエラー
  - Back to Meetings → 会議一覧へ戻る
  - Loading... / Please wait → 読み込み中… / お待ちください
  - Meeting Transcription → 会議文字起こし
  - authorization was cancelled or denied →
    Googleカレンダーの連携がキャンセルまたは拒否されました
  - authorization failed: ${oauthError} →
    Googleカレンダーの連携に失敗しました: ${oauthError}
  - Missing OAuth callback parameters →
    OAuthコールバックのパラメータが不足しています
  - Failed to complete Google Calendar OAuth →
    Googleカレンダー連携の完了に失敗しました
  - Unexpected error during callback →
    コールバック処理中に予期しないエラーが発生しました
- サーバー由来の `completeData?.error` はそのまま表示でよい(スコープ外)。

### Step 4: UI-10 サイドバー導線追加

- `sidebar.tsx:117-124` の `navigation` 配列に4項目を追記:
  - `{ name: copy.nav.profile, href: "/profile", icon: User }`
  - `{ name: copy.nav.webhooks, href: "/webhooks", icon: Webhook }`
  - `{ name: copy.nav.mcpSetup, href: "/mcp", icon: Plug }`
  - `{ name: copy.nav.settings, href: "/settings", icon: Settings }`
  アイコンは lucide-react から import(User / Webhook / Plug / Settings)。
  ラベルは必ず `copy.nav.*` を使う(文言ハードコード禁止)。
  既存の会議系項目の下に置く。区切り(セパレータや小見出し)は任意・最小限。
- 対象4ページ(/profile /webhooks /mcp /settings)は実在確認済み。ページ側は触らない。

### Step 5: テスト追加

- `tests/test_brand_and_nav_ui.test.ts` を新規作成。既存
  `test_meeting_cards_ui.test.ts` と同じ「readFileSync + expect(source)」方式で:
  1. logo.tsx が `/icons/kabosu.svg` を参照し `vexadark` / `vexalight` を含まない
  2. layout.tsx が `vexadark` を含まず kabosu.svg フォールバックを持つ
  3. `src/app/icon.svg` が存在し `public/icons/kabosu.svg` と内容一致、
     `src/app/favicon.ico` が存在しない
  4. version-chip.tsx が `Vexa-ai` / `href` / 全角「ａ」を含まない
  5. callback page が代表的英語文言(Connecting Google Calendar / Calendar
     Connected / Back to Meetings / Please wait)を含まず、日本語文言を含む
  6. sidebar.tsx が `/profile` `/webhooks` `/mcp` `/settings` の4 href と
     `copy.nav.profile` 等の参照を含む

### Step 6: 検証

- `cd services/dashboard && npm install --no-audit --no-fund && npm test`
- `bash .hw/hooks/pr-ready-gate.sh p07-08-brand-nav`(内部で .hw/verify.sh)
- 詳細は verification-contract.md。

## 制約(必ず守る)

- 上記8ファイル以外を変更しない。特に以下は絶対に触れない:
  - Codex 未コミット変更中の4ファイル: meeting-card.tsx /
    src/app/login/page.tsx / deploy/compose/docker-compose.yml /
    tests/test_meeting_cards_ui.test.ts
    (衝突判定済み: login/page.tsx の dirty はブランド非接触。logo.tsx 修正で
    ログイン画面のロゴも自動的に反映されるため login/page.tsx の編集は不要)
  - st9〜st14 / p05 対象: pyproject.toml / CI workflow / sweeps.py / meetings.py /
    retry.py / src/types/vexa.ts / test_meeting_status_display.test.ts /
    src/app/meetings/[id]/page.tsx / test_meeting_detail_wiring.test.ts /
    deployment-meeting-api.yaml / postgres 統合テスト関連
  - src/components/layout/header.tsx(VersionChip の呼び出し元だが props 互換を
    保てば変更不要)/ src/lib/release-version.ts / src/lib/dashboard-brand.ts /
    src/lib/dashboard-copy.ts(必要ラベルは全て既存)
- sidebar.tsx footer の `https://vexa.ai` フォールバック(hosted モード限定)は
  今回のスコープ外。触らない(後続 UI-9 系で扱う)。

## Why(実装者に渡さない)

- カボス単一ブランド・日本語限定はクライアント合意済み方針(2026-07-03 起票群)。
  Vexa ロゴ・上流 OSS への外部リンク・英語画面は社内パイロットの信頼を直接毀損する
  ため Phase 0(信頼回復)に置かれている。
- favicon.ico を削除して app/icon.svg に置き換えるのは、Next.js App Router で
  file-based metadata(app/favicon.ico)が config-based(metadata.icons)を
  上書きするため。layout.tsx の文字列だけ直すと「コードは直ったのにタブは Vexa の
  まま」という検証不能な状態になる。
- VersionChip をリンクごと消さず span 化に留めるのは、バージョン表示自体は運用時の
  問い合わせ(どのビルドか)に有用で、撤去要求は「上流 OSS への外部リンク」に
  限定されるため。契約は最低合格ライン、作り込まない。
- 導線4項目は既存の翻訳キーとページ実体が揃っており、配列追記が最小手。
  トーストが誘導する到達不能ページ問題(UI-10 の実害)がこれで解消する。
