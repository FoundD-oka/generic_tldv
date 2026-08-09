---
generated_by: fable
task_id: p2x-advisory-cleanup-ci-triggers
base-commit: 0fe5ea5a4473312412d4ab5c5f48c605668d0949
size: S
---

# CI トリガーの盲点を閉じる(advisory 5 / P1-4 由来2件)

## How

1. `.github/workflows/` の `test-admin-api.yml` / `test-api-gateway.yml` /
   `test-meeting-api.yml` / `test-packages.yml`: **`pull_request.paths` に自
   workflow ファイルのパスを追加**(push 側は既に含まれている。触らない)。
2. `verify-compose-config.yml`: `push.branches` に `hw/**` を追加。
3. `deploy-dashboard-gcp.yml`: `push` に `paths` を追加 —
   `services/dashboard/**`、`packages/transcript-rendering/**`(dashboard は
   `file:../../packages/transcript-rendering` 依存)、
   `.github/workflows/deploy-dashboard-gcp.yml` の3つ。`branches: [main]`・
   `concurrency`・jobs は一切変更しない。
4. 差分はトリガー節(`on:`)のみ。jobs / steps / permissions に触れない。
5. 検証は verification-contract.md のとおり(静的検査 + PR 上での実走確認)。

## Why(実装者に渡さない)

- 4本の pull_request 側欠落は「workflow だけ変える PR で CI が走らず、未検証の
  workflow がマージされる」穴。本タスク群の後続(runtime-api CI 新設、dashboard
  typecheck 追加)がまさに workflow を触るので、先に閉じておくと後続 PR の CI が
  信頼できる。これが本タスクを先頭に置く理由。
- deploy-dashboard-gcp の paths は test-dashboard.yml の paths と同じ集合+自分に
  揃える。deploy が依存する外側(compose 等)を含めない判断は「compose だけの PR で
  本番再デプロイされる」実害の除去を優先したもの。テスト緑を deploy の前提条件に
  するかは方針判断としてユーザーに委ねた(本タスクのスコープ外)。
