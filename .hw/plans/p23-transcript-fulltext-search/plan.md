---
generated_by: fable
task_id: p23-transcript-fulltext-search
base-commit: 7c3c6555f63950eaf5a496da07b6a84da62fd0e8
size: M
type: umbrella
subtasks:
  - p23-transcript-search-api   # migration + 検索API + gateway(先行)
  - p23-transcript-search-ui    # ダッシュボード統合(後続)
---

# P2-3 横断全文検索(FT-3)— 親プラン

各サブタスク着手時にコーディネータが planner(Fable)でサブタスク用
plan/contract を本プランから抽出・確定し、base-commit を当時の HEAD へ更新すること。

## 依頼の文字通りの内容と再設計後のゴール

文字通りの依頼: 「Transcription テーブルへ Postgres FTS(pgvector はスコープ外)」。

reframe(手段の読み替え。目的とスコープ外指定は不変):
「Postgres FTS」の語は tsvector/tsquery 機構を暗示するが、日本語 UI 限定方針の本製品では
`to_tsvector('english'|'simple', ...)` は分かち書きされない日本語文を実質1トークンに潰し
機能しない。日本語対応 FTS 拡張(pg_bigm / pgroonga / textsearch_ja)は、
**全配備系統で使われている公式イメージ `postgres:17-alpine` に含まれず利用不可**
(下記リサーチで実測)。したがって手段を「**pg_trgm(同イメージに同梱)+ GIN
トライグラムインデックス + ILIKE 部分一致**」へ読み替える。部分一致は日本語では
分かち書き問題を回避できる正攻法であり、検索精度の面でも tsvector 方式の劣化版ではない。

再設計後のゴール(FT-3 の解消):
- 認証ユーザーが自分の全会議の文字起こし本文を横断検索し、ヒットした会議と
  該当発言(スニペット)から会議詳細へ到達できる。
- 検索はインデックスに支えられ、本番規模(~507K 行)で実用速度で返る
  (3文字以上のクエリで index scan。2文字はフォールバックの seq scan を許容)。
- 既存の認可境界(Meeting.user_id によるオーナーシップ)を踏襲し、
  他ユーザーの文字起こしが決して混入しない。

## リサーチ結果(仮説 → 反証確認 → 確信度 → 覆る条件)

すべて 2026-08-09、稼働中の実コンテナ `vexa-postgres-1`(postgres:17-alpine,
PostgreSQL 17.10 aarch64-musl)で実測。

1. **仮説: 日本語 FTS 拡張(pg_bigm/pgroonga)は使えない** → 実測で確定:
   `pg_available_extensions` に該当なし。利用可能なのは `pg_trgm 1.6` と
   `fuzzystrmatch 1.2` のみ(pgvector も無い)。配備3系統の確認:
   compose = `postgres:17-alpine`(docker-compose.yml:32)、
   helm = 同(values.yaml:347、本番 LKE の statefulset-postgres.yaml が参照)、
   GCP = dashboard のみ(deploy/gcp/cloudbuild-dashboard.yaml。DB は GCP に無い)。
   マネージド Postgres は不使用。**確信度: 高**。
   覆る条件: カスタム Postgres イメージの導入を別途決断した場合(本タスクでは行わない。
   イメージ変更は全配備系統・バックアップ運用に波及し FT-3 の必要範囲を超える)。
2. **仮説: pg_trgm は Alpine/musl でも日本語(マルチバイト)トライグラムを生成する**
   (musl の locale 制限で CJK が落ちる懸念があった)→ 実測で確定:
   `show_trgm('日本語の会議記録')` が9個のトライグラムを生成。
   50,000行の生成データ + `gin_trgm_ops` GIN インデックスで、
   4文字クエリ `ILIKE '%買収監査%'` は Bitmap Index Scan(0.105ms)、
   2文字クエリ `ILIKE '%買収%'` はトライグラム抽出不能で Seq Scan(24ms/50K行)。
   **確信度: 高**。覆る条件: Postgres メジャーアップグレードでの挙動変化(可能性低)。
