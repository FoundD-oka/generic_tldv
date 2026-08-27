---
generated_by: fable
task_id: fix-dashboard-auth-redirect-loop
base_commit: db823063f083bc66cfb6f95ac1c7e3bd9d92298c
created: 2026-08-15
---

# Kabosu Dashboard `/login`↔`/meetings` リダイレクトループの恒久修正

## 依頼の文字通りの内容

Docker 再起動後、既存ブラウザで kabosu-dashboard (3002) が `/login` と `/meetings`
を往復し続けるのを直し、稼働環境へ反映する。

## 再設計したゴール(reframe)

文字通りの「ループを止める」だけでは、ブラウザデータ消去と同じ場当たり対応に
なり得る。真のゴールは **「未検証のクライアント側 auth state と、失敗を考慮しない
無条件 redirect という2つの構造欠陥を除去し、shared-login の成功/失敗/legacy state
のどの組合せでも UI が決定的な終端状態(認証済み or 静止エラー)に収束すること」**。
ユーザーは「再発しないコード修正と稼働反映」を明示要求しており、この reframe は
要求と整合する(クライアント合意済み要求の変更ではない)。

## Why(実装者に渡さない)

### ループの構造(ソース実読で確認済み)

1. `stores/auth-store.ts` は `vexa-auth` localStorage に
   `user/token/isAuthenticated` を永続化する。rehydrate 直後、サーバー検証前から
   `isAuthenticated=true` になり得る。
2. `app/login/page.tsx` の effect は (a) ローカル `isAuthenticated` だけを信じて
   `router.push("/")`、(b) `/api/config` の `sharedAuth.enabled=true` なら認証成功を
   確認せず `router.replace("/meetings")`。
3. `components/auth/auth-provider.tsx` は保護ページで認証失敗時に shared login を
   1回試み、それも失敗すると `router.push("/login")`。
4. よって shared-login 失敗・401・ネットワーク失敗・古い永続 state のとき、
   `/login` と `/meetings` の無限循環が成立する。
5. public route (`/login`) では AuthProvider の `checkAuth()` が走らないため、古い
   `vexa-auth` の `isAuthenticated=true` が検証されずに残り続ける。

### 反証の検討

- shared-login API の恒常故障ではない。現時点で POST は 200 を返し、新規ブラウザでは
  `/meetings` に安定到達する。
- サーバー側 cookie 名の不整合でもない。compose 定義で kabosu-dashboard は専用 cookie 名を
  持ち、新規セッションで動作している。
- 残る本命は「古い永続 auth state + 一時的 shared-login 失敗」で、構造がそれを無限ループへ
  増幅している。

### RF-04A (`d2c2113`) の扱い

cookie-only 化 + legacy artifact 掃除の完全版だが 23ファイル/834行で、`token` をストアから
完全排除すると `useAuthStore` 参照 14ファイルに波及する。今回は最小適合として
(a) `isAuthenticated` の非永続化、(b) persist version bump + migrate、
(c) legacy auth key 掃除、(d) source-assertion 型テストのみを移植する。
cookie-only 完全移行は別タスク。

### デプロイ制約の理由

`make -C deploy/compose up` は既知の guard 403 誤判定で無関係サービスを `latest` へ
差し替える事故歴があるため禁止。`dashboard` と `kabosu-dashboard` は同一 image を共有し、
IMAGE_TAG はシェル環境変数で上書きして `.env` は触らない。

### GitNexus

CLI 無応答・MCP 未接続のため影響範囲は Grep で裏取り済み。編集直前の impact 実行を
検証契約 (AT-101) に入れる。

## How(実装手順 — Opus 5 implementer向け)

対象は `services/dashboard/` のみ。作業前に `git rev-parse HEAD` が
`db823063f083bc66cfb6f95ac1c7e3bd9d92298c` であることを確認する。

### Step 0: GitNexus impact(契約 AT-101)

`node .gitnexus/run.cjs` 経由で `useAuthStore` / `AuthProvider` / `LoginPage` の upstream impact
を実行し、出力を `evidence/impact.txt` へ保存。CLI が無応答の場合はその旨と Grep 裏取り結果を
同ファイルに記録し「概算」と明記。HIGH/CRITICAL が出た場合は作業を止めて報告。

