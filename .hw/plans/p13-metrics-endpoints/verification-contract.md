# Verification Contract — p13-metrics-endpoints

対象: `base-commit..HEAD` の差分(base-commit はコーディネータが着手時に
plan.md frontmatter へ書き込んだ値)。テストは commit 済み clean tree に対して実行する。

## ベースライン取得手順(FP-001/FP-002 の前提。転記値の使用禁止)

1. 実装着手前に base-commit をチェックアウトした clean tree で、以下2つの
   fresh venv(python3.11、`~/.cache/hw-venvs/p13-metrics-endpoints-*` 等 /tmp 以外)を作成:
   - meeting-api: `pip install -e libs/admin-models/ -e services/meeting-api/` +
     `pip install pytest pytest-asyncio httpx` → `python -m pytest services/meeting-api/tests`
   - runtime-api: `pip install -e "services/runtime-api/[dev]"` →
     `python -m pytest services/runtime-api/tests`
2. 各サマリ全文を `.hw/gates/p13-metrics-endpoints/pytest-baseline-<svc>-<commit>.txt` に保存する。
3. **この実測値が本契約のベースライン**である。handoff や過去契約からの転記値は無効。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | **監査3指標の露出(本質要求)**: meeting-api `GET /metrics` が 200 + `text/plain; version=0.0.4` で、`meeting_api_meetings_24h{status=...}`(参加成功率の分母分子)・`meeting_api_final_transcription_latency_seconds{quantile="0.5"/"0.95"}`(文字起こし遅延)・`meeting_api_final_transcription_backlog{status=...}`(sweep滞留)の3系統を含む text を返す(DB クエリ関数は patch 可) | `python -m pytest services/meeting-api/tests/test_metrics.py -v` | pytest ログを `.hw/gates/p13-metrics-endpoints/` に保存 |
|AT-002 | 既存カウンタの露出: `meeting_api_sweep_iterations_total` / `meeting_api_final_transcription_worker_iterations_total` と各 last_iteration timestamp gauge が sweeps モジュールの現在値を反映する(monkeypatch で値を仕込んで反映を確認 = import 時束縛でないことも同時に検証) | unit | pytest ログ |
| AT-003 | text format 準拠: render 純関数の出力が各 metric に `# HELP` / `# TYPE`(counter または gauge)行を持ち、ラベル値の `"` `\` 改行がエスケープされ、末尾改行で終わる | unit | pytest ログ |
| AT-004 | 部分故障耐性: DB 集計関数が例外を投げても /metrics は 200 を返し、プロセス内カウンタ系 metric は出力に残る。Redis 不可時は `meeting_api_collector_stream_lag` が -1 | unit | pytest ログ |
| AT-005 | runtime-api /metrics: 認証ヘッダなしで 200(skip list 追加の検証)+ `runtime_api_idle_loop_iterations_total` 行を含む(fakeredis) | `python -m pytest services/runtime-api/tests/test_metrics.py -v` | pytest ログ |
| AT-006 | sweep滞留クエリの整合: backlog の SQL 条件が sweeps.py の final-transcription 対象選定(queued / running / failed+retryable)と同じ status 集合を数える(テストで SQL 文字列または結果分類を assert) | unit | pytest ログ |
| AT-007 | 運用手順の記載: deploy/compose/README.md に /metrics の取得コマンドと3指標の読み方の節が追加されている | `grep -n "metrics" deploy/compose/README.md` | grep 出力 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | meeting-api テスト: ベースラインに対し新規 fail 0・passed 増は本タスクの新規テスト分のみ・skipped 増加なし | ベースラインと変更後を同一 venv・同一コマンドで実行しサマリ比較 | 両サマリ全文 |
| FP-002 | runtime-api テスト: 同上(fresh venv 実測ベースライン比較) | 同上 | 両サマリ全文 |
| FP-003 | 変更ファイルが `services/meeting-api/meeting_api/metrics.py`(新規)・`services/meeting-api/meeting_api/main.py`・`services/meeting-api/tests/`・`services/runtime-api/runtime_api/main.py`・`services/runtime-api/tests/`・`deploy/compose/README.md` のみ(sweeps.py / final_transcription.py / lifecycle.py / compose yml / helm は無変更) | `git diff --name-only base-commit..HEAD` | diff 出力 |
| FP-004 | 依存追加なし: meeting-api pyproject.toml・runtime-api pyproject.toml の dependencies が無変更(prometheus_client 等を足していない) | `git diff base-commit..HEAD -- services/meeting-api/pyproject.toml services/runtime-api/pyproject.toml` が空 | diff 出力 |
| FP-005 | 既存エンドポイント無変更: /health・/readyz・/health/collector のハンドラ本体に差分なし(ルート追加と import のみ) | `git diff base-commit..HEAD -- services/meeting-api/meeting_api/main.py services/runtime-api/runtime_api/main.py` のハンクレビュー(Fable) | diff + レビュー verdict |
| FP-006 | runtime-api 認証の弱化なし: skip list への追加が `/metrics` のみ(既存 skip 対象の変更・認証ロジックの変更なし) | 同上ハンクレビュー | diff + レビュー verdict |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | /metrics にバックグラウンド収集タスク・キャッシュ・新規スレッドを作らない(スクレイプ時オンデマンドの DB/Redis 読みのみ) | metrics.py ソースレビュー(asyncio.create_task / Thread / 定期実行がない) | レビュー verdict |
| NFT-002 | DB 集計はすべて窓付きまたは既存 sweep と同型の条件で、全件スキャンの新設がない(meetings 24h 窓は created_at index、backlog は sweeps.py:725 と同型) | SQL ソースレビュー | レビュー verdict |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p13-metrics-endpoints/` へ。`.hw/plans/` に後 commit しない)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

- Prometheus text format 0.0.4 は安定仕様(数年来不変)であり外部 API 依存なし。
  prometheus_client 不採用のため、ライブラリ最新動向への依存もない。
