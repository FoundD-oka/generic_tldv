# 調査概要

## 問い

1. `main` 更新だけで既存の `kabosu-dashboard` を安全に更新できるか。
2. 既存のCloud Run環境変数とSecret参照を壊さずに更新できるか。
3. Cloud Build実行主体へ必要な最小権限は何か。

## 仮説と確認結果

- 仮説A: 既存Cloud Build定義へ明示的なDocker pushと `gcloud run services update --image` を追加すれば、同じ定義でbuildからrevision切替まで完結する。ローカルのgcloudヘルプと現行構成確認で支持。
- 仮説B: `gcloud run services update` へイメージだけ指定すれば、既存の環境変数、Secret参照、認証設定を保持できる。現行サービス設定を更新前後で比較して検証する。
- 仮説C: Cloud Build既定サービスアカウントにはCloud Run Developerと、Cloud Run実行サービスアカウントに対するService Account Userが必要。現行IAM確認で未付与と判明。
- 仮説D: GitHub App接続は対話OAuthが必要で自動設定できない。一方、公開リポジトリに対する署名Secret付きWebhookトリガーなら、GCP側で認証しつつ `body.ref` を `refs/heads/main` に限定できる。公式Cloud Build資料とCLIで支持。

## 反証確認

- Artifact RegistryへpushするだけではCloud Runのrevisionは変わらないため不十分。
- Cloud Runサービスを作り直す方式は既存環境変数やSecret参照を失うリスクがあるため不採用。
- 全ブランチを対象にすると未承認変更が本番へ出るため不採用。

## 信頼度

高。GCPの現行サービス、実行アカウント、既存Cloud Build履歴を実環境で確認済み。初回Webhookトリガー実行はpushで確定する。
