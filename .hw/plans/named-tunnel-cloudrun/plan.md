---
generated_by: fable
task_id: named-tunnel-cloudrun
size: L
runtime: inline
base_commit: dafb0796e4a71085f3ec6305b35a4d9d1f899703
---

# named-tunnel-cloudrun — Cloud Run dashboard の API 経路を Cloudflare Named Tunnel 固定URLへ移行

## ゴール

- 文字通りの依頼: 期限切れ quick tunnel(`*.trycloudflare.com`)を Named Tunnel に置き換え、Cloud Run `kabosu-dashboard` の3つのURLを固定URLへ切り替える。
- 本当に達成したい成果: Cloud Run 上の dashboard から共有ログインと `/meetings` が **恒久的に**(tunnel 再起動・再デプロイをまたいでも)動くこと。quick tunnel 依存を設定・image の両方から排除すること。
- reframe: なし(クライアント合意済みの明示要求)。ただし「env を変えるだけ」では image に焼き込まれた rewrites 先が残るため、**固定URLでの再ビルドを必須手順に含める**(下記 How 2-3)。

## スコープ外

- Cloudflare Access / WAF による API 公開面の保護(残存リスクとして記録のみ)。
- ローカル Mac / Docker の高可用化(単一障害点として明記のみ)。
- ダッシュボードや gateway のコード変更(不要。gitnexus impact は編集シンボルが無いため対象外)。

## 前提(実装前に実装者が確認し evidence/preflight.txt に記録)

- P-1 `docker ps` で api-gateway が `127.0.0.1:8056` を listen、`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8056/` が 5xx でない。
- P-2 `gcloud auth list` / `gcloud config get-value project` が `pm-qe-mgmt-20260624` を指し、`gcloud run services describe kabosu-dashboard --region asia-northeast1` が成功する。
- P-3 既存 `pm-assist-tunnel` の起動方式(launchd plist のパス、token ファイルのパス・権限)を `launchctl list | grep -i cloudflared` と plist 参照で特定する。**token の値は出力しない**。
- P-4 Cloudflare は **ブラウザUIを使わず v4 API のみ** で操作する。ユーザーから提供された **Cloudflare Account ID** と **Cloudflare API Token** の2値だけを How 1-0 の方法(対話 `read -rs`)で受け取る。**Access Key ID は受け取らない・使わない**(R2/S3 の S3互換認証情報であり Named Tunnel / DNS の v4 API 認証には無関係。Bearer API Token のみで足りる)。この理由を preflight.txt に「Access Key ID: 未使用(R2/S3用。Tunnel/DNS API は Bearer token で認証)」と1行記録する。
- P-5 `curl` と `jq` が利用可能(`jq --version`)。`cloudflared --version` と `cloudflared tunnel run --help` に `--token-file` が存在する。
- P-6 API token verify と読み取り権限プローブ(How 1-1)が全て成功する。失敗時は STOP-1(何も変更せず終了)。

## 固定値

| 項目 | 値 |
|---|---|
| Tunnel 名 | `kabosu-api-tunnel`(既存 `pm-assist-tunnel` とは分離。触らない) |
| 固定ホスト名 | `kabosu-api.bonginkan.com`(衝突時のみ `kabosu-api-2.bonginkan.com`。採用値を evidence/preflight.txt に記録) |
| Origin | `http://127.0.0.1:8056` |
| token 保存先 | `~/.cloudflared/kabosu-api-tunnel.token`(mode 600、リポジトリ外) |
| Cloud Run | project `pm-qe-mgmt-20260624` / region `asia-northeast1` / service `kabosu-dashboard` |
| 切替前リビジョン | `kabosu-dashboard-00065-954` |
| 証跡ルート | `.hw/plans/named-tunnel-cloudrun/evidence/`(秘密情報は必ず redact) |

## How

### 0. スナップショット(切替前・変更なし)

```
E=.hw/plans/named-tunnel-cloudrun/evidence; mkdir -p "$E"
gcloud run services describe kabosu-dashboard --project pm-qe-mgmt-20260624 --region asia-northeast1 --format=json \
 | jq '(.spec.template.spec.containers[0].env // []) |= map(if (.name|test("KEY|SECRET|PASS|TOKEN|PASSWORD";"i")) then .value="<redacted>" else . end)' \
 > "$E/cloudrun-before.json"
gcloud run revisions list --service kabosu-dashboard --project pm-qe-mgmt-20260624 --region asia-northeast1 --format=json > "$E/revisions-before.json"
curl -s https://<cloudrun-url>/api/health > "$E/health-before.json"
curl -sI https://pm.bonginkan.com | head -1 > "$E/pm-assist-before.txt"
```
- `cloudrun-before.json` に `trycloudflare` が含まれることを確認(現状の証明)。
- Cloud Run の URL は `.status.url` から取り evidence/preflight.txt に記録。

