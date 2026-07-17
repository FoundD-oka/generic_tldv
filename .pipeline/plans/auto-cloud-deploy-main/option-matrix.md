# 選択肢比較

| 方式 | 自動化 | 既存設定保持 | 運用負荷 | 判定 |
|---|---:|---:|---:|---|
| Cloud Build署名Webhookトリガー + Cloud Runイメージ更新 | 高 | 高 | 低 | 採用 |
| Cloud Build GitHub Appトリガー | 高 | 高 | 低 | 対話OAuthが必要なため今回は不採用 |
| GitHub ActionsからGCPへデプロイ | 高 | 高 | 中 | GCP側設定という依頼から不採用 |
| Artifact Registryへpushのみ | 中 | 高 | 高 | Cloud Runが更新されないため不採用 |
| Cloud Runサービス再作成 | 高 | 低 | 中 | 環境変数・Secret参照を壊すため不採用 |
