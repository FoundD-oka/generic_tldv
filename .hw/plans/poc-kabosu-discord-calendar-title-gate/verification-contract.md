# Verification Contract — poc-kabosu-discord-calendar-title-gate

前提: すべて commit 済み clean tree で実行。テストの削除・skip・期待値緩和は違反。
`<base>` は `.hw/plans/poc-kabosu-discord-calendar-title-gate/base-commit` の値。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | `calendar_event.title` 非空かつ `data.title` と strip 後に完全一致 → 従来どおり `status="posted"`、選択チャンネルへ 1 回投稿、`discord_notify` 記録、commit 1 回 | 既存 `test_handle_posts_to_selected_channel` が無改変で PASSED + 新規 `test_handle_posts_when_calendar_title_matches_after_strip` | pytest 出力 |
| AT-002 | `calendar_event` 欠落 / `title` キー欠落 / 空白のみ → `{"status": "skipped", "reason": "calendar_title_missing"}` | `test_handle_skips_when_calendar_title_missing` | pytest 出力 |
| AT-003 | `calendar_event.title` 非空だが `data.title` と strip 後不一致 → `{"status": "skipped", "reason": "calendar_title_mismatch"}` | `test_handle_skips_when_calendar_title_mismatches` | pytest 出力 |
| AT-004 | skip時: `http_client_factory`、guild channels、model、message、`discord_notify` 更新、commit が全て0回 | AT-002/003テストで各カウンタをassert | pytest出力 + grep |
| AT-005 | ゲートは meeting 取得後・duplicate 判定後・外部 I/O 前。env未設定+不一致 → `discord_not_configured`、meeting無し+不一致 → `meeting_not_found` | `test_handle_title_gate_runs_after_existing_checks` + 既存duplicateテスト | pytest出力 |
| AT-006 | Google Drive 保存(meeting-api)は従来どおり | `git diff --name-only <base>..HEAD -- services/meeting-api` が空 | 出力 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 既存テストの削除・改変なし | `git diff <base>..HEAD -- services/calendar-service/tests/test_discord_notify.py \| grep -c '^-[^-]'` が0 | 出力 |
| FP-002 | `meeting-{meeting_id}` がDiscord/Groqへ渡る経路なし | `grep -n 'meeting-{meeting_id}' services/calendar-service/app/discord_notify.py` が0件 | grep出力 |
| FP-003 | HMAC・503・401・400の既存受信検証は不変 | main.py差分なし + 既存routeテスト | 出力 |
| FP-004 | 一致タイトル時の既存fallbackとduplicate判定は不変 | 既存fallback/duplicateテスト | pytest出力 |
| FP-005 | `calendar_event` がdict以外でも例外にならず `calendar_title_missing` | AT-002のparametrizeに非dictケース | pytest出力 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | 実装差分は `discord_notify.py` と同テストのみ、新規依存なし | base比較 | 出力 |
| NFT-002 | calendar-service全テスト通過 | `(cd services/calendar-service && PYTHONPATH=. pytest tests -q)` | 出力 |
| NFT-003 | hw機械検証通過 | `bash .hw/verify.sh` | `[hw][verify] ok` |
| NFT-004 | impact実測LOW、commit前detect_changesの影響が同関数のみ | GitNexus出力 | 出力 |

## KPI Checks

該当なし。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: no
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no

## Research Freshness Checks

該当なし。
