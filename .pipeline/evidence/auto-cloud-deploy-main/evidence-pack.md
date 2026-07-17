# 自動デプロイ検証証跡

## 結果

- mainのローカルHEADとGitHubの `origin/main` は `3d1f022b1739cd8c6d5f26fb467946ac6b524bb7` で一致した。
- GitHub Actions `Deploy dashboard to GCP` はrun `29546909824` で成功した。
- Workload Identity認証に成功し、長期サービスアカウント鍵は使用していない。
- Cloud Build `78956af1-350a-4124-af18-977fc92bc789` は成功し、`_DEPLOY_SHA` とイメージタグがmain HEADに一致した。
- Cloud Run revision `kabosu-dashboard-00027-b8l` が100%のトラフィックを受けている。
- 公開URLの `/` と `/api/config` はHTTP 200を返し、ブランド名は「カボス」だった。
- 既存の環境変数名22件とSecret参照3件が維持された。

## GCP権限境界

- Workload Identity providerは既存リポジトリ条件を保持しつつ、`FoundD-oka/generic_tldv` の `refs/heads/main` のみ追加した。
- `github-deploy` サービスアカウントへのWorkload Identity User付与も同リポジトリに限定した。
- Cloud Build既定サービスアカウントにはCloud Run Developerと、実行サービスアカウントに対するService Account Userを付与した。
- 検証で不採用となったCloud Build Webhookトリガー、Webhook Secret、APIキー、GitHub hookは削除した。
