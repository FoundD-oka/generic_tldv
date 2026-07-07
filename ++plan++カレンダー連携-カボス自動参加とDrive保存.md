---
name: カレンダー連携-カボス自動参加とDrive保存
status: draft
priority: high
---

## 背景・課題
<!-- なぜこのプランが必要なのか -->

現状、カボスを会議に参加させるにはダッシュボードから毎回手動で参加操作が必要。ユーザーの自然な動線は「Googleカレンダーで打ち合わせを組むとき、参加者にカボスのアドレスを足すだけ」であり、そこで完結させたい。また会議後の議事録(最終文字起こし)も、ダッシュボードを開かずチームの共有フォルダ(Google Drive)に自動で届く状態にしたい。

Vexa本体には `services/calendar-service`(507行)が存在するが、(1) **各ユーザーが自分のカレンダーをOAuth連携する**モデルであり「カボスを招待するだけ」モデルではない、(2) composeで `NO-SHIP for 0.10` としてコメントアウトされ未デプロイ・DoD未テスト、(3) 会議後のファイル出力機能は存在しない。

## ゴール
<!-- このプランで達成したいこと -->

1. Googleカレンダーの予定の参加者に **e@bonginkan.ai** を追加すると、会議開始時刻(リードタイム前)にカボスが自動で会議に参加する。
2. 会議が終了しカボスが退出した後、**正式版文字起こし(録音からの再転写)の完成を待って**、議事録ファイルを指定のGoogle Driveフォルダに自動保存する。

## 要件
<!-- 具体的な要件をリストアップ -->

