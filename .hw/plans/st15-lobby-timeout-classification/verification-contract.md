# Verification Contract — st15-lobby-timeout-classification

対象: vexa-bot の Google Meet admission outcome 分類
(admission.ts / admission-classifier.ts(新規) / meetingFlow.ts / unified-callback.ts)。

## テスト実行方法

vexa-bot には CI が無い(ST-26 / P1-4 で予定)。本契約はローカル実行ログを証跡とする。
Node 20 以上:

```bash
cd services/vexa-bot/core
npm install
npm test 2>&1 | tee /tmp/st15-npm-test-head.log
```

ベースライン取得(実装前に1回、base-commit 525306f8 のツリーで同一手順を実行し
`/tmp/st15-npm-test-base.log` に保存する。worktree 使用可)。

## 最低合格ライン

1. **ベースライン比較**: 実装 HEAD の `npm test` で、base-commit 時点で pass して
   いたテストが1件も fail に転じないこと。base-commit 時点で既に fail している
   項目があれば証跡に列挙し、その項目のみ既知失敗として除外する(新規失敗が
   1件でもあれば契約違反)。
2. AT-001〜AT-005 が全て PASS であること。
3. 既存テストの needle・期待値を弱める変更をしないこと(下記アンチゲーミング)。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | admission.ts の待機室ブランチのタイムアウトが `AdmissionError("lobby_timeout", ...)` を throw する。(a) needle: `new AdmissionError("lobby_timeout", "Bot is still in the Google Meet waiting room after timeout` が admission.ts に存在。(b) 不在 needle: `throw new Error("Bot is still in the Google Meet waiting room` が admission.ts に**存在しない** | unit(admission.test.ts に構造チェック2件を追記し、`tsx src/platforms/googlemeet/admission.test.ts` で実行) | npm test ログの PASS 行 |
| AT-002 | `classifyAdmissionError` が次の5入力を正しく分類する: (1) `{outcome:"denial"}` → `{rejected:true, reason:"admission_rejected_by_admin"}`、(2) `{outcome:"lobby_timeout"}` → `{rejected:false, reason:"admission_timeout"}`、(3) `{outcome:"join_failure"}` → `{rejected:false, reason:"join_failure"}`、(4) `new Error("Bot admission was rejected by meeting admin")`(outcome なし)→ `{rejected:true, reason:"admission_rejected_by_admin"}`、(5) `new Error("Bot is still in the Teams waiting room after timeout")` → `{rejected:false, reason:"admission_timeout"}` | unit(admission-classifier.test.ts) | 同上 |
| AT-003 | `mapExitReasonToStatus` の分類: (1) `("join_failure", 0)` → `{status:"completed", completionReason:"join_failure"}`、(2) `("admission_timeout", 0)` → `{status:"completed", completionReason:"awaiting_admission_timeout"}`、(3) `("admission_rejected_by_admin", 0)` → `{status:"completed", completionReason:"awaiting_admission_rejected"}`、(4) `("join_meeting_error", 1)` → `{status:"failed", completionReason:"join_failure"}` | unit(admission-classifier.test.ts か unified-callback.test.ts のどちらかに追加。mapExitReasonToStatus は export 済みの純関数) | 同上 |
| AT-004 | meetingFlow.ts の waitForAdmission catch が分類器を使う。needle: `classifyAdmissionError` が meetingFlow.ts に存在し、かつ catch 内の旧 inline 照合(needle `msg.includes("rejected by meeting admin")`)が meetingFlow.ts に存在しない | unit(admission-classifier.test.ts に構造チェック) | 同上 |
| AT-005 | `cd services/vexa-bot/core && npm test` が exit 0(ベースライン比較で新規失敗ゼロを含む)。package.json の test チェーンに admission.test.ts と admission-classifier.test.ts が追加されていること(needle: package.json に両ファイル名) | command | base/head 両ログ + diff 要約 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | Teams/Zoom の素の Error(outcome なし)の分類が従来と同一(AT-002 の (4)(5) が保証) | unit | npm test ログ |
| FP-002 | 非 admitted 分岐の gracefulLeave が exitCode 0 のまま(needle: meetingFlow.ts に `gracefulLeaveFunction(page, 0, decision.reason` が2箇所存在) | unit(構造チェック) | 同上 |
| FP-003 | 差分スコープ: `git diff 525306f8..HEAD --name-only` が plan.md「対象ファイル」の7点(+ `.hw/plans/st15-lobby-timeout-classification/` 配下)のみ。特に services/dashboard・services/meeting-api 配下に差分が無いこと(st12/st13/st14/p05/p06/p07-08 の保護) | command | diff --name-only の出力 |
| FP-004 | 既存テストの needle・期待値の削除/緩和が無いこと。admission.test.ts への変更は追記のみ、unified-callback.test.ts への変更は AT-003 のケース追加のみ(既存 expect の変更は違反) | source check(`git diff 525306f8..HEAD -- '*test*'` のレビュー) | diff 出力 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | `npm run build`(tsc)が exit 0(npm test に内包) | command | npm test ログ冒頭 |

## KPI Checks

kpi-backcast-roadmap.md なし。適用外。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(base/head の npm test ログ、diff --name-only 出力を
  `.hw/gates/st15-lobby-timeout-classification/` に保存する。
  ※ planner 原案は `.hw/plans/<task>/evidence/` を指定していたが、CLAUDE.md の
  「READY は base..HEAD 全差分に束縛される。evidence は gitignore 済みの
  `.hw/gates/<task>/` へ」の規律に反し READY を失効させるため、保存先のみ
  `.hw/gates/` に読み替える。合格基準・提出物の内容は不変)
- hash-bound approval required: yes
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

外部 API・ライブラリ・UI 仕様の鮮度に依存する判断なし(分類は純内部ロジック)。適用外。

## アンチゲーミング条項

- AT の needle をテスト側で弱める(部分文字列を短くする等)変更は契約違反。
- npm test チェーンからの既存テストファイルの削除は契約違反。
- ベースライン比較で「既知失敗」を主張する場合、base-commit ツリーでの実測ログ提出が
  必須。自己申告は認めない。
