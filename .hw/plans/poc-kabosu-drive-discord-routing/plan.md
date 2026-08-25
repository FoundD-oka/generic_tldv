---
generated_by: fable
task_id: poc-kabosu-drive-discord-routing
base-commit: 67ea03210c2de4c8723780402d302948b138d939
size: M
runtime: inline
---

# PoC: Drive議事録エクスポート完了 → Discordチャンネル自動選択通知(カボス)

## ゴール

文字通りの依頼: Drive への Markdown 議事録エクスポート完了後、共有リンクを
`bonginkan.ai` ドメイン閲覧可にし、Discord Guild の現在チャンネル一覧からモデルが
選んだチャンネル(confidence ≥ 0.85)へリンク付きで投稿する。失敗系は default
channel へフォールバック。同一 `event_id` の再送で二重投稿しない。

reframe(合意済み要求は変えない。経路の選択だけを現物で修正):
「calendar-service の bot 作成ヘッダに Webhook URL/secret を付け、per-meeting
`webhook_url` 経路で受信する」案は **成立しない**。`webhooks.py` の全送信経路は
`validate_webhook_url()` を通し、これは Docker サービス名と private IP
(172.16/12 等)を SSRF 対策で拒否する(`webhook_url.py:14-50, 86-137`)。
compose 内の `http://calendar-service:8050` は private IP に解決されるので黙って
捨てられる。代わりに、既存の **内部フック経路**(`post_meeting.py::fire_post_meeting_hooks`
が使う `outbound_events` ledger + `deliver_with_result`、URL 検証なし・operator 設定)
を `drive_export.completed` に横展開する。envelope/HMAC は同一実装
(`build_envelope` / `build_headers`)なので要求 8 は満たす。bot 作成ヘッダ
(`X-User-Webhook-*`)は触らない。

達成する成果(合意済み要求 1〜10 をそのまま採用):
- 通知は `drive_export.status=done` 確定後のみ。`meeting.completed` は使わない。
- Drive ファイルに `type=domain, role=reader, allowFileDiscovery=false` を付与。
- Discord チャンネルは毎回 API 取得。固定 allowlist なし。
- モデル出力は `{channel_id, confidence, reason}` の厳格 JSON。閾値 0.85。
- 全失敗系は default channel。投稿は Drive リンク含み `allowed_mentions.parse=[]`。
- 再送 `event_id` は Discord へ二重投稿しない。
- config 未設定時は既存 Drive export / calendar sync の挙動不変。`.env`・本番資材は不変。

## How(実装者向け)

変更対象は 9 ファイル以内(`.hw/plans/<task-id>/` の計画成果物は数えない)。
製品コード: meeting-api 1 + calendar-service 3、テスト 2、配備定義 2、README 1。新規依存なし(httpx / fastapi / sqlalchemy は既存)。

### 0. 事前・事後の GitNexus

- 実装前: `impact({target: "run_drive_export", direction: "upstream"})` と
  `impact({target: "upload_markdown_to_drive", direction: "upstream"})` を実行し
  blast radius を記録する(既知の呼び出し元: `sweeps.py:935` の
  `_sweep_drive_export_jobs` のみ。HIGH/CRITICAL なら停止して報告)。
  MCP 不可の場合は Grep の呼び出し元一覧(`grep -rn "run_drive_export\|upload_markdown_to_drive" services`)を
  代替証拠として plan ディレクトリ外の PR 本文に残す。
- commit 前: `detect_changes()` を実行し、影響が上記関数と新規シンボルに限られることを確認。

### 1. meeting-api: `services/meeting-api/meeting_api/drive_export.py`

**1-a. ドメイン閲覧権限**

- 定数 `DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"` を追加。
- 新関数 `async def grant_domain_reader_permission(file_id: str, *, access_token: Optional[str] = None) -> Dict[str, Any]`
  - `domain = os.getenv("KABOSU_DRIVE_SHARE_DOMAIN", "").strip()`。空なら
    `{"skipped": True}` を返す(呼び出し側で no-op)。
  - `access_token` が None なら `refresh_google_access_token()`。
  - `POST {DRIVE_FILES_URL}/{file_id}/permissions` params
    `{"supportsAllDrives": "true", "fields": "id,type,role,domain"}`、
    JSON body `{"type": "domain", "role": "reader", "domain": domain, "allowFileDiscovery": False}`。
    timeout は `KABOSU_DRIVE_UPLOAD_TIMEOUT_SECONDS`(既存、既定 60)。
  - `status >= 400` → `DriveExportError(f"Drive permission failed: {code} {text[:300]}", retryable=_retryable_google_status(code))`。
  - 成功時 `resp.json()` を返す。
