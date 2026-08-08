---
generated_by: fable
task_id: st15-lobby-timeout-classification
base-commit: 525306f8dd29d0e5845f4d9536da5c21edd43f01
size: M
---

# ST-3: Google Meet admission outcome(denial / lobby_timeout / join_failure)の正確な分類伝搬

## ゴール

依頼の文字通りの内容: 「ロビータイムアウトが join_failure に誤分類される問題
(googlemeet/admission.ts:281, 382-387)を修正する」。

再設計後のゴール(reframe): admission.ts の `AdmissionError.outcome` は現状どこからも
消費されておらず、meetingFlow.ts の catch が message 文字列照合で全 admission エラーを
`admission_timeout` に潰している。:281 のタグ修正だけでは下流挙動が何も変わらない。
本タスクのゴールは「**admission の3区分(denial / lobby_timeout / join_failure)を
outcome フィールド経由で end-to-end に正しく着地させる**」こと。具体的には:

- ロビータイムアウト(待機室で待ち切れ)→ completion_reason `awaiting_admission_timeout`
- ホスト拒否 → completion_reason `awaiting_admission_rejected`(現状維持)
- ロビー未到達の join failure → completion_reason `join_failure`(現状は
  `awaiting_admission_timeout` に誤着地)

dashboard(st12 の `COMPLETED_FAILURE_REASONS`)と meeting-api(Pack D の
`JOIN_FAILURE` canonical 化)は既に3区分すべてに対応済みのため、変更は
**vexa-bot のみ**で完結する。dashboard・meeting-api・他プラットフォーム
(Teams/Zoom)の挙動は変えない。

## 対象ファイル(この7点以外に差分を作らない)

1. `services/vexa-bot/core/src/platforms/googlemeet/admission.ts`(1行変更)
2. `services/vexa-bot/core/src/platforms/shared/admission-classifier.ts`(新規)
3. `services/vexa-bot/core/src/platforms/shared/meetingFlow.ts`(catch の置換)
4. `services/vexa-bot/core/src/services/unified-callback.ts`(case 1つ追加)
5. `services/vexa-bot/core/src/platforms/googlemeet/admission.test.ts`(needle 追記)
6. `services/vexa-bot/core/src/platforms/shared/admission-classifier.test.ts`(新規)
7. `services/vexa-bot/core/package.json`(test スクリプトへの2ファイル追加のみ)

## How(設計判断は全て確定済み。実装者の裁量に委ねない)

### 1. admission.ts — 待機室ブランチのタイムアウトを lobby_timeout として throw

base-commit :281 の

    throw new Error("Bot is still in the Google Meet waiting room after timeout - not admitted to the meeting");

を次に置換する(message は変えない。AdmissionError は catch(:382-387)で
そのまま re-throw される):

    throw new AdmissionError("lobby_timeout", "Bot is still in the Google Meet waiting room after timeout - not admitted to the meeting");

他の throw(:163, :295, :368 denial / :376 lobby_timeout / :378, :383 join_failure)は
一切変更しない。

### 2. admission-classifier.ts(新規)— outcome 優先の純関数分類器

`services/vexa-bot/core/src/platforms/shared/admission-classifier.ts` を新規作成。
**重い import を一切持たない**(googlemeet の AdmissionError クラスも import しない。
duck-typing で `outcome` を読む。理由: shared → googlemeet の依存を作らない、かつ
テストが index.ts の副作用を踏まずに単体で走るようにするため)。

    export type AdmissionErrorDecision = {
      admitted: false;
      rejected: boolean;
      reason: "admission_rejected_by_admin" | "admission_timeout" | "join_failure";
    };

    /**
     * waitForAdmission の reject を AdmissionDecision に分類する。
     * 1) error.outcome(googlemeet AdmissionError)を最優先で使う。
     * 2) outcome が無い場合(Teams/Zoom の素の Error)は従来の message 照合に
     *    フォールバックし、既存挙動を完全維持する。
     */
    export function classifyAdmissionError(error: unknown): AdmissionErrorDecision {
      const outcome = (error as any)?.outcome;
      if (outcome === "denial") {
        return { admitted: false, rejected: true, reason: "admission_rejected_by_admin" };
      }
      if (outcome === "lobby_timeout") {
        return { admitted: false, rejected: false, reason: "admission_timeout" };
      }
      if (outcome === "join_failure") {
        return { admitted: false, rejected: false, reason: "join_failure" };
      }
      const msg: string = (error as any)?.message || String(error);
      if (msg.includes("rejected by meeting admin")) {
        return { admitted: false, rejected: true, reason: "admission_rejected_by_admin" };
      }
      return { admitted: false, rejected: false, reason: "admission_timeout" };
    }