3. **2文字クエリの seq scan は許容できる**: 本番 transcriptions は ~507K 行
   (models.py:92 コメント、2026-07-08 時点)、ローカル実測 avg 42字/p95 152字/行。
   テーブル全体は数十MB規模で、seq scan でも推定 1秒未満。**確信度: 中**
   (本番ハードでの実測は未実施)。覆る条件: 行数が桁で増えた場合
   → その時は最低クエリ長を3文字に引き上げる逃げ道がある(API パラメータ検証のみで変更可)。
4. **マイグレーション運用**: Alembic は requirements にあるが実運用は
   `scripts/migrations/` の手書きオンラインスクリプト(psycopg2、バッチ、
   CREATE INDEX CONCURRENTLY)+ 起動時 schema-sync(`libs/schema-sync`。
   `info={'online_only': True}` のインデックスは起動時作成をスキップ)。
   **マージ後・デプロイ前に migration 先行実行**が確立手順(20260708 スクリプト
   docstring と話者アトリビューション時の運用で確認)。CI(test-meeting-api.yml)は
   postgres:16-alpine のサービスコンテナに migration を適用してから
   `RUN_POSTGRES_INTEGRATION_TESTS=1` で統合テストを回す前例あり(辞書 migration)。
5. **既存の検索資産**: サーバ側は `/bots?search=` のタイトル系 ILIKE のみ
   (meetings.py:1460-1467。FT-3 の指摘どおり本文検索なし)。ダッシュボード
   詳細ページに会議内クライアントサイド検索あり(リファクタ v2 RF-12 が
   「リテラル一致へ固定」を予定 → 新 API も LIKE メタ文字をエスケープした
   リテラル一致契約で揃える)。assistant-context API は会議単位で横断検索とは非衝突。
6. **リファクタ v2 との衝突確認**: RF-12(dashboard 会議内検索)とは対象が別で非衝突。
   D1〜D7 配備境界(認可・秘密値)には触れない — 新エンドポイントは既存の
   `get_user_and_token` + `Meeting.user_id` フィルタという確立済み認可パターンの
   純追加であり、境界の変更を伴わない。

## スコープ(最低合格ライン。膨らませない)

含む:
- transcriptions.text への pg_trgm GIN インデックス(migration + モデル定義)
- meeting-api の横断検索エンドポイント `GET /transcripts/search`(会議単位に
  グルーピングした結果 + マッチ発言スニペット)
- api-gateway の転送ルート
- ダッシュボード会議一覧ページの検索への統合(文字起こしヒットの表示、日本語コピー)

含まない(根拠):
- 話者・日付での絞り込み: FT-3 の監査事実は「本文の横断検索が無い」ことのみ。
  既存の status/platform フィルタは残るし、追加絞り込みは後付け可能。契約を最小に保つ。
- pgvector・意味検索・AI 層: 別プロジェクト(ユーザー指示)。
- 検索結果から会議詳細内の該当発言への自動スクロール: 詳細ページには既存の
  会議内検索があり到達手段は既にある。advisory 候補として残す。
- 共有ビュー・MCP・assistant-context への検索追加: FT-4/別経路の責務。
- 検索権限モデルの再設計: セキュリティフェーズ。ただし**既存認可境界の維持は
  本タスクの責務**(契約 FP-301)。

## タスク分割の判断: 2タスク(2PR)へ分割する

| task-id | スコープ | 着手順 |
|---|---|---|
| `p23-transcript-search-api` | migration + モデル index + 起動時 extension + 検索エンドポイント + gateway ルート + テスト + CI 配線 | 1(先行) |
| `p23-transcript-search-ui` | ダッシュボード統合(一覧ページ検索の拡張、日本語コピー、テスト) | 2(後続) |

根拠: 検証手段が完全に別系統(pytest + CI postgres / vitest + lint ラチェット)で、
API が確定しないと UI の契約が書けない。1タスクに束ねると差分が meeting-api・
api-gateway・dashboard・workflows へ跨がり Fable レビューの焦点が散る。
逆にこれ以上細かく(migration と API を分割)すると、インデックス無しの検索 API という
中間状態が生まれ検証できない。

## How — Stage 1: `p23-transcript-search-api`

