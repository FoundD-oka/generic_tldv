---
generated_by: fable
task_id: meeting-join-422
base_commit: 67ea03210c2de4c8723780402d302948b138d939
---

# meeting-join-422 修正計画: platformNeeded URL + 空 native_meeting_id が 422 になる問題

## 1. 問題の要約

2026-09-01 10:30/10:31 JST の `POST /bots` が 422 を返し、runtime-api にコンテナ生成なし、Google OAuth は 200。ボット起動前の Pydantic 入力検証失敗である。副作用なし再現により以下が確定した。

- `platform=google_meet` + canonical URL + `native_meeting_id=""` は URL 解析が ID を補完し成功する。
- `platform` + white-label/SSO URL + `native_meeting_id=""` はパーサ失敗後に空文字が残り、`validate_native_meeting_id` が 422 にする。
- `validate_meeting_or_agent` は Shape B「platform + meeting_url」を正当と明記するが、field validator が先に空文字を拒否するため契約が不整合。
- Dashboard は platformNeeded URL で `native_meeting_id: parsedInput.meetingId || ""` を送る。
- 共通 API 応答処理は FastAPI の `detail` 配列を文言化せず、UI は汎用的な Unprocessable Entity 表示になる。

## 2. 仮説・反証・確信度

- 仮説 H1: 422 は「platformNeeded URL + native_meeting_id 空文字」経路であり、空文字拒否が直接原因。
  - 根拠: 稼働中 `MeetingCreate` で成功/失敗を経路ごとに分離再現できた。runtime-api にコンテナ生成がない事実とも整合する。
  - 反証: canonical URL は同条件で成功し、Google ログイン拒否・OAuth 失敗・同時実行上限は棄却済み。
  - 確信度: 高(0.9)。
  - 覆る条件: 当該 422 の `detail[].msg` が別フィールドだった場合。ただし本修正は Path 3 契約の明白な不整合を独立に解消する。
- 仮説 H2: `validate_native_meeting_id` の呼び出し元は Pydantic 内部のみ。
  - `rg` で直接呼び出しがないことを確認し、見つかった場合は影響範囲を再評価する。

## 3. How

### 3.1 API: mode=before で空 ID を正規化

`MeetingCreate.parse_meeting_url_if_provided` の `isinstance(data, dict)` 確認直後で、`meeting_url` と `platform` が両方あり、`native_meeting_id` が空文字または空白文字列なら `None` に正規化する。

- field validator の `None` 分岐を通し、Shape B(platform + meeting_url)契約と整合させる。
- canonical URL は正規化後に既存 parser が ID を補完する。
- `meeting_url` なしの空 ID、`platform` なし + 未知 URL + 空 ID は引き続き 422。
- `validate_native_meeting_id` 本体は変更しない。

### 3.2 Dashboard: 空 ID を payload から省略

新規 `services/dashboard/src/components/join/join-modal-helpers.ts` にローカル純関数を置く。

- `buildJoinBotRequest`: `meetingId.trim()` が空なら `native_meeting_id` キーを含めない。共有 `CreateBotRequest` interface は変更せず、局所型と条件付き spread を使う。
- `withPostMeetingAutoStop` / `applyBotCreationDefaults` は変更しない。

### 3.3 Dashboard: JoinModal 限定の 422 文言化

同 helper に `extractFastApi422Messages(details: unknown): string[] | null` を追加する。

- `{detail: Array<{msg: string}>}` の非空 msg のみ抽出する。
- 不正形は `null` とし、既存 `getUserFriendlyError` へ流す。
- JoinModal の 402 分岐後に 422 分岐を追加し、具体説明を toast 表示する。
- 共通 `handleResponse`、`getUserFriendlyError` は変更しない。

### 3.4 回帰テスト

- API: `test_url_parser_and_dry_run.py` に以下を追記する。
  1. white-label URL + platform + 空 ID は成功し `None`
  2. white-label URL + platform + 空白 ID は成功し `None`
  3. canonical Zoom URL + 空 ID は ID 補完
  4. canonical Meet URL + 空白 ID は ID 補完
  5. meeting_url なし + 空 ID は拒否維持
  6. platform なし + 未知 URL + 空 ID は拒否維持
- Dashboard: 新規 `test_join_modal_request.test.ts` で以下を固定する。
  1. defaults 合成後も空 ID のキーが存在せず、JSON にも出ない
  2. 非空 ID は保持
  3. FastAPI 422 detail 配列から msg を抽出
  4. malformed details は `null`

## 4. 非目標

- `validate_native_meeting_id`、`handleResponse`、`getUserFriendlyError`、`CreateBotRequest` interface の変更。
- `parse-meeting-input.ts` の platformNeeded 判定変更。
- 422 以外のエラー表示改善や他画面への横展開。
- `.env`、deploy 資材、認証、コンテナ再起動、本番操作。

## 5. 変更予定ファイル

1. `services/meeting-api/meeting_api/schemas.py`
2. `services/dashboard/src/components/join/join-modal-helpers.ts`
3. `services/dashboard/src/components/join/join-modal.tsx`
4. `services/meeting-api/tests/test_url_parser_and_dry_run.py`
5. `services/dashboard/tests/test_join_modal_request.test.ts`

## 6. 実装順

1. `rg "validate_native_meeting_id" services/` で直接呼び出しゼロを確認。
2. API テストを先に追加し red を確認後、schemas.py を直して green にする。
3. Dashboard helper とテストを追加して green にする。
4. JoinModal を helper 使用へ変更し、旧空文字送信経路がないことを確認する。
5. verification-contract の pytest / vitest / tsc / lint / diff ガードを実行する。

## 7. リスク

- 中: これまで 422 だった platform + URL + 空 ID が成功に変わる。Path 3 契約どおりの変更だが、拒否境界を反証テストで固定する。
- 低: defaults helper が将来 ID を追加する可能性。最終合成オブジェクトでキー省略を検証する。
- 低: locale 取り違え。既存パターンどおり ja/en コピーを使う。
