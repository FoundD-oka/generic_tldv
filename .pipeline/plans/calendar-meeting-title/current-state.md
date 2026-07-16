# Current State Report: calendar-meeting-title-goal

## Known Facts
- カレンダータイトルは保存済みだが一覧APIで除外され、参加者名または会議コードが表示される

## Issue Goal
- カレンダー起動会議を一覧でカレンダータイトルにより識別できるようにする

## Suggested Quality Checkpoint
- 手動編集名、カレンダータイトル、会議コードの優先順位がAPIと画面で検証できる

## Quality Conditions
- qc-api: 一覧APIが保存済みカレンダータイトルを返し検索対象にする -> Meeting API対象テストが通る
- qc-ui: カード表示が手動編集名、カレンダータイトル、会議コードの順になる -> Dashboard対象テストが通る
- qc-types: API追加項目とカード参照の型が整合する -> TypeScript型検査が通る
- qc-scope: 変更影響が会議一覧経路に限定される -> GitNexus変更検査が通る
