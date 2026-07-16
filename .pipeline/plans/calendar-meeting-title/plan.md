# 実装計画: カレンダー会議タイトルの一覧表示

## 目的

カレンダーから起動した会議を一覧で人間が識別できるようにし、表示名の優先順位を「手動編集名 → カレンダータイトル → 会議コード」に統一する。

## 現状

- カレンダータイトルは `meeting.data.calendar_event.title` に保存済み。
- 会議一覧APIの軽量レスポンスは `calendar_event` を除外しているため、ダッシュボードへタイトルが届かない。
- カード表示は参加者由来タイトルを会議コードより優先している。

## 実装

1. 会議一覧APIの軽量データへ `calendar_title` を追加する。
2. 一覧検索でもカレンダータイトルを検索対象にする。
3. 完全データレスポンスでは `calendar_event.title` を直接読むフォールバックを設ける。
4. カード表示と編集開始時の値を「手動編集名 → カレンダータイトル → 会議コード」に変更する。
5. 参加者由来タイトルの表示優先を削除する。
6. Meeting APIとDashboardの回帰テストを更新する。

## 対象ファイル

- `services/meeting-api/meeting_api/meetings.py`
- `services/meeting-api/tests/test_meetings.py`
- `services/dashboard/src/types/vexa.ts`
- `services/dashboard/src/components/meetings/meeting-card.tsx`
- `services/dashboard/src/app/meetings/page.tsx`
- `services/dashboard/tests/test_meeting_cards_ui.test.ts`

## 調査判断

外部仕様・最新ライブラリ・法規制には依存せず、リポジトリ内に保存済みのカレンダーメタデータを既存一覧へ接続する変更のため、`research-brief.md` と `option-matrix.md` は省略する。

## 完了条件

- 手動編集名がある場合は常に最優先で表示される。
- 手動編集名がなく、カレンダータイトルがある場合はそのタイトルが表示される。
- どちらもない場合は会議コードが表示される。
- カレンダータイトルで一覧検索できる。
- 対象テスト、型検査、GitNexus変更検査が通る。
