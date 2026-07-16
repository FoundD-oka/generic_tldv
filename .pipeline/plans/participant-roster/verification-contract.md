# 検証契約: participant-roster

- size: M
- external consultation required: no

## 必須条件

1. 無言参加者を含むロスターが会議中の複数スナップショットから累積され、途中退席者が残る。
2. ボット自身、空名、Google Meetのジャンク名、重複参加者を保存しない。
3. 終了コールバックだけが任意の `participant_roster` を送り、未指定時の既存ペイロード契約を壊さない。
4. meeting-apiがロスターを正規化して保存し、`participants` を実参加者名で更新する。
5. ロスターがない旧会議では、従来の文字起こし話者フォールバックが維持される。

## Required Commands

- `cd services/vexa-bot/core && npx tsx src/services/participant-roster.test.ts`
- `cd services/vexa-bot/core && npx tsx src/services/unified-callback.test.ts`
- `cd services/vexa-bot/core && npm run build`
- `cd services/meeting-api && PYTHONPATH=. python -m pytest tests/test_callbacks.py tests/test_post_meeting_idempotency.py -q`
- `cd services/meeting-api && PYTHONPATH=. python -m pytest tests -q`
- `node .gitnexus/run.cjs detect-changes --repo /Users/bonginkan-3-gouki/project/generic_tldv --scope compare --base-ref main`
- `bash .claude/hooks/pr-ready-gate.sh participant-roster`
- `bash scripts/harness/outcome-judge.sh participant-roster`

## Evidence Rule

- `.pipeline/evidence/participant-roster/` にコマンド結果と変更範囲を保存する。
- Evidence Manifestに `missing_evidence` がないこと。