### 1. Named Tunnel 作成(Cloudflare v4 API、最小権限・秘密値非出力)

共通ルール: API Token・connector token・Account ID を **コマンド引数・shell history・標準出力・evidence・リポジトリに一切残さない**。`set -x` 禁止。`echo $CF_*`・`cat hdr.cfg` 禁止。ヘッダは curl の `-K`(設定ファイル)で渡し、`-H "Authorization: ..."` の引数渡しは禁止(`ps` に露出するため)。

**1-0 秘密値の受け取り(対話 stdin → mode 600 の一時ファイル)**

```
set +o history 2>/dev/null; unset HISTFILE
umask 077
E=.hw/plans/named-tunnel-cloudrun/evidence; mkdir -p "$E"
CF_TMP="$(mktemp -d "${TMPDIR:-/tmp}/cf.XXXXXX")"; chmod 700 "$CF_TMP"
trap 'rm -rf "$CF_TMP"' EXIT
IFS= read -rs -p 'Cloudflare Account ID: ' CF_ACCOUNT_ID; echo
IFS= read -rs -p 'Cloudflare API Token: ' CF_API_TOKEN; echo
printf 'header = "Authorization: Bearer %s"\n' "$CF_API_TOKEN" > "$CF_TMP/hdr.cfg"
unset CF_API_TOKEN
CF_API=https://api.cloudflare.com/client/v4
cf() { curl -sS -K "$CF_TMP/hdr.cfg" -H 'Content-Type: application/json' "$@"; }
redact() { jq 'walk(if type=="object" then with_entries(select(.key|test("^(token|tunnel_secret|credentials_file|token_file)$")|not)) else . end)' | sed "s/${CF_ACCOUNT_ID}/<ACCOUNT_ID>/g"; }
```

- Access Key ID のプロンプトは設けない(P-4)。
- 生の API 応答は必ず `$CF_TMP` に置き、evidence へは `redact` を通した後だけ書く。

**1-1 token verify と権限プローブ(失敗 = STOP-1、変更ゼロで終了)**

```
cf "$CF_API/user/tokens/verify" > "$CF_TMP/verify.json"
jq -e '.success and .result.status=="active"' "$CF_TMP/verify.json" >/dev/null || echo STOP-1a
redact < "$CF_TMP/verify.json" > "$E/cf-token-verify.json"

cf "$CF_API/accounts/$CF_ACCOUNT_ID/cfd_tunnel?name=kabosu-api-tunnel&is_deleted=false" > "$CF_TMP/tunnel-list.json"
jq -e '.success' "$CF_TMP/tunnel-list.json" >/dev/null || echo STOP-1b
redact < "$CF_TMP/tunnel-list.json" > "$E/cf-tunnel-list.json"

cf "$CF_API/zones?name=bonginkan.com&status=active" > "$CF_TMP/zone.json"
jq -e '.success and (.result|length==1)' "$CF_TMP/zone.json" >/dev/null || echo STOP-1b
jq -e --arg a "$CF_ACCOUNT_ID" '.result[0].account.id==$a' "$CF_TMP/zone.json" >/dev/null || echo STOP-1d
CF_ZONE_ID=$(jq -r '.result[0].id' "$CF_TMP/zone.json")
redact < "$CF_TMP/zone.json" > "$E/cf-zone.json"

CF_HOST=kabosu-api.bonginkan.com
cf "$CF_API/zones/$CF_ZONE_ID/dns_records?name=$CF_HOST" > "$CF_TMP/dns-before.json"
jq -e '.success' "$CF_TMP/dns-before.json" >/dev/null || echo STOP-1b
redact < "$CF_TMP/dns-before.json" > "$E/cf-dns-before.json"
```

- 書き込み権限(Cloudflare Tunnel:Edit / DNS:Edit)は副作用なしに検査できないため、1-2 以降の最初の書き込みが `success:false`(errors[].code 10000/9109/10001 等の権限系)なら **STOP-1c** とし、1-7 の補償を実行して終了する。
- preflight.txt に「要求最小権限: Account=Cloudflare Tunnel:Edit、Zone(bonginkan.com)=Zone:Read + DNS:Edit」と verify 結果(status のみ)を記録。token の id・値は書かない。

