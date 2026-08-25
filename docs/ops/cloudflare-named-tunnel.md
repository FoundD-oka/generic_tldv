# Cloudflare Named Tunnel 運用手順

対象は Cloud Run の `kabosu-dashboard` からローカル API gateway へ接続する経路です。期限付きの Quick Tunnel は使わず、固定ホスト名 `kabosu-api.bonginkan.com` を Named Tunnel `kabosu-api-tunnel` に割り当てます。

## 1. 構成

```text
Cloud Run kabosu-dashboard
  └─ HTTPS / WSS: kabosu-api.bonginkan.com
       └─ Cloudflare edge
            └─ Named Tunnel: kabosu-api-tunnel
                 └─ cloudflared (Mac LaunchAgent)
                      └─ http://127.0.0.1:8056 (Docker api-gateway)
```

Cloudflare 側の ingress は remote-managed です。先頭ルールは固定ホスト名から `http://127.0.0.1:8056`、末尾ルールは `http_status:404` とします。Cloud Run の `VEXA_API_URL`、`VEXA_ADMIN_API_URL`、`VEXA_PUBLIC_API_URL` はすべて固定ホスト名を参照します。

## 2. 起動・停止・再起動

LaunchAgent:

- Label: `com.bonginkan.kabosu-api-tunnel`
- plist: `~/Library/LaunchAgents/com.bonginkan.kabosu-api-tunnel.plist`
- log: `~/Library/Logs/kabosu-api-tunnel.log`
- connector token: `~/.cloudflared/kabosu-api-tunnel.token`（mode 600）

```bash
# 状態確認
launchctl print gui/$(id -u)/com.bonginkan.kabosu-api-tunnel

# 初回起動
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bonginkan.kabosu-api-tunnel.plist

# 停止
launchctl bootout gui/$(id -u)/com.bonginkan.kabosu-api-tunnel

# 再起動
launchctl kickstart -k gui/$(id -u)/com.bonginkan.kabosu-api-tunnel

# 到達確認
curl -sS -o /dev/null -w '%{http_code}\n' https://kabosu-api.bonginkan.com/
curl -sS https://kabosu-dashboard-yye63pfv3a-an.a.run.app/api/health
```

`pm-assist-tunnel` は別サービス `ai.bonginkan.pm-tunnel` です。この手順では plist、token、Cloudflare設定のどれも変更しません。

## 3. ロールバックと補償（COMP-1）

イメージだけを戻す場合は、固定URLの環境変数が入った `kabosu-dashboard-00066-fgk` へ traffic を戻します。

```bash
gcloud run services update-traffic kabosu-dashboard \
  --project pm-qe-mgmt-20260624 \
  --region asia-northeast1 \
  --to-revisions kabosu-dashboard-00066-fgk=100
```

切替前の `kabosu-dashboard-00065-954` は期限切れ Quick Tunnel を参照する壊れた状態です。全面撤回の履歴復元先ではありますが、機能回復先としては使いません。

Cloudflare資源も撤回する COMP-1 は `.hw/plans/named-tunnel-cloudrun/evidence/created-resources.json` を確認し、`dns_created:true` / `tunnel_created:true` の資源だけを DNS → tunnel connections → tunnel の順に v4 API で削除します。削除前に tunnel 名が `kabosu-api-tunnel` と一致することを必ず確認します。既存資源や `pm-assist-tunnel` は削除しません。その後、LaunchAgentを停止し、今回の plist と connector token file を削除します。

## 4. 単一障害点

- Mac の停止、スリープ、再起動、OS更新。LaunchAgent はユーザーのGUIログイン後に動作します。
- Docker Desktop または api-gateway コンテナの停止。`127.0.0.1:8056` が閉じると tunnel は接続中でもAPIが失敗します。
- `cloudflared` プロセス停止。KeepAliveで再起動しますが、連続クラッシュ時はログ確認が必要です。
- 宅内回線、Cloudflare edge、Cloud Run の障害。
- 固定ホスト名の公開期間が長いため、API key管理が重要です。Cloudflare Access / WAF の追加は別タスクです。

## 5. Connector token の保管と再発行

token は `~/.cloudflared/kabosu-api-tunnel.token` に保存し、所有者だけが読める mode 600 にします。値をチャット、コマンド引数、shell history、ログ、リポジトリへ出してはいけません。

再発行時は scoped Cloudflare API Token を対話で非表示入力し、v4 API `GET /accounts/{account_id}/cfd_tunnel/{tunnel_id}/token` の `.result` を protected temporary file 経由で connector token file へ直接書き込みます。更新後は `chmod 600` と LaunchAgent の再起動を行い、固定URLと Cloud Run `/api/health` を確認します。`cloudflared tunnel login` と `cloudflared service install <token>` は使いません。

## 6. Cloudflare 資格情報の扱い

Cloudflare操作はブラウザUIではなく v4 API を使います。理由は、操作の再現性、JSONでの検証、秘密値を含まない証跡、資源IDに基づく安全な補償を確保するためです。

要求する最小権限は次のとおりです。Cloudflareの現行API表記では Write、管理画面では Edit と表示される場合があります。

- Account: Cloudflare Tunnel Write（Cloudflare Tunnel:Edit）
- Zone `bonginkan.com`: Zone Read
- Zone `bonginkan.com`: DNS Write（DNS:Edit）

Account ID と API Token は `read -rs` の対話入力だけで受け取り、コマンド引数、history、evidence、リポジトリに残しません。HTTP Authorization header は mode 600 の一時 curl config から渡し、処理終了時に削除します。

Access Key ID は R2/S3互換API用で、Cloudflare Tunnel / DNS v4 API のBearer認証には使わないため未使用です。
