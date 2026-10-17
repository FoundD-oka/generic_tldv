# Google認証失敗時のゲスト参加と履歴表示

## 実装した動作

- authenticatedなGoogle Meet Botで、Googleアカウント画面への遷移、またはMeetの未認証の名前入力欄を検出した場合だけゲスト参加へ切り替える。
- 切り替えは同じBot・同じ会議で1回のみ。読み込んだCookieとGoogleのローカルストレージをBotのブラウザ内で消去し、アカウント選択パラメータを除いたURLから通常のゲスト参加を行う。
- 保存元の認証プロファイルを削除しない。ゲスト切り替え後は終了時のプロファイル書き戻しも行わない。
- 入室拒否、ネットワークエラー、参加ボタンが見つからないだけの場合は認証失敗と決めつけず、従来の失敗処理を維持する。
- `google_auth_fallback`（理由と初回検知時刻）をcallbackで伝え、会議のJSONBへ保存する。joiningのまま再試行するcallbackは状態遷移を追加しない。
- 履歴はactive・completed・failedへの遷移や停止要求が競合しても保持する。カードに「Googleへのログイン失敗」を会議ステータスと別の警告色バッジで表示する。

## 検証

実行ログ: `.hw/evidence/google-auth-guest-fallback/`

- Bot `npm test`: 成功。既存の参加・承認・拒否テストを含む。
- 新規Chromiumテスト: 認証切れ、未認証画面、遅れて発生するGoogleログインリダイレクト、ゲスト参加も失敗、有効な認証、通常のゲスト参加、ボタン未検出、拒否画面、通信失敗の9ケースを通過。実際の会議へは接続せず、隔離ブラウザ内のHTTP fixtureのみで検証。
- API全体: 861 passed、19 skipped（既存のskipを変更していない）。初回は子プロセスのPYTHONPATH不足で7件失敗。変更前HEADの対象63件と、正しいPYTHONPATHでの変更後全体を通過し、コード変更なしで解消。
- Dashboard全体: 449 passed。新規カード描画テスト7件で、待機・成功・終了・失敗後の履歴表示と、対象外の非表示を確認。
- Dashboard型検査・対象eslint: 成功。削除済みルートを参照する旧`.next/dev/types`を一時ディレクトリへ退避し、`next typegen`後に型検査を通過。
- hw verify: 成功。これは未commitの作業ツリーに対する開発中検証で、clean HEADの最終gateではない。
- GitNexus: 古い増分索引の不整合によりfile指定impactでUNKNOWNが出たため、実呼び出しをテキストでも確認し、索引をforce rebuild。再構築後のjoinGoogleMeetingはLOW。共通callback・カード経路を含む全差分はCRITICALとしてレビュー。`detect-changes --scope all --limit 1000`を新規ファイル込みで実行。process生成の上限があるため、網羅性の証明には使わない。

## 未実施

commit・clean HEADの最終gate・実稼働へのデプロイ・実会議での入室試験は未実施。
今回より前の失敗履歴への推測によるバッジ付与は行っていない。
反映時は新しいcallback項目とjoining再通知を受け付けるmeeting-apiを先に更新し、その後Botイメージとダッシュボードを更新する。
