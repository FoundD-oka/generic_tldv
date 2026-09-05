# 調査根拠 — 初期表示・終了済み会議一覧・録音再生

基準HEAD: `67ea03210c2de4c8723780402d302948b138d939`。2026-09-05。
ソースは変更していない。本番へのアクセス、実ユーザーの会議・録音・ログの取得、依存インストール、テスト・ビルド・デプロイは実施していない。実際の所要秒数・エラー率は未計測。

## 調査範囲

Git管理対象2,140ファイルの配置を棚卸し。実行コード・テスト・CI・配置設定を928ファイル / 190,734行、機械走査。PythonはAST、JS/TSはimportと関数宣言の抽出。画像・録音・lockfile・旧`.pipeline/`・過去の計画書はコード解析対象から除外。主要経路の本文と既存テストを精読。これは928ファイルすべての全行を人が意味解釈したという主張ではない。

GitNexus 1.6.11の索引は同じHEADで、1,154 covered files一致。`query 'meeting list recording playback dashboard loading'`からmaster配信・一覧・詳細のexecution flowを特定し、`context getRecordingMasterStreamUrl`とimpactを実行。動的・HTTP越しの依存はソース文字列とrouter登録で補完。

## 確認した事実と症状への示唆

|ID|種別 / 根拠（基準HEAD）|コード上の事実|症状への示唆・確度|
|---|---|---|---|
|F01|重複 / `services/dashboard/src/app/meetings/page.tsx:126–137`|mount時の取得effectとfilter effectの両方がfetchMeetingsを実行する。|初回の同一一覧要求が重なる。コード上確定。本番での寄与率は未計測。|
|F02|エラー処理 / `services/dashboard/src/app/api/vexa/[...path]/route.ts:132–183`|/botsが非2xxまたは5秒で失敗すると/bots/statusにfallback。後者にtimeoutなし。失敗しても200の空配列を返す。|終了済み会議を含まない別データ集合を正常な履歴に見せる。認証失敗・402も消える。|
|F03|転送責務 / `services/api-gateway/main.py:322–419`|client.requestをawaitした後resp.contentをResponseへ渡す。rawも同じhelper。BFFの直接raw/MP3はJSON416のContent-Rangeを落とす（route.ts:379–420）。|Meeting APIのwindow streamingがGatewayで全量bufferに戻る。録音サイズに比例する待ち/メモリ候補。|
|F04|重複 / `services/meeting-api/meeting_api/recordings.py:708–757,795–876`|masterで録音を検索後download_media_fileを呼び、同じ所有者付き検索を再実行。|master解決ごとのDB往復増。副作用のない共通resolverで削減可能。|
|F05|非同期処理の穴 / 同 `recordings.py:839–859`|async handler内で同期SDKのfile_existsとget_presigned_urlを直接呼ぶ。|署名・存在確認が遅いと同一workerの別一覧/音声requestも待つ可能性。raw range読込は既にto_thread。|
|F06|責務重複 / `hooks/use-meeting-polling.ts:10–88` と `hooks/use-meeting-live-data.ts:21–26`|setIntervalはPromise完了を待たない。成果物の3呼出しをreturn/awaitせず、stoppingでstatusとartifact pollが併存。初回bootstrapとchatも重複する。|遅いサーバーほど未完了requestが積み上がる。|
|F07|競合 / `stores/meetings-store.ts:264–380,431–440,511–539`|詳細successのgenerationチェックは一部のみ。失敗の遅着・clearCurrentMeeting・transcript/chatに同等のguardなし。|会議を切り替えると旧会議の録音/テキスト/errorが新画面に流入しうる。|
|F08|責務混在 / `hooks/use-meeting-playback.ts:36–82`|同値のrecordings配列でもeffect再実行。audio全体がPromise.allでall-or-nothing。audio/videoが共通errorをset/clearする。|URL再解決が増え、片方の成功で他方の失敗が消える。1録音障害で全体が空になる。ただし単純な部分再生化は時系列を変えるので禁止。|
|F09|直値・回復処理 / `components/recording/audio-player.tsx:237–247,309–322`|errorイベントごと1500ms後にloadを再実行。回数上限なし。手動retryは既にある。|永続404や対応外codecでも自動要求が続く。MediaErrorだけではHTTP原因を識別できない。|
|F10|認証エラー分類 / `services/api-gateway/main.py:427–466,1848–1865`、`app/api/auth/me/route.ts:30–53`、`stores/auth-store.ts:225–309`|Gateway resolverが内部認証403/5xx/接続例外をNoneへ変換し/auth/meが401にする。upstream非2xxをすべて401へ変換しCookieを削除。ネットワーク例外は500だがstoreは非401もunauthorized寄りに扱う。fetchに明示deadlineなし。|一時障害が再ログインへ連鎖。初期表示待ちが有界でない。認証を省略して速くする変更は不可。|
|F11|データ読取量 / `meeting_api/meetings.py:1410–1510`|出力は要約済みだがselect(Meeting)で大きいdata JSONBを全取得後にPythonで要約。|DB→APIの量は減っていない。DB projection/indexは実行計画・データ型の証拠を取得して別計画化。現時点で主要因と断定しない。|
|F12|重複 / `lib/api.ts:63–81` と `stores/meetings-store.ts:29–41`|一時エラーの判定ロジックが重複。|将来の分類差。即時の大規模api client分割はせず、該当修正のテストが守れる範囲のみ共通化候補。|
|F13|命名 / `lib/api.ts:85–114`、`meeting_api/models.py:41–47`|wireのnative_meeting_idとUIのplatform_specific_id、数値idと文字列idの変換境界がある。|互換adapter。単なる命名不統一として一括renameしてはいけない。|
|F14|デッドコード候補 / `lib/api.ts:523–607`|旧audio/video URL helper群に現行UIからの直接参照が見つからないものがある。|object methodの動的参照を索引で解決できない。未使用の断定・削除はしない。|
|F15|巨大関数 / `meeting_api/meetings.py:754–1310`|request_botが557行。|会議作成に多くの責務。今回の読み込み改善の変更対象外。|
|F16|巨大関数 / `services/transcription-service/gemini_adapter.py:1778–2616,2619–3231`|境界解決839行・segment結合613行。|録音生成/文字起こし品質の重要部。巨大なことだけを根拠に今回分割しない。|
|F17|巨大関数 / `meeting_api/final_transcription.py:1195–1698`|run_deferred_transcriptionが504行。|確定文字起こしworker。閲覧pathの修正から切り離す。|
|F18|エラー処理 / `lib/single-flight-polling.ts:14–29`|runはfinallyだけでcatchなし。timerからvoid runする。|rejectを返す利用者ではunhandled rejection。変更時は既存再文字起こしpollerも検証。|

