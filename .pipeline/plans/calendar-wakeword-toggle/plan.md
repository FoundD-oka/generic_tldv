# Plan — calendar-wakeword-toggle

> 事後記録: 本タスクは Codex App ランタイムで実装・検証済み（outcome-card 参照）。
> ゲート成果物として計画を事後文書化したもの。

## Goal

1. 手動参加モーダルにカボスのウェイクワード（音声エージェント）トグルを追加し、
   `voice_agent_enabled` としてbot起動リクエストへ伝播する。
2. カレンダー起点の自動参加botは `voice_agent_enabled=false` を既定とし、
   `KABOSU_VOICE_AGENT_ENABLED=true` でのみ有効化する（opt-in）。
3. カレンダー起点botに `automatic_leave.max_time_left_alone` を設定し、
   会議終了後の自動退出を確実にする。Google Meet の「一人検知」を強化
   （botタイル除外＋ゼロタイル状態の120秒猶予後にalone扱い）。

## Scope

- services/calendar-service: `app/sync.py`, `tests/test_sync.py`, `README.md`
- services/dashboard: `join-modal.tsx`, `dashboard-copy.ts`, `test_export_and_bot_defaults.test.ts`
- services/vexa-bot: `platforms/googlemeet/recording.ts`（参加者カウント）
- deploy: `docker-compose.yml`, `env-example`, `compose/README.md`

## Out of Scope

- Zoom/Teams の alone 検知
- wake-orchestrator 側の変更

## Verification

`.pipeline/plans/calendar-wakeword-toggle/verification-contract.md` と
`.pipeline/outcomes/calendar-wakeword-toggle/outcome-card.json` の
verification リストを参照。
