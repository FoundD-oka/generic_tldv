# verification-contract: meeting-join-422

前提: clean committed treeで、base `67ea03210c2de4c8723780402d302948b138d939` からの実装ブランチ上で実行する。

## AT — 受け入れテスト

- AT-A1 (API: Path 3 空 ID 正規化)
  - `cd services/meeting-api && python3 -m pytest tests/test_url_parser_and_dry_run.py -v -k "TestPath3EmptyNativeIdNormalization"`
  - 追加6ケースが全 passed。white-label + platform + 空/空白 ID は `None`、canonical URL は ID 補完、meeting_url なしおよび platform なしの拒否は維持。
- AT-A2 (API: 即時スモーク)
  - `cd services/meeting-api && python3 -c "from meeting_api.schemas import MeetingCreate; m=MeetingCreate(platform='zoom', meeting_url='https://zoom-lfx.platform.linuxfoundation.org/meeting/96088138284?password=x', native_meeting_id=''); print(m.native_meeting_id)"`
  - 例外なく `None`。
- AT-D1 (Dashboard: payload から空 ID を省略)
  - `cd services/dashboard && npx vitest run tests/test_join_modal_request.test.ts`
  - 空 meetingId の最終オブジェクトと JSON に `native_meeting_id` がなく、非空時は値付きで存在。
- AT-D2 (Dashboard: 422 detail 文言抽出)
  - AT-D1 と同一実行。
  - FastAPI detail 配列から msg を抽出し、malformed 形は `null`。
- AT-D3 (旧経路の除去)
  - `rg -n -F 'native_meeting_id: parsedInput.meetingId || ""' services/dashboard/src/components/join/join-modal.tsx`
  - マッチ0件。
  - `rg -n "extractFastApi422Messages|status === 422" services/dashboard/src/components/join/join-modal.tsx`
  - 422分岐とhelper使用が存在。

## FP — 反証・回帰ガード

- FP-1: `cd services/meeting-api && python3 -m pytest tests/test_url_parser_and_dry_run.py -v`
  - 既存テストを含め全 passed、skip増加なし。
- FP-2: `cd services/dashboard && npm test`
  - 新規 fail 0。
- FP-3: meeting_url なし空 ID は `MeetingCreate(platform='google_meet', native_meeting_id='')` が ValidationError。
- FP-4: platform なし + 未知 URL + 空 ID は ValidationError。
- FP-5: テスト削除・skip・期待値緩和なし。
  - `git diff 67ea03210c2de4c8723780402d302948b138d939..HEAD --diff-filter=D --name-only` が空。
  - 差分に新規 `.skip(` / `.only(` がない。

## NFT — 非機能・不変条件

- NFT-1: 変更禁止ファイル不変。
  - `src/lib/api.ts`、`src/types/vexa.ts`、`src/lib/error-messages.ts`、`src/lib/bot-create-defaults.ts`、`.env`、`deploy/`、Docker資材に差分なし。
- NFT-2: 変更ファイル集合は plan.md §5 と本タスクの `.hw/plans/meeting-join-422/` のみ。
- NFT-3: `cd services/dashboard && npx tsc --noEmit` が exit 0。
- NFT-4: `cd services/dashboard && npm run lint`。既存baselineから新規 error/warning 増加0。
- NFT-5: `cd services/meeting-api && python3 -c "import meeting_api.schemas as s; print('ok')"` が `ok`、exit 0。
