# Verification Contract — p05-detail-wiring

前提環境(実測ベースライン): `services/dashboard` で
`npm install --no-audit --no-fund && npm test`(vitest)= 28 files / 199 tests 全pass。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | エクスポートドロップダウン2箇所(通常/コンパクト)に SRT・VTT 項目がある: `handleExport("srt")` / `handleExport("vtt")` が page.tsx に各2回以上、`.srtをダウンロード` / `.vttをダウンロード` が出現 | unit(ソース契約テスト test_meeting_detail_wiring.test.ts) | `npm test` 出力(該当テスト pass) |
| AT-002 | BotFailedIndicator に `onRetry={handleRetryBot}` が渡り、ハンドラが `vexaAPI.createBot` + `applyBotCreationDefaults(withPostMeetingAutoStop(` + 成功時 `router.push(\`/meetings/${meeting.id}\`)` を含む | unit(ソース契約テスト) | 同上 |
| AT-003 | retry リクエストが platform / platform_specific_id を用い、data の passcode / meeting_url / transcribe_enabled===false を条件付きで引き継ぐ | unit(ソース契約テスト: `native_meeting_id: currentMeeting.platform_specific_id` / `request.passcode` / `request.meeting_url` / `transcribe_enabled` の出現) | 同上 |
| AT-004 | 二重発火ガード: ハンドラ先頭に `isRetryingBot` による早期 return がある | unit(ソース契約テスト) | 同上 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 既存 dashboard テストが全pass(ベースライン 28 files / 199 tests + 新規ファイル)。失敗0件 | `npm test` | 実行ログ全文 |
| FP-002 | 変更ファイルが `services/dashboard/src/app/meetings/[id]/page.tsx` と `services/dashboard/tests/test_meeting_detail_wiring.test.ts` の2つ(+ `.hw/plans/p05-detail-wiring/` 配下)のみ。禁止リスト(pyproject.toml / .github/workflows / sweeps.py / meetings.py / retry.py / src/types/vexa.ts / test_meeting_status_display.test.ts / meeting-card.tsx / login/page.tsx / docker-compose.yml / test_meeting_cards_ui.test.ts)との交差が空 | `git diff --name-only 5cae3a0..HEAD` | コマンド出力 |
| FP-003 | TypeScript 新規エラーなし: base-commit 時点と実装 HEAD で `npx tsc --noEmit` を同一環境で実行し、エラー集合の差分(新規)が0件(ベースライン比較方式。既存エラーは対象外) | `npx tsc --noEmit` 2回実行+diff | 両ログと差分 |
| FP-004 | `bot-status-indicator.tsx` / `lib/export.ts` / `lib/bot-create-defaults.ts` は無変更(受け口・実装側は触らない契約) | `git diff --name-only` に不出現 | FP-002 と同一出力 |
| FP-005 | `make smoke`(.hw/verify.sh)で verify-baseline に無い新規失敗0件 | `bash .hw/verify.sh` | 実行ログ |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | 追加UI文字列がすべて日本語(カボス日本語限定方針) | ソース契約テストの文字列断言(AT-001/AT-002 の日本語文字列)で兼ねる | `npm test` 出力 |

## KPI Checks

対象外(kpi-backcast-roadmap.md なし)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: yes
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

対象外(既存コード内APIの配線のみで、外部API・ライブラリの現行仕様に依存しない)。
