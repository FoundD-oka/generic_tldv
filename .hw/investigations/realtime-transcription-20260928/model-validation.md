# Whisperモデルと実行環境の検証（2026-09-28）

## 判断

Whisper HTTP API経路を維持する。まず短文削除・音声欠落・確定処理を修復し、モデルの第1候補を `small` とする。
`large-v3-turbo` は今回のCPU条件では音声時間より遅く、モデル名だけの差替えは採用しない。
Apple SiliconのMLXでは有望な速度が出たが、現在のLinux Docker / faster-whisperとは実行基盤が異なる。
本番設定・稼働モデルは変更していない。精度や会議数の保証ではなく、次の実会議検証の候補選定である。

## 入力・測定条件

- macOS標準音声Kyoko、速度180で生成した日本語8例、計31.92875秒、正規化後124文字。
- 短い肯定・否定、日付、金額、送信禁止、依頼、技術文、正当な繰返しを含む。
- 16 kHz / mono / PCM、先頭300 ms・末尾600 msの無音。話者1名、雑音・重なりなし。
- 文字誤り率（CER）はNFKC・小文字化後、空白・句読点・記号を除いた編集距離 / 正解文字数。
  固有名詞や同音語の特別な正規化はしない。
- Mac M3 Pro、arm64、メモリ18 GiB、macOS 26.5.1。Python 3.12.13の隔離venvを使用。
- faster-whisper 1.2.1 / CTranslate2 4.8.2、CPU int8、4 threads、beam 5。
  日本語固定、temperature 0、word timestamps有効、VAD無効、condition_on_previous_text無効、
  repetition_penalty 1.1、no_repeat_ngram_size 3。
- MLX Whisper 0.4.3 / MLX 0.32.2。日本語固定、temperature 0、word timestamps有効、
  condition_on_previous_text無効。それ以外のデコーダ設定はMLXの既定で、CPUとの完全同条件比較ではない。
- モデル読込・ダウンロード・1例目のウォームアップは処理時間から除外。
  他のモデル推論を同時に行わない測定値を採用した。

取得されたモデルのsnapshot（再実行時は同じrevisionを使う）:

| モデルリポジトリ | revision |
| --- | --- |
| Systran/faster-whisper-tiny | `d90ca5fe260221311c53c58e660288d3deb8d356` |
| Systran/faster-whisper-small | `536b0662742c02347bc0e980a01041f333bce120` |
| mobiuslabsgmbh/faster-whisper-large-v3-turbo | `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` |
| mlx-community/whisper-large-v3-turbo | `a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb` |

## 測定結果

| エンジン / モデル | CER | 8例の処理時間 | 音声時間比（RTF） |
| --- | ---: | ---: | ---: |
| faster-whisper CPU / tiny | 25.0% | 1.87秒 | 0.058 |
| faster-whisper CPU / small | 4.8% | 12.24秒 | 0.383 |
| faster-whisper CPU / large-v3-turbo | 0.0% | 50.45秒 | 1.580 |
| MLX / large-v3-turbo | 0.0% | 11.79秒 | 0.369 |

`tiny` は「承認」を「商品」、「見積もり」を「三つ森」とする誤認識があった。
`small` は短文・否定文・繰返しを正しく認識したが、「見積もり」「音声」に誤りが残った。
`large-v3-turbo` のCER 0%はこの8例だけの結果であり、日本語会議全般で誤りがないという意味ではない。
RTFは独立した音声ファイルを一度ずつ処理した値。ライブ経路の重複推論や複数話者・同時会議の負荷を含まない。

## 現行HTTP経路での確認

検証専用コンテナ `vexa-stt-quality-validation` をlocalhost:18092に起動。
既存transcription-serviceイメージ、CPU 4コア上限・メモリ3 GiB、隔離したsmallモデルキャッシュを使用。
稼働中の8092番サービス・本番.env・デプロイ資材には変更を加えていない。
コンテナはCTranslate2 4.8.1 / NumPy 1.26.4で、ネイティブ測定の版とは異なる。

実HTTPへ同じ8例を送信し、日本語短文の認識結果とword timestampsの応答を確認した。
この経路ではVAD有効、max_speech_duration_s=5、min_silence_duration_ms=100。
「精度」が「制度」、「音声」が「本性」になる例があり、モデル単体比較とは条件も結果も異なる。

修正前のSpeakerStreamManagerへ、PCMを実時間に沿って投入し、同HTTP応答を返す再現実験では、
「違います」「来週の月曜です」「予算は5万円です」「承認していません。まだ送信しないでください。」が
すべて確定出力0件になった。認識モデルの誤りとは別に、アプリ側の削除・終了処理が欠落を生んでいた。
ブラウザ・Redis・会議プラットフォーム・画面表示を含むE2Eではない。