## 既に対策されているので壊してはいけない箇所

- 一覧：50件page、limit+1によるhas_more、offsetは表示件数ではなくAPI page単位、重複id排除、一覧の要約とdetailの完全データの分離、silent refreshのin-flight抑止。
- 録音：canonical masterだけ再生。master未生成の404はnull。GCS等で署名できない場合は認証付きraw URL。lane mediaは内部用で公開しない。
- raw/MP3：200/206/416、Content-Range・Content-Length・Accept-Ranges、8MiBのwindow read、blocking SDKのrange読込はto_thread。MP3は変換のため180秒timeout。
- Nextのmaster proxy：raw URLを優先、range転送、headers取得後にtimerを解放してbody streamingを続ける実装とテストがある。
- AudioPlayer：React listener登録前にmetadataイベントが終わる場合のreadyState再確認と手動retryが既存。
- 認証：ブラウザ永続化のtoken/isAuthenticatedを信用しない変更が基準HEADに存在。401とネットワーク不明状態を分ける方向を維持。

## 変更影響の根拠

|対象|graph判定|到達経路|
|---|---|---|
|Gateway forward_request|CRITICAL / 61 direct callers|Bot、録音、Calendar、テキスト、Agent等。録音3routeへのopt-inが必要。|
|Next proxyRequest|MEDIUM / 5 direct callers|HTTP method exports。認証・声紋body制限・MP3も含む。|
|useMeetingPlayback|LOW / 1 direct caller|MeetingDetailPage。UIテストを別途必要とする。|
|download_media_file|LOW / 1 direct caller|get_recording_master。HTTP direct downloadにも公開されておりgraphより広い。|
|startImmediateIntervalPolling|LOW / 3 impacted|useMeetingPolling→useMeetingLiveData→MeetingDetailPage。|
|list_user_bots|UNKNOWN / 0 callers|@router.get('/bots')、Gateway list_bots_proxy、BFF /meetings。未使用ではない。|
|_resolve_token|MEDIUM / 73 impacted、6 direct|auth_meだけstrictモードを追加。保守的に高リスクとして計画。|
|AudioPlayer|LOW / 1 direct caller|MeetingDetailPage。media eventとtimerは動的なためtestが必要。|
|fetchMeetings / checkAuth|UNKNOWN / 名前の複数候補がすべて0|store object methodの参照とeffectを本文で確認。0は未使用を意味しない。|
|fetchTranscripts|UNKNOWN / name解決不成立|store object method。useMeetingLiveData/useMeetingPolling/詳細actionsからの参照を本文で確認。|

## 検証基盤の注意

`.ai/BUILD.md`のinstall/test/build欄は未記入。入口はMakefileだがrootの`make build`はpublishを含むのでローカル検証には不適切。具体コマンドは`.github/workflows/test-{dashboard,api-gateway,meeting-api}.yml`を根拠にする。
`make test`はtests3差分選択で、frontendやstreamingの動作テストをすべて保証するものではない。
`.hw/verify.sh`は既知baselineとの差を判定する。新規失敗をbaseline追加で隠さない。
`test_meeting_polling.test.ts`は旧pollerの重なりを明示的に固定している。移行時にこのテストを削除/期待値緩和する方式を採らない。

## 未取得の証拠

本番HAR、TTFB、DB EXPLAIN、録音サイズ別RSS、upstream 5xx、storage credential、配備イメージのHEADは未確認。したがって、今回の計画は構造から確認できた問題を対象にする。速度改善率や本番の完全解消を保証しない。
