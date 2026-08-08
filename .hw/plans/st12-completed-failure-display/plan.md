---
generated_by: fable
task_id: st12-completed-failure-display
base-commit: 5cae3a05550e8679b156e88652b0dfa2193d30f1
size: M
---

# ST-2(表示側): 失敗ミーティングが「完了・文字起こしが完了しました」と表示される欠陥の修正

## ゴール

依頼の文字通りの内容: 「vexa.ts:402 付近で、失敗したミーティングが『完了』と
表示されるのを直す」。

再設計後のゴール(reframe): 守るべき表示上の不変条件は
「**文字起こしが存在し得ないミーティングに『文字起こしが完了しました』と表示しない**」。
DB上の status は `completed` のままでも(バックエンド是正は RF-11 スコープ)、
`data.completion_reason` が「ボットが一度も active に達していない」ことを示す値の
場合は失敗として表示する。乖離は小さく、監査 ST-2 の意図(ユーザーに嘘をつかない)
と完全に整合する。

## 対象(変更してよいファイルはこの2つだけ)

- `services/dashboard/src/types/vexa.ts` — `getDetailedStatus`(base-commit 時点で
  :369-407 付近)
- `services/dashboard/tests/test_meeting_status_display.test.ts` — 既存テストの
  改訂(下記で限定)+新規テスト追加

**`meeting-card.tsx` は変更禁止**(別セッション Codex がメインリポジトリで同
ファイルに未コミット変更を持つ。詳細は Why 参照)。カード・詳細ページは
`getDetailedStatus` の戻り値をそのまま描画するため、本修正だけで両方に反映される。

## 設計判断(確定事項。実装者の裁量に委ねない)

1. **失敗扱いにする completion_reason の集合**(vexa.ts のモジュールレベルに定数
   として定義し export する):

   ```ts
   export const COMPLETED_FAILURE_REASONS = [
     "awaiting_admission_timeout",
     "awaiting_admission_rejected",
     "join_failure",
     "validation_error",
   ] as const;
   ```

   選定原理は「ボットが active に達する前に終わった reason」= 文字起こしが
   存在し得ない reason(meeting-api `schemas.py` の `MeetingCompletionReason` と
   `callbacks.py:_classify_stopped_exit` の `_explicit_failure_reasons` に整合)。
   `evicted` / `max_bot_time_exceeded` / `left_alone` は active 到達後の終了で
   途中までの文字起こしが存在し得るため**含めない**(「完了」のまま)。

2. **判定位置と優先順位**: `getDetailedStatus` 内、`status === "completed"` かつ
   `COMPLETED_FAILURE_REASONS` に `data?.completion_reason` が含まれる場合の分岐を、
   既存の再文字起こしチェック(`queued/running` → 処理中、`failed` → 再処理失敗)
   より**前**に置く。参加できなかった会議に「処理中」「再処理失敗」を出さない。

3. **失敗表示の内容**(色は既存 failed 分岐と同一):

   ```ts
   label: "参加失敗",
   color: "text-red-600 dark:text-red-400",
   bgColor: "bg-red-100 dark:bg-red-950/50",
   ```

   description は reason 別(既存 failed 分岐の文言と揃える):
   - `awaiting_admission_timeout` → `"ボットの入室が許可されませんでした。文字起こしはありません"`
   - `awaiting_admission_rejected` → `"ボットの入室が拒否されました。文字起こしはありません"`
   - `join_failure` → `"会議への接続に失敗しました。文字起こしはありません"`
   - `validation_error` → `"会議情報の検証に失敗しました。文字起こしはありません"`

4. **`stopped_before_admission`(ユーザーが入室前に停止)**: 失敗ではない
   (ユーザー意図。meeting-api Pack C の設計)ため label は `"完了"`・色も既存
   completed のままとするが、description を
   `"入室前に停止したため、文字起こしはありません"` に差し替える。
   「文字起こしが完了しました」という虚偽の説明だけを除く。

5. **上記以外の completed**(reason なし / `stopped` / `meeting_ended` /
   `left_alone` / `evicted` / 未知の値 など)は現行どおり
   `label: "完了"` / `description: "文字起こしが完了しました"` を維持する。
   未知の reason を失敗に倒さない(fail-open。表示層で誤って成功会議を失敗と
   表示する方が害が大きい。真の失敗分類はバックエンド RF-11 の責務)。

6. **vexa.ts :399-401 の方針コメント**(「every successfully finalized meeting
   consistently as completed」)は新方針に合わせて書き換える。

7. **`MEETING_STATUS_CONFIG` は変更しない**。`live-session.tsx`(join フローの
   一時表示、MeetingData を持たない)・`bot-status-indicator.tsx` も変更しない。

## 実装手順(How)

