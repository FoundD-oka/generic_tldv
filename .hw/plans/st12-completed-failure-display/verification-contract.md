# Verification Contract — st12-completed-failure-display

対象: `services/dashboard/src/types/vexa.ts` の `getDetailedStatus`
(completed + 失敗系 completion_reason の表示)。

## テスト実行方法

```bash
cd services/dashboard
npm install --no-audit --no-fund
npm test   # = vitest run
```

base-commit 5cae3a0 実測: 28 test files / 199 tests、全 pass(planner 実測済み)。
lint / tsc は base 時点で既に red のため本契約の合格ラインに含めない。

## 最低合格ライン

1. 実装 HEAD で `npm test` が **exit 0**(全ファイル pass)。
2. passed テスト総数が **205 以上**(base 199 + 新規 AT 最低6件)。
3. AT-001〜AT-006 に対応するテストが `tests/test_meeting_status_display.test.ts`
   に存在し、いずれも pass。
4. 変更ファイルが `services/dashboard/src/types/vexa.ts` と
   `services/dashboard/tests/test_meeting_status_display.test.ts` の2つのみ
   (+ `.hw/plans/st12-completed-failure-display/` 配下)。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | `getDetailedStatus("completed", { completion_reason: "awaiting_admission_timeout" })` が `label: "参加失敗"`、`color` に `text-red-600` を含み、`description: "ボットの入室が許可されませんでした。文字起こしはありません"` を返す。`"完了"` / `"文字起こしが完了しました"` を含まない | unit(vitest) | vitest ログの該当テスト名 pass 行 |
| AT-002 | 同 `"awaiting_admission_rejected"` で `label: "参加失敗"`、`description: "ボットの入室が拒否されました。文字起こしはありません"` | unit | 同上 |
| AT-003 | 同 `"join_failure"` → description `"会議への接続に失敗しました。文字起こしはありません"`、`"validation_error"` → `"会議情報の検証に失敗しました。文字起こしはありません"`(いずれも label `"参加失敗"`) | unit | 同上 |
| AT-004 | 同 `"stopped_before_admission"` は `label: "完了"` のまま `description: "入室前に停止したため、文字起こしはありません"`(「文字起こしが完了しました」を返さない) | unit | 同上 |
| AT-005 | 優先順位: `completed` + `completion_reason: "awaiting_admission_timeout"` + `final_transcription.status` が `"queued"` / `"running"` / `"failed"` のいずれでも `label: "参加失敗"`(「処理中」「再処理失敗」を出さない) | unit(it.each で3値) | 同上 |
| AT-006 | fail-open: `completed` で reason が `undefined` / `"stopped"` / `"meeting_ended"` / `"left_alone"` / `"evicted"` / `"unknown_reason"` のとき従来どおり `label: "完了"`, `description: "文字起こしが完了しました"` | unit(it.each) | 同上 |

## Failure Patterns(回帰禁止)

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | `status === "failed"` の既存分岐(error_code 別 description)が無変更で動く | `npm test` フルスイート exit 0 + `git diff` で当該分岐に変更が無いこと | vitest ログ + diff |
| FP-002 | 失敗系 reason を持たない completed の再文字起こし表示(処理中 / 再処理失敗 / succeeded 復帰)が既存テスト無変更で pass | 既存テスト(test_meeting_status_display.test.ts の第2ブロック以降)無変更 pass | vitest ログ + diff |
| FP-003 | base で green の28テストファイルが全て pass のまま(特に test_meeting_cards_ui.test.ts) | `npm test` exit 0 | vitest ログ |

## アンチゲーミング条項(レビューで機械的に確認)

- `git diff 5cae3a05550e8679b156e88652b0dfa2193d30f1..HEAD` の変更ファイルは
  `services/dashboard/src/types/vexa.ts` と
  `services/dashboard/tests/test_meeting_status_display.test.ts` の2つ
  (+ `.hw/plans/st12-completed-failure-display/` 配下)に限る。
  それ以外(特に `meeting-card.tsx` — Codex 並行セッションの未コミット変更と
  競合する)への変更は契約違反。
- 既存テストの改変は「第1ブロック `it.each` からの `"awaiting_admission_rejected"`
  除去とテスト名変更」のみ許可。他の既存 assert の削除・skip/xfail 追加・期待値
  緩和は契約違反。
- AT の文言検証は `toMatchObject` で label と description の**完全一致文字列**を
  assert すること。`toBeTruthy` や部分一致だけの検証は契約違反。
- passed 総数の底上げ目的の空テスト(assert なし it)は契約違反。

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | `bash .hw/hooks/pr-ready-gate.sh st12-completed-failure-display` が pass | ゲート実行 | ゲート出力 |
| NFT-002 | `COMPLETED_FAILURE_REASONS` が vexa.ts から export され、値が plan 記載の4値と一致(将来 RF-11 側との整合確認に使う) | source check | diff |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(vitest フルスイートログ。base 199 pass との比較を含む)
- hash-bound approval required: yes
- research brief required: no(外部依存なし。全て repo 内コードで確定)
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
