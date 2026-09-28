# 日本語リアルタイム文字起こし: 実装方法の調査

確認日: 2026-09-28 JST。公式ドキュメント、開発元の実装、研究者の公開実装を参照。
実装・モデル比較・有料 API 呼び出し・稼働環境変更はこの調査では実施していない。
合意した目的と制約は [intent.md](intent.md)。

## 追補: 現行方式との互換性を踏まえた推奨の訂正

ユーザーから「システム的にSONIOXとwhisperでは音声の処理方式が違いSONIOXを使用するのは難しかった気がするのだけど？」
という指摘を受け、送受信経路を再確認した。下記のエンジン候補表は初回調査時の評価であり、
「既存 async 連携があるのでリアルタイムも第一候補」という優先度は撤回する。
精度優先という目的は変更せず、移行に必要な設計変更を評価へ織り込む。

確認できた相違:

1. SpeakerStreamManager.submitBuffer は未確定範囲の音声全体を再送し、
   TranscriptionClient は毎回独立した WAV を HTTP POST して verbose_json を待つ。
   同じ音声が連続リクエストへ重複して含まれる。Soniox の継続ストリームへそのまま流すと
   過去音声を新しい発言として送ることになるため、この送信方式は再利用できない。
2. Soniox Real-time は WebSocket セッションへ音声を順次送り、トークンごとの final/non-final を受ける。
   現行の全文再認識・独自の先頭一致による確定は、そのまま接続する契約ではない。
3. soniox_adapter.py:176 はファイルアップロード→ジョブ作成→ポーリング→結果取得という async 処理。
   main.py:327 のルーティングも stt-async 用で、リアルタイムの接続実装は含まない。
4. final_transcription.py:913 の接続先分離は、会議後のバックエンドをリアルタイムに影響させず変更するためのもの。
5. ライブ側の品質判定には Whisper の avg_logprob / no_speech_prob / compression_ratio を使う。
   他プロバイダーの confidence を同じ閾値へ機械的に置換できない。

