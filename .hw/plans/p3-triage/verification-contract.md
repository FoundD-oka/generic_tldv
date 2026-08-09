# Verification Contract — p3-triage

対象: `base-commit..HEAD` の差分。実装なし・PR なしの記録タスク。
判定主体: **ゲート** = pr-ready-gate(後続タスクの PR に同乗してコミットされる)。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-901 | `.hw/plans/p3-triage/` に plan.md / verification-contract.md / sml-decision.json / runtime-decision.json / base-commit が存在する | ls | ゲート | ファイル一覧 |
| AT-902 | 本タスク名義の製品コード差分が存在しない: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` に p3-triage 起因の変更が含まれない | diff | ゲート | diff 出力 |

## Gate Requirements

- preflight result required: no(コード変更なし)
- evidence pack required: no
- hash-bound approval required: no(S・記録のみ)
