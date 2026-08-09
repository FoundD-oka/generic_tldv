# Verification Contract — p2x-advisory-cleanup-ci-triggers

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体: **CI** = GitHub Actions / **ゲート** = pr-ready-gate 実行者が
`.hw/gates/p2x-advisory-cleanup-ci-triggers/` の証跡を確認。S のため Fable レビューなし。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-401 | test-admin-api / test-api-gateway / test-meeting-api / test-packages の各 `pull_request.paths` に `.github/workflows/<自ファイル名>` が含まれる | 静的検査: `grep -A8 'pull_request:' .github/workflows/<f> \| grep '<自ファイル名>'` を4本分実行しログ保存 | ゲート | grep 出力を `.hw/gates/` へ |
| AT-402 | verify-compose-config.yml の `push.branches` が `[main, feature/*, hw/**]` を含む | 静的検査(grep) | ゲート | grep 出力 |
| AT-403 | deploy-dashboard-gcp.yml の `push.paths` が `services/dashboard/**`・`packages/transcript-rendering/**`・`.github/workflows/deploy-dashboard-gcp.yml` の3件ちょうど | 静的検査(該当節の全文をログ保存) | ゲート | 節の全文 |
| AT-404 | 本 PR 上で4本の test workflow(自パス追加により発火)が**実際に走って緑** | `gh run list --branch <branch>` で **workflow 名**を確認(job 名ではない) | CI + ゲート | gh run list 出力 |
| AT-405 | 本 PR で deploy-dashboard-gcp が走らない(push main 限定のまま) | `gh run list --branch <branch>` に同 workflow が現れない | ゲート | 同上 |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-401 | 変更ファイルが上記6 workflow のみ: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` の結果が `.github/workflows/` の6件と一致 | diff | ゲート | diff 出力 |
| FP-402 | 各 workflow の差分が `on:` 節のみ(jobs / steps / permissions / concurrency に差分なし) | `git diff base-commit..HEAD -- .github/workflows/` のハンク確認 | ゲート | diff 出力 |
| FP-403 | 既存 paths エントリの削除がない(追加のみ) | diff に `-` 行の paths エントリがない(YAML 整形での並び替えも不可) | ゲート | diff 出力 |

## Non-Functional Checks

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| NFT-401 | YAML として妥当(構文破壊なし) | AT-404 の実走が証明(GitHub が parse できなければ run が現れない/エラーになる)。加えて手元に actionlint があれば実行 | CI | gh run list |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p2x-advisory-cleanup-ci-triggers/`)
- hash-bound approval required: no(S・機械検証のみ)
- research brief required: no

## 改訂履歴

### 2026-08-09 base-commit 実測値へ同期

プラン作成後に plan コミット(1e242e0)を積んだため `base-commit` が
`0fe5ea5` → `1e242e0` へずれていた。`base-commit` ファイルと plan.md
frontmatter の両方を実測 HEAD `1e242e08c492d4009160b0edb6f265120d07fbc6`
へ更新した。0fe5ea5..1e242e0 の差分は `.hw/plans/` のみ(実装差分なし)。

### 2026-08-09 deploy-dashboard-gcp.yml の paths 発火検証(AT-403 補足)

`deploy-dashboard-gcp.yml` への paths 追加は**本番デプロイの発火条件を変える**
ため、GitHub の paths フィルタ相当(`**`=`/`含む任意 / `*`=`/`除く任意)の
マッチャで具体パスを機械照合した。証跡は
`.hw/gates/p2x-advisory-cleanup-ci-triggers/nft-401-and-paths-sim.txt`。
Fable は差分しか見えないため結果をここへ転記する。

**発火する(従来どおりデプロイされる)= 12/12 期待どおり**

`services/dashboard/src/app/page.tsx` / `services/dashboard/package.json` /
`services/dashboard/package-lock.json` / `services/dashboard/Dockerfile` /
`services/dashboard/next.config.ts` / `services/dashboard/docker-entrypoint.sh` /
`services/dashboard/public/logo.svg` /
`services/dashboard/scripts/assert-release-version.mjs` /
`packages/transcript-rendering/src/index.ts` /
`packages/transcript-rendering/dist/index.js` /
`packages/transcript-rendering/package.json` /
`.github/workflows/deploy-dashboard-gcp.yml`

**発火しない(不要な本番デプロイが消える)= 10/10 期待どおり**

`deploy/compose/docker-compose.yml` / `services/meeting-api/meeting_api/main.py` /
`services/api-gateway/main.py` / `services/transcription-service/app.py` /
`libs/admin-models/models.py` / `docs/README.md` / `README.md` /
`.hw/plans/foo/plan.md` / `.github/workflows/test-dashboard.yml` /
`scripts/migrations/001.sql`

**契約の3件では拾えない実体依存(契約外・未対応。要判断)**

`deploy-dashboard-gcp.yml` は `gcloud builds submit . --config
deploy/gcp/cloudbuild-dashboard.yaml` を実行し、`services/dashboard/Dockerfile`
はリポジトリ root をビルドコンテキストとして次もコピーする。これらだけを変更した
main push では**今後デプロイが発火しない**(従来は発火していた)。

- `deploy/gcp/cloudbuild-dashboard.yaml`(ビルド設定そのもの)
- `VERSION`(`COPY VERSION /repo/VERSION` → `npm run assert-release-version`)
- `deploy/helm/charts/vexa/Chart.yaml`(同上)

AT-403 が「3件ちょうど」を要求するため本タスクでは追加していない。拡張が必要なら
契約改訂(planner)で `deploy/gcp/**` `VERSION` `deploy/helm/charts/vexa/Chart.yaml`
を足す。

### 2026-08-09 AT-404 / AT-405 の判定時期

AT-404(4本の実走緑)と AT-405(deploy 不発火)は PR 上の `gh run list` を
判定手段とする契約であり、本コミット時点では PR 未作成のため未実施。
静的な事前シミュレーション(`at-404-405-presim.txt`)では、本差分の6ファイルに
対し test-admin-api / test-api-gateway / test-meeting-api / test-packages の
4本すべてが自ファイルパスにマッチして発火予定、`deploy-dashboard-gcp.yml` は
`pull_request` トリガーを持たず `branches: [main]` のみのため PR では不発火。
実走の確認は PR 作成後にゲート実行者が行う。
