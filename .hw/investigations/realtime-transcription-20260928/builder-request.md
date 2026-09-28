# Builder依頼: Whisper経路の日本語品質修復

原要求と合意は intent.md を最初に読む。受入条件は .hw/tasks/realtime-transcription-quality.json。
research.md の冒頭追補が最新方針。Soniox移行はしない。

この依頼で実装・必要な回帰テスト・動作確認を最後まで行う。commit/push/deployは主担当が行うため実行しない。
主担当は別途モデル比較を担当する。モデル用コードや本番設定はこのBuilderの担当外。

対象は services/vexa-bot/core のライブ経路。
hallucination-filter.ts / speaker-streams.ts / vad.ts / index.ts と必要なテスト、既存のテスト入口を中心に最小限で修復する。
この作業ツリーの他の調査メモは変更しない。.envやデプロイ資材、稼働サービスは読み取りも最小限にし変更しない。

確認済み:
- 空白なし10文字未満は短文として削除される。
- flushSpeaker は進行中推論があると fullReset し、音声と応答が消える。
- 日本語の確定判定が空白分割、word timestamps はクライアントにはあるがマネージャーに渡っていない。
- VAD の isSpeechStreaming はチャンク末尾のtriggeredだけを返し、途中発話を落とし得る。
- VAD クラスの inputBuffer が共有のまま await をまたぐため、並列話者の状態混入にも注意。
- idle/flush中のfeed、応答待ち中の新しい音声、hard cap、removeAll/cleanup、非同期callbackのawait不足、
  応答の世代識別、confirm後draft生成の時刻、確定バッチの最終publishも自分で確認する。

設計の要求:
- pending audio / submitted snapshot / confirmed audioの寿命を区別し、応答後に到着した音声を破棄しない。
- HTTP失敗と認識成功の空文字を区別。終了時に未処理があれば黙って成功として捨てない。
- 日本語の短い肯定/否定/数字/日時を通すが、既知幻覚や無音対策を失わない。
- 日本語で空白区切りに依存せず安定した先頭を確定でき、末尾を保持。backend wordsを使う場合、
  時刻とUnicode境界を保ち、word timestamps未対応応答への互換fallbackも考慮。
- VADは発話前後の余白を保持し、チャンク内の発話・端数サンプル・話者別状態を正しく扱う。
- 既存 stt.v1 / 字幕配信形式を保つ。既定の確定回数は精度優先で検討し、環境変数を無断書換えしない。
- 語頭語尾の再現テスト、遅延応答とflush/feedの競合、エラー後再試行、長話/上限での欠落、
  日本語の先頭一致/短い発話/正当な繰返しのテストを作る。時間はfake clock等で再現可能にする。

変更前のGitNexus分析を必ず実施。主担当は索引を --index-only --force --no-parse-cache で再構築済み。
.hw/evidence/realtime-transcription-quality/impact-*.txt にクラス/関数の分析がある。
isHallucination CRITICAL、SpeakerStreamManager HIGH、cleanupPerSpeakerPipeline HIGH はユーザーに報告済み。
handlePerSpeakerAudioData UNKNOWN は callback/exposeFunction による参照をテキストで確認済み。
個別の変更methodや追加で変更する関数はimpactを行い、UNKNOWNなら参照を確認。
新しいHIGH/CRITICAL対象があれば .hw/evidence/realtime-transcription-quality/builder-progress.md に先に明記。

既存ビルド/テスト入口は .ai/BUILD.md と Makefile / core/package.json。
既存テストは消さずskip/期待緩和しない。古いテストが不整合なら、触らずbaselineでも同じか確認し報告。
npm buildと対象回帰、python3 .hw/runtime/hw.py verify を実行し、最終gateとは区別。
進捗を .hw/evidence/realtime-transcription-quality/builder-progress.md へ必要時に更新。
最終報告は .hw/evidence/realtime-transcription-quality/builder-result.md に、日本語で変更/実行結果/未確認を残す。
