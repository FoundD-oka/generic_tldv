# Verification Contract — p06-ui-regression-restore

前提: base-commit 96c07857 実測ベースライン = dashboard テスト **30 files /
224 tests 全パス**(`cd services/dashboard && npm install --no-audit --no-fund
&& npm test`)。以下の Evidence のコマンドはすべて commit 済み clean tree で実行する。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | タイトル解決ヘルパーが存在し、name > title > calendar_title > calendar_event.title > platform_specific_id > 「無題の会議」の優先順位で解決する | unit | `tests/test_meeting_title.test.ts` が存在し、優先順位・空文字スキップ・data未定義の最低6ケースを実挙動で検証して green |
| AT-002 | (UI-1)カード: タイトルあり時に会議コードが font-mono の補助行で表示される | unit (source contract) | `test_meeting_cards_ui.test.ts` が cardSource に `hasCustomTitle && meeting.platform_specific_id` と `font-mono` を要求して green |
| AT-003 | (UI-2)カード: 参加者を最大3名表示、4名以上は「ほかN名」 | unit (source contract) | 同テストが `participants.slice(0, 3).join(", ")` と `` ほか${participants.length - 3}名 `` を要求して green |
| AT-004 | (UI-4)カード: 年付き絶対日付と相対日時を併記 | unit (source contract) | 同テストが `"yyyy年M月d日"` と `formatDistanceToNow(parseUTCTimestamp(timeSource), { addSuffix: true, locale: ja })` を要求して green |
| AT-005 | (UI-3)カード: タイトルクリックは詳細遷移、編集は専用鉛筆ボタンのみ | unit (source contract) | 同テストが `aria-label="タイトルを編集"` を要求し、かつ cardSource が素の `<button` を含まないことを要求して green |
| AT-006 | (UI-1後段)詳細ページのタイトル解決が一覧と同一ヘルパーに統一され、calendar_title を参照する | unit (source contract) | `tests/test_meeting_detail_title.test.ts` が pageSource に `from "@/lib/meeting-title"`、`resolveMeetingTitle(` >= 4回、`getCustomMeetingTitle(` >= 3回を要求し、`data?.name \|\|` の残存0を要求して green |
| AT-007 | (UI-5)カード: メモありの会議に「メモ」インジケータ+先頭100文字ツールチップ | unit (source contract) | `test_meeting_cards_ui.test.ts` が `<span>メモ</span>` と `meeting.data.notes.substring(0, 100)` を要求して green |
| AT-008 | 回帰テストがCI実行対象に載っている | command | `cd services/dashboard && npm test` の出力に上記3テストファイルが含まれ、**Test Files >= 32 / Tests >= 232 / 失敗0** |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | ベースライン224テストを1件も落とさない | command | `npm test` 全パス(失敗0)。テスト削除で数合わせしない: Test Files >= 32 かつ Tests >= 232 |
| FP-002 | st12(参加失敗ステータス表示)を壊さない | command | `test_meeting_status_display.test.ts` green。`src/types/vexa.ts` は差分に含めない |
| FP-003 | p05(SRT/VTTエクスポート・onRetry 配線)を壊さない | command + source check | 既存 export/detail 系テスト green。`git diff base..HEAD -- 'services/dashboard/src/app/meetings/[id]/page.tsx'` の変更が D3 の7箇所+import に限られる |
| FP-004 | 開始前会議の created_at フォールバック表示を維持 | unit (source contract) | `test_meeting_cards_ui.test.ts` の `meeting.start_time \|\| meeting.created_at` 断言を維持して green |
| FP-005 | タイトル編集機能自体は動き続ける(保存・キャンセル・編集中の遷移抑止) | source check | cardSource に `handleStartEdit` / `handleSaveTitle` / `handleCancelEdit` / `isEditingTitle && event.preventDefault()` が残存 |
| FP-006 | TZ表示 lock(#265)を割らない: カードのツールチップの `toLocaleString` + `Intl.DateTimeFormat` 経路を維持 | command | `bash .hw/verify.sh` green(新規失敗ID 0件) |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | lint 通過 | command | `cd services/dashboard && npm run lint` exit 0 |
| NFT-002 | 差分スコープ: 変更ファイルは plan.md「対象」の6ファイル(+ `.hw/plans/p06-ui-regression-restore/` 配下)のみ | command | `git diff --name-only <base-commit>..HEAD` が対象一覧と一致 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: yes
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed
