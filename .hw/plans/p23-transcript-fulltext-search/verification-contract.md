# Verification Contract — p23-transcript-fulltext-search(親契約)

本タスクは2サブタスクへ分割して実装する。親契約の完了条件は次の2点のみ。

1. `p23-transcript-search-api` の契約が PASS し PR がマージされていること。
2. `p23-transcript-search-ui` の契約が PASS し PR がマージされていること。

サブタスク着手時に planner(Fable)が本契約の該当ステージの表を抽出してサブタスク
契約を確定し、base-commit を当時の HEAD に更新、ベースラインを下記手順で実測する。

判定主体の凡例: **Fable** = base-commit..HEAD の差分と契約本文のみで判定
(gitignore 領域 `.hw/gates/` には到達できない。実測値は契約本文の改訂履歴に記入させる)/
**CI** = GitHub Actions(最終権威)/ **ゲート** = pr-ready-gate 実行者が
`.hw/gates/<task-id>/` の証跡を確認。

## 監査 ID → 検証項目の対応

| 監査 ID | 要求 | 検証先 |
|---|---|---|
| FT-3 | 会議横断の全文検索(現状タイトル ILIKE のみ、meetings.py:1426-1438 系) | Stage 1 AT-301〜308 / Stage 2 AT-311〜313 |
| 依頼者指定 | 既存認可境界の維持(他ユーザーの文字起こしが検索で漏れない) | FP-301 |

## ベースライン取得手順(転記値の使用禁止)

- **Stage 1(meeting-api)**: 着手時 base-commit の clean tree で python3.11 fresh venv を
  `~/.cache/hw-venvs/p23-transcript-search-api` に作成(/tmp 不可)し、
  `pip install -e libs/admin-models/ -e services/meeting-api/` +
  `pip install pytest pytest-asyncio httpx psycopg2-binary`。
  `python -m pytest services/meeting-api/tests --ignore=services/meeting-api/tests/test_integration_live.py`
  のサマリ全文を `.hw/gates/p23-transcript-search-api/pytest-baseline-<commit>.txt` へ保存し、
  数値と failed テスト名を**サブタスク契約の改訂履歴へ記入して commit** する。
  api-gateway も同様に既存テストのベースラインを取る(スイートがあれば)。
- **Stage 2(dashboard)**: `cd services/dashboard && npm install --no-audit --no-fund &&
  npm test` と lint ベースライン比較(`lint-baseline.json`)の結果を同様に記録。

## Stage 1: p23-transcript-search-api

### Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-301 | migration スクリプト `up` が `CREATE EXTENSION IF NOT EXISTS pg_trgm` と `CREATE INDEX CONCURRENTLY ... ix_transcription_text_trgm (gin_trgm_ops)` を実行し、2連続実行しても成功する(冪等)。`down`/`status` あり | CI の postgres サービスで up→up→status→down→up を実行する workflow ステップ or 統合テスト | CI + Fable(diff) | CI ログ |
| AT-302 | `GET /transcripts/search?q=<日本語>` が、認証ユーザー所有会議の本文マッチを会議単位グルーピング(Meeting.created_at 降順、1会議あたりマッチ最大3件 + match_count、limit+1 方式の has_more)で返す | postgres 統合テスト(日本語データ投入、`RUN_POSTGRES_INTEGRATION_TESTS=1`) | CI + ゲート | pytest ログ |
| AT-303 | **リテラル一致契約**: `\` `%` `_` を含むクエリがエスケープされ、`50%` は文字列 `50%` を含む行のみにヒットする(全行ヒットしない) | postgres 統合テスト + エスケープ関数ユニットテスト | CI + Fable | pytest ログ |
| AT-304 | 入力検証: q 欠落・空・strip後1文字・空白のみ・101文字以上 → 422。limit 既定20・最大50、offset≥0 | ユニット/統合テスト | CI + Fable | pytest ログ |
| AT-305 | モデルの `ix_transcription_text_trgm` が `info={'online_only': True}` を持ち、起動時 schema-sync が既存テーブルへ同期作成しない(本番 ~507K 行で書き込みロックを取らない) | `test_schema_sync_online_index.py` の前例に倣うユニットテスト + diff | CI + Fable | pytest ログ + diff |
| AT-306 | meeting-api 起動系が ensure_schema の前に `CREATE EXTENSION IF NOT EXISTS pg_trgm` を発行し、失敗時は warning のみで起動続行する(fail-fast にしない) | ユニットテスト(発行順・失敗許容)+ diff | CI + Fable | pytest ログ |
| AT-307 | api-gateway に `GET /transcripts/search` の転送ルートがあり、既存 `/transcripts/{platform}/{native_meeting_id}` と同じ認証・スコープ意味論で MEETING_API_URL へ転送する | gateway テスト(既存転送テストパターン)+ diff | CI + Fable | テストログ + diff |
| AT-308 | CI(test-meeting-api.yml)が新 migration を適用してから統合テストを実行し、paths に `scripts/migrations/**` が含まれる | workflow 差分 + 実 CI 実行(`gh run list --branch` で workflow 名を確認) | Fable(diff)+ CI | workflow 実行ログ |

### Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-301 | **認可境界**: ユーザーAの検索結果にユーザーBの会議・文字起こしが決して含まれない(検索条件・グルーピング・スニペット取得のすべての経路で `Meeting.user_id` フィルタが効く) | postgres 統合テスト(2ユーザー、同一文言をB側に投入してAで検索 → 0件) | CI + ゲート | pytest ログ |
| FP-302 | 変更ファイルが plan.md 記載の Stage 1 集合のみ。判定コマンドは `git diff --name-only <base>..HEAD -- ':(exclude).hw/plans'`(`.hw/plans/` は許容。それ以外の `.hw/` は除外しない) | diff | Fable | diff |
| FP-303 | 既存 `/bots?search=`(タイトル ILIKE)・既存 `/transcripts/{platform}/{native_meeting_id}` の挙動に差分なし | diff + 既存テスト非退行 | Fable + CI | diff + pytest ログ |
| FP-304 | meeting-api 既存テスト非退行: ベースライン failed 集合に対し新規 fail 0(passed 増は新設テスト分のみ) | 同一 venv・同一コマンドでサマリ比較 | ゲート + Fable(改訂履歴の実測値と照合) | 両サマリ全文 |
| FP-305 | `libs/schema-sync/` に差分なし(extension 作成は meeting-api 側に置く) | diff | Fable | diff |
| FP-306 | 新規ランタイム依存なし(requirements.txt / pyproject.toml の依存追加なし。migration は既存同様 psycopg2 を実行時 import) | diff | Fable | diff |

### Non-Functional Checks

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| NFT-301 | 2文字クエリでもエラーにせず結果を返す(seq scan フォールバック許容。拒否は UX 破壊のため不可) | 統合テスト | CI | pytest ログ |
| NFT-302 | レスポンス上限が構造的に有界(会議 limit ≤ 50、マッチ ≤ 3/会議。全文 blob を返さない) | テスト + diff | Fable + CI | pytest ログ |

## Stage 2: p23-transcript-search-ui

### Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-311 | 会議一覧ページの検索入力(strip後2文字以上、既存300msデバウンス)で `/api/vexa/transcripts/search` が呼ばれ、「文字起こしに一致」セクションに会議タイトル(詳細への既存導線)+スニペット(最大3件)+総マッチ数が表示される | vitest(整形・呼び出しロジック)+ 手動確認1回 | CI + ゲート(スクリーンショット) | テストログ + 証跡 |
| AT-312 | マッチ部分がリテラル一致で `<mark>` 強調される(正規表現メタ文字を含むクエリでも安全) | 強調ユーティリティのユニットテスト(メタ文字ケース含む) | CI + Fable | テストログ |
| AT-313 | ローディング・0件・エラー状態が日本語コピーで表示される。新設文言はすべて日本語(dashboard-copy.ts 経由) | vitest + diff(新設文字列レビュー) | Fable + CI | diff |

### Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-311 | 既存のタイトル検索・status/platform フィルタ・一覧表示の挙動に差分なし(検索ボックスは共用だが一覧の絞り込み結果は従来どおり) | vitest 既存テスト + diff | CI + Fable | テストログ |
| FP-312 | dashboard テスト非退行 + lint ベースライン比較(lint-baseline.json)を悪化させない(eslint exit≥2 は即 fail) | CI(#63 の仕組み) | CI | CI ログ |
| FP-313 | 変更ファイルが `services/dashboard/` 配下のみ(`:(exclude).hw/plans` 方式で判定) | diff | Fable | diff |

## Gate Requirements

- preflight result required: yes(各サブタスク側)
- evidence pack required: yes(`.hw/gates/<subtask-id>/` へ)
- hash-bound approval required: yes(両サブタスクとも M、Fable 契約レビュー必須)
- research brief required: no(本プランのリサーチ節に実測記録済み)
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks(実装時に実測し、結果をサブタスク契約の改訂履歴へ記入)

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-301 | CI の postgres サービス(postgres:16-alpine)でも `CREATE EXTENSION pg_trgm` と gin_trgm_ops が成功すること(プラン時実測は 17-alpine のみ) | CI 実行ログで migration ステップの成功を確認 | CI ログ + 契約改訂履歴 |
| RF-302 | インデックスが実際に効くこと: 50K 行以上の生成データで strip後3文字以上のクエリが Bitmap Index Scan になること(EXPLAIN)。2文字クエリの seq scan 実測時間も記録(プラン時実測: index 0.105ms / seq 24ms @50K行、postgres:17-alpine) | 実装時にローカル postgres で EXPLAIN ANALYZE を1回実測 | `.hw/gates/` に EXPLAIN 出力 + 契約改訂履歴に数値 |
| RF-303 | CREATE INDEX CONCURRENTLY がトランザクション外実行を要すること(psycopg2 autocommit)。20260708 スクリプトの実装を踏襲して確認 | migration スクリプトの statusコマンド実測 + 既存スクリプトとの diff 比較 | `.hw/gates/` に実行ログ |
| RF-304 | api-gateway の API_SCOPES(`"/transcripts": {"tx"}`)のパス解決が `/transcripts/search` にどう適用されるか(prefix 一致か完全一致か)。既存 `/transcripts/...` と同じスコープ意味論になることを現物確認 | gateway のスコープ解決関数を実装時に読み、テストで固定 | テストコード + 契約改訂履歴 |

## 改訂履歴

### プラン時実測(2026-08-09、planner 実施、vexa-postgres-1 = postgres:17-alpine / PG 17.10)

- `pg_available_extensions`: pg_trgm 1.6 / fuzzystrmatch 1.2 のみ(pg_bigm・pgroonga・
  textsearch_ja・pgvector なし)。
- `show_trgm('日本語の会議記録')` → 9 トライグラム(マルチバイト動作確認)。
- 50K 行 + gin_trgm_ops GIN: `ILIKE '%買収監査%'` = Bitmap Index Scan 0.105ms、
  `ILIKE '%買収%'`(2文字)= Seq Scan 24ms。
- ローカル transcriptions: 5,949行 / avg 42字 / p95 152字 / total 5MB。
  本番想定 ~507K 行(models.py:92 コメント)。

### Stage 1 `p23-transcript-search-api` 実装時実測(2026-08-09、implementer 実施)

base-commit = `7c3c6555f63950eaf5a496da07b6a84da62fd0e8`(clean tree で実測)。
venv = `~/.cache/hw-venvs/p23-transcript-search-api`(python 3.11.15、fresh)。
証跡は `.hw/gates/p23-transcript-search-api/`(gitignore 領域)。

**ベースライン(base-commit、実装前)**

- meeting-api: `python -m pytest services/meeting-api/tests
  --ignore=services/meeting-api/tests/test_integration_live.py`
  → **693 passed, 11 skipped, 0 failed**(failed テスト名: なし)。20.37s。
- api-gateway: `python -m pytest services/api-gateway/tests/`(別 venv、
  `~/.cache/hw-venvs/p23-transcript-search-api-gw`)
  → **117 passed, 4 errors, 0 failed**。errors は既存の
  `test_gate_g5_websocket.py::TestG5WebSocketLiveDelivery`(fixture setup の
  `KeyError: 'meetings'`、実サービス依存)4件でベースライン既存。

**実装後(同一 venv・同一コマンド)**

- meeting-api: **726 passed, 19 skipped, 0 failed**(passed +33 = 新設ユニット
  テスト分、skipped +8 = 新設 postgres 統合テストが env 未設定でスキップ)。
- api-gateway: **120 passed, 4 errors, 0 failed**(passed +3、errors はベース
  ラインと同一の4件で増減なし)。
- postgres 統合(`RUN_POSTGRES_INTEGRATION_TESTS=1`、postgres:16-alpine):
  `tests/integration/test_transcript_search_postgres.py` → **8 passed**
  (FP-301 の2ユーザー分離テスト2件を含む)。
- 新 migration の up→up→status→down→status→up→status を実測し全て成功
  (`rf303-migration-run.txt`)。

**Research Freshness 実測結果**

- **RF-301(PASS)**: CI と同じ `postgres:16-alpine`(PostgreSQL 16.14
  aarch64-linux-musl)の使い捨てコンテナで確認。`pg_available_extensions` は
  `pg_trgm 1.6` と `fuzzystrmatch 1.2` のみ(pg_bigm・pgroonga・vector なし)で
  17-alpine と同結論。`CREATE EXTENSION pg_trgm` と `gin_trgm_ops` GIN index の
  作成が成功。`show_trgm('日本語の会議記録')` → 9 トライグラム(musl でも
  マルチバイト動作)。CI 実行ログでの最終確認は PR 後。
- **RF-302(PASS)**: 同コンテナに 50,001 行(avg 28 字)を投入し
  `ANALYZE` 後に EXPLAIN ANALYZE 実測:
  4文字 `ILIKE '%買収監査%'` = Bitmap Index Scan on ix_transcription_text_trgm
  **0.067ms**、3文字 `ILIKE '%予算計%'`(10,000 行ヒット)= Bitmap Index Scan
  **9.711ms**、2文字 `ILIKE '%会議%'`(10,000 行ヒット)= Seq Scan **26.701ms**。
  実装の実クエリ形(meetings JOIN + user_id フィルタ + EXISTS + ORDER BY
  created_at DESC LIMIT 21)も Bitmap Index Scan を使い **0.079ms**。
  プラン時実測(17-alpine: index 0.105ms / seq 24ms)と同傾向で、結論は不変。
- **RF-303(PASS)**: `BEGIN; CREATE INDEX CONCURRENTLY ...; COMMIT;` は
  `ERROR: CREATE INDEX CONCURRENTLY cannot run inside a transaction block` を
  返すことを実測。よって 20260708 スクリプトと同じく psycopg2 の
  `conn.autocommit = True` に切り替えてから発行する構成を踏襲した。
  up の2連続実行・down 後の再 up がいずれも成功(冪等)。
- **RF-304(PASS)**: api-gateway のスコープ解決は `forward_request` 内の
  `req_path.startswith(prefix)` による**前方一致**(services/api-gateway/main.py)。
  したがって `/transcripts/search` は `ROUTE_SCOPES["/transcripts"] = {"tx"}` に
  一致し、既存 `/transcripts/{platform}/{native_meeting_id}` と同一のスコープ
  意味論になる。`tests/test_transcript_search_route.py` で
  scope=bot → 403、scope=tx → 転送成功、APIキー無し → 401 を固定した。