- `run_drive_export` の `upload = await upload_markdown_to_drive(...)` 直後(同じ try 内)に:
  ```
  permission = dict(current.get("domain_permission") or {})
  if os.getenv("KABOSU_DRIVE_SHARE_DOMAIN", "").strip() and not permission.get("permission_id"):
      granted = await grant_domain_reader_permission(upload["id"])
      permission = {"domain": ..., "permission_id": granted.get("id"), "granted_at": _utcnow_iso()}
  ```
  - 権限付与の失敗は既存の `except DriveExportError` に落ちる。**そのとき
    `_set_drive_export_state(... status="failed", ...)` に `file_id=upload.get("id")`
    を追加して保存する**(次回リトライが同じファイルを PATCH し、重複ファイルを
    作らないため)。`upload` 変数はアップロード成功後にだけ束縛されるので、
    `upload_file_id = None` を try の前で初期化し、アップロード成功直後に代入する。
  - `done` 状態と `rerun_requested→queued` 状態の両方に `domain_permission=permission`
    を書く(再エクスポートで二重付与しない)。
  - 環境変数未設定なら権限ステップは完全にスキップ(既存挙動不変)。

**1-b. 完了フック送信**

- 新関数 `async def send_drive_export_completed_hook(meeting_id: int, db: AsyncSession, *, file_id, web_view_link, filename, content, calendar_event) -> Optional[str]`
  - `url = os.getenv("KABOSU_DRIVE_EXPORT_WEBHOOK_URL", "").strip()`、
    `secret = os.getenv("KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET", "").strip()`。url 空なら
    `None` を返し何もしない。`validate_webhook_url` は**呼ばない**(operator 設定の内部宛先)。
  - `from .outbound_events import claim_outbound_event, event_key, mark_outbound_event`、
    `from .webhook_delivery import build_envelope, deliver_with_result`。
  - `key = event_key("drive_export_hooks", "drive_export.completed", meeting_id, url)`
    を `event_id` として `build_envelope("drive_export.completed", data, event_id=key)`。
    → 同一 meeting/file の再送・再エクスポートで event_id が決定的になる。
  - `data`:
    ```
    {"meeting": {"id", "platform", "native_meeting_id", "status", "start_time", "end_time"(iso|None)},
     "calendar_event": calendar_event(dict、無ければ {}),
     "title": calendar_event.title or native_meeting_id or f"meeting-{id}",
     "drive_export": {"file_id", "web_view_link", "filename", "completed_at": _utcnow_iso()},
     "context_excerpt": content[:int(os.getenv("KABOSU_DRIVE_EXPORT_CONTEXT_CHARS", "3000"))]}
    ```
  - `claim_outbound_event(db, meeting_id=..., channel="drive_export_hooks", event_type="drive_export.completed", destination=url, payload=payload)`
    → `should_deliver` が False なら送らずログ(`already <status>`)。
  - `deliver_with_result(url=url, payload=payload, webhook_secret=secret or None, timeout=30.0, label=f"drive-export-hook meeting={meeting_id}", metadata={"meeting_id": meeting_id, "outbound_event_key": key})`
    → `mark_outbound_event(db, meeting_id=..., key=key, status=result.status, attempts=int(ledger_event.get("attempts") or 0)+1, error=result.error, status_code=...)`。
  - 戻り値は `result.status`。
- `run_drive_export` の `done` 分岐で `await db.commit()` の**後**に
  `try: await send_drive_export_completed_hook(...) except Exception: logger.warning(...)` を
  呼ぶ。`queued`(rerun_requested)分岐では呼ばない。フック失敗で export 結果は変えない。

### 2. calendar-service: 新規 `services/calendar-service/app/discord_notify.py`

環境変数(すべて `os.getenv` で関数呼び出し時に読む。テストの monkeypatch のため
モジュール定数にしない):

