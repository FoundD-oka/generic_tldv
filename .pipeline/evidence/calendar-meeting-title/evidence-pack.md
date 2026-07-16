# Evidence Pack: calendar-meeting-title-cp-001

## Target State

手動編集名、カレンダータイトル、会議コードの優先順位がAPIと画面で検証できる

## Summary

This pack summarizes the machine-readable Evidence Manifest for checkpoint review.

## Quality Conditions

- [pass] qc-api: 一覧APIが保存済みカレンダータイトルを返し検索対象にする -> Meeting API対象テストが通る (passed)
  Evidence: command:api-test
- [pass] qc-ui: カード表示が手動編集名、カレンダータイトル、会議コードの順になる -> Dashboard対象テストが通る (passed)
  Evidence: command:dashboard-test
- [pass] qc-types: API追加項目とカード参照の型が整合する -> TypeScript型検査が通る (passed)
  Evidence: command:dashboard-type
- [pass] qc-scope: 変更影響が会議一覧経路に限定される -> GitNexus変更検査が通る (passed)
  Evidence: command:gitnexus

## Acceptance Criteria Status

- [pass] qc-api: Meeting API対象テストが通る (passed)
  Evidence: command:api-test
- [pass] qc-ui: Dashboard対象テストが通る (passed)
  Evidence: command:dashboard-test
- [pass] qc-types: TypeScript型検査が通る (passed)
  Evidence: command:dashboard-type
- [pass] qc-scope: GitNexus変更検査が通る (passed)
  Evidence: command:gitnexus

## Evidence Manifest

- Manifest: .pipeline/evidence/calendar-meeting-title/evidence-manifest.json
- Base SHA: 2d3591ce2c086f75aee23e1b5defa4f7362f4f14
- Head SHA: ebe159dcd9f74496c0d171dde7656943b3fe5b16
- Branch: harness/calendar-meeting-title
- Worktree: /Users/bonginkan-3-gouki/project/generic_tldv/.pipeline/worktrees/calendar-meeting-title/checkout

## Verification Commands

| Command | Required | Exit Code | Log |
|---|---:|---:|---|
| `api-test` | true | 0 | `.pipeline/evidence/calendar-meeting-title/logs/api-test.log` |
| `dashboard-test` | true | 0 | `.pipeline/evidence/calendar-meeting-title/logs/dashboard-test.log` |
| `dashboard-type` | true | 0 | `.pipeline/evidence/calendar-meeting-title/logs/dashboard-type.log` |
| `gitnexus` | true | 0 | `.pipeline/evidence/calendar-meeting-title/logs/gitnexus.log` |

## Artifacts

| Artifact | Exists | Path |
|---|---:|---|

## Scope Result

- Changed files: 10
- Forbidden paths changed: 0
- Outside allowed paths: 0

## Missing Evidence

- none

## Decision Needed

- Approval state: pending
- Approval record: .pipeline/approvals/calendar-meeting-title/approval-decision.json
- approve / request changes / split smaller / change scope

## Generated

2026-07-16T13:56:14.403057+00:00
