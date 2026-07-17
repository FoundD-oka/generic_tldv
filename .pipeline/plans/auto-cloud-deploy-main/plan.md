# 実装計画

1. Cloud Build定義へ明示的なイメージpushとCloud Run更新を追加する。
2. Cloud Build既定サービスアカウントへCloud Run更新の最小権限を付与する。
3. GitHub `FoundD-oka/generic_tldv` の `main` push限定WebhookトリガーをGCP側に作成する。
4. 設定変更をmainへコミットしてpushし、自動ビルドを発火する。
5. Build成功、Cloud Run新revisionの100%配信、環境変数・Secret参照保持、公開URL応答を確認する。
