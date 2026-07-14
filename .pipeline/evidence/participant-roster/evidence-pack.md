# Evidence Pack: participant-roster-cp-001

## Target State

ロスター収集・終了保存・手動値保護・旧経路互換が自動テストと変更影響証跡で確認できる

## Summary

This pack summarizes the machine-readable Evidence Manifest for checkpoint review.

## Quality Conditions

- [pass] qc-001: 無言・途中退出参加者を累積する -> bot roster tests pass (passed)
  Evidence: command:bot-test
- [pass] qc-002: 既存callback契約を維持する -> bot callback tests pass (passed)
  Evidence: command:bot-test
- [pass] qc-003: TypeScript全体が型検査とbundle生成に成功する -> bot build passes (passed)
  Evidence: command:bot-build
- [pass] qc-004: terminal・deferred・手動優先・旧fallbackを保持する -> meeting-api targeted tests pass (passed)
  Evidence: command:api-target
- [pass] qc-005: meeting-apiの既存単体回帰を壊さない -> meeting-api suite passes (passed)
  Evidence: command:api-all
- [pass] qc-006: 既存ダッシュボード参加者表示契約を維持する -> dashboard contract tests pass (passed)
  Evidence: command:dashboard-test
- [pass] qc-007: 変更影響が想定範囲内である -> GitNexus change detection passes (passed)
  Evidence: command:gitnexus

## Acceptance Criteria Status

- [pass] qc-001: bot roster tests pass (passed)
  Evidence: command:bot-test
- [pass] qc-002: bot callback tests pass (passed)
  Evidence: command:bot-test
- [pass] qc-003: bot build passes (passed)
  Evidence: command:bot-build
- [pass] qc-004: meeting-api targeted tests pass (passed)
  Evidence: command:api-target
- [pass] qc-005: meeting-api suite passes (passed)
  Evidence: command:api-all
- [pass] qc-006: dashboard contract tests pass (passed)
  Evidence: command:dashboard-test
- [pass] qc-007: GitNexus change detection passes (passed)
  Evidence: command:gitnexus

## Evidence Manifest

- Manifest: .pipeline/evidence/participant-roster/evidence-manifest.json
- Base SHA: 8cb85dbdf3bd76affdb593120ea0ff5156f47ad4
- Head SHA: 8cb85dbdf3bd76affdb593120ea0ff5156f47ad4
- Branch: codex/participant-roster
- Worktree: /Users/bonginkan-3-gouki/project/generic_tldv/.pipeline/worktrees/participant-roster/checkout

## Verification Commands

| Command | Required | Exit Code | Log |
|---|---:|---:|---|
| `bot-test` | true | 0 | `.pipeline/evidence/participant-roster/logs/bot-test.log` |
| `bot-build` | true | 0 | `.pipeline/evidence/participant-roster/logs/bot-build.log` |
| `api-target` | true | 0 | `.pipeline/evidence/participant-roster/logs/api-target.log` |
| `api-all` | true | 0 | `.pipeline/evidence/participant-roster/logs/api-all.log` |
| `dashboard-test` | true | 0 | `.pipeline/evidence/participant-roster/logs/dashboard-test.log` |
| `gitnexus` | true | 0 | `.pipeline/evidence/participant-roster/logs/gitnexus.log` |

## Artifacts

| Artifact | Exists | Path |
|---|---:|---|

## Scope Result

- Changed files: 21
- Forbidden paths changed: 0
- Outside allowed paths: 0

## Missing Evidence

- none

## Decision Needed

- Approval state: pending
- Approval record: .pipeline/approvals/participant-roster/approval-decision.json
- approve / request changes / split smaller / change scope

## Generated

2026-07-14T07:33:13.090032+00:00
