# Calendar Service

## Why

Users shouldn't have to manually send a bot to every meeting. Most meetings are already on their calendar with URLs attached. The calendar service watches for upcoming events and auto-schedules bots, turning Vexa from "tool you invoke" into "tool that acts on your behalf." Without it, every meeting requires a manual `POST /bots` call or a Telegram message.

## What

Syncs Google Calendar events and automatically schedules meeting bots to join upcoming calls. Runs a background sync loop that polls all connected users on a configurable interval.

## What

### Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/internal/webhooks/drive-export-completed` | Receive meeting-api's `drive_export.completed` hook and post the Drive link to Discord (HMAC required) |
| `POST` | `/calendar/connect` | Trigger initial sync after OAuth connection |
| `GET` | `/calendar/status` | Check if a user has a calendar connected |
| `GET` | `/calendar/events` | List upcoming calendar events for a user |
| `PUT` | `/calendar/preferences` | Set auto-join and lead time preferences |
| `DELETE` | `/calendar/disconnect` | Remove OAuth tokens and stop syncing |
| `GET` | `/health` | Health check |

All `/calendar/*` endpoints accept `user_id` as a query parameter.

### Drive export 完了 → Discord 通知

meeting-api が Drive への Markdown 議事録エクスポートを `done` にした直後、内部フック
`drive_export.completed` を `POST /internal/webhooks/drive-export-completed` へ送る。
この受信エンドポイントは:

1. `X-Webhook-Signature` / `X-Webhook-Timestamp`（`sha256=HMAC(secret, "<ts>." + body)`）を
   検証する。`KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET` が未設定なら 503（fail-closed）、
   署名不一致・タイムスタンプ期限切れは 401。`Authorization: Bearer` は判定に使わない。
2. `GET /guilds/{guild}/channels` を毎回呼び、text(0)/announcement(5) だけを候補にする
   （固定 allowlist は持たない）。
3. 候補一覧・会議タイトル・本文抜粋をモデルへ渡し、`{channel_id, confidence, reason}` の
   JSON を厳格パースする。
4. 投稿先を決める。`confidence >= KABOSU_DISCORD_ROUTER_CONFIDENCE_THRESHOLD` かつ候補内の
   id ならそのチャンネル、それ以外は default チャンネル
   （`model_unavailable` / `low_confidence` / `unknown_channel`）。
   選択先が 403/404 を返したら default へ 1 回だけ再投稿する（`selected_403` / `selected_404`）。
   default 投稿も失敗したら 502 を返し、送信側のリトライに委ねる。
5. 投稿は Drive リンクを含み `allowed_mentions.parse=[]` でメンションを抑止する。
   同じ `event_id` の再送は `meeting.data.discord_notify` を見て二重投稿しない。

Discord の 3 変数（token / guild / default channel）のいずれかが空なら、外部呼び出しを
一切せず `skipped` を返す。

| Variable | Default | Description |
|----------|---------|-------------|
| `KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET` | — | 受信 HMAC 秘密。空なら 503 |
| `KABOSU_WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS` | `300` | timestamp 許容差（秒） |
| `KABOSU_DISCORD_BOT_TOKEN` | — | Discord Bot token |
| `KABOSU_DISCORD_GUILD_ID` | — | 対象 Guild id |
| `KABOSU_DISCORD_DEFAULT_CHANNEL_ID` | — | フォールバック先チャンネル id |
| `KABOSU_DISCORD_API_BASE` | `https://discord.com/api/v10` | Discord API base |
| `KABOSU_DISCORD_TIMEOUT_SECONDS` | `10` | Discord HTTP timeout |
| `KABOSU_DISCORD_ROUTER_CONFIDENCE_THRESHOLD` | `0.85` | 採用する確信度の閾値 |
| `GROQ_API_KEY` / `GROQ_API_BASE` / `GROQ_MODEL` | — / `https://api.groq.com/openai/v1` / `openai/gpt-oss-20b` | 投稿先選択モデル |
| `KABOSU_DISCORD_ROUTER_TIMEOUT_SECONDS` | `8` | モデル呼び出し timeout |
| `KABOSU_DISCORD_ROUTER_CONTEXT_CHARS` | `3000` | モデルへ渡す本文の上限文字数 |

## How It Works

Two sync modes are supported:

1. `per_user` (legacy): users connect their Google Calendar via OAuth and the refresh token is stored in `users.data.google_calendar.oauth`.
2. `single_account`: the dedicated Kabosu account (`e@bonginkan.ai`) is synced from `KABOSU_GOOGLE_REFRESH_TOKEN`. Inviting that account to an event is enough to schedule Kabosu.

In `single_account` mode, upcoming events with meeting URLs are scheduled with:

- `bot_name=カボス`
- `language=ja`
- `voice_agent_enabled=false` by default, so calendar-created joins do not wake Kabosu unless `KABOSU_VOICE_AGENT_ENABLED=true` is set.

After bot creation, the service stores `meeting.data.calendar_event` so meeting-api can export only calendar-origin meetings to Google Drive after final transcription finishes.

## How

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Log level |
| `SYNC_INTERVAL_SECONDS` | `300` | Seconds between calendar sync cycles |
| `DATABASE_URL` | — | PostgreSQL connection string (via admin-models/meeting-api) |
| `KABOSU_CALENDAR_MODE` | `per_user` | Set `single_account` for invite-only Kabosu calendar sync |
| `KABOSU_CALENDAR_ACCOUNT_EMAIL` | — | Dedicated Kabosu Google account to invite to calendar events |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | — | OAuth client used to refresh the Kabosu Google token |
| `KABOSU_GOOGLE_REFRESH_TOKEN` | — | Refresh token with `calendar.events`, `calendar.readonly`, and `drive.file` scopes. `calendar.events` is needed when Kabosu/Codex creates test meetings; invite-only auto-join reads events. |
| `KABOSU_BOT_OWNER_USER_ID` | — | Vexa user ID that owns calendar-created bots; must match the wake orchestrator API key owner |
| `KABOSU_DRIVE_FOLDER_ID` | — | Drive folder used by meeting-api for Markdown transcript export |
| `BOT_API_TOKEN` | — | API token for the owner user; used to call meeting-api `/bots` |

### Run

```bash
cd services/calendar-service
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8050 --reload
```

Requires PostgreSQL with the shared database schema initialized.

In Compose:

```bash
docker compose --profile calendar up -d calendar-service
```

## DoD

| # | Check | Weight | Ceiling | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|--------|----------|--------------|-------|
| 1 | `GET /health` returns 200 | 15 | ceiling | untested | — | — | — |
| 2 | `POST /calendar/connect` triggers initial sync for user | 20 | ceiling | untested | — | — | — |
| 3 | `GET /calendar/events` returns upcoming events for connected user | 20 | — | untested | — | — | — |
| 4 | Background sync loop polls on `SYNC_INTERVAL_SECONDS` and schedules bots | 20 | ceiling | untested | — | — | — |
| 5 | `DATABASE_URL` set and PostgreSQL reachable | 15 | ceiling | untested | — | — | — |
| 6 | `DELETE /calendar/disconnect` removes OAuth tokens and stops syncing | 10 | — | untested | — | — | — |

Confidence: 0 (untested — experimental service, not in default compose, no tests3 checks)