| 変数 | 意味 | 既定 |
|---|---|---|
| `KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET` | 受信 HMAC 秘密 | 空=受信拒否(503) |
| `KABOSU_WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS` | timestamp 許容差 | 300 |
| `KABOSU_DISCORD_BOT_TOKEN` | Bot token | 空=skipped |
| `KABOSU_DISCORD_GUILD_ID` | Guild id | 空=skipped |
| `KABOSU_DISCORD_DEFAULT_CHANNEL_ID` | fallback 先 | 空=skipped |
| `KABOSU_DISCORD_API_BASE` | Discord API base | `https://discord.com/api/v10` |
| `KABOSU_DISCORD_TIMEOUT_SECONDS` | Discord HTTP timeout | 10 |
| `KABOSU_DISCORD_ROUTER_CONFIDENCE_THRESHOLD` | 閾値 | 0.85 |
| `GROQ_API_KEY` / `GROQ_API_BASE` / `GROQ_MODEL` | wake と同名 | `https://api.groq.com/openai/v1` / `openai/gpt-oss-20b` |
| `KABOSU_DISCORD_ROUTER_TIMEOUT_SECONDS` | モデル timeout | 8 |
| `KABOSU_DISCORD_ROUTER_CONTEXT_CHARS` | モデルへ渡す本文上限 | 3000 |

公開関数(純関数を優先し、I/O は httpx.AsyncClient を引数注入できる形にする):

- `verify_webhook_signature(raw_body: bytes, signature_header: str | None, timestamp_header: str | None, secret: str, *, now: float | None = None, tolerance: int) -> bool`
  - `hmac.compare_digest("sha256=" + HMAC_SHA256(secret, f"{ts}.".encode() + raw_body), signature)`。
    ts が int でない / `abs(now - ts) > tolerance` / header 欠落 → False。
    送信側 `webhook_delivery.build_headers` と同一アルゴリズム。
- `candidate_channels(channels: list[dict]) -> list[dict]`
  - `type in {0, 5}`(GUILD_TEXT / GUILD_ANNOUNCEMENT)のみ。各要素
    `{"id": str, "name": str, "topic": str|"" , "category": <parent_id を type 4 の name に解決、無ければ "">}`。
    `position` 昇順。threads は API が返さないので考慮不要。
- `parse_model_selection(text: str) -> dict | None`
  - `json.loads` 失敗 → None。dict でない / `channel_id` が str でない(int は str 化して許容) /
    `confidence` が数値でない or 範囲 [0,1] 外 / `reason` が str でない → None。
    余分なキーは無視。返却 `{"channel_id": str, "confidence": float, "reason": str}`。
- `resolve_target(selection: dict | None, candidates: list[dict], default_channel_id: str, threshold: float) -> tuple[str, str | None]`
  - selection None → `(default, "model_unavailable")`;
    `confidence < threshold` → `(default, "low_confidence")`;
    `channel_id` が candidates の id 集合に無い → `(default, "unknown_channel")`;
    それ以外 → `(channel_id, None)`。
- `build_message(title: str, web_view_link: str, calendar_event: dict, reason: str | None) -> dict`
  - `content` は日本語で「カボス議事録: {title}」「日時」「{web_view_link}」を含む。
    2000 文字以内に切る。返却 `{"content": ..., "allowed_mentions": {"parse": []}}`。
- `async def select_channel_with_model(title: str, context: str, candidates: list[dict], *, client: httpx.AsyncClient) -> dict | None`
  - `POST {GROQ_API_BASE}/chat/completions` に `{"model", "temperature": 0, "stream": False, "reasoning_format": "hidden", "max_completion_tokens": 256, "messages": [system, user]}`。
    **`response_format` は渡さない**(RF-003 実測: `openai/gpt-oss-20b` では reasoning が枠を使い切り HTTP 400 `json_validate_failed`)。JSON 強制は strict system prompt と `parse_model_selection` の厳格 parse で担保する。
    system: 「候補チャンネルから 1 つ選び `{"channel_id","confidence","reason"}` の JSON のみ返す。候補外 id を返さない」。
    user: title / `context[:CONTEXT_CHARS]` / 候補の `id/name/topic/category` を JSON で列挙。
  - `httpx.TimeoutException` / `httpx.HTTPError` / `raise_for_status` 失敗 / 応答構造不正 → None(ログ warning)。
    それ以外は `parse_model_selection(choices[0].message.content)`。
- `class DiscordClient` (httpx.AsyncClient 注入、`Authorization: Bot <token>`)
  - `async list_guild_channels(guild_id) -> list[dict]`: `GET /guilds/{id}/channels`。非 2xx は `DiscordHTTPError(status_code, body)`。
  - `async create_message(channel_id, message: dict) -> dict`: `POST /channels/{id}/messages`。非 2xx は `DiscordHTTPError`。
