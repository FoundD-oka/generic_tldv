# Verification Contract — named-tunnel-cloudrun

対象差分: `dafb0796e4a71085f3ec6305b35a4d9d1f899703..HEAD`(想定差分: `.hw/plans/named-tunnel-cloudrun/**`, `docs/ops/cloudflare-named-tunnel.md` のみ)
証跡ルート: `.hw/plans/named-tunnel-cloudrun/evidence/`
固定URL: `https://kabosu-api.bonginkan.com`(変更時は `evidence/preflight.txt` の採用値を正とする)
Cloud Run URL: `evidence/preflight.txt` に記録した `.status.url`

## Acceptance Tests

| ID | Requirement(最低合格ライン) | Method | Evidence |
|---|---|---|---|
| AT-001 | 切替前スナップショット3点(Cloud Run describe redact済 JSON、revisions 一覧、`/api/health` 出力)が存在し、describe JSON に `trycloudflare` を含む(現状証明) | command | `evidence/cloudrun-before.json`, `evidence/revisions-before.json`, `evidence/health-before.json` |
| AT-002 | Named Tunnel `kabosu-api-tunnel` が存在し remote-managed で、ingress が固定URL → `http://127.0.0.1:8056` + catch-all `http_status:404`、DNS CNAME が `<tunnel-id>.cfargotunnel.com`(proxied)である。**Cloudflare UI screenshot は使わず**、redact 済み API 応答で証明する | command(jq) | `evidence/cf-tunnel.json`, `evidence/cf-tunnel-config.json`, `evidence/cf-dns.json`, `evidence/preflight.txt` |
| AT-003 | `curl -s -o /dev/null -w '%{http_code}' https://kabosu-api.bonginkan.com/` が `<500`、`/admin/users?limit=1` に dummy キーで `401` か `403` | command | `evidence/tunnel-reachability.txt` |
| AT-004 | Cloud Run env の `VEXA_API_URL` / `VEXA_ADMIN_API_URL` / `VEXA_PUBLIC_API_URL` が全て固定URL。`--update-env-vars` のみ使用し、他 env のキー集合が before と一致(`jq` でキー名を比較) | command | `evidence/cloudrun-after.json`, `evidence/env-key-diff.txt` |
| AT-005 | 固定URLで Cloud Build が成功し、そのイメージが serving リビジョンになっている(`.status.traffic[]` の 100% リビジョンの image タグが `manual-named-tunnel-*`) | command | `evidence/cloudbuild.txt`, `evidence/cloudrun-after.json` |
| AT-006 | `redact 済 cloudrun-after.json`、`evidence/**`、`docs/ops/cloudflare-named-tunnel.md` の全てで `grep -ci trycloudflare` が 0 | command | `evidence/trycloudflare-grep.txt` |
| AT-007 | Cloud Run `/api/health`: `.checks.adminApi.reachable==true`、`.checks.vexaApi.reachable==true`、`error` に `fetch failed` を含まない | command | `evidence/health-after.json` |
| AT-008 | Cloud Run `/api/config`: `apiUrl` と `publicApiUrl` が固定URL、`wsUrl` が `wss://kabosu-api.bonginkan.com/ws`、`sharedAuth.enabled==true` | command | `evidence/config-after.json` |
| AT-009 | `curl -s -o /dev/null -w '%{http_code}' -X POST https://<cloudrun-url>/api/auth/shared-login` が `200`、body に `user` と `token` キーがある(値は redact) | command | `evidence/shared-login.txt` |
| AT-010 | ブラウザで Cloud Run URL を開き、共有ログイン後に `/meetings` が描画される。DevTools Network で `trycloudflare` へのリクエスト 0 件 | browser | `evidence/browser-meetings.png`, `evidence/browser-network.txt` |
| AT-011 | `launchctl kickstart -k` で tunnel 再起動後 90 秒以内に AT-003 と AT-007 が再び合格(Cloud Run の設定変更なし) | command | `evidence/restart-test.txt` |
| AT-012 | 既存 `pm.bonginkan.com` の `curl -sI | head -1` が切替前後で同一 | command | `evidence/pm-assist-before.txt`, `evidence/pm-assist-after.txt` |
| AT-013 | `docs/ops/cloudflare-named-tunnel.md` が存在し、構成・再起動・ロールバック(COMP-1 含む)・単一障害点(Mac 停止/スリープ、Docker 停止、cloudflared 停止、回線)・token 保管場所と API 再発行手順・Cloudflare 資格情報の扱い(API 操作の理由、要求最小権限、**Access Key ID 未使用の理由**)の6節を含む | source check | ファイル本体 |
| AT-014 | 実装差分が `.hw/plans/named-tunnel-cloudrun/**` と `docs/ops/cloudflare-named-tunnel.md` 以外を含まない(`git diff --name-only dafb079..HEAD`) | command | `evidence/diff-files.txt` |
| AT-015 | API token verify が成功している: `cf-token-verify.json` の `.success==true` かつ `.result.status=="active"`。読み取りプローブ3点(`cf-tunnel-list.json`, `cf-zone.json`, `cf-dns-before.json`)の `.success==true`。`cf-zone.json` の `.result[0].name=="bonginkan.com"` | command(jq) | `evidence/cf-token-verify.json`, `evidence/cf-tunnel-list.json`, `evidence/cf-zone.json`, `evidence/cf-dns-before.json` |
| AT-016 | 重複作成なし: 作業後の `cf-tunnel-list-after.json` で同名 tunnel が **1件**。作成・再利用の区分は `created-resources.json` と整合する | command(jq) | `evidence/created-resources.json`, `evidence/cf-tunnel-list-after.json` |
| AT-017 | connector token を API 取得し token file へ直接保存した: `cf-tunnel-token-fetch.json` が `{success:true, result_present:true}` で `result` キーを含まない。token file が mode 600 | command | `evidence/cf-tunnel-token-fetch.json`, `evidence/preflight.txt` |
| AT-018 | preflight.txt に「Access Key ID: 未使用(R2/S3用。Tunnel/DNS API は Bearer token で認証)」および要求最小権限(Cloudflare Tunnel:Edit / Zone:Read / DNS:Edit)の記載がある | source check | `evidence/preflight.txt` |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 秘密情報の非表示: `evidence/**` と docs に Cloudflare API Token・tunnel connector token・Account ID・`VEXA_ADMIN_API_KEY`・API key・cookie 値が含まれない。token系キーを除去し Account ID は `<ACCOUNT_ID>` に置換する | command | `evidence/secret-scan.txt` |
| FP-002 | 最小権限・最小露出: `~/.cloudflared/cert.pem` を作成していない。token ファイルは mode 600。資格情報は対話 `read -rs` でのみ受け取り、`commands.txt` に Authorization header、`cloudflared service install`、`cloudflared tunnel login`、token引数、Access Key ID の参照が無い | command audit | `evidence/preflight.txt`, `evidence/commands.txt` |
| FP-003 | `pm-assist-tunnel` の remote config・launchd・token に変更なし | command + AT-012 | `evidence/pm-assist-after.txt` |
| FP-004 | `--set-env-vars` 未使用、`gcloud run services delete` 未使用、`make -C deploy/compose up` 未実行(実行コマンド一覧を記録) | command audit | `evidence/commands.txt` |
| FP-005 | 停止条件 STOP-1〜4 のいずれかが発火した場合、plan.md 記載の動作を実施し `evidence/stop.txt` に記録している(未発火なら「未発火」と記載) | manual | `evidence/stop.txt` |
| FP-006 | リポジトリのアプリコード・CI・Dockerfile・cloudbuild 設定を変更していない | AT-014 | `evidence/diff-files.txt` |
| FP-007 | 既存資源の不可侵: 補償対象は `created-resources.json` の `*_created:true` のみ。`pm-assist-tunnel` の id に対する API 呼び出しが `commands.txt` に無い | command audit | `evidence/created-resources.json`, `evidence/compensation.json`(未発火なら「未発火」) |
| FP-008 | ブラウザ使用は AT-010(Cloud Run `/meetings`)のみ。Cloudflare ダッシュボードの screenshot・操作記録が evidence に存在しない | manual + source check | `evidence/commands.txt`, `evidence/` ファイル一覧 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | Cloud Run `/api/health` の応答が 5 秒以内(health の内部 timeout と同値) | `curl -w '%{time_total}'` 3回 | `evidence/health-after.json` 付記 |
| NFT-002 | 報告・runbook が日本語で、単一障害点とロールバック先(00065-954 は「切替前の壊れた状態」)を明記 | source check | `docs/ops/cloudflare-named-tunnel.md`, 最終報告 |

## Gate Requirements

- preflight result required: yes(`evidence/preflight.txt`)
- evidence pack required: yes
- hash-bound approval required: yes
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-001 | token-file 方式(`cloudflared tunnel run --token-file`)がインストール済み版で有効 | `cloudflared --version` と `cloudflared tunnel run --help` に `--token-file` があること | `evidence/preflight.txt` |
| RF-002 | `gcloud run services update --update-env-vars` が他 env を保持する挙動 | AT-004 のキー集合比較で実証 | `evidence/env-key-diff.txt` |
| RF-003 | Cloudflare v4 API の endpoint と応答形が現行仕様のまま: tunnel作成、configuration PUT、connector token GET、DNS作成、token verify | 各応答の `.success==true` と AT-002 / AT-015 / AT-017 の jq 検査が通ること | `evidence/cf-*.json` |