**1-2 既存 tunnel 確認と重複回避**

- `tunnel-list.json` の件数が1なら再利用し、0なら `POST /accounts/{account_id}/cfd_tunnel` に `{name:"kabosu-api-tunnel", config_src:"cloudflare"}` を送って新規作成する。2件以上なら STOP-1e。
- 直後に `created-resources.json` を作り、tunnel ID、`tunnel_created`、DNS record ID、`dns_created` を記録する。補償では `*_created:true` の資源だけを対象にする。

**1-3 remote-managed ingress 設定**

- ingress を固定ホスト名 → `http://127.0.0.1:8056`、末尾 catch-all → `http_status:404` として `PUT /accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations` へ送る。
- 既存 tunnel 再利用時は先に現在設定を GET し、完全一致なら PUT を省略、一致しない場合は STOP-1e として上書きしない。
- GET 応答を redact した `evidence/cf-tunnel-config.json` で ingress を検査する。

**1-4 DNS CNAME 作成(`<tunnel-id>.cfargotunnel.com`)**

- 対象レコードが0件なら proxied CNAME を作成、1件かつ期待する tunnel ID の CNAME なら再利用する。
- 別宛先との衝突時のみ `kabosu-api-2.bonginkan.com` に切り替え、tunnel ingress と DNS 検査をやり直す。再度衝突なら STOP-1e。
- 作成時は `POST /zones/{zone_id}/dns_records` を使い、作成した record ID と `dns_created:true` を補償台帳へ記録する。

**1-5 connector token を API 取得し token file へ直接保存(値は一切出力しない)**

- `GET /accounts/{account_id}/cfd_tunnel/{tunnel_id}/token` の `.result` 文字列を `~/.cloudflared/kabosu-api-tunnel.token` へ直接保存し mode 600 にする。evidence には `{success, errors, result_present}` だけを残す。
- `cloudflared tunnel login`(cert.pem)・`cloudflared service install <token>`・token の `echo`/`cat` は禁止。

**1-6 API 証跡(redact 済)と後処理**

- tunnel、configuration、DNS、token verify、一覧の応答は Account ID と token系キーを redact し、`evidence/cf-*.json` として保存する。
- evidence の secret scan が0件であること、token file が mode 600 であることを記録する。tunnel ID・ホスト名・zone ID・DNS record ID は記録可、Account ID と token は記録しない。
- API 操作用一時ディレクトリを削除し、資格情報変数を unset する。

**1-7 補償手順 COMP-1(STOP-1c / STOP-2 / ROLLBACK-Full で使用)**

- `created-resources.json` の `*_created:true` だけを DNS → tunnel connections → tunnel → token file の順に削除する。再利用資源は削除しない。
- tunnel 削除前に name が `kabosu-api-tunnel` であることを API で照合する。`pm-assist-tunnel` は絶対に対象にしない。
- 各応答は redact して `evidence/compensation.json` に記録し、STOP ID と理由を `evidence/stop.txt` に残す。

### 2. ローカルで tunnel を常駐化(pm-assist-tunnel と同方式)

1. P-3 で特定した pm-assist-tunnel の launchd plist を複製し、Label `com.bonginkan.kabosu-api-tunnel`、`ProgramArguments` を `cloudflared tunnel run --token-file /Users/<user>/.cloudflared/kabosu-api-tunnel.token`、`KeepAlive` true、ログ先を `~/Library/Logs/kabosu-api-tunnel.log` にする。plist 本体を(パスのみ、token なし)evidence/launchd-plist.txt に保存。
2. `launchctl bootstrap gui/$(id -u) <plist>` で起動。
3. 外部到達確認(60秒以内に成功しなければ STOP-2):
   `curl -s -o /dev/null -w '%{http_code}\n' https://kabosu-api.bonginkan.com/` が `< 500`、
   `curl -s -o /dev/null -w '%{http_code}\n' -H "X-Admin-API-Key: dummy" https://kabosu-api.bonginkan.com/admin/users?limit=1` が `401` または `403`(gateway 到達の証明)。
   結果を evidence/tunnel-reachability.txt に記録。
4. `curl -sI https://pm.bonginkan.com | head -1` が手順0と同じであること(既存 tunnel 不干渉)。

### 3. Cloud Run 切替(2段階、各段で検証)