- `async def handle_drive_export_completed(db: AsyncSession, envelope: dict, *, http_client_factory=httpx.AsyncClient) -> dict`
  1. Discord 3 変数のいずれか空 → `{"status": "skipped", "reason": "discord_not_configured"}`(Discord・Groq を呼ばない)。
  2. `event_id = envelope["event_id"]`、`meeting_id = envelope["data"]["meeting"]["id"]`。
     `select(Meeting).where(id==meeting_id).with_for_update()`。無ければ `{"status": "skipped", "reason": "meeting_not_found"}`。
  3. `notify = dict(meeting.data.get("discord_notify") or {})`。
     `notify.get("event_id") == event_id and notify.get("status") == "posted"` → `{"status": "duplicate", "channel_id": notify["channel_id"]}`(Discord 呼ばない)。
  4. `channels = await discord.list_guild_channels(guild)` → `candidates`。
     `selection = await select_channel_with_model(...)`;
     `target, fallback_reason = resolve_target(selection, candidates, default, threshold)`。
  5. `message = build_message(...)`。`create_message(target, message)`。
     `DiscordHTTPError` で status ∈ {403, 404} かつ `target != default` →
     `fallback_reason = f"selected_{status}"` として default へ再投稿(1 回だけ)。
     default 投稿が失敗(いかなる HTTP エラー / timeout)→ `DiscordDeliveryError` を raise
     (route は 502 を返し、meeting-api 側の with_retry / Redis retry に委ねる。
     `discord_notify` は書かない)。
  6. 成功: `meeting.data["discord_notify"] = {"event_id", "status": "posted", "channel_id": target, "message_id": resp["id"], "selected_channel_id": selection and selection["channel_id"], "confidence": selection and selection["confidence"], "model_reason": selection and selection["reason"], "fallback_reason", "posted_at": iso}`、
     `attributes.flag_modified(meeting, "data")`、`await db.commit()`。
     返却 `{"status": "posted", "channel_id": target, "fallback_reason": fallback_reason}`。

### 3. calendar-service: `services/calendar-service/app/main.py`

- `POST /internal/webhooks/drive-export-completed`
  - `raw = await request.body()`。`secret` 空 → `HTTPException(503, "webhook secret not configured")`。
  - `verify_webhook_signature(raw, request.headers.get("X-Webhook-Signature"), request.headers.get("X-Webhook-Timestamp"), secret, tolerance=...)` False → `HTTPException(401, "invalid signature")`。
    `Authorization: Bearer` ヘッダは**判定に使わない**。
  - `json.loads(raw)` 失敗 / `event_type != "drive_export.completed"` / `data.meeting.id` 欠落 → 400。
  - `handle_drive_export_completed(db, envelope)` を await。`DiscordDeliveryError` → `HTTPException(502)`。
  - 200 でハンドラの dict を返す。
- 既存ルート・`sync_loop` は変更しない。

### 4. 配備定義(`.env` は触らない)

- `deploy/compose/docker-compose.yml`
  - `meeting-api.environment` に追加:
    `KABOSU_DRIVE_SHARE_DOMAIN=${KABOSU_DRIVE_SHARE_DOMAIN:-}`、
    `KABOSU_DRIVE_EXPORT_WEBHOOK_URL=${KABOSU_DRIVE_EXPORT_WEBHOOK_URL:-}`、
    `KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET=${KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET:-}`。
  - `calendar-service.environment` に追加:
    `KABOSU_DRIVE_EXPORT_WEBHOOK_SECRET`、`KABOSU_DISCORD_BOT_TOKEN`、`KABOSU_DISCORD_GUILD_ID`、
    `KABOSU_DISCORD_DEFAULT_CHANNEL_ID`、`KABOSU_DISCORD_ROUTER_CONFIDENCE_THRESHOLD`(既定 0.85)、
    `GROQ_API_KEY=${GROQ_API_KEY:-}`、`GROQ_MODEL=${GROQ_MODEL:-openai/gpt-oss-20b}`。
  - 既定値はすべて空(=機能 OFF)。`KABOSU_DRIVE_EXPORT_WEBHOOK_URL` の推奨値は
    env-example のコメントに `http://calendar-service:8050/internal/webhooks/drive-export-completed` として記載。
- `deploy/env-example` の Kabosu Drive 節に上記キーを空値+日本語コメントで追加
  (`KABOSU_DRIVE_SHARE_DOMAIN=bonginkan.ai` は例示値として書いてよい。token 類は空)。
