# Builder依頼: Whisper推論中のAPIイベントループ停止を修復

原要求・合意は intent.md、受入条件は .hw/tasks/realtime-transcription-quality.json。
Whisper HTTP API を維持して品質と実行環境を改善する依頼の一部。

担当範囲は services/transcription-service/main.py と、その遅延generatorの回帰テストだけ。
別Builderが services/vexa-bot/core を編集中なので触らない。
.env、デプロイ設定、稼働サービス、旧 .pipeline/.harness-init は変更・参照しない。
commit/push/deploy は主担当が担当するので行わない。

主担当の確認:
- model.transcribe は segments generator を返す。公式 faster-whisper README にも反復時に推論すると明記。
- main.py は executor 内で model.transcribe を返し、その後async関数本体でgeneratorを反復している。
- 隔離した検証用HTTPサーバー（small/CPU）で推論開始0.5秒後の /health が約2.42秒待たされた。
  生記録: .hw/evidence/realtime-transcription-quality/benchmark/api-event-loop-baseline.json。
- GitNexus impact transcribe_audio --file services/transcription-service/main.py を実施済み。
  .hw/evidence/realtime-transcription-quality/impact-transcribe_audio.txt。UNKNOWN。
  @app.post('/v1/audio/transcriptions')と test_config.py に参照を確認。UNKNOWNを安全とは扱わない。

実装修復:
- 重いgeneratorの反復までexecutor内で完了させ、APIイベントループを止めない。
- 結果のJSON形式、品質判定、認証、並列数制限、エラー時の解放を保つ。
- 実装に必要な他symbolの編集前にはimpactを実行する。
- 遅延generatorでスレッドIDを観測し、同時healthやイベントループ処理が進むことと、
  generatorの例外が従来のエラー経路へ伝わりスロットが解放されることを検証する。
- 既存テストは削除・skip・緩和しない。追加テストはローカルでネットワーク/実モデルなしに再現可能にする。
- 対象テストを実行し、日本語の結果を .hw/evidence/realtime-transcription-quality/server-builder-result.md に保存。
  hw全体verifyは他Builderと変更が競合するため主担当が統合後に行う。

この限定範囲の実装と対象検証まで進めて完了を返す。
