# Plan — Dashboard UI/UX improvements from メモ.md

## Goal

メモ.md に記録されたカボス ダッシュボードの UI/UX 指摘 12 項目のうち、コード変更で解決できるものを services/dashboard 内で実装する。

## Context

- フロントエンド: services/dashboard (Next.js 16 App Router, React 19, Tailwind, lucide-react, next-themes)
- 文言は src/lib/dashboard-copy.ts の辞書と直書きが混在。既に辞書を使うファイルでは辞書経由を維持する。
- アイコン全滅の静的調査結果: (a) `public/icons/icons8-mcp-96 (1).png` というスペース・括弧入りファイル名を直接参照、(b) `/icons/...` 直書きで `withBasePath()` 未適用の箇所が多数(logo.tsx のみ適用済み)、(c) next/image 最適化(sharp 依存)が standalone 環境で失敗すると PNG アイコンのみ全滅する。lucide の SVG は inline なので壊れない。

## Implementation Scope (3 分割・ファイル排他)

### Work A — 会議一覧 src/app/meetings/page.tsx (+ dashboard-copy.ts の一覧関連キー)

1. 会議名: タイトル(meeting.data.title 等があれば)または参加者名を主見出しに、Meet コードは小さいグレーの補助表示に降格。
2. 参加者列: 省略時に title 属性ホバーで全員表示。
3. 状態ラベル: 「停止済み/完了/失敗」に title ツールチップで意味を補足。時間 "—" は状態に応じた説明を検討。
4. 行クリック: cursor-pointer と hover 背景を明確化し、行全体をクリック可能に。
5. 日時: 相対表示をメイン、年を含む絶対日時を title ホバーに整理。
6. 同ファイル内の `/icons/...` 直書きを `withBasePath()` でラップし、小型 PNG アイコンの `next/image` に `unoptimized` を付与。
7. 一覧空状態の案内文言を確認し、なければ追加。

### Work B — 会議詳細 src/app/meetings/[id]/page.tsx, src/components/recording/audio-player.tsx, src/components/transcript/*

1. 音声準備中: スピナーのみでなく状態文言を明確化し、エラー時に再試行ボタンを表示。
2. 発言ごとの「再生」ボタンを行ホバー時のみ表示(group-hover)。キーボードフォーカス時も表示されるよう focus-within を併用。
3. 同一話者の連続発言をブロックとしてグルーピング(余白・区切りの追加)。
4. 失敗した会議を開いたときの失敗理由表示と再実行/戻る導線(meeting.data 内のエラー情報があれば表示)。
5. 同ファイル群内の `/icons/...` 直書きを `withBasePath()` でラップ + `unoptimized`。

### Work C — 参加モーダル・アイコン資産 src/components/join/join-modal.tsx, public/icons/, src/app/mcp/page.tsx, src/components/mcp/mcp-config-button.tsx, src/app/login/page.tsx, src/components/meetings/meeting-card.tsx

1. 「認証済み参加」トグルにツールチップ/補足説明(近日提供の内容説明)。
2. 「会議 / ブラウザ」タブに用途説明文を追加。
3. `icons8-mcp-96 (1).png` → `icons8-mcp-96.png` にリネームし参照を更新。
4. 上記ファイル群の `/icons/...` 直書きを `withBasePath()` でラップ + 小型 PNG に `unoptimized`。

## Non-Scope

- dashboard-copy.ts 辞書への全面 i18n 移行(直書き文言の一括辞書化)。
- Logo の kabosu.svg 切替(ブランド課題、メモに含まれない)。
- バックエンド(meeting-api 等)の変更。
- 音声プレーヤーの進捗率・待ち時間予測(バックエンドの進捗 API が必要)。

## Execution Notes

- GitNexus MCP は本セッションで利用不可。編集対象はすべて UI リーフコンポーネント(page コンポーネント/表示専用コンポーネント)。各エージェントは編集前に grep で参照元を確認する。
- 実装は sonnet サブエージェント 3 並列(A/B/C はファイル排他)。
- 既存ブランチ codex/issues-4-19-kabosu-harness 上の未コミット変更(calendar-service/meeting-api)には触れない。