### Step 1: `src/stores/auth-store.ts`

1. persist 設定を `version: 2` にし、`migrate` を追加。旧 payload(version 0/1)からは
   `user` と `didLogout` のみ引き継ぎ、`isAuthenticated` と `token` は破棄する。
2. `partialize` から `isAuthenticated` と `token` を外す。`token` はメモリ上では維持し、
   既存参照 API を変えない。
3. `AuthState` に `authError: "none" | "shared_login_failed" | "network" | "unauthorized"`
   を追加。`checkAuth` のネットワーク失敗時はローカル user/token があっても認証済みにせず、
   `isAuthenticated:false, isLoading:false, authError:"network"` にする。401 は全クリア +
   `authError:"unauthorized"`、成功時は `authError:"none"`。
4. `signInSharedDashboard` 失敗時は `authError:"shared_login_failed"`。
5. hydrate 前に legacy キー6種を localStorage/sessionStorage から削除する
   `removeLegacyBrowserAuthState()` を export。`vexa-auth` 本体は migrate が処理する。

### Step 2: `src/app/login/page.tsx`

1. `sharedAuth.enabled` の無条件 `/meetings` redirect を廃止し、`signInSharedDashboard()` を
   マウントごとに1回だけ実行。成功時だけ `router.replace("/meetings")`、失敗時は静止する。
2. 失敗時は日本語エラー + 「再試行」ボタンを表示。再試行は手動のみで、成功時だけ遷移。
3. hosted mode 分岐は維持する。

### Step 3: `src/components/auth/auth-provider.tsx`

1. shared login 失敗後の `/login` redirect は1回だけと ref で保証する。
2. `authError === "network"` のときは redirect せず、その場で日本語エラー +
   `checkAuth()` の再試行ボタンを表示する。
3. マウント時に `removeLegacyBrowserAuthState()` を1回呼ぶ。

### Step 4: 回帰テスト

新規 `test_auth_redirect_loop.test.ts` と `test_login_shared_auth.test.tsx` を追加し、最低限:

- 旧 `vexa-auth` seed後は `isAuthenticated===false`、token 非永続。
- legacy キー6種が掃除される。
- `/api/auth/me` 401 と fetch reject が非認証の終端状態になる。
- shared-login 200 のみ `/meetings` へ1回遷移。
- 500/network reject は遷移0回、エラーUIと再試行表示。再試行成功時のみ遷移。
- AuthProviderは shared-login失敗時 `/login` へ1回だけ、network時は遷移せずエラーUI。
- shared-login APIの成功 contract を維持。

既存テストの削除・skip・期待値緩和は禁止。

### Step 5: ローカル検証と commit

1. `cd services/dashboard && npm test`
2. `npm run build`
3. リポジトリルートで `bash .hw/verify.sh`
4. GitNexus `detect_changes` を実行・記録し、対象範囲内を確認。
5. 日本語commit。dirty treeで次工程へ進まない。

### Step 6: 稼働反映(`make -C deploy/compose up` 禁止)

一意タグ `fix-auth-loop-<timestamp>-<shortsha>` で dashboard image を1回buildし、
`dashboard` と `kabosu-dashboard` の2サービスだけを
`--no-deps --pull never --force-recreate` で更新する。前後の `docker ps`、`.env` hash、
volume一覧を保存し、2サービス以外が不変であることを確認する。

### Step 7: 反映後検証

1. 3001/3002 `/api/health` が200。3001 sharedAuth=false、3002=true。
2. 3002 shared-loginが200でuser/tokenを含む。
3. cookie付き `:3002/api/vexa/meetings` が200。
4. ブラウザURL時間サンプリング(1秒×60秒):新規、legacy state、shared-login 500固定の3ケース。
   failureケースはredirect 0回で日本語エラー + 再試行を表示する。
5. commit済みclean treeでFable reviewを実行し、READY後にpr-ready-gateを通す。

### 証跡置き場

`.hw/plans/fix-dashboard-auth-redirect-loop/evidence/` に impact / detect_changes / tests /
deploy snapshots / health checks / URL sampling / screenshots を保存する。