- **招待→自動参加**
  - カボス専用Googleアカウント(e@bonginkan.ai)のカレンダーを単一アカウントとして同期する(ユーザーごとのOAuth連携は不要)。招待された予定は招待側の操作なしでカボスのカレンダーに現れる性質を利用する。
  - Google Meet URLを持つ予定を対象に、開始 `DEFAULT_LEAD_TIME_MINUTES`(既定2分)前にbotを作成する。Zoom/Teams URLは既存の `extract_meeting_url` が対応済みのため受け付けるが、動作保証はMeetのみ。
  - bot作成パラメータ: `bot_name=カボス`、`language=ja`、`voice_agent_enabled=true`(issue #7/#8/#10 の既定値統一と整合させる)。
  - 予定のキャンセル・時刻変更・カボスの招待解除に追従する(既存の incremental sync + upsert を利用)。同一予定への二重参加をしない(既存の `status="scheduled"` 管理を利用)。
  - **bot所有者**: calendar-service が作成するbotは、wake-orchestrator の `VEXA_API_KEY` と同一ユーザーに属すること(そうでないと会議中のカボス応答が機能しない。issue #14 の所有者スコープ制約)。
- **退出→Drive保存**
  - 正式版文字起こし完了(`final_transcription` success)を契機に議事録をエクスポートする。issue #6 の置換ガードで `skipped_no_speaker_events` になった場合はリアルタイム版transcriptをエクスポートする(保存されないよりよい)。
  - 出力形式: Markdown 1ファイル。ファイル名 `YYYY-MM-DD_HHmm_<会議タイトル>.md`(タイトルはカレンダー予定から。無ければ native_meeting_id)。内容: 会議メタ(日時・タイトル・参加者)+話者ラベル付き全文。
  - 保存先: 環境変数 `KABOSU_DRIVE_FOLDER_ID` で指定した単一のDriveフォルダ(初期実装)。Drive APIはカボスアカウントの同一OAuthトークン(scope: `calendar.events` + `calendar.readonly` + `drive.file`)を使う。招待予定の読み取りだけなら `calendar.readonly` で足りるが、Discord/Codexからテスト予定を作成するには `calendar.events` が必要。
  - アップロード失敗はリトライされること(final_transcription と同じ sweep パターンで `meeting.data["drive_export"]` にステータス管理)。
  - エクスポート対象はカレンダー起点の会議のみ(初期実装)。ダッシュボード手動参加の会議は対象外。
- **デプロイ**
  - calendar-service のコメントアウトを解除し、composeプロファイル(例: `calendar`)で起動可能にする。必須env(`GOOGLE_CLIENT_ID/SECRET`、カボスアカウントの refresh token、`KABOSU_DRIVE_FOLDER_ID`、`BOT_API_TOKEN`)を env-example に追記する。

## 設計方針
<!-- アプローチ・技術選定 -->

**方式A: カボス専用アカウントの単一同期(採用)**

既存 calendar-service の同期エンジン(`app/sync.py` の incremental sync / `CalendarEvent` upsert / `schedule_upcoming_bots` の meeting-api `/bots` 呼び出し)をそのまま再利用し、認証モデルだけを差し替える:

- 現行: `users.data.google_calendar.oauth.refresh_token` を全ユーザー分ループ
- 変更: 環境変数(または管理設定)で与えるカボスアカウント単一の refresh token で同期する「サービスアカウントモード」を追加。`KABOSU_CALENDAR_MODE=single_account` のときは per-user ループをスキップし、取得イベントを固定の所有ユーザー(=wake-orchestrator と同じユーザー)のbotとしてスケジュールする。既存の per-user モードはコードとして残す(削除しない)。

対案として方式B(Google Workspaceのドメインワイド委任+Service Account)も検討したが、Workspace管理者設定の負担が大きく、招待ベースのUXには単一アカウントのrefresh tokenで十分なため見送り。

**Drive保存の実装位置**

meeting-api 側にエクスポートを置く(calendar-service ではなく):
1. `final_transcription` の成功/skip 時に `meeting.data["drive_export"]={status:"queued"}` を記録(カレンダー起点の会議のみ。`CalendarEvent` との紐付けで判定)。
2. 既存の sweep ループに `_sweep_drive_export_jobs` を追加し、queued を拾って transcript を整形→Drive `files.create`(multipart upload)→ `status:"done", file_id, web_view_link` を記録。リトライ・上限は final_transcription sweep と同型。
3. Drive APIクライアントは httpx 直叩き(google-api-python-client の依存追加を避ける。トークンrefreshは calendar-service の `refresh_access_token` と同型)。トークンは calendar-service が管理するため、meeting-api からは calendar-service の内部エンドポイント(新設 `GET /internal/kabosu-token`)経由で取得するか、同じ refresh token を両サービスのenvに配る(初期実装は後者のシンプル案)。

**Google側の準備(運用手順としてプランに含む)**

1. Google Workspace で e@bonginkan.ai アカウントを用意。
2. GCPプロジェクトで OAuth クライアント(デスクトップ or Web)を作成し、`calendar.events` + `calendar.readonly` + `drive.file` スコープで e@bonginkan.ai として同意→refresh token を取得(取得用ワンショットスクリプトを `scripts/` に追加)。
3. 保存先Driveフォルダを作成し、フォルダIDを `KABOSU_DRIVE_FOLDER_ID` に設定。カボスアカウントに編集権限を付与。
4. Workspaceの「外部からの招待の自動表示」設定を確認(外部ドメインからの招待がカレンダーに出ない設定だと動かない)。

**S/M/L見込み**: L(新規外部連携=Drive API、composeで未出荷サービスの有効化、meeting-api sweepへの追加を含む)。ハーネス規約に従い、実装時に `.pipeline/plans/` へ正式プラン+検証契約を作成し、Lゲート(tribunal review)を通す。

## スコープ外
<!-- 明示的にやらないこと -->

- ユーザーごとのGoogleカレンダーOAuth連携UI(既存モデルの改善)
- Zoom/Teams招待の動作保証(コード上は受け付けるがE2E対象外)
- Drive上の権限管理・共有設定の自動化(フォルダの共有設定は運用で行う)
- 議事録の要約・整形(全文保存のみ。要約は将来プラン)
- ダッシュボード手動参加会議のDrive保存
- カレンダー予定への「参加します」自動返信(auto-accept)

## 懸念・リスク
<!-- 想定されるリスクや未解決事項 -->

- **calendar-service は未テスト出荷停止品**(README の DoD が全部 untested)。再有効化にあたり最低限の統合テスト(同期→CalendarEvent upsert→bot作成のモックE2E)を検証契約に含める必要がある。
- **依存issue**: #7(bot名既定)、#8(voice_agent_enabled既定)、#10(言語ja既定)が先に入っていないと、カレンダー起点botの名前・言語・音声応答が揃わない。#6(置換ガード)のステータス値にエクスポータが依存する。
- **refresh token の失効**(パスワード変更・6ヶ月未使用等)で同期が静かに止まる。ヘルスチェック(最終同期成功時刻の監視ログ/エンドポイント)を入れる。
- 外部ドメイン主催の会議では、Workspace設定によってはカボスのカレンダーに予定が現れない/Meetのゲスト承認(ノック)で入室が止まる。既存botのadmitted検知はあるが、誰も承認しないと参加失敗する—これは仕様として明記。
- タイトル由来のファイル名のサニタイズ(絵文字・スラッシュ等)。
- 同一会議URLで複数予定がある場合の重複判定(既存 `CalendarEvent` の event_id ベース upsert で概ね吸収できる想定だが要確認)。

## 参考
<!-- 関連ドキュメント・URL -->

- `services/calendar-service/README.md`(既存実装の説明・DoD)
- `services/calendar-service/app/sync.py` / `app/google_calendar.py`(再利用する同期エンジン)
- `deploy/compose/docker-compose.yml:588`(NO-SHIPコメントアウト箇所)、`:229`(api-gatewayの `CALENDAR_SERVICE_URL` は配線済み)
- `services/meeting-api/meeting_api/final_transcription.py` / `sweeps.py`(エクスポートのフック先・sweepパターン)
- 関連issue: #6(置換ガード)、#7(bot名)、#8(voice_agent_enabled)、#10(言語ja)、#14(APIキー所有者スコープ)
- Google Drive API `files.create`(multipart)、OAuth scopes: `https://www.googleapis.com/auth/calendar.events`, `https://www.googleapis.com/auth/calendar.readonly`, `https://www.googleapis.com/auth/drive.file`