音声形式自体は障壁と断定しない。Soniox は Float32 PCM と16kHz mono PCMを扱える。
必要なのはセッション寿命、音声の重複防止、確定結果の変換、会議時刻への対応、再接続時の復旧設計。
無音時の keepalive、話者別接続にした場合の同時接続数と費用も評価対象になる。
[Soniox: Real-time transcription](https://soniox.com/docs/stt/rt/real-time-transcription)
[Soniox: Connection keepalive](https://soniox.com/docs/stt/rt/connection-keepalive)
[Soniox: Limits & quotas](https://soniox.com/docs/stt/rt/limits-and-quotas)

推奨を「現行のHTTP文字起こし契約を保って共通不具合を修正し、その構成で日本語対応・確定処理・
モデル実行環境を改善する」に訂正する。Soniox Real-time は、独立した音声処理経路を追加する
構成変更案として残す。現在の処理を迂回して capture→新規streaming adapter→既存の字幕配信形式へ
つなぐ案は技術的候補だが、実現可能性・工数・精度・費用はまだ検証していない。
この確認から「Sonioxが利用不可能」「Whisperの方が高精度」とは結論しない。

GitNexus も照会したが、transcribe_via_soniox の context は無関係な関係を大量に含み、
信頼できる呼出関係の証拠には用いていない。上記は実ファイルと公式API資料の照合による。
実装変更前にはグラフの状態を確認し、要求された impact analysis を別途実施する。

## 結論

採用したい設計は、音声の連続性を保ち、暫定表示と確定を分離し、日本語に適した単位で
認識結果を扱うこと。モデルの交換だけでは、現行コードの短文削除や未処理音声の破棄は直らない。
これは下記資料と現行コードを照合した本プロジェクトへの提案であり、特定モデルの精度保証ではない。

前の回答の「約1秒分から認識する」には補足が必要。短い間隔で送信・更新すること自体は問題ではない。
送信間隔、モデルに渡す累積音声の長さ、結果を確定する条件は別に設計する。
現行実装にも累積バッファはあるため、「毎秒すべて独立認識するだけの実装」とは評価しない。

## 1. 音声の送信単位と認識・確定単位を分ける

Google は自社 Streaming API の送信フレームとして 100ms を推奨するが、これは認識文脈を
100ms ごとに捨てる意味ではない。PCM 等の音声形式と実サンプルレートを正しく伝え、
独立した話者チャンネルが得られる場合はその分離を保つことも推奨している。
100ms をすべてのプロバイダーに通用する必須値とはしない。
[Google Cloud: Best practices](https://docs.cloud.google.com/speech-to-text/docs/best-practices)

本プロジェクトへの提案:

- キャプチャした PCM とサンプル位置を基準にし、送信順序と欠落を追跡する。
- リサンプリングが必要なバックエンドでは境界で一度行い、変換後のレートを宣言する。
- 話者別に実際に分離できている音声は混ぜない。混合音声の話者推定と参加者名の対応付けは別問題とする。
- 受信時刻や推論完了時刻だけで字幕時刻を作らず、元音声の時刻へ対応付ける。

## 2. 暫定結果は更新し、確定済み部分だけ積み上げる

Soniox の Streaming API は final/non-final のトークンを区別する。non-final は後続音声で変わり得る。
Google も interim/final を分け、安定度と認識の正しさを表す confidence は異なると説明している。
[Soniox: Real-time transcription](https://soniox.com/docs/stt/rt/real-time-transcription)
[Google: Streaming responses](https://docs.cloud.google.com/speech-to-text/docs/v1/speech-to-text-requests)

本プロジェクトへの提案:

- 既存の completed=false/true を活用し、暫定部分は同じ音声範囲の更新として置換する。
- プロバイダーが返す final を利用する方式と、自前で安定部分を判定する方式を混同しない。
- Whisper を継続する場合は LocalAgreement のように、追加音声を含む複数回の認識で一致した
  先頭部分を確定する。単に同じ音声を再送して一致したことを、追加文脈による安定性の証拠にしない。
- 確定範囲の音声位置と文字列を対応付け、未確定の末尾は次の推論に残す。

Whisper-Streaming は固定窓による単純分割の問題と LocalAgreement を説明している。
ただし同プロジェクトは後継の SimulStreaming を案内しており、旧実装の丸ごと導入を最新の推奨とはしない。
[UFAL: Whisper-Streaming](https://github.com/ufal/whisper_streaming)

## 3. 日本語を空白区切りの単語として扱わない

faster-whisper の tokenizer は、日本語など空白で分かち書きしない言語を Unicode に基づく分割へ
振り分ける。バックエンドの words は、日本語の形態素解析による単語と同義ではない。
[faster-whisper: tokenizer.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/tokenizer.py)

現行コードで確認したこと:

- `services/vexa-bot/core/src/services/hallucination-filter.ts:67` は空白なし・10文字未満を削除する。
  前ターンの実行で「違います」「来週の月曜です」「予算は五万円です」が削除されると確認済み。
- `services/vexa-bot/core/src/services/speaker-streams.ts:201` は認識文を空白で分割して先頭一致を調べる。
- `services/vexa-bot/core/src/index.ts` は認識サーバーの words を取得するが、SpeakerStreamManager へは
  text/start/end に変換して渡すため、その確定判定では words が使われない。

提案: 正当な短文を文字数だけで捨てる規則を撤廃し、確定判定にはバックエンドの時刻付き単位を渡す。
文字列一致だけでなく、同じ音声範囲の結果であることを確かめる。単語数ベースの他のフィルターも点検する。

## 4. VAD は語頭・語尾を保護する。会議と音声コマンドでは終了条件を分ける

faster-whisper の VAD は発話区間の前後へのパディングと無音継続時間を持つ。
上限で区切る際も無音位置を利用する処理がある。具体的な閾値はデータごとの調整対象。
[faster-whisper: vad.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/vad.py)

Soniox は早い endpoint detection が認識精度を下げ得ること、話者分離の精度を最大化したい場合は
endpoint detection を使わないことを説明している。これは Soniox 固有の説明で、全エンジンへ一般化しない。
会議字幕では暫定表示ができるため、音声コマンドの応答開始と同じ終了条件にする必然性はない。
[Soniox: Endpoint detection](https://soniox.com/docs/stt/rt/endpoint-detection)

現行コードでの懸念:

- Google Meet は `index.ts:1856` で VAD 判定が false ならチャンク全体を feedAudio 前に落とす。
- `vad.ts:183` はチャンク処理後の triggered を返す。チャンク途中に発話があっても終了時点で無音なら
  false になり得る。実会議での発生頻度と損失量は未測定。
- rawCaptureService への受け渡しも VAD の後なので、この録音だけでは入口の削除を検証できない。

提案: 発話を含む区間と前後の余白を保ち、VAD 前の音声と送信後の音声を必要な検証範囲で比較できるようにする。
無音を除去する場合は元音声への時刻対応を保持する。会議字幕と wake-stt の確定ポリシーは別に管理する。

## 5. 終了時は処理完了まで待ち、未処理音声を解放しない

Soniox の manual finalize では完了マーカーが返る。通常のセッション終了も最終応答を受信してから閉じる。
[Soniox: Manual finalization](https://soniox.com/docs/stt/rt/manual-finalization)
[Soniox: Real-time transcription](https://soniox.com/docs/stt/rt/real-time-transcription)

本プロジェクトへの提案:

- セッションと音声範囲を識別するリクエストIDを持ち、遅れて返った結果が別の発言へ混ざらないようにする。
- flush は進行中の処理と残り音声の処理を待つ。送信を予約しただけで完了扱いにしない。
- 上限超過・タイムアウト・通信切断は明示的な状態として扱う。未処理音声を黙って削除しない。
- バッファ容量は有限とし、滞留量を測定する。回復不能な欠落は記録・表示し、無限にメモリへ溜めない。

前ターンのローカル再現では、SpeakerStreamManager の認識中に flushSpeaker を呼ぶと、
32,000サンプルが0になり、返された認識結果の確定通知も0件だった。実会議での頻度は未確認。

## 6. 言語と用語を認識器へ渡す

Soniox は予想する言語を language_hints、専門用語を context.terms で指定できる。
言語ヒントは強制的な言語制限とは異なる。
[Soniox: Language hints](https://soniox.com/docs/stt/concepts/language-hints)
[Soniox: Context](https://soniox.com/docs/stt/concepts/context)

提案: 日本語会議では ja を既定候補とし、ユーザーの明示した言語設定を優先する。
日英混在は選択可能にする。既存の用語辞書をライブ側にも受け渡す経路を設計する。
現在のライブ経路で渡している直前の確定文は、固有名詞辞書の代わりにはならない。
用語ヒントも誤認識を完全に防ぐ保証はなく、認識後の強制文字列置換とは区別する。

## エンジン構成の候補と採用判断

| 候補 | このプロジェクトでの位置付け | 未確認・制約 |
| --- | --- | --- |
| Soniox Real-time API | 外部利用を許容する場合の第一評価候補。日本語対応・暫定/確定・用語ヒントがあり、既存に async 用アダプターがある | 既存アダプターは stt-async のみ。WebSocket 統合は新規実装。費用・同時接続・送信対象・保存条件を採用前に確認。日本語精度の優位性は未実測 |
| faster-whisper + 日本語対応の安定確定 | 現行資産を使う最小変更案。large-v3 / large-v3-turbo を性能検証候補とする | 現在観測した tiny/CPU 環境でモデルだけ大型化しても、リアルタイム要件を満たすとは限らない |
| SimulStreaming | 自前運用でストリーミング部分を置き換える候補。AlignAtt による確定制御 | large-v3 用に開発元が推奨する GPU は VRAM 10GB以上。日本語の末尾処理・既存時刻契約の適合は要検証 |
| Qwen3-ASR | 自前運用の追加候補。日本語とストリーミング対応 | 公開実装のストリーミングは vLLM 限定、タイムスタンプ返却非対応。既存の時刻付き字幕への統合に追加設計が必要 |

上記の優先度は実装適合性からの判断であり、モデル精度ランキングではない。
Soniox の async 実装があることは、リアルタイム利用の承認や精度を意味しない。

出典:

- [Soniox: Supported languages](https://soniox.com/docs/stt/concepts/supported-languages)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [SimulStreaming](https://github.com/ufal/SimulStreaming)
- [Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)

## 次の実装と、その後の比較検証

1. エンジン共通の欠落要因を修正する。短文削除、flush、VAD 境界、時刻対応が対象。
2. 暫定/確定・音声範囲・話者トラック・失敗状態の契約を整理し、エンジンの結果をここへ変換する。
3. 日本語設定と既存辞書の受け渡しを追加する。独立した音声コマンドの挙動は混同しない。
4. その後に、同一の日本語音声を実時間で流して比較する。比較検証はまだ実施していない。

比較時には、CER（文字誤り率）に加え、短文の取りこぼし、否定・数字・固有名詞の誤り、
重複、暫定表示までの時間、確定までの時間、長時間運転での滞留、話者と時刻のずれを分けて測る。
正式な合格値はまだ合意していない。まず現状値を得てから決め、文献の遅延値をそのまま合格値にしない。
単体テストの成功だけを、実会議の精度改善や最終 gate の合格として報告しない。