### 3. meetingFlow.ts — catch を分類器に置換

base-commit :143-148 の

    .catch((error: any) => {
      const msg: string = error?.message || String(error);
      if (msg.includes("rejected by meeting admin")) {
        return { admitted: false, rejected: true, reason: "admission_rejected_by_admin" } as AdmissionDecision;
      }
      return { admitted: false, rejected: false, reason: "admission_timeout" } as AdmissionDecision;
    })

を次に置換する:

    .catch((error: any) => classifyAdmissionError(error) as AdmissionDecision)

import を追加: `import { classifyAdmissionError } from "./admission-classifier";`

**それ以外の meetingFlow.ts は変更しない。** 特に:
- 非 admitted 分岐の `gracefulLeaveFunction(page, 0, decision.reason || ...)` の
  exitCode 0 は3区分すべてで維持する(join_failure でも 0。graceful leave 試行と
  meeting-api への completed+reason 送信という既存の退出様式を踏襲し、meeting-api の
  canonical 化(completed+join_failure → FAILED)に着地させるため)。
- `decision.rejected` 分岐、performLeaveAction 試行、fallback 文字列
  ("admission_rejected_by_admin" / "admission_timeout")も現状維持。

### 4. unified-callback.ts — mapExitReasonToStatus に exit-0 の join_failure case 追加

exitCode === 0 の switch(:264 付近)の `admission_rejected_by_admin` case の直後に
追加する:

    case "join_failure":
      // Google Meet admission phase: bot reached the page but never reached
      // the lobby and no meeting indicators appeared. Distinct from
      // admission_timeout (lobby reached, host did not admit). meeting-api's
      // Pack D canonicalization routes completed+join_failure to FAILED.
      return { status: "completed", completionReason: "join_failure" };

これが無いと reason "join_failure" は default に落ちて completed+"stopped" になり、
meeting-api の `_classify_stopped_exit` が STOPPED_BEFORE_ADMISSION 等へ誤分類する。
exitCode !== 0 側の既存 case("join_meeting_error" → failed+join_failure)は変更しない。

### 5. テスト(検証契約の AT を満たす最小構成)

- `admission-classifier.test.ts`(新規): 既存の手書き expect 様式
  (unified-callback.test.ts と同形式、末尾 `process.exit(failed > 0 ? 1 : 0)`)で
  分類器の5ケース + meetingFlow.ts / admission.ts への構造 needle
  (verification-contract.md の AT-002/AT-004/FP-001 参照)。
- `admission.test.ts`(追記): 構造 needle 2件を追加(AT-001 参照)。既存 needle は
  変更しない。
- `package.json` の test スクリプト末尾に追加:
  `&& tsx src/platforms/googlemeet/admission.test.ts && tsx src/platforms/shared/admission-classifier.test.ts`

### 6. やらないこと(スコープ外)

- meeting-api / dashboard / runtime-api の変更(不要と裏取り済み)。
- Teams・Zoom の admission タイムアウト vs join failure の区分改善(Teams にも同種の
  曖昧さがあるが ST-3 は Meet 対象。フォールバックで現状挙動を完全維持する)。
- vexa-bot の CI 新設(ST-26 / P1-4 の責務)。
- ST-4(参加失敗の能動通知)。

## Why(実装者に渡さない)

- 監査 ST-3 の文字通りの修正(:281 のみ)は、outcome が下流で捨てられているため
  no-op になる。P0-1「失敗を失敗と表示する」の意図を満たすには outcome チャネルの
  消費まで含める必要がある。ユーザー実害は「ロビー未到達の接続失敗」が
  「ホストが入室を許可しなかった」と誤表示されることで、ユーザーの対処
  (ホストに承認依頼 vs URL/環境を疑う)を誤誘導し、運用統計も汚す。
- exitCode 0 を維持するのは、admission 系の兄弟経路(admission_timeout /
  admission_rejected_by_admin)と同じ「graceful に退出し completed+reason を送り、
  サーバ側 canonical 化で FAILED に落とす」様式に揃えるため。exit 1 +
  join_meeting_error 再利用案は、STOPPING 競合時(ユーザーが lobby 中に停止)の
  Pack C 経路(user stop は失敗にしない)を素通りしてしまうため不採用。
- 分類器を shared の独立モジュールに切り出すのは、meetingFlow.ts が index.ts を
  import しており(hasStopSignalReceived 等)、テストから直接 import すると
  副作用が爆発するため。純関数化が唯一の決定的テスト手段。