- `services/calendar-service/README.md` に「Drive export 完了 → Discord 通知」の節
  (受信エンドポイント、必要 env、フォールバック規則)を追加。

### 5. テスト

- `services/meeting-api/tests/test_drive_export.py` に追加(既存テストは削除・変更しない。
  既存 `test_run_drive_export_uploads_markdown_and_marks_done` は env 未設定のまま通ること):
  - 権限付与: `KABOSU_DRIVE_SHARE_DOMAIN=bonginkan.ai` で `grant_domain_reader_permission` が
    `.../files/drive-file-1/permissions` に `supportsAllDrives=true` と
    body `{type: domain, role: reader, domain: bonginkan.ai, allowFileDiscovery: False}` を POST する。
  - 権限失敗(500)→ `run_drive_export` が `DriveExportError` を raise、状態 `failed` かつ
    `file_id` 保存、`retryable=True`。フックは送られない。
  - 権限成功→ `done` 状態に `domain_permission.permission_id` が入り、2 回目(再エクスポート)では
    permission API を呼ばない。
  - フック: `KABOSU_DRIVE_EXPORT_WEBHOOK_URL` 設定時に `deliver_with_result` が
    `event_type="drive_export.completed"`、`event_id == event_key(...)`(決定的)、
    `data.drive_export.web_view_link` 入りで呼ばれる。未設定時は呼ばれない。
    `claim_outbound_event` が `should_deliver=False` なら `deliver_with_result` は呼ばれない。
  - `data["meeting"]` の必須キーが存在する。
- `services/calendar-service/tests/test_discord_notify.py`(新規、`@pytest.mark.asyncio` を明示):
  - 署名: 正しい署名 True / 改竄 body False / ts が tolerance 超 False / header 欠落 False。
    送信側と同じ計算式で作った署名を使う(`f"{ts}.".encode() + body`)。
  - `candidate_channels`: type 0/5 のみ、category 解決、threads/voice/category 自体は除外。
  - `parse_model_selection`: 正常 / invalid JSON / confidence 範囲外 / channel_id 欠落 → None。
  - `resolve_target`: 0.9+既知 → 選択、0.84 → default `low_confidence`、未知 id → `unknown_channel`、None → `model_unavailable`。
  - `select_channel_with_model`: `httpx.TimeoutException` を投げるクライアントで None。
  - `handle_drive_export_completed`(FakeDiscord / Fake Groq を注入):
    - 正常 → 選択チャンネルへ 1 回 POST、`allowed_mentions == {"parse": []}`、content に web_view_link、
      `meeting.data.discord_notify.event_id` 記録、`db.commit` 1 回。
    - 選択先 403 → default へ投稿、`fallback_reason == "selected_403"`。404 も同様。
    - default 投稿失敗 → `DiscordDeliveryError`、`discord_notify` 未記録、commit 0 回。
    - 同じ `event_id` 再送(既に posted)→ Discord `create_message` 呼び出し 0 回、`status == "duplicate"`。
    - Discord env 未設定 → `skipped`、外部呼び出し 0 回。
  - route(`httpx.AsyncClient(transport=ASGITransport(app))`、startup は走らせない):
    署名不正 → 401、secret 未設定 → 503、`event_type` 不一致 → 400、正常 → 200
    (`handle_drive_export_completed` と `get_db` は monkeypatch / dependency_overrides)。

### 6. ローカル実行手順(実装者)

```
python3.11 -m venv /tmp/kabosu-venv && . /tmp/kabosu-venv/bin/activate
pip install -e libs/admin-models/ -e services/meeting-api/ -r services/calendar-service/requirements.txt \
    pytest pytest-asyncio httpx psycopg2-binary
pytest services/meeting-api/tests/test_drive_export.py services/meeting-api/tests/test_post_meeting_idempotency.py services/meeting-api/tests/test_webhooks.py -q
pytest services/meeting-api/tests/ -q --ignore=services/meeting-api/tests/test_integration_live.py
(cd services/calendar-service && PYTHONPATH=. pytest tests -q)
docker compose -f deploy/compose/docker-compose.yml --profile calendar config -q
bash .hw/verify.sh
```
テストの削除・skip・期待値緩和は禁止。

## 検証契約

`verification-contract.md` を参照。すべて通過が完了条件。

## リサーチ記録(外部 API・ツール挙動への依存)

