# 会議動画への音声合成

## 原因と変更

Meet・Teams・Zoom Web の音声は分割アップロード後にサーバーで結合される。
Bot の終了処理に残っていたローカル音声ファイルとの合成は、この経路では実行されず、
画面収録だけが無音で保存されていた。

サーバーの録画確定処理で、同じ recording の混合音声マスターを先に作り、映像と合成する。
出力は `video/master.av.webm` / `master.av.mp4` / `master.av.mkv`。
映像ストリームは再圧縮せず、音声を WebM では Opus、MP4/MKV では AAC にする。
既存の再生・ダウンロードAPIが同じ合成済みファイルを配信する。元の映像と音声は保持する。

録音開始イベントで得た `start_time_utc` を分割・一括の音声アップロードに付け、映像の既存の開始時刻と
ともにAPIで保存する。音声が遅く始まれば冒頭に無音を補い、早く始まれば先行部分を切る。
映像の実際の長さを ffprobe で読み、音声の末尾を必要なだけ無音で補うため、音声終了が早くても
映像は切り詰めない。入力の時間範囲が重ならない場合や音声ストリームがない場合は失敗させる。

成功した合成には `video_audio_mux` を記録する。失敗した処理は成功メタデータを公開せず、
音声の再生URLだけが既に存在する場合も、既存の録画修復スイープが未合成動画を再試行する。
スイープの既存の対象件数・取得順は変更していないため、過去の全録画の一括修復は保証しない。

## 検証

FFmpeg / ffprobe と meeting-api のテスト依存関係が必要。

```sh
python -m pytest services/meeting-api/tests/test_record*.py \
  services/meeting-api/tests/test_sweeps_unfinalized_recordings.py \
  services/meeting-api/tests/test_video_audio_mux.py -q
```

実ファイルで WebM/Opus と MP4/WAV、音声開始の前後差、動画全フレームの保持、
チャンク結合から配信URLまでの経路、合成失敗と再試行、旧データ、音声ストリーム欠落を検証する。
`recording-capture-time.test.ts` は実HTTPアップロードを使い、文字起こしが無効でも開始時刻が
遅延アップロードや重複開始通知で変化しないことを検証する。Bot の `npm test` に登録済み。

Bot の依存関係は `core` 内で `npm ci --workspaces=false` により単体のロックファイルから導入する。
親workspaceに巻き込まれたインストールでは `playwright-core` が解決できず、`browserInstance`
の型エラーも連鎖していた。単体の依存関係を復元した後、コード変更なしでビルド成功を確認した。

## 適用と限界

新規録画の同期には meeting-api と Bot の両方への反映が必要。
本番反映は後続の明示依頼に従い `docs/video-audio-mux-release-request.md` の範囲で行う。
旧録画で両方の開始時刻が保存されていない場合は両方を時刻0から合成し、ログに明示する。
片方だけ開始時刻が欠けている場合は、同期位置を確定できないため合成を失敗として再試行対象に残す。
その場合の同期精度は保証できない。取得時点のアップロード時刻を録音開始時刻の代用にはしない。
