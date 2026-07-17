# 選択肢比較

| 方式 | 自動化 | 既存設定保持 | 運用負荷 | 判定 |
|---|---:|---:|---:|---|
| GitHub Actions + GCP Workload Identity + Cloud Build | 高 | 高 | 低 | 採用 |
| Cloud Build署名Webhookトリガー | 高 | 高 | 低 | Webhook受信APIがHTTP 400のため不採用 |
| Cloud Build GitHub Appトリガー | 高 | 高 | 低 | 対話OAuthが必要なため今回は不採用 |
| Artifact Registryへpushのみ | 中 | 高 | 高 | Cloud Runが更新されないため不採用 |
| Cloud Runサービス再作成 | 高 | 低 | 中 | 環境変数・Secret参照を壊すため不採用 |