| 仮説 | 反証探索 | 確信度 | 覆る条件 |
|---|---|---|---|
| Discord `GET /guilds/{id}/channels` は threads を含まず、text(0)/announcement(5) の絞り込みで投稿候補が得られる | 公式 docs(2026-08-26 確認、依頼文所与) | 高 | 新 channel type の追加。→ 既知 type のみ許容するので新 type は自動除外(安全側) |
| Create Message は `allowed_mentions.parse=[]` で全メンション抑止 | 公式 docs 所与 | 高 | — |
| Drive `permissions.create` で `type=domain, role=reader, allowFileDiscovery=false` は My Drive / Shared Drive 双方で `supportsAllDrives=true` 付きで動く | 公式 docs 所与。Shared Drive では組織ポリシーで domain 共有が禁止され 403 になり得る(未検証) | 中 | Workspace 管理ポリシーで外部/ドメイン共有制限 → 403 は retryable 扱いで sweep が再試行し続ける。運用で `KABOSU_DRIVE_SHARE_DOMAIN` を空にすれば通知経路を止めずに回避可能(権限ステップだけスキップ) |
| Groq OpenAI 互換 API は `response_format={"type":"json_object"}` を `openai/gpt-oss-20b` で受け付ける | **実測済み(2026-08-26、覆った)**: `response_format=json_object` + `max_completion_tokens=256` で reasoning が枠を使い切り HTTP 400 `json_validate_failed`。`response_format` を外すと同 256 tokens で `{"channel_id":"222","confidence":0.95,...}` を取得 | 高(実測) | 覆る条件どおり `response_format` **不採用**。strict system prompt + `parse_model_selection` の厳格 parse + default fallback で JSON 契約を担保(AT-004/005 不変)。モデルが JSON 以外を返す場合は parse None → default へフォールバックし機能は止まらない |
| `validate_webhook_url` は compose 内部宛先を拒否する | `webhook_url.py` 現物確認(private IP 全拒否・Docker 名ブロック) | 高 | — (そのため内部フック経路を採用) |

## Why(実装者に渡さない)

- **なぜ per-meeting webhook_url ではなく内部フックか**: 上記のとおり SSRF 検証で
  内部宛先が拒否される。さらに bot 作成ヘッダ方式は `meeting.completed` 等の他イベントも
  同じ URL へ流し込み、受信側で無関係イベントを弾く分岐が増える。内部フックは
  operator 設定・宛先固定・ledger 付きで、`post_meeting_hooks` に既存パターンがある。
- **なぜ event_id を決定的(event_key)にするか**: 要求 7 は「同じ event_id の再送で
  二重投稿しない」。ランダム id だと meeting-api 側の再実行(話者名修正での再エクスポート、
  sweep の再走)で別 id になり、受信側 dedupe が効かず同じリンクを何度も投稿する。
  `outbound_events` ledger と同じ key を使えば送信側も受信側も同じ鍵で冪等になる。
  副作用として「再エクスポートでは再通知しない」— リンクは同一ファイルで不変なので PoC では妥当。
- **なぜ権限失敗で export を failed にするか**: 要求 2 は「社員がリンク閲覧できる」こと。
  権限なしのリンクを通知しても要件を満たさない。failed(retryable)にすれば既存 sweep が
  backoff 付きで再試行し、権限が付くまで通知しない。`file_id` を failed 状態に保存する
  改修は、再試行で重複ファイルを作らないために必須。
- **なぜ default 失敗を 502 にするか**: 受信側で握りつぶすと通知が静かに消える。5xx を
  返せば送信側の `with_retry` と Redis retry queue が既存機構として面倒を見る。
  `discord_notify` を書かないので再送時に重複扱いされず投稿できる。
- **なぜ calendar-service が受信側か**: Kabosu 専用 profile で稼働中、meeting_api.models を
  既に import、Meeting.data を書く実績あり、Google/Kabosu の env が既に集約されている。
  agent-api は compose で NO-SHIP のため採用しない。
- **なぜ context_excerpt を payload に載せるか**: 受信側での transcript 再読込を省き、
  モデルへ渡す bounded context を送信側で確定させる。ledger にも payload が保存されるため
  上限 3000 文字で JSONB 膨張を抑える。
- **公開ポート注意**: calendar-service は compose で `8050` をホスト公開している。受信
  エンドポイントは HMAC 必須・secret 未設定時 503(fail-closed)にしてあるのはそのため。
- **Fable レビュー観点**: 契約に列挙した失敗系が「テストで落ちる形」で書かれているか、
  既存 drive export テストが無改変で通るか、`.env`/本番資材が差分に含まれないか。
