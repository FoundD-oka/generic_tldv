# Verification Contract — fix-voice-toggle-enforcement

対象: `voice_agent_enabled=false` の会議をウェイク購読・`/speak`・bot発話の全経路で無音化し、既定ON互換を維持する。検証はcommit済みclean treeで行う。

## 最低合格ライン

1. false指定時、`Meeting.data["voice_agent_enabled"] is False`。
2. 省略時、同キーは `True` として保存される。
3. OFF会議への `/speak` は403で、Redis publishは0回。
4. wake-orchestratorは明示的falseだけを除外し、true/キー欠落は発見する。
5. botはOFF時に `speak` と `speak_audio` の両方をログ付きno-opにする。
6. ON/キー欠落時の既存挙動および既存テストを退行させない。

## Acceptance Tests

| ID | Requirement | Method |
|---|---|---|
| AT-001 | false/省略がMeeting.dataへFalse/Trueで保存される | `cd services/meeting-api && python3 -m pytest tests/test_meetings.py -q -k voice_agent -p no:cacheprovider` |
| AT-002 | OFF会議の`/speak`が403、publishゼロ。ON/欠落は202、publishあり | `cd services/meeting-api && python3 -m pytest tests/test_voice_agent.py -q -k voice_agent -p no:cacheprovider` |
| AT-003 | 自動発見がfalseのみ除外しtrue/欠落を含める | `cd services/wake-orchestrator && python3 -m pytest tests/test_clients.py -q -k voice_agent -p no:cacheprovider` |
| AT-004 | `speak`/`speak_audio`両分岐にOFFガード・ログ・no-opがあり、test chainから到達する | `cd services/vexa-bot/core && npm test` |
| AT-005 | meeting-api全回帰 | `cd services/meeting-api && python3 -m pytest tests -q -p no:cacheprovider` |
| AT-006 | wake-orchestrator全回帰 | `cd services/wake-orchestrator && python3 -m pytest tests -q -p no:cacheprovider` |
| AT-007 | hw機械ゲート | `bash .hw/verify.sh` |

すべてexit 0が必須。実行ログはgitignore済みの `.hw/gates/fix-voice-toggle-enforcement/` に保存する。

## Failure Patterns

| ID | Must Not Regress | Detection |
|---|---|---|
| FP-001 | falseがtrueへ丸められる、または保存されない | AT-001 |
| FP-002 | キー欠落をfalse扱いし、既存会議を無音化する | AT-001〜AT-003 |
| FP-003 | 403を返す前にRedis publishする | AT-002の`assert_not_called()` |
| FP-004 | `speak`だけ守り`speak_audio`が発話する | AT-004 |
| FP-005 | 既存テスト削除・skip追加・期待値緩和で通す | test diff review |
| FP-006 | 許可範囲外へ変更が波及する | `git diff --name-only 67ea03210c2de4c8723780402d302948b138d939..HEAD` |

## Non-Functional Checks

- 既存Meeting.dataのキー欠落はtrue扱いでmigration不要。
- OFF時のbotログは無効化とコマンド名を識別できる。
- `package.json` はtest chain追記のみで、依存変更なし。
- Redis channelとcommand payload形式を変更しない。

## Allowed Diff

- `services/meeting-api/meeting_api/meetings.py`
- `services/meeting-api/meeting_api/voice_agent.py`
- `services/wake-orchestrator/app/clients.py`
- `services/vexa-bot/core/src/index.ts`
- `services/meeting-api/tests/test_meetings.py`
- `services/meeting-api/tests/test_voice_agent.py`
- `services/wake-orchestrator/tests/test_clients.py`
- `services/vexa-bot/core/src/voice-command-guard.test.ts`
- `services/vexa-bot/core/package.json`
- `.hw/plans/fix-voice-toggle-enforcement/**`

## Gate Requirements

1. テスト削除・skip・xfail・期待値緩和は禁止。
2. 実装と検証結果をcommitし、`git status --porcelain`を空にする。
3. `python3 .hw/fable_review.py fix-voice-toggle-enforcement` のviolationsを0にする。advisoryは修正義務なし。
4. 修復でhashが変わればレビューを再実行する。
5. `bash .hw/hooks/pr-ready-gate.sh fix-voice-toggle-enforcement` を通す。
6. CIを最終権威とする。