修復後の実ONNX VAD → SpeakerStreamManager → small HTTP経路では、肯定・否定・日付・金額・
送信禁止・繰返しの発話が確定出力として残った。実HTTPのword分割は「し」から「します」へ変わる例があり、
文中の連続音声を切っていた初回修正では「します」が欠落した。
同じPCMを16384→49152→98304サンプルで送る決定論的な再現を作り、
確定時に次の語との間の余白も確認する修復後、同じ実HTTP再生で「します」を含む全文が残ることを確認した。
「精度→制度」「音声→本性」というモデルの誤認識は残り、本文を手動で置換していない。
再現スクリプトは `cut-repro.ts`、生結果は `cut-repro-before.json` / `cut-repro-after.json`。

リプレイ設定はminAudioDuration=1、submitInterval=1、confirmThreshold=2、maxBufferDuration=15、
idleTimeoutSec=5。現行起動資材はconfirmThreshold=1を明示しているため、コードの既定値2だけでは
稼働設定は変わらない。導入時にはモデル変更と併せて確定回数の設定も判断する必要がある。

サーバーには、faster-whisperの遅延generatorをasync処理内で反復し、推論中にAPI全体を待たせる問題もあった。
generatorの反復までexecutor内で実行する修正後、同じ技術文の推論中の `/health` 応答は
修正前約2.419秒から約0.0032秒になった。応答時にも推論が継続中であることを確認した。
単発の疎通実験であり、負荷試験のp95やSLAではない。

## 再現と限界

`benchmark/` のスクリプトを `.hw/evidence/realtime-transcription-quality/benchmark/` にコピーして実行する。
`generate.py` で音声を生成し、`run.py cpu tiny`、`cpu small`、
`cpu large-v3-turbo`、`mlx mlx-community/whisper-large-v3-turbo` を順に実行する。
macOSの `say`（Kyoko）とffmpeg、および上記Python依存が必要。モデルは公開レジストリから取得する。
音声・モデル・詳細ログは `.hw/evidence/realtime-transcription-quality/benchmark/` に置き、Gitへ含めない。
入力文・認識文・条件・集計値は `benchmark/results.json` に保存した。

実会議のマイク品質、固有名詞、方言、話者重複、複数会議、継続負荷、メモリ最大値は未測定。
音声に十分な間がないまま15秒の上限に達すると強制区切りが入り、その境界では語頭欠けの可能性が残る。
終了時の音声確定待ちはコード既定20秒。配信側にも別の有限待機予算を設ける。
待機上限や継続障害を超えた処理の完了は保証せず、未処理の破棄・配信未完了はログと統計へ記録する。
起動側の停止猶予も導入前に整合確認が必要。現行 `runtime_api/backends/docker.py` の `stop` は既定10秒、
botの `gracefulShutdown` はSIGTERM処理全体を30秒で打ち切る。正常退室と強制停止の実経路は未実測で、
認識モデルや終了待ち時間だけを変更して、最後の音声が必ず保存されるとは判断しない。これらの稼働設定は今回変更していない。
次の運用判断は、修復コードを適用した経路で同一の実会議音声を使い、smallの欠落・重複・遅延を測ること。
さらに高精度が必要なら、HTTP契約を保つApple SiliconのネイティブworkerかNVIDIA GPUを別途検証する。
今回MLXのHTTP worker実装やGPU性能の検証はしていない。

## 追加実験: 確定直後の反復語の脱落（2026-09-29、隔離 small）

8発話を連続させた31.93秒の再生で、ct=1 のときに末尾の「違います、違います。」が1回に減った。
この原因を単発送信で測った。生ログは `.hw/evidence/realtime-transcription-quality/boundary-repair/` に保存。
ネットワーク不要の再現は `prompt-repeat-retention.test.ts` と実応答fixture `prompt-repeat-drop.json` に含めた。
prompt に直前の確定文「違います。」を付けると、先頭が「違います」で始まる音声から
その語が出力されなかった。同じ音声を prompt なしで送ると認識された。
prompt なしのときは、語頭を最大 70ms 削っても認識された。
prompt 付きのときは、先頭に 0.3s 以上の無音を足しても脱落した。
修正後のクライアントは、prompt 付きの結果で先頭の発話が認識されずに残った場合だけ
prompt なしで再送する。失敗した境界を実HTTPで再現した ct=1 の2系列はどちらも、
修正前は欠落し、修正後は保持した。ct=2 は修正前も修正後も保持した。
実時間の再生（VADあり ct=1 を3回、ct=2 を2回、無VAD ct=1 を1回）のCERは 0.0565〜0.0887 だった。
再送が起きたのは失敗境界の音声だけだった。
再送した区間には句読点が付かないことがある。雑音のある実会議音声での誤発動率と
追加負荷は未測定。

## 公式資料

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper): CPU int8、GPU要件、segments generatorを反復して初めて推論する仕様。
- [MLX Whisper large-v3-turbo](https://huggingface.co/mlx-community/whisper-large-v3-turbo): Apple Silicon用モデルとmlx-whisperの利用例。

資料は2026-09-28に参照。モデル選択の数値は上記のローカル測定による。
