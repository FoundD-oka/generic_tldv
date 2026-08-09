---
generated_by: fable
task_id: p23-transcript-search-api
base-commit: 7c3c6555f63950eaf5a496da07b6a84da62fd0e8
size: M
parent: p23-transcript-fulltext-search
---

# P2-3 Stage 1: 横断全文検索 API(migration + 検索エンドポイント + gateway)

親プラン `.hw/plans/p23-transcript-fulltext-search/plan.md` の Stage 1 を独立レビュー
可能な形で切り出したもの。リサーチ結果(pg_trgm 選定の実測根拠)・スコープの線引き・
Why は親プランを正とする。本プランは実装者へ渡す How と契約のみ。

## ゴール

- transcriptions.text に pg_trgm GIN インデックスが付き、本番(~507K 行)へ
  無ロックで導入できる migration が存在する。
- 認証ユーザーが自分の全会議の文字起こし本文を `GET /transcripts/search` で
  横断検索でき、会議単位グルーピング + マッチ発言スニペットが返る。
- 既存の認可境界(Meeting.user_id)を踏襲し、他ユーザーの文字起こしが決して混入しない。
- CI(test-meeting-api.yml)が migration 適用込みで統合テストを回す。

## How

変更ファイル(この集合のみ。契約 FP-302。`.hw/plans/` 配下は規約準拠のため別途許容):

- `scripts/migrations/20260809_add_transcription_text_trgm.py`(新規)
- `services/meeting-api/meeting_api/models.py`
- `services/meeting-api/meeting_api/database.py`
- `services/meeting-api/meeting_api/search.py`(新規ルータ)
- `services/meeting-api/meeting_api/main.py`(ルータ登録1行)
- `services/meeting-api/tests/`(新規テスト)
- `services/api-gateway/main.py`(転送ルート)
- `services/api-gateway/tests/`(ルートのテスト)
- `.github/workflows/test-meeting-api.yml`(migration 適用ステップ + paths 追加)

### 1. migration スクリプト(20260708_add_speaker_cluster.py の構成を踏襲)

- up: `CREATE EXTENSION IF NOT EXISTS pg_trgm` →
  `CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transcription_text_trgm
  ON transcriptions USING gin (text gin_trgm_ops)`(autocommit、トランザクション外)
- down: `DROP INDEX CONCURRENTLY IF EXISTS ...`(extension は残す)
- status: extension と index の有無を表示
- up は2連続実行で成功する(冪等)

### 2. models.py

`Transcription.__table_args__` へ追加(speaker_cluster の前例と同形):

```python
Index('ix_transcription_text_trgm', 'text',
      postgresql_using='gin',
      postgresql_ops={'text': 'gin_trgm_ops'},
      info={'online_only': True}),
```

`online_only` により起動時 schema-sync は既存テーブルへの同期作成をスキップする
(~507K 行の本番で書き込みロックを取らないため必須)。新規インストールは
create_all がテーブルごと作るためロック問題なし。

### 3. database.py(新規インストール対応)

ensure_schema 呼び出しの直前に `CREATE EXTENSION IF NOT EXISTS pg_trgm` を実行する。
pg_trgm は PG13 以降 trusted extension のため DB オーナー権限で作成可。
失敗時は warning ログのみで起動続行(fail-fast にしない)。

### 4. 検索エンドポイント(meeting_api/search.py)

```
GET /transcripts/search?q=<str>&limit=<int>&offset=<int>
依存: get_user_and_token(既存パターン)
```

- 検証: q は strip 後 2〜100 文字。空・1文字・空白のみは 422。
  limit 既定 20・最大 50(会議単位)。offset ≥ 0。
- LIKE メタ文字(`\`, `%`, `_`)をエスケープした**リテラル一致**
  (リファクタ v2 RF-12 のリテラル契約と整合)。ILIKE で大文字小文字非区別。
- transcriptions JOIN meetings、`Meeting.user_id == current_user.id` を必ず適用。
  会議単位グルーピング、`Meeting.created_at DESC` 順。1会議あたりマッチ発言は
  start_time 昇順で最大3件 + 総マッチ数。limit+1 方式で has_more。
  SQL の組み立て方は実装者の裁量。契約は挙動のみ。
- レスポンス形は親プラン §How Stage 1-4 の JSON(query / results[] /
  meeting 要約 + match_count + matches[] / has_more)。title は
  `_meeting_list_data_summary` と同じ解決順を再利用。
- ハイライト位置の計算はサーバでは行わない。

### 5. api-gateway(main.py)

`GET /transcripts/search` を `MEETING_API_URL` へ転送する明示ルートを、既存の
`/transcripts/{platform}/{native_meeting_id}` ルートより上に宣言。
`dependencies=[Depends(api_key_scheme)]`。API_SCOPES の解決が新パスへどう効くかを
現物確認し、既存 `/transcripts/...` と同じスコープ意味論に揃える(契約 RF-304)。

### 6. テスト

- ユニット(DB 不要): q 検証・LIKE エスケープ関数・online_only マーク
  (test_schema_sync_online_index.py の前例に追加)・起動時 extension 発行順と失敗許容。
- postgres 統合(`RUN_POSTGRES_INTEGRATION_TESTS=1` ゲート):
  日本語本文ヒット・グルーピング・順序・match_count・has_more、
  メタ文字リテラル一致、**2ユーザー分離(FP-301)**。
- api-gateway: 新ルートの認証・スコープ・転送のテスト。

### 7. CI(test-meeting-api.yml)

- 辞書 migration ステップの後に本 migration の `up` を追加。
- paths へ `scripts/migrations/**` を追加。

### 8. デプロイ手順(PR 説明に記載)

マージ後・デプロイ前に本番(LKE の postgres pod)で
`DATABASE_URL=... python scripts/migrations/20260809_add_transcription_text_trgm.py up`
を先行実行(CONCURRENTLY のため書き込みを止めない)。先行実行すれば起動時
schema-sync は no-op。