**3-a env 更新(即時復旧)**
```
gcloud run services update kabosu-dashboard --project pm-qe-mgmt-20260624 --region asia-northeast1 \
  --update-env-vars VEXA_API_URL=https://kabosu-api.bonginkan.com,VEXA_ADMIN_API_URL=https://kabosu-api.bonginkan.com,VEXA_PUBLIC_API_URL=https://kabosu-api.bonginkan.com
```
- `--set-env-vars` は他の env を消すので **禁止**。`--update-env-vars` のみ。
- 直後に `curl -s https://<cloudrun-url>/api/health | jq '.checks.adminApi.reachable, .checks.vexaApi.reachable'` が両方 `true`。false なら STOP-3(ロールバック)。

**3-b 固定URLで image 再ビルド(rewrites の焼き込み排除)**
```
gcloud builds submit . --project pm-qe-mgmt-20260624 --config deploy/gcp/cloudbuild-dashboard.yaml \
  --substitutions "_VEXA_API_URL=https://kabosu-api.bonginkan.com,_DEPLOY_SHA=manual-named-tunnel-$(date +%Y%m%d%H%M)" --quiet
```
- リポジトリ root、clean tree(`git status --porcelain` 空)で実行。ビルドID・image タグを evidence/cloudbuild.txt に記録。
- 完了後、`gcloud run services describe ... --format=json` を手順0と同じ redact で `evidence/cloudrun-after.json` に保存。`grep -c trycloudflare` が 0。

### 4. 検証(verification-contract.md の AT を全て実行し evidence に保存)

- `/api/health`、`/api/config`、`POST /api/auth/shared-login`(curl)。
- **ブラウザ使用は本項のみ**(Cloudflare 側の操作・確認にブラウザは使わない): Cloud Run URL を開き、共有ログイン後 `/meetings` 到達をスクリーンショット(`evidence/browser-meetings.png`)。DevTools Network で `trycloudflare` へのリクエストが 0 件であることを `evidence/browser-network.txt` に記録。
- 再起動耐性: `launchctl kickstart -k gui/$(id -u)/com.bonginkan.kabosu-api-tunnel` → 90秒以内に `https://kabosu-api.bonginkan.com/` が `<500` に戻り、Cloud Run `/api/health` の両 reachable が `true`(env 変更なし)。`evidence/restart-test.txt`。
- 既存 `pm.bonginkan.com` の応答が手順0と同一。

### 5. 運用証跡と runbook

- 証跡はすべて `.hw/plans/named-tunnel-cloudrun/evidence/` に置き、commit する。commit 前に `grep -rEi 'token|api[_-]?key|secret' evidence/` で値の漏れがないこと(キー名の言及は可、値は不可)。
- `docs/ops/cloudflare-named-tunnel.md`(新規、日本語)に次の6節を記載: (1) 構成図(Cloud Run → Cloudflare edge → kabosu-api-tunnel → 127.0.0.1:8056)、(2) 起動/停止/再起動コマンド、(3) ロールバック手順(COMP-1 を含む)、(4) **単一障害点**(ローカル Mac の停止・スリープ、Docker 停止、cloudflared プロセス停止、宅内回線)、(5) token の保管場所と再発行手順(API `GET /accounts/{account_id}/cfd_tunnel/{tunnel_id}/token` を mode 600 ファイルへ直接書き出す。値は書かない)、(6) **Cloudflare 資格情報の扱い**: ブラウザUIではなく v4 API で操作する理由、要求最小権限(Account: Cloudflare Tunnel:Edit / Zone: Zone:Read, DNS:Edit)、Account ID と API Token は対話入力のみで引数・history・evidence・リポジトリに残さない、**Access Key ID は R2/S3(S3互換)用であり Tunnel/DNS API の認証に使わないため未使用**、と明記。
- リポジトリのコード・CI・Dockerfile は変更しない。

## ロールバックと停止条件

