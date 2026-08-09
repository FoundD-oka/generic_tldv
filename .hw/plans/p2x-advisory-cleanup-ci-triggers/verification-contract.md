# Verification Contract — p2x-advisory-cleanup-ci-triggers

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体: **CI** = GitHub Actions / **ゲート** = pr-ready-gate 実行者が
`.hw/gates/p2x-advisory-cleanup-ci-triggers/` の証跡を確認。S のため Fable レビューなし。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-401 | test-admin-api / test-api-gateway / test-meeting-api / test-packages の各 `pull_request.paths` に `.github/workflows/<自ファイル名>` が含まれる | 静的検査: `grep -A8 'pull_request:' .github/workflows/<f> \| grep '<自ファイル名>'` を4本分実行しログ保存 | ゲート | grep 出力を `.hw/gates/` へ |
| AT-402 | verify-compose-config.yml の `push.branches` が `[main, feature/*, hw/**]` を含む | 静的検査(grep) | ゲート | grep 出力 |
| AT-403 | deploy-dashboard-gcp.yml の `push.paths` が次の**8件ちょうど**(過不足なし): `services/dashboard/**`・`packages/transcript-rendering/**`・`.github/workflows/deploy-dashboard-gcp.yml`・`deploy/gcp/**`・`VERSION`・`deploy/helm/charts/vexa/Chart.yaml`・`.dockerignore`・`.gcloudignore` | 静的検査(該当節の全文をログ保存)+ paths 発火シミュレーション再実行: 既存22件に加え、FIRE 側へ `deploy/gcp/cloudbuild-dashboard.yaml`・`VERSION`・`deploy/helm/charts/vexa/Chart.yaml`・`.dockerignore`・`.gcloudignore` の5件、SKIP 側へ `deploy/helm/charts/vexa/values.yaml`・`deploy/helm/charts/vexa/templates/deployment.yaml` の2件を追加した29件で全件期待どおり | ゲート | 節の全文 + シミュレーション出力を `.hw/gates/` へ |
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

### 2026-08-09 AT-403 改訂: deploy の push.paths 要求集合を 3件 → 8件へ拡張(planner)

実装者の paths 発火検証(前節)で「契約の3件では拾えない実体依存」が報告されたため、
**要求集合を実体依存に合わせて広げる**。合格ラインは不変(「指定集合とちょうど一致」の
厳密性は維持。集合の内容だけを是正)。方向は fail-safe: デプロイが「走るべきなのに
走らない」(本番が黙って古いまま残る)方が「余分に走る」より有害なので、判断に迷う
パスは含める側へ倒した。

**追加5エントリと根拠(全て現物確認、2026-08-09)**

1. `deploy/gcp/**` — `.github/workflows/deploy-dashboard-gcp.yml:51` が
   `--config=deploy/gcp/cloudbuild-dashboard.yaml` で直接使用。ディレクトリの現内容は
   同ファイル1件のみだが、fail-safe 方針により glob で将来の gcp ビルド資産も拾う。
2. `VERSION` — `services/dashboard/Dockerfile:24` の `COPY VERSION /repo/VERSION`。
   `scripts/generate-release-version.js:33` が読み、`scripts/assert-release-version.js:26`
   がビルド成果物と照合する(不一致でビルド fail)。
3. `deploy/helm/charts/vexa/Chart.yaml` — `services/dashboard/Dockerfile:25` の COPY。
   `generate-release-version.js:34` が `appVersion` を読む。ファイル単位で指定
   (`deploy/helm/**` にはしない: values/templates の変更は dashboard イメージに
   無関係で、余分な本番デプロイを常態化させるため。Chart.yaml だけが実体依存)。
4. `.dockerignore`(リポジトリ root、git 管理下) —
   `deploy/gcp/cloudbuild-dashboard.yaml:22-26` の `docker build .` はリポジトリ root を
   コンテキストにするため、root `.dockerignore` の変更はビルド内容を直接変える。
5. `.gcloudignore`(リポジトリ root、git 管理下) — workflow:49 の
   `gcloud builds submit .` のアップロード対象集合を決定する。

**同種の見落とし確認(現物読了の結果、上記以外なし)**

- `services/dashboard/Dockerfile` の COPY 全行(7,8,11,19-23,24,25,57 行)を確認:
  24-25 行以外はすべて `services/dashboard/**` と `packages/transcript-rendering/**`
  で被覆済み。
- `deploy/gcp/` の内容は `cloudbuild-dashboard.yaml` 1件のみ(ls 確認)。
- npm スクリプト: `prebuild` の `sync-packages` は `../../packages/.../dist` の存在
  ガード付きで、コンテナ内(WORKDIR /app)では不在によりスキップ。
  `generate-release-version.js` / `assert-release-version.js` の外部参照は
  `VERSION` と `Chart.yaml` のみ(上記2・3で被覆)。
- `services/.dockerignore`・`services/dashboard/.dockerignore` は、コンテキストが
  リポジトリ root の本ビルドでは docker が参照しない(root の `.dockerignore` のみ
  有効。`<Dockerfile>.dockerignore` 形式でもない)。後者はどのみち
  `services/dashboard/**` で被覆。

AT-405(本 PR で deploy 不発火)・FP-403(削除なし・追加のみ)は本改訂の影響を
受けない。契約 hash が変わるため、レビュー・ゲートは本改訂後の状態に対して実行する。

### 2026-08-09 改訂後 AT-403 の再検証結果(実装者)

`deploy-dashboard-gcp.yml` の `push.paths` を8エントリへ修正し、改訂後の要求で
全項目を再実行した。証跡は `.hw/gates/p2x-advisory-cleanup-ci-triggers/`
(`at-401-403-static.txt` / `nft-401-and-paths-sim.txt` / `fp-401-403-diff.txt`)。

- AT-403: `push.paths` は要求8件と順不同でなく**完全一致・過不足なし**。
  `branches: [main]`・`concurrency`・jobs は不変。
- paths 発火シミュレーション(GitHub の `**`/`*` 相当マッチャ)= **29件全件期待どおり**
  (FIRE 17 / SKIP 12、exit 0)。改訂で追加された7件の内訳:
  - FIRE(新規5): `deploy/gcp/cloudbuild-dashboard.yaml`・`VERSION`・
    `deploy/helm/charts/vexa/Chart.yaml`・`.dockerignore`・`.gcloudignore`
  - SKIP(新規2): `deploy/helm/charts/vexa/values.yaml`・
    `deploy/helm/charts/vexa/templates/deployment.yaml`
    (= `deploy/helm/**` にせずファイル単位で指定した判断が効いていることの確認)
- 前々節「契約の3件では拾えない実体依存(契約外・未対応)」は本改訂で解消。
- AT-401 / AT-402 / FP-401(6ファイル)/ FP-402(`on:` 節のみ)/ FP-403(paths の
  削除ゼロ)/ NFT-401(pyyaml で6本パース成功、`bash .hw/verify.sh` は既知失敗3件のみ)
  はいずれも改訂後も PASS。AT-404 / AT-405 は PR 上での実走確認のため引き続き未実施。
