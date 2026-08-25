# Verification Contract — poc-kabosu-drive-discord-routing

前提: すべて commit 済み clean tree で実行。テストの削除・skip・期待値緩和は違反。
証拠はコマンド出力(exit code と該当行)を PR 本文または `.hw/plans/<task>/evidence/` に貼る。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | 通知フックは `drive_export.status=done` 確定(commit 後)にのみ送られ、`event_type="drive_export.completed"`、`data.drive_export.web_view_link` を含む。`rerun_requested→queued` 分岐では送らない | unit: `services/meeting-api/tests/test_drive_export.py` に `deliver_with_result` を patch したテスト(done 分岐で 1 回、queued 分岐で 0 回) | pytest 出力(該当テスト名 PASSED) |
| AT-002 | `KABOSU_DRIVE_SHARE_DOMAIN` 設定時、新規 Drive ファイルに `POST /drive/v3/files/{id}/permissions?supportsAllDrives=true` body `{type:domain, role:reader, domain:<env>, allowFileDiscovery:false}` を 1 回発行し、`drive_export.domain_permission.permission_id` を保存。2 回目の export では発行しない | unit: httpx.AsyncClient を patch し呼び出し URL/params/json を assert | pytest 出力 |
| AT-003 | 受信側は毎回 `GET /guilds/{guild}/channels` を呼び、type 0/5 のみを候補化し `id/name/topic/category` を持つ。固定 allowlist 定数がコードに存在しない | unit: `candidate_channels` テスト + `grep -n "allowlist\|ALLOWED_CHANNELS" services/calendar-service/app/discord_notify.py` が 0 件 | pytest 出力 + grep 出力 |
| AT-004 | モデル呼び出しは title・`context_excerpt`・候補一覧を渡し、応答は `{channel_id, confidence, reason}` に厳格パースされる(欠落/型不一致/範囲外は None) | unit: `parse_model_selection` 4 ケース + `select_channel_with_model` の request body に title/候補 id が含まれる assert | pytest 出力 |
| AT-005 | `confidence >= 0.85` かつ候補内 id → そのチャンネルへ投稿。それ以外(low_confidence / unknown_channel / model_unavailable)→ default | unit: `resolve_target` 境界(0.85 採用・0.849 default) + handler で選択チャンネルに `create_message` 1 回 | pytest 出力 |
| AT-006 | 投稿 body に `web_view_link` を含み `allowed_mentions == {"parse": []}` | unit: FakeDiscord が受けた message を assert | pytest 出力 |
| AT-007 | 同一 `event_id` の再送は `create_message` 0 回、`status="duplicate"` | unit: `meeting.data.discord_notify.event_id` 既存で handler 呼び出し | pytest 出力 |
| AT-008 | 送信側 envelope は `build_envelope`、署名は `build_headers`(`sha256=HMAC(secret, ts.body)`)を使い、受信側は同一式で検証し不一致/ts 期限切れ/欠落を 401 で拒否 | unit: `verify_webhook_signature` 4 ケース + route 401 テスト。送信側は `services/meeting-api/tests/contracts` が無改変で通る | pytest 出力 |
| AT-009 | `event_id` は `event_key("drive_export_hooks","drive_export.completed",meeting_id,url)` で決定的。送信側 ledger が delivered/queued/pending なら再送しない | unit: 2 回呼んで `deliver_with_result` 1 回、`event_id` が一致 | pytest 出力 |
| AT-010 | config 未設定(`KABOSU_DRIVE_SHARE_DOMAIN`・`KABOSU_DRIVE_EXPORT_WEBHOOK_URL` 空)で既存 drive export テストが無改変で全通過し、permission/hook の外部呼び出しが 0 回 | `pytest services/meeting-api/tests/test_drive_export.py -q` + `git diff base-commit..HEAD -- services/meeting-api/tests/test_drive_export.py` に削除行(既存 assert の削除)が無い | pytest 出力 + diff |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | モデル timeout(`httpx.TimeoutException`)→ None → default へ投稿、例外を外へ出さない | unit | pytest 出力 |
| FP-002 | モデル invalid JSON / スキーマ不一致 → default(`fallback_reason="model_unavailable"`) | unit | pytest 出力 |
| FP-003 | 未知 channel_id → default(`unknown_channel`) | unit | pytest 出力 |
| FP-004 | 選択先 403 / 404 → default へ 1 回だけ再投稿、`fallback_reason="selected_403"/"selected_404"` | unit(両 status) | pytest 出力 |
| FP-005 | default 投稿失敗 → `DiscordDeliveryError` → route 502、`discord_notify` 未記録、commit 0 回 | unit(handler + route) | pytest 出力 |
| FP-006 | 署名不正 401 / secret 未設定 503 / `event_type` 不一致 400。`Authorization: Bearer` だけでは通らない | unit(route) | pytest 出力 |
| FP-007 | Drive permission 失敗 → `DriveExportError`(retryable は status 依存)、状態 `failed` に `file_id` 保存、フック未送信 | unit | pytest 出力 |
| FP-008 | Discord env 未設定 → `skipped`、Groq/Discord 呼び出し 0 回 | unit | pytest 出力 |
| FP-009 | 既存 `webhooks.py` の per-meeting 経路・`validate_webhook_url`・bot 作成ヘッダ(`X-User-Webhook-*`)は差分に含まれない | `git diff --stat base-commit..HEAD` に `webhooks.py` `webhook_url.py` `meetings.py` `api-gateway` が無い | diff --stat |
| FP-010 | `.env`・`deploy/gcp`・`deploy/helm`・`deploy/lite`・AGENTS.md・CLAUDE.md は差分に含まれない | `git diff --name-only base-commit..HEAD` | 出力 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | 変更ファイル数 ≤ 12、新規 pip 依存なし | `git diff --name-only base-commit..HEAD \| wc -l`、requirements/pyproject の diff が無い | 出力 |
| NFT-002 | compose 定義が有効で calendar profile を含む | `docker compose -f deploy/compose/docker-compose.yml --profile calendar config -q` exit 0 | 出力 |
| NFT-003 | hw 機械検証通過 | `bash .hw/verify.sh` exit 0(baseline 以外の新規失敗なし) | 出力末尾 `[hw][verify] ok` |
| NFT-004 | meeting-api 全テスト通過(CI と同条件) | `pytest services/meeting-api/tests/ -q --ignore=services/meeting-api/tests/test_integration_live.py` exit 0 | 出力 |
| NFT-005 | calendar-service 全テスト通過 | `(cd services/calendar-service && PYTHONPATH=. pytest tests -q)` exit 0 | 出力 |
| NFT-006 | GitNexus impact(実装前)と detect_changes(commit 前)を実施し、影響が `run_drive_export` / `upload_markdown_to_drive` / 新規シンボルに限られる | MCP 出力(不可なら Grep 呼び出し元一覧) | 出力 |

## KPI Checks

該当なし(kpi-backcast-roadmap.md なし)。

## Gate Requirements

- preflight result required: yes(実装前 impact 結果)
- evidence pack required: yes(上記コマンド出力)
- hash-bound approval required: yes(M のため Fable READY 必須)
- research brief required: no(plan.md 内リサーチ記録で代替)
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-001 | Discord channel type 0/5 が投稿候補、Get Guild Channels が threads を含まない | source check(公式 docs、2026-08-26 所与) | plan.md リサーチ記録 |
| RF-002 | Drive permissions.create の domain/allowFileDiscovery パラメータ | source check(公式 docs 所与) | plan.md リサーチ記録 |
| RF-003 | Groq `response_format=json_object` の `openai/gpt-oss-20b` 対応 | 実環境で 1 回手動確認(400 なら plan の覆る条件に従い response_format を外す。契約 AT-004/005 は不変) | 手動メモ(PR 本文) |
