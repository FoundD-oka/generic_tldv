---
generated_by: fable
task_id: fix-voice-toggle-enforcement
base-commit: 67ea03210c2de4c8723780402d302948b138d939
size: M
---

# 会議参加時の音声トグルを全経路で強制する

## ゴール

会議参加モーダルで `voice_agent_enabled=false` を選んだ会議では、録音と文字起こしだけを行い、ウェイクワードへの応答を含む発話を一切行わない。キー欠落は既存互換のため `true` と扱い、ON会議の挙動は維持する。UIは既に正しい値を送っているため変更しない。

## 対象ファイル

1. `services/meeting-api/meeting_api/meetings.py`
2. `services/meeting-api/meeting_api/voice_agent.py`
3. `services/wake-orchestrator/app/clients.py`
4. `services/vexa-bot/core/src/index.ts`
5. `services/meeting-api/tests/test_meetings.py`
6. `services/meeting-api/tests/test_voice_agent.py`
7. `services/wake-orchestrator/tests/test_clients.py`
8. `services/vexa-bot/core/src/voice-command-guard.test.ts`(新規)
9. `services/vexa-bot/core/package.json`(test連鎖への追加のみ)
10. `.hw/plans/fix-voice-toggle-enforcement/**`

## How(実装手順 — Opus 5 implementer向け)

### 1. フラグを会議データへ永続化

`request_bot` で `meeting_data["voice_agent_enabled"]` に解決済みboolを保存する。`req.voice_agent_enabled is None` のときだけ `True`、それ以外は `bool(req.voice_agent_enabled)` とする。既存の `BOT_CONFIG.voiceAgentEnabled` への伝搬は維持する。

### 2. `/speak` をfail-closedにする

`bot_speak` は対象Meeting取得直後、Redis publishより前に `(meeting.data or {}).get("voice_agent_enabled", True)` を確認する。明示的に `False` なら HTTP 403、detail `voice agent disabled for this meeting` を返し、Redisへ何もpublishしない。キー欠落・`None`・`True` は既存挙動を維持する。

### 3. ウェイク自動発見からOFF会議を除外

`VexaClient.list_running_bots` は `/bots/status` の各botについて、`(bot.get("data") or {}).get("voice_agent_enabled", True) is False` なら `MeetingRef` を作らず除外する。キー欠落は含め、既存の順序・alias解決を変えない。

### 4. bot側に最後の防御を置く

`runBot` 内のRedis command dispatchで、`speak` と `speak_audio` の各分岐先頭に `currentBotConfig?.voiceAgentEnabled === false` のガードを置く。無効ならコマンド名を含むログを1行出して、そのコマンド処理だけno-opにする。他コマンドとON/キー欠落時の既存処理は変えない。

### 5. 回帰テスト

- meeting-api: false/省略が `Meeting.data` に False/True として保存される。
- meeting-api `/speak`: falseで403かつpublishゼロ、true/キー欠落で従来どおりpublish。
- wake-orchestrator: true/false/キー欠落のうちfalseだけ発見対象外。
- vexa-bot: `index.ts` を直接importせず、両command分岐に同じ無効ガード・ログ・早期no-opがあることを構造テストで固定し、`npm test` から実行する。

## やらないこと

- UI表示・文言変更。
- 既存会議のバックフィルやDB migration。
- 実行中会議のON/OFF動的切替。
- `leave`、`chat_send`、`screen_show` 等の権限制御変更。
- TTS品質や初期化方式の再設計。

## Why(実装者に渡さない)

現状はフラグがbot起動設定にしか残らず、status経由のウェイク発見ではOFFを識別できない。さらに `/speak` とbot command dispatchのどちらにも拒否境界がないため、ウェイク側が全Botを購読するとOFFでも発話できる。単一点の修正では別経路から再発するため、永続化・発見フィルタ・API拒否・bot no-opの4層で同じ明示的Falseを強制する。キー欠落=trueは既存の既定ONを維持するため。

外部仕様への依存はない。変更は3サービスとbot境界にまたがるため不確定性M、作業量は1セッション内のためruntimeはinline。
