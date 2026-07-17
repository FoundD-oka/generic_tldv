# Verification Contract: auto-cloud-deploy-main

- size: M
- external consultation required: no
- external consultation provider: claude-fable-cli

## Required Commands
- GitHub Actions `Deploy dashboard to GCP` がmain pushで成功する
- 対応するCloud Buildが成功し、`_DEPLOY_SHA` がmain HEADと一致する
- `gcloud run services describe kabosu-dashboard --region=asia-northeast1`
- 公開URLの `/` と `/api/config` がHTTP 200
- デプロイ前後でCloud Runの環境変数名とSecret参照が維持される

## Evidence Rule
- Evidence Manifest must have no missing_evidence entries.
