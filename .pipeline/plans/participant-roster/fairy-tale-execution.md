# Fairy Tale実行枠

## Glass Slipper Gate

- 目的: Google Meetの無言・途中退出参加者を保存し、既存UI契約へ安全に反映する。
- 成功条件: KPI逆算ロードマップの6条件とverification contractを満たす。
- 書込範囲: `services/vexa-bot/core`、`services/meeting-api`、参加者関連ドキュメント、`participant-roster`のHarness成果物。
- 最大変更ファイル: 実装・テスト・ドキュメント24ファイル（生成証跡を除く）。
- 最大サブエージェント: 同時3、総計5。
- 最大Web検索: 4クエリ。一次・公式ソースを優先する。
- 外部相談: plan/postあわせて最大2回。
- 停止条件: 同一テストが修正後も2回失敗、CRITICAL範囲が追加で拡大、実会議の認証・参加者同意が必要、既存手動データを安全に保護できない。
- 検証: 対象テスト、全単体回帰、UI契約、GitNexus、Harness gate。
- ロールバック: `participant_roster`は任意フィールド。送信停止で旧話者フォールバックへ戻せる。

## Evidence Map

| 主張 | 情報源 | 信頼度 | 行動・検証 |
|---|---|---:|---|
| 現在の参加者は話者由来 | ローカル`post_meeting.py` | 高 | 旧経路を回帰テストで保持 |
| 公開Vexa APIにロスターイベントはない | Vexa公式WebSocket仕様、2026-07-14確認 | 高 | ボットDOM観測を採用 |
| Google Meet DOMで無言参加者を観測できる | ローカルPeople panel/tile取得経路 | 中 | DOM依存を明記し、タイルfallbackを用意 |
| 共通callback変更は広い影響を持つ | GitNexus impact CRITICAL | 高 | 末尾任意引数・任意JSONフィールドのみ追加 |
| `participants`は既存UIへ伝搬する | dashboardソースと契約テスト | 高 | UIコードは変更せずテストで確認 |
| 実会議で100%取得できる | 未検証 | 低 | 主張しない。運用サンプル比較を残す |

## Residency確認

- Codexの`fairy-tale`本体とbenchmark-feedback本体を確認済み。
- `.agents`版と`.codex`版の`fairy-tale`は差分なし。
- 公式Vexa仕様とローカル実装を別の根拠として扱う。