1. `services/dashboard/src/types/vexa.ts` の `getDetailedStatus` に設計判断 1〜6 の
   分岐を追加する。分岐順: ① completed+失敗reason → 参加失敗、
   ② completed+`stopped_before_admission` → 完了(説明差し替え)、
   ③ 既存の再文字起こしチェック、④ 既存の completed 既定表示、以降は現行のまま。

2. `services/dashboard/tests/test_meeting_status_display.test.ts` を改訂する:
   - 既存の `it.each([...])`「shows completed meetings as 完了 regardless of
     completion reason」から **`"awaiting_admission_rejected"` を除去**し、
     テスト名を「文字起こしが存在し得る完了は reason に関わらず 完了 と表示する」
     相当へ改める(`"stopped"`, `"meeting_ended"`, `"kicked"`, `"removed"`,
     `"unknown_reason"` は残し、`"left_alone"`, `"evicted"` を追加してよい)。
     この1件以外の既存テスト(停止中・処理中・再処理失敗・succeeded 復帰)は
     一切変更しない。
   - verification-contract.md の AT-001〜AT-006 に対応する新規テストを追加する。
     様式は同ファイルの既存 `it.each` / `toMatchObject` に合わせる。

3. `cd services/dashboard && npm install --no-audit --no-fund && npm test` で
   全スイート green を確認する(base 5cae3a0 実測: 28 files / 199 tests 全 pass)。

## スコープ外

- バックエンドの status 分類是正(unified-callback.ts / callbacks.py)= RF-11。
- `meeting-card.tsx` / `meetings/[id]/page.tsx` / `live-session.tsx` /
  `bot-status-indicator.tsx` の変更。
- UI-1〜4(カード退行復元、P0-6)・UI-5(onRetry)。
- ST-3(ロビータイムアウトの join_failure 誤分類)はボット側でありスコープ外。

## Why(実装者に渡さない)

- 監査 ST-2(current-state.md:12、深刻度 高)。入室拒否・承認タイムアウトの会議が
  「完了・文字起こしが完了しました」と表示され、会議1回分が静かに失われたことに
  ユーザーが気づけない。信頼を直接毀損する出荷ブロッカー。
- 発生機序の裏取り: vexa-bot `unified-callback.ts` の `mapExitReasonToStatus` が
  exitCode 0 の `admission_failed/admission_timeout` を
  `completed + completionReason=awaiting_admission_timeout` に、
  `admission_rejected_by_admin` を `completed + awaiting_admission_rejected` に
  マップする。meeting-api 側には Pack J.4(`_classify_stopped_exit`)で FAILED へ
  再分類するロジックが既にあるが、(a) `stop_requested`(Pack C)経由は
  completed のまま reason を保持、(b) 過去データは completed のまま DB に残存、
  (c) 全エジット経路が classifier を通る保証は RF-11 で是正予定。表示側で
  reason を見て正直に描画するのは、バックエンド是正後も無害に共存する
  (RF-11 後は status=failed になり既存の failed 分岐が同等の表示をする)。
- 既存テスト `test_meeting_status_display.test.ts` の第1ブロックは「reason に
  関わらず完了と表示する」という**旧方針そのもの**を固定しており、監査が欠陥と
  認定した挙動のエンコードである。よってこの1件のみ改訂を許可した(それ以外の
  既存 assert 変更はアンチゲーミング条項で禁止)。
- 失敗集合に `evicted`/`max_bot_time_exceeded`/`left_alone` を入れないのは、
  active 到達後の終了で部分的な文字起こし・録音が実在し得るため。「完了」表示の
  ままの方が正直。未知 reason を fail-open にしたのも同じ理由(表示層の誤爆防止)。
- Codex 並行セッションの未コミット4ファイル(meeting-card.tsx / login/page.tsx /
  docker-compose.yml / test_meeting_cards_ui.test.ts)と diff で突き合わせ済み。
  meeting-card.tsx の dirty 変更は「削除ボタン+リンク入れ子解消」であり status
  表示には触れていない。本タスクが vexa.ts とステータス表示テストのみに閉じれば
  物理衝突・意味的衝突ともに無し。唯一の相互作用は Codex 側
  `isDeletable = status === "completed" || "failed"` が本件の疑似 completed 会議
  にも削除ボタンを出すことだが、参加失敗会議を削除できるのはむしろ望ましく無害。
- `label: "参加失敗"`(「失敗」でなく)としたのは、status=failed 由来の「失敗」
  と区別でき、かつ RF-11 前の暫定期間中にどちらの経路でも意味が通るため。
- lint(61 errors)と tsc(生成ファイル欠落ほか)は base 時点で既に red のため
  合格ラインに使えない。機械検証は vitest フルスイート(base で green)に固定した。
