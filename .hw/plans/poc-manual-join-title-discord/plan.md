---
generated_by: fable
task_id: poc-manual-join-title-discord
base_commit: 0ce72b26872269c80df47844978b0672b285b5b1
size: M
runtime: inline
---
# 「会議に参加」モーダルの任意タイトルを Drive 出力と Discord 通知へ通す

## ゴール
ダッシュボードの `JoinModal` で任意の会議タイトルを入力できるようにし、
meeting-api が `meeting.data.meeting_title = {"title": ..., "source": "manual_join"}` として保存する。
Drive ファイル名・Markdown 見出し・`drive_export.completed` payload の `title` は
Google Calendar タイトル > manual_join タイトル > 既存 fallback の優先順で決める。
calendar-service の Discord ゲートは payload を信用せず、同じ DB の `meeting.data` と再照合して
(a) `calendar_event.source == "google_calendar"` のタイトル一致、または
(b) `meeting_title.source == "manual_join"` のタイトル一致のときだけ通知する。
それ以外は外部 I/O も DB 更新も行わず skip。会議 ID や自動 fallback タイトルは Discord へ出さない。

## 確定事項
1. `meeting_title` は任意。strip 後空文字は未指定扱い。API 側で strip、200 文字超は 422。
2. 保存先は `meeting.data["meeting_title"] = {"title": <str>, "source": "manual_join"}`。migration 不要。
   `data["title"]` / `data["name"]` は既存の検索・表示が参照するため使わない。
3. Drive 側のタイトル決定は 1 か所の純関数に集約し、filename / markdown / hook payload の 3 経路で同じ値を使う。
4. Discord 側の判定順は既存どおり `discord_not_configured` → `meeting_not_found` → `duplicate` → タイトルゲート → 外部 I/O。
5. ゲート通過後の Groq 選定・閾値 0.85・default fallback・403/404 再投稿は無改変。
6. タイトル未入力時は会議参加・文字起こし・Drive 保存に影響なし。Discord だけ skip。
7. Zoom OAuth の pending request は `CreateBotRequest` 全体を JSON 保存して再送するため、フィールド追加だけで引き継がれる。追加コード不要。
8. `join-form.tsx` は別の参加フォームで要求外。触らない。

## 仮説と反証条件
- H1: calendar 由来 payload は `_calendar_metadata` を経由し `source == "google_calendar"` を含む。確信度: 高。
  覆る条件: `_calendar_metadata` を経由しない送信経路が見つかった場合。
- H2: calendar-service の既存投稿テストは DB fixture に calendar_event を持たないため、DB 再照合導入時に source 付き metadata の補完が必要。確信度: 高。
  これは期待値緩和ではなく前提データ補完とし、テスト関数・assert は減らさない。
- H3: pydantic の `Field(max_length=200)` は strip 前に効く可能性がある。確信度: 中。
  validator 内で strip → 空なら None → 長さ判定の順にして、前後空白付き200文字をテストする。
- H4: JoinModal の描画テストはなく純関数・ソース検査中心。確信度: 高。
  正規化を純関数へ切り出して単体テストする。

## 対象ファイル
- dashboard: `services/dashboard/src/components/join/join-modal.tsx`, `services/dashboard/src/lib/dashboard-copy.ts`,
  `services/dashboard/src/types/vexa.ts`, 新規 `services/dashboard/src/lib/manual-meeting-title.ts`,
  新規 `services/dashboard/tests/test_manual_meeting_title.test.ts`
- meeting-api: `services/meeting-api/meeting_api/schemas.py`, `services/meeting-api/meeting_api/meetings.py`,
  `services/meeting-api/meeting_api/drive_export.py`, `services/meeting-api/tests/test_url_parser_and_dry_run.py`,
  `services/meeting-api/tests/test_meetings.py`, `services/meeting-api/tests/test_drive_export.py`
- calendar-service: `services/calendar-service/app/discord_notify.py`, `services/calendar-service/tests/test_discord_notify.py`
- ハーネス成果物: `.hw/plans/poc-manual-join-title-discord/**`

## How（実装者向け）
1. dashboard に `normalizeManualMeetingTitle` と `isManualMeetingTitleTooLong` の純関数を追加し、任意型 `meeting_title?: string`、日英コピー、JoinModal 入力欄・空時非送信・200文字境界を実装してテストする。
2. `MeetingCreate` に任意 `meeting_title` と strip/空/200文字 validator を追加し、`request_bot` が指定時だけ由来付き入れ子データを保存する。schema と保存のテストを追加する。
3. Drive に `resolve_export_title` を追加し、calendar > manual_join > fallback を filename / Markdown / hook payload で共有する。payload に監査用 `title_source` を追加してテストする。
4. Discordゲートは DB の calendar_event/manual_join を優先順に再照合する。payload calendar_event は信用しない。calendar一致またはmanual一致だけ既存チャンネル選定へ進め、それ以外は外部I/O・DB更新ゼロでskipする。
5. 既存投稿テストのDB fixtureだけを必要最小限補完し、新しいmanual許可・不一致・calendar優先・payload偽装拒否・fallback拒否を追加する。
6. dashboard typecheck/test、meeting-api focused/full、calendar-service focused/full、GitNexus detect_changes、`.hw/verify.sh` を実行する。
7. commit済みclean treeでFableレビューREADY、続いてpr-ready-gateを通す。

## ロールバック
単一 PR の revert で完結する。DB schema変更なし。追加JSONキーはrevert後の既存コードが無視できる。

## 検証契約
`verification-contract.md` 全通過 + Fable 契約レビュー READY が完了条件。契約は最低合格ライン。

## Why(実装者に渡さない)
- DB 再照合により、送信側が将来 fallback title を payload に入れても、由来付き保存値と一致しない限り投稿されない。
- `source` を保存することで、将来の汎用 title 更新を manual_join と誤認しない。
- calendar 優先により、DriveとDiscordのタイトルが一貫する。
- `data["title"]` を避け、既存検索・表示の影響範囲を広げない。

