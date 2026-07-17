# 現状

- `main` はGitHubへpushされても自動デプロイされない。
- `deploy/gcp/cloudbuild-dashboard.yaml` はDashboardイメージのbuild/pushまでを担うが、Cloud Run更新処理を持たない。
- 本番DashboardはGCPプロジェクト `pm-qe-mgmt-20260624`、リージョン `asia-northeast1`、Cloud Runサービス `kabosu-dashboard` で稼働している。
- Cloud Buildの既定サービスアカウントはArtifact Registryへのpush権限を持つが、Cloud Run更新権限を持たない。
- Cloud BuildのGitHub App接続は対話OAuthが必要。署名Webhook受信APIは公式の最小構成でもHTTP 400を返すため、既存Workload Identityを `FoundD-oka/generic_tldv` のmainへ限定して拡張する。
