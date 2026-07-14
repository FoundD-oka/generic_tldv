# Current State Report: participant-roster-goal

## Known Facts
- 話者由来participantsのみで無言参加者が欠落していた。実装と対象テストは完了し、全体ゲート前の状態

## Issue Goal
- Google Meetで発言しなかった参加者と途中退出者も実参加者として保存し、既存ダッシュボードへ反映する

## Suggested Quality Checkpoint
- ロスター収集・終了保存・手動値保護・旧経路互換が自動テストと変更影響証跡で確認できる

## Quality Conditions
- qc-001: 無言・途中退出参加者を累積する -> bot roster tests pass
- qc-002: 既存callback契約を維持する -> bot callback tests pass
- qc-003: TypeScript全体が型検査とbundle生成に成功する -> bot build passes
- qc-004: terminal・deferred・手動優先・旧fallbackを保持する -> meeting-api targeted tests pass
- qc-005: meeting-apiの既存単体回帰を壊さない -> meeting-api suite passes
- qc-006: 既存ダッシュボード参加者表示契約を維持する -> dashboard contract tests pass
- qc-007: 変更影響が想定範囲内である -> GitNexus change detection passes
