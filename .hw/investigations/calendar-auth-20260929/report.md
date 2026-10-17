# カレンダー自動参加の joining 失敗調査（2026-09-29）

## 結論

今回の会議77は、保存Cookieの復元後にGoogleの再ログイン画面へ遷移した。
Botがそれを検知せず、認証済みMeet参加画面として参加ボタンを60秒待ったため失敗した。
手動の会議79は同じ会議IDにゲストとして参加し、承認後activeになった。
文字起こし開始前の認証・参加処理の問題である。

## 実稼働の証拠

PostgreSQLはBEGIN READ ONLYで照合。Cookie値・認証トークンは取得していない。
時刻は日本時間。会議のURL・参加者名は本報告に含めない。

| 時刻 | 対象 | 確認結果 |
|---|---|---|
| 10:28:01 | カレンダーイベント69087 → 会議77 | authenticated=trueで作成。予定開始10:30の約2分前 |
| 10:28:06 | 会議77の保存bot_logs | cookie-store: Restored 55 cookies |
| 10:28:07 | 会議77の保存bot_logs | MAIN-FRAMEがaccounts.google.com/v3/signin/confirmidentifierへ遷移 |
| 10:28:09 | 会議77の保存bot_logs | Authenticated mode: skipping name input |
| 10:29:21 | 会議77の保存bot_logs | 21個の参加ボタンselectorが全て不一致。申告と同じ60000msエラー |
| 10:29:24 | meeting-apiログ | joining → failed |
| 10:31:52–53 | 会議78（authenticated=true） | 52 Cookieを復元し、同じGoogle再ログイン画面へ遷移 |
| 10:32:22–28 | 会議78 | 停止要求により終了。completedは参加成功を意味しない |
| 10:32:34 | 会議79 | authenticated指定なしで作成 |
| 10:32:40–45 | 会議79 | 名前入力欄を検出・入力し、Ask to joinをクリック |
| 10:32:47–53 | 会議79 | joining → awaiting_admission → active |

DB比較で会議77・78・79のplatform_specific_idの一致を確認。
稼働calendar-serviceの設定はKABOSU_MEET_AUTHENTICATED=true、
KABOSU_CALENDAR_MODE=single_account、KABOSU_BOT_OWNER_USER_ID=3、
DEFAULT_LEAD_TIME_MINUTES=2。

## 実装上の原因

- `services/calendar-service/app/sync.py:278` が自動参加へauthenticated=trueを指定する。
- `services/meeting-api/meeting_api/meetings.py:1183` が所有者別の保存ブラウザ情報を指定する。
- `services/vexa-bot/core/src/index.ts:2567` でプロファイルとCookieを復元する。
  Cookieの読み込み成功と、Google側がセッションを有効と認めることは別である。
- `services/vexa-bot/core/src/platforms/googlemeet/join.ts:150` は設定値を根拠に
  認証済み経路へ入り、再ログイン画面を検知しないまま`:179`で参加ボタンを待つ。
- ダッシュボードの手動参加はauthenticated初期値falseで、UIの認証済み参加スイッチも無効。
  実際の会議79もゲスト経路を通った。
- 終了処理`services/vexa-bot/core/src/index.ts:915`は認証失敗時にもCookieを保存する。
  会議77で55個復元後52個保存、次の会議78で52個復元した事実はあるが、
  個数の減少が失効原因とは断定できない。

## 未確定事項と対応順

Googleが再ログインを要求した理由（期限切れ、失効、追加確認など）は未確定。
過去の全失敗が同原因という証明もしていない。

1. 認証済み参加を維持するなら、Bot用ブラウザでGoogleへ再ログインして保存し、
   新しく起動した別Botでもログイン状態を復元できることを検証する。
2. 実装修正ではGoogleログイン画面を検知して再認証が必要と明示する。
   認証できたことを確認するまで、正常な保存プロファイルを上書きしない。
3. 暫定的に手動と同じゲスト経路へ統一する選択肢はあるが、主催者の承認が必要になり、
   ゲスト禁止の会議には入れないため、認証済み参加と同じ保証にはしない。

## 実施範囲

アプリコード・稼働設定・会議状態の変更、再ログイン、新規Bot起動、再起動は未実施。
調査報告のみ追加。GitNexusの索引を更新してcontextを参照したが、FTS構築失敗と
process列挙の上限警告があり、今回の結論は実コード・保存ログ・DB照合を根拠とする。
