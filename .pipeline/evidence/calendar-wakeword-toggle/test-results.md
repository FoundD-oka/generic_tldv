# Test Evidence — calendar-wakeword-toggle (M)

Date: 2026-07-10. Verification re-run independently by Claude Code
（実装ランタイムは Codex App。自己申告に依存せず再実行して記録）。

- `pytest services/calendar-service/tests/test_sync.py -q` → **2 passed**
  （PYTHONPATH=services/calendar-service:services/meeting-api:libs/admin-models、
  /tmp/generic_tldv_meeting_api_venv/bin/python）
- `cd services/dashboard && npm test -- test_export_and_bot_defaults.test.ts`
  → **6 passed**（vitest 4.1.0）
- `cd services/dashboard && npx tsc --noEmit --pretty false` → exit 0
- GitNexus `detect-changes` — 変更シンボルは想定内
  （sync.py: KABOSU_POST_MEETING_AUTO_STOP_TIMEOUT_MS/payload/schedule_upcoming_bots、
  join-modal: JoinModal/handleSubmit、dashboard-copy: EN/JA辞書、
  googlemeet recording: startGoogleRecording系フロー8件）
- pr-ready gate（本記録前の時点）: harness-residency / preflight / hd-gate /
  doc-staleness / adapter-contract / sml-decision(M) / verification-contract すべて pass

備考: /tmp/generic_tldv_calendar_testdeps の pydantic_core は現行 python3.11 と
ABI不整合のため使用不可。meeting_api venv で代替実行した。