| ID | 条件 | 動作 |
|---|---|---|
| STOP-1a | `GET /user/tokens/verify` が `success:false` または `status!="active"` | 何も変更せず終了。人間へ「token 無効/期限切れ」を報告(token 値・id は出さない) |
| STOP-1b | Tunnel 一覧 / zone 取得 / DNS 一覧の読み取りプローブが失敗(Account ID 誤り、Cloudflare Tunnel・Zone・DNS の Read 不足、zone 不在) | 何も変更せず終了。不足権限名を報告 |
| STOP-1c | 書き込み API(tunnel 作成・configurations PUT・DNS 作成・token 取得)が `success:false`(権限不足 10000/9109 等を含む) | COMP-1 で作成済み分のみ削除して終了。errors[].code と message を evidence/stop.txt に記録 |
| STOP-1d | `bonginkan.com` zone の `account.id` が提供 Account ID と不一致 | 何も変更せず終了 |
| STOP-1e | 同名 tunnel が複数、または既存 tunnel の ingress / 既存 DNS が想定と異なり自動解決不可 | 何も変更せず終了。人間へ報告 |
| STOP-2 | 手順2-3 の外部到達が 60 秒以内に成功しない | Cloud Run は触らない。launchd を `bootout`、COMP-1 を実行して終了 |
| STOP-3 | 3-a 後 `/api/health` の reachable が両方 true にならない | `gcloud run services update-traffic kabosu-dashboard --region asia-northeast1 --project pm-qe-mgmt-20260624 --to-revisions kabosu-dashboard-00065-954=100`。原因を evidence/stop.txt に記録し終了 |
| STOP-4 | 3-b ビルド失敗、または新リビジョンで AT が落ちる | 3-a 直後の(env 更新済み)リビジョンへ `--to-revisions` で戻す。3-a の状態は動作しているので本番は復旧済み扱い |
| ROLLBACK-Full | 全面撤回が必要 | 00065-954 へ traffic 100%、launchd bootout、COMP-1(作成済み tunnel と DNS を API 削除、token ファイル削除) |

ロールバック先 00065-954 は「切替前と同じ壊れた状態」であり、機能回復ではないことを報告に明記する。

## 単一障害点(報告・runbook に必ず記載)

- ローカル Mac の停止・スリープ・再起動・OS更新(launchd 自動再起動はログイン後のみ)。
- Docker Desktop の停止、api-gateway コンテナの停止(8056 が閉じる)。
- cloudflared プロセス・宅内回線・Cloudflare 側障害。
- API 公開面(`kabosu-api.bonginkan.com`)は API key 認証のみで保護。quick tunnel 時代と同等だがホスト名が固定化されるため露出時間は長くなる(Access 導入は別タスク)。

## Why(実装者に渡さない)

- 2段階切替にした理由: env 更新だけで health は復旧するが、`next.config.ts` の `/b/*`・`/ws` rewrites は `Dockerfile` の `ARG VEXA_API_URL` でコンパイル時に固定される。ここに trycloudflare が残ると同一オリジン経由の bot 操作や WS フォールバックが壊れ、かつ「本番設定から排除」要件を満たさない。
- 別 tunnel を新設し pm-assist-tunnel を触らない理由: remote config の編集ミスで `pm.bonginkan.com` を巻き込む事故を避ける(最小影響範囲)。
- cert.pem を作らず、ブラウザUIも使わず v4 API にした理由: `cloudflared tunnel login` の証明書はアカウント全体の tunnel/DNS 操作権限を持ち端末紛失時のリスクが大きい。ブラウザUIは操作が再現不能で、証跡が screenshot(塗り潰し漏れリスク)になる。ユーザーが提供した scoped API Token なら権限を Tunnel:Edit / Zone:Read / DNS:Edit に絞れ、応答 JSON を redact して機械可読な証跡にできる。Access Key ID は R2 の S3互換認証情報で v4 API では認証に使えないため受け取らない(不要な秘密値を扱わない=最小権限)。
- 秘密値を `read -rs` + curl `-K` ヘッダファイルにした理由: `-H "Authorization: Bearer ..."` の引数渡しは `ps`・shell history・エージェントのツール呼び出しログに残る。env 変数も `unset` 前に子プロセスへ継承される。mode 600 の一時ファイルを `trap` で消すのが最も漏洩面が小さい。補償台帳 `created-resources.json` を分けたのは、既存 tunnel/DNS を再利用した場合に補償で誤って削除しないため。
- CI 追従の根拠: `.github/workflows/deploy-dashboard-gcp.yml` が Cloud Run の現行 `VEXA_API_URL` を読んでビルドに渡すため、env を固定URLにすれば以後の自動デプロイは自然に固定URLで焼かれる。よってコード変更は不要。
- S/M/L を L にした理由: 本番外部状態(Cloudflare/DNS/Cloud Run)を触り、主要検証がブラウザ・外部到達の手動証跡で CI 再実行不能。ロールバック先も機能復旧ではない。迷ったら上へ倒す規律に従う。
- R軸を inline にした理由: 所要 1〜2 時間、状態量小、並行委譲不要。Cloudflare・GCP の資格情報を扱うため、ユーザー権限で任意コードを実行する Prime へは倒さない。