変更ファイル(この集合のみ。契約 FP-302):
- `scripts/migrations/20260809_add_transcription_text_trgm.py`(新規)
- `services/meeting-api/meeting_api/models.py`
- `services/meeting-api/meeting_api/database.py`
- `services/meeting-api/meeting_api/search.py`(新規ルータ)+ ルータ登録1行
  (肥大化した meetings.py へは足さない。登録箇所は実装時に main の既存
  include_router 位置を確認)
- `services/meeting-api/tests/`(新規テスト)
- `services/api-gateway/main.py`(転送ルート)+ api-gateway のテスト
- `.github/workflows/test-meeting-api.yml`(migration 適用ステップ +
  paths へ `scripts/migrations/**` 追加)

### 1. migration スクリプト(20260708_add_speaker_cluster.py の構成を踏襲)

- up: `CREATE EXTENSION IF NOT EXISTS pg_trgm` →
  `CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transcription_text_trgm
  ON transcriptions USING gin (text gin_trgm_ops)`(autocommit、トランザクション外)
- down: `DROP INDEX CONCURRENTLY IF EXISTS ...`(extension は他用途の可能性を考え残す)
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
pg_trgm は PG13 以降 trusted extension のため DB オーナー権限で作成可
(全配備系統でアプリユーザーが DB オーナー)。失敗時は warning ログのみで起動続行
(#57 の教訓: 起動 fail-fast を安易に増やさない)。

### 4. 検索エンドポイント(meeting_api/search.py)

```
GET /transcripts/search?q=<str>&limit=<int>&offset=<int>
依存: get_user_and_token(既存パターン)
```

- 検証: q は strip 後 2〜100 文字。空・1文字・空白のみは 422。
  limit 既定 20・最大 50(会議単位)。offset ≥ 0。
- LIKE メタ文字(`\`, `%`, `_`)をエスケープした**リテラル一致**
  (RF-12 のリテラル契約と整合)。大文字小文字は ILIKE で非区別。
- クエリ: transcriptions JOIN meetings、`Meeting.user_id == current_user.id`
  を必ず適用。会議単位にグルーピングし `Meeting.created_at DESC` 順。
  1会議あたり返すマッチ発言は start_time 昇順で最大3件 + 総マッチ数。
  limit+1 件取得方式で has_more(list_user_bots の既存パターン)。
  SQL の組み立て方(ウィンドウ関数か2段クエリか)は実装者の裁量。契約は挙動のみ。
- レスポンス:

```json
{
  "query": "...",
  "results": [
    {
      "meeting": {"id": 1, "platform": "...", "native_meeting_id": "...",
                   "status": "...", "title": "...", "start_time": "...",
                   "created_at": "..."},
      "match_count": 5,
      "matches": [
        {"start_time": 12.3, "end_time": 15.0, "speaker": "...", "text": "..."}
      ]
    }
  ],
  "has_more": false
}
```

  title は `_meeting_list_data_summary` と同じ解決順
  (data.name → data.title → calendar_event.title)を再利用する。
- ハイライト位置の計算はサーバでは行わない(クライアントがリテラル一致で再現可能)。

### 5. api-gateway(main.py)

`GET /transcripts/search` を `MEETING_API_URL` へ転送する明示ルートを、既存の
`/transcripts/{platform}/{native_meeting_id}` ルートより**上に**宣言
(セグメント数が違うため実害はないが可読性のため)。`dependencies=[Depends(api_key_scheme)]`。
API_SCOPES の `"/transcripts": {"tx"}` の解決が新パスにどう効くかは実装時に現物確認し、
既存 `/transcripts/...` と同じスコープ意味論に揃える(Research Freshness RF-304)。

### 6. テスト

- ユニット(DB 不要): q 検証(空/1文字/空白/101文字/正常)、LIKE エスケープ関数、
  online_only マーク(test_schema_sync_online_index.py の前例に追加)。
- postgres 統合(`RUN_POSTGRES_INTEGRATION_TESTS=1` ゲート、新規
  `tests/integration/test_transcript_search_postgres.py`):
  日本語本文でのヒット・会議グルーピング・順序・match_count・has_more、
  メタ文字リテラル一致(`50%` が `50%` を含む行のみにヒット)、
  **2ユーザー分離(ユーザーAの検索にユーザーBの行が決して出ない)**。
- api-gateway: 既存テストスイートへのルート追加テスト(既存の転送テストパターンに
  倣う。無ければ非退行のみ)。

### 7. CI(test-meeting-api.yml)

- 辞書 migration ステップの後に本 migration の `up` を追加。
- paths へ `scripts/migrations/**` を追加(migration のみ変更の PR で CI が走るように)。

### 8. デプロイ手順(PR 説明に記載させる)

マージ後・デプロイ前に本番(LKE の postgres pod)で
`DATABASE_URL=... python scripts/migrations/20260809_add_transcription_text_trgm.py up`
を実行(CONCURRENTLY のため書き込みを止めない。~507K 行で数十秒〜数分想定)。
先行実行すれば起動時 schema-sync は no-op。

## How — Stage 2: `p23-transcript-search-ui`

変更ファイル: `services/dashboard/src/`(meetings/page.tsx、dashboard-copy.ts、
新規コンポーネント・lib)と `services/dashboard/tests/` のみ。

- 会議一覧ページの既存検索ボックス(300ms デバウンス済み)を拡張し、strip 後2文字以上の
  入力で既存タイトル検索(`/bots?search=`)に**加えて** `/api/vexa/transcripts/search` を
  呼ぶ。既存の一覧絞り込み挙動は変えない。
- 文字起こしヒットは一覧の上または下に「文字起こしに一致」セクションとして表示:
  会議タイトル(リンク → `/meetings/{id}` 相当の既存詳細導線)+ マッチ発言スニペット
  (最大3件)+ 総マッチ数。クエリ文字列のリテラル一致部分を `<mark>` で強調。
- ローディング・0件・エラーの各状態を日本語コピーで表示(dashboard-copy.ts へ追加。
  英語文言の新設は不可 — 日本語限定方針)。
- テスト(vitest): レスポンス整形・スニペット強調ユーティリティのユニットテスト。
  lint はベースライン比較方式(lint-baseline.json)を悪化させない。

## Why(実装者に渡さない)

- **pg_trgm を選んだ理由**: 唯一、配備3系統すべての公式イメージで追加インストール不要の
  索引付き部分一致手段だから。代替案の比較:
  (a) pg_bigm/pgroonga — 検索品質は上だがカスタムイメージが必要になり、DB イメージの
  変更は本タスクの影響半径(バックアップ・helm・compose・lite)を大きく超える。
  (b) simple パーサ + アプリ側 bigram 生成の tsvector — 拡張不要だが生成列と
  アプリの二重実装が要り、精度は pg_trgm の部分一致と実質同等。複雑さに見合わない。
  (c) 素の ILIKE + インデックスなし — 507K 行では動くが、スケールで黙って劣化する。
  pg_trgm はインデックスという「効いていることを EXPLAIN で機械検証できる」構造を残す。
- **2文字クエリの seq scan を許容した理由**: 日本語の2文字語(会議・予算など)は
  頻出だが、トライグラム抽出の数学的制約で index が効かない。現規模では seq scan が
  実用域(実測 24ms/50K 行)で、拒否する(3文字未満をエラーにする)方が UX を壊す。
  規模が伸びたら最低長を上げる逃げ道を API 検証に残してある。
- **UI を含めた理由**: FT-3 は製品ギャップ監査であり、API だけでは「横断全文検索なし」の
  ユーザー体験は変わらない。ただし PR は分け、API を先に固める。
- **絞り込み・セマンティック検索を切った理由**: 契約は最低合格ライン。話者・日付絞り込みは
  監査事実の外で、後続タスクで純追加できる。
- **認可を FP にした理由**: 検索は「自分の全データを横断する」初のクエリ面で、
  JOIN の書き損じ一発で全ユーザーのテキストが漏れる。既存境界の維持は
  セキュリティフェーズを待たない本タスクの責務(依頼者指定)。
- **CREATE EXTENSION を起動時に置いた理由**: 新規インストール(compose/lite)は
  migration スクリプトを回さず create_all で立ち上がるため、extension が先に無いと
  トライグラム index の作成で起動が壊れる。trusted extension なので権限リスクは低い。
