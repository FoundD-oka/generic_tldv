# 本番ブラウザ確認

対象: Cloud Run revision `kabosu-dashboard-00070-ltj`、会議54、録音841188337344。2026-09-07 14時台 UTC (JST 23時台)、Codex in-app Chromium。

- 再読み込み後: duration=3381.024 (56:21)、readyState=4、音声読み込み失敗UIなし。
- UIのミュートを有効にして先頭から再生。currentTime=50.621556、paused=false、readyState=4を観測。先頭10秒以上の進行を確認。
- UIのシークバーをHome、PageUp×5で中央1690.5秒へ移動。currentTime=1743.74979、paused=false、readyState=4を観測。中央10秒以上の進行を確認。
- シークバーfillだけでは音声位置に反映されないため、3350.9をfill後ArrowRightで3351秒(55:51)へ実際にシーク。終端まで待ち、currentTime=3381.024、duration=3381.024、ended=true、paused=true、readyState=4、error=null、errorVisible=falseを確認。
- 開発者Networkパネルは使用していない。実ブラウザのChrome User-Agentからの録音master取得について、Cloud RunのHTTPアクセスログで206の連続を確認する (`browser-request-log.json`)。Range本文とヘッダは別途 `http-checks.txt` で実測。
- WebMダウンロードをUIから実施。最初のdownloadイベント待機はCUAツールの30秒タイムアウトとなったが、実際のファイル保存は完了していた。状態復帰後の確認操作でも全量保存できた。2ファイルとも52,676,203 bytesで元録音の全サイズと一致し、全文SHA256も一致。先頭64KiBのSHA256は調査時backend値と一致 (`browser-download-files.json`)。進捗100%のトーストは捕捉していないため、100%の証拠は保存ファイルの全量一致とする。

音声の終端到達画面 (会議内容を含まないプレイヤー部分):

![終端56:21まで正常再生](playback-ended.jpg)

アクセスログ: media proxyへのChrome要求23件すべて206。別途masterメタデータ要求3件は200。対象時間帯 2026-09-07T14:47:59.961295Z〜2026-09-07T14:58:32.690560Z。responseSizeはHTTPヘッダ込みなので、本文の上限はhttp-checks.txtのContent-Lengthで判定する。
