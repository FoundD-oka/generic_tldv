---
generated_by: fable
task_id: poc-kabosu-discord-calendar-title-gate
size: S
runtime: inline
---
# Discord通知をカレンダー登録タイトル一致時だけ実行する

## ゴール
`handle_drive_export_completed`(`services/calendar-service/app/discord_notify.py:312`)に「`calendar_event.title` が非空で、payload `data.title` と前後空白除去後に完全一致」のゲートを追加する。欠落・空・不一致なら Discord guild channels 取得・Groq・Discord投稿・`discord_notify` 更新・commit を一切行わず `skipped` を返す。Drive 保存(meeting-api 側)は触らない。

## How(実装者向け)
対象は `services/calendar-service/app/discord_notify.py` と `services/calendar-service/tests/test_discord_notify.py` の 2 ファイルのみ。README・main.py・meeting-api は変更しない。
1. `calendar_event = data.get("calendar_event") or {}`(現 341 行)直後、`threshold`/`timeout` の env 読取と `async with http_client_factory(...)` より前に挿入:
   - `if not isinstance(calendar_event, dict): calendar_event = {}`
   - `calendar_title = str(calendar_event.get("title") or "").strip()`; 空なら `return {"status": "skipped", "reason": "calendar_title_missing"}`
   - `payload_title = str(data.get("title") or "").strip()`; `calendar_title != payload_title` なら `return {"status": "skipped", "reason": "calendar_title_mismatch"}`
   - 通過後 `title = calendar_title`。既存の `f"meeting-{meeting_id}"` フォールバック式は削除する(ゲート通過後は到達不能。FP-002 で grep 検査)。
2. 判定順は変えない: `discord_not_configured` → `meeting_not_found` → `duplicate` → タイトルゲート → 外部 I/O。skip の戻り値は既存 skipped と同形(`status`/`reason` の 2 キーのみ)。
3. テスト追加(既存テストの削除・変更・skip・期待値緩和は禁止。`_envelope()` は一致タイトルなので無改変で通る):
   - `test_handle_skips_when_calendar_title_missing`: parametrize で `calendar_event` キー削除 / `{"start_time": ...}` のみ / `title: "  "` / 非dict の 4 ケース → `calendar_title_missing`
   - `test_handle_skips_when_calendar_title_mismatches`: `calendar_event.title="週次定例"`, `data.title="meeting-42"` → `calendar_title_mismatch`
   - `test_handle_posts_when_calendar_title_matches_after_strip`: strip 後一致 → posted、投稿 content に正規化済みタイトル
   - `test_handle_title_gate_runs_after_existing_checks`: meeting 無し+不一致 → `meeting_not_found`; env 未設定+不一致 → `discord_not_configured` かつ DB 取得 0 回
   - skip 系で factory / Discord / model / commit が全て0回、`discord_notify` 未記録を assert
4. GitNexus: impact 実測(impactedCount=1, direct=`drive_export_completed`, processes=0, risk=LOW)。commit 前に `detect_changes()` を実行する。
5. ローカル実行: `(cd services/calendar-service && PYTHONPATH=. pytest tests -q)` → `bash .hw/verify.sh`。

## 検証契約
`verification-contract.md` 全通過が完了条件。S のため Fable 契約レビューなし(機械検証のみ)。契約は最低合格ライン。通ったら止める。

## 仮説と覆る条件
- 送信側は `title = calendar_event.title or native_meeting_id or meeting-{id}` を組むため、実運用の不一致は calendar title 欠落時のみ発生する。確信度: 高。送信側が title を別ソースから作るよう変更された場合は AT-001 を再確認する。
- READMEへのskip理由追記は要件外。advisoryとして残し本タスクでは行わない。
