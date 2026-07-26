# Option Matrix: リファクタリング戦略

評価は5点満点。安全性と実行者の判断量を最重視する。

| 選択肢 | 概要 | 安全性 | 価値到達 | 途中rollback | 実行者の判断量 | 結論 |
|---|---|---:|---:|---:|---:|---|
| A. 巨大ファイルから先に分割 | 行数の大きい順にmodule/componentへ移す | 1 | 2 | 2 | 4 | 不採用 |
| B. 将来MANIFESTへ一括移行 | `contracts/` と `packages/` を先に作り全サービスを移す | 2 | 3 | 1 | 5 | 不採用 |
| C. 境界修正→契約固定→move-only抽出 | P0バグを独立修正し、goldenを置いて責務を段階移動 | 5 | 5 | 5 | 1 | 採用 |
| D. 問題修正だけで分割しない | セキュリティ・競合だけ直し構造は維持 | 4 | 3 | 5 | 1 | 不採用 |

## 採用理由

選択肢Cなら、認証・状態遷移・非同期競合・検証の偽陽性を先に正せる。後半の抽出は、
固定済みの契約を保つmove-only変更になり、項目単位でrevertできる。

## 重要な設計選択

| 論点 | 採用 | 不採用 |
|---|---|---|
| 認証主体 | Gateway解決済みsubjectを下流の唯一の主体とする | body/queryの `user_id` を信頼 |
| 公開User JSON | allow-list化した公開DTO | 任意JSONに対する秘密名deny-listだけ |
| Agent scope | 既存 `browser` scopeへ割当。新scopeは作らない | 未承認の `agent` scope追加 |
| Calendar scope | `bot` | 新scope追加 |
| Recording scope | `tx` | `bot`とのOR許可 |
| Browser保存 | session-bound WS + waiter登録後send + request ID | Redis共有channelのplain `done`、生成containerへのRedis credential |
| Admin session | HMAC署名検証をserver-only helper 1実装へ統合 | proxy側だけbase64 decode |
| User token管理 | `/auth/me` subject + owner-scoped `/user/tokens` | user-info email + Admin key代理操作 |
| Workload credential | service/audience/operation別capability、subject-owned provider credential、server-side Zoom issuer/egress broker | Admin/Internal/Redis/Transcription/Wakeのglobal secret、platform共有Claude credential、Zoom client secret、proxy user-infoの配布 |
| URL parser | pure shared package +旧import re-export | サービスごとの正規表現追加 |
| ORM分離 | metadata snapshot後にshared model packageへmove | DB migrationとの同時実施 |
| `tests3` | fail-closed化し、役割を明記して段階縮小 | 一括削除または過去feature sidecarの復元 |
| UI分割 | race/polling/orderを修正後に抽出 | 2,000行componentを先に分割 |

## 採用しない拡張

- Redis Streamsへの全面置換（trusted service内部の既存streamは維持し、workload直結だけbroker化）
- token保存の暗号化方式変更
- 新しいAPI version、status、scope、DB schema
- React/Next.js/FastAPI/Redis等の依存更新
- 0.11モジュール全面移行
