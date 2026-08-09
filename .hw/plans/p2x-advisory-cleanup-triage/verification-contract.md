# Verification Contract — p2x-advisory-cleanup-triage

本タスクは選別記録のみでコード変更なし。PR 単体では出さず、後続タスクの
`plan(hw):` コミットに同乗する。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-T01 | plan.md に 25 件(番号 1〜19 + P1-2 4件 + P1-4 2件)全ての採否と理由が列挙されている | source check | ゲート | plan.md |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-T01 | `.hw/plans/p2x-advisory-cleanup-*` 以外に差分がない | `git diff --name-only` | ゲート | diff |

## Gate Requirements

- preflight result required: no(コード変更なし)
- evidence pack required: no
- hash-bound approval required: no(S・記録のみ)
