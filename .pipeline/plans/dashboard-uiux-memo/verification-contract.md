# Verification Contract — dashboard-uiux-memo

## Must Pass (deterministic)

1. `cd services/dashboard && npm run build` が成功する(VEXA_API_URL ダミー指定可)。
2. `cd services/dashboard && npx vitest run` が既存テスト含め全件成功する。
3. `grep -rn "icons8-mcp-96 (1)" services/dashboard/src` が 0 件。
4. `public/icons/icons8-mcp-96.png` が存在する。
5. `grep -rn '"/icons/' services/dashboard/src` の残存箇所がすべて `withBasePath` ラップ済みであること(生文字列 src 指定 0 件)。

## Must Verify (inspection)

6. /meetings 一覧: 主見出しが会議コード以外(タイトル/参加者)になり、会議コードが補助表示。
7. 一覧行に cursor-pointer + hover 背景がある。
8. 状態ラベル・参加者省略・日時に title ツールチップがある。
9. transcript-segment の再生ボタンが hover/focus 時のみ表示。
10. audio-player エラー時に再試行導線が表示される(コード上の分岐確認)。
11. join-modal: 認証済み参加の説明、会議/ブラウザタブの説明が表示される。

## Best Effort

12. ローカルで dev サーバを起動しスクリーンショットで目視確認(バックエンド起動が必要なため環境依存)。

## Evidence

- .pipeline/evidence/dashboard-uiux-memo/test-results.md にビルド・テスト・grep 結果を記録。
