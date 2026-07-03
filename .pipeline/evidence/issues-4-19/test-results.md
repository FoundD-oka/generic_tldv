# テスト結果 - Issues #4-#19

## 範囲

GitHub issue #4-#19 は、Lサイズの一括作業として実装した。対象がリダクション、最終転写、ボット既定値、カボス persona/ブランド、wake 運用、WebSocket イベント、削除時 cleanup、assistant context、日本語ドキュメント/テストにまたがり、実行時契約を共有しているため。

## 成功したコマンド

```text
cd services/dashboard && npm test
```

結果: 成功。12 test files、61 tests。

```text
cd services/dashboard && VEXA_API_URL=http://localhost:8056 npm run build
```

結果: 成功。Next.js の compile、TypeScript validation、static generation が完了。

```text
/tmp/generic-tldv-py311-venv/bin/pytest tests/test_final_transcription.py tests/test_redaction.py tests/test_meetings.py::test_meeting_create_defaults_voice_agent_enabled_true tests/test_meetings.py::TestCreateMeeting::test_create_meeting_defaults_voice_agent_enabled_in_runtime_config tests/test_meetings.py::TestCreateMeeting::test_create_meeting_respects_explicit_voice_agent_disabled tests/test_meetings.py::TestDeleteMeetingArtifacts tests/test_meetings.py::TestAssistantContext
```

結果: 成功。meeting-api の対象テスト 18件が成功。

```text
PYTHONPATH=. /tmp/generic-tldv-pytest-venv/bin/pytest tests/test_text.py tests/test_clients.py
```

結果: 成功。wake-orchestrator の対象テスト 46件が成功。

```text
PYTHONPATH=. /tmp/generic-tldv-pytest-venv/bin/pytest tests/test_chat.py
```

結果: 成功。agent-api の対象テスト 20件が成功。

```text
cd services/agent-api && PYTHONPATH=. /tmp/generic-tldv-pytest-venv/bin/pytest tests/test_chat.py::test_kabosu_persona_matches_shared_source
```

結果: 成功。共有カボス persona の同期テストは、共有元が欠けている場合に skip せず失敗する fail-closed 条件になっている。

```text
cd services/wake-orchestrator && PYTHONPATH=. /tmp/generic-tldv-pytest-venv/bin/pytest tests/test_clients.py::test_kabosu_persona_matches_shared_source
```

結果: 成功。共有カボス persona の同期テストは、共有元が欠けている場合に skip せず失敗する fail-closed 条件になっている。

```text
/tmp/generic-tldv-py311-venv/bin/python -m py_compile services/mcp/main.py services/telegram-bot/bot.py services/api-gateway/main.py services/meeting-api/meeting_api/collector/endpoints.py services/meeting-api/meeting_api/collector/processors.py services/meeting-api/meeting_api/final_transcription.py services/wake-orchestrator/app/clients.py services/wake-orchestrator/app/orchestrator.py services/wake-orchestrator/app/persona.py services/agent-api/agent_api/kabosu_persona.py
```

結果: 成功。

```text
cd deploy/compose && docker compose --env-file ../../.env -f docker-compose.yml --profile wake config --services
```

結果: 成功。`wake` profile には core runtime と wake-orchestrator が含まれ、wake-stt と tts-service は含まれないことを確認。

```text
cd deploy/compose && docker compose --env-file ../../.env -f docker-compose.yml --profile wake --profile wake-stt config --services
```

結果: 成功。`wake-stt` profile を追加した場合のみ、wake-stt が明示的に含まれることを確認。

## 契約確認 grep

```text
rg "Vexa - Open Source Bot" services/dashboard/src
rg "\|\| \"en\"|\|\| 'en'" services/dashboard/src
rg "ユーザーと同じ言語|Available transcript context|No transcript context available" services packages
```

結果: 成功。該当なし。

```text
rg "transcript\.mutable" services docs
```

結果: 成功。期待どおり、deprecated compatibility として残している dashboard runtime types/hooks と wake-orchestrator inbound compatibility のみ該当。

## Harness メモ

`bash .claude/hooks/pr-ready-gate.sh issues-4-19` は、初回実行時に evidence 不足、QA judgment 不足、Lサイズ用 sidechain evidence 不足、approval hash 未記録、feedback pruning 未完了、追加された test skip により block された。その後、skip 条件を削除し、harness-init の core 変更に対する feedback ledger を記録し、この evidence pack を作成した。

```text
HARNESS_TASK_ID=issues-4-19 bash .claude/hooks/preflight.sh --full
```

結果: warn。ブロック対象の test weakening pattern は残っていない。残る warning は、確認済みファイルに対する auth/schema path risk flag。

```text
HARNESS_TASK_ID=issues-4-19 HARNESS_FEEDBACK_REQUIRED=1 bash .claude/hooks/feedback-prune.sh --required
```

結果: 成功。harness-init feedback ledger は kept rule 1件、conflict 0件。

```text
bash scripts/harness/outcome-judge.sh issues-4-19
```

結果: 成功。出力は `outcome pass: issues-4-19`。

```text
node .gitnexus/run.cjs detect-changes --repo generic_tldv
```

結果: critical。GitNexus は 72 changed files、233 changed symbols、64 affected processes を報告した。dashboard、meeting-api、wake-orchestrator、docs、harness contracts にまたがる広域変更のため、risk level は critical。

```text
bash .claude/hooks/pr-ready-gate.sh issues-4-19
```

結果: Lサイズの approval hash のみ block。その他の check は pass、または非ブロックの warn。現在の承認対象 hash は `sha256:f3b18930f38e1f6f4a83926b38dfb117dff455a7a4cf926211e6b9e6af41df5e`。

この Lサイズ bundle の PR readiness を最終化するには、hash-bound human approval の記録がまだ必要。
