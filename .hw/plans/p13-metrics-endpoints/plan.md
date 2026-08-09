---
generated_by: fable
task_id: p13-metrics-endpoints
base-commit: 5559ff9e57e004805106e10b41da9451b1207923
size: M
---

# ST-18: meeting-api / runtime-api に /metrics(Prometheus text format)を追加する(P1-3 第1弾)

## ゴール

依頼の文字通りの内容: 「主要サービスに /metrics がない。観測したい指標は
参加成功率・文字起こし遅延・sweep滞留」(監査 ST-18)。

reframe(前提の修正): どの配備(compose / helm / lite)にも Prometheus / Grafana /
Alertmanager は存在しない(deploy/ 全域 grep 0件、現物確認済み)。したがって
「/metrics を作れば観測できる」は成立せず、スクレイプ基盤の新設は本タスクの
スコープ外(最小セットの範囲を超える)。達成すべき成果は:

- **運用者が `curl` 一発で3指標(参加成功率・文字起こし遅延・sweep滞留)と
  ワーカー生存カウンタを読める**(Prometheus text format は人間可読)。
- 将来 Prometheus を足すとき、サービス側は無変更でスクレイプできる
  (標準 text format 準拠を守る)。
- 露出手順を deploy/compose/README.md に記載する(書かなければ本当に
  「誰も見ないメトリクス」になる)。

対象サービスは meeting-api(3指標すべての算出主体)と runtime-api
(lifecycle.py:172 のコメントが /metrics 露出を予告済みのカウンタ)。
transcription-service は GPU ホスト別 compose のためスコープ外。

## 現状分析(現物確認済み)

- /metrics 実装は全域 0件(ST-18 は未解消・監査は正しい)。
- 露出先を待っている既存カウンタ:
  - `meeting_api/sweeps.py:66-71`: `sweep_iterations` / `sweep_last_iteration_at` /
    `final_transcription_worker_iterations` / `final_transcription_worker_last_iteration_at`(#54)
  - `runtime_api/lifecycle.py:175-177`: `idle_loop_iterations` / `idle_loop_last_iteration_at`
    (直上コメント「External observable: read these from a /metrics or /health endpoint」)
- 3指標の算出可否(すべて既存データから算出可能):
  - 参加成功率: `meetings.status`('completed'/'failed')+ `data->completion_reason`
    (Pack J / #51 で分類精度是正済み)。`created_at` は index 付きで窓クエリ可。
  - 文字起こし遅延: `data->final_transcription` に `queued_at` / `started_at` /
    `completed_at` / `failed_at`(ISO 文字列、final_transcription.py:195-211, 1618)。
    `completed_at - queued_at` で算出可。
  - sweep滞留: `sweeps.py:725-733` の SQL(status = queued / failed+retryable /
    running)がそのまま backlog クエリ。ワーカー staleness は last_iteration_at。
- meeting-api は uvicorn 単一プロセス起動(Dockerfile CMD、--workers なし)
  → モジュールグローバルカウンタの直接露出で正しい(multiprocess 問題なし)。
- 公開経路の安全性: meeting-api は compose で host 非公開(`expose: 8080` のみ)、
  api-gateway は明示ルートのみで catch-all proxy なし(main.py 確認)→ /metrics が
  外部へ漏れる経路はない。runtime-api は 127.0.0.1 bind。
- runtime-api の認証 middleware(main.py:149-150)は `/health` `/docs`
  `/openapi.json` のみ skip → `/metrics` の skip 追加が必要。

## 方式判断(依存追加の是非)

**prometheus_client は導入せず、Prometheus text format(0.0.4)を自前レンダリングする。**

- 露出するのは counter / gauge のみで、text format は数十行の純関数で書ける。
- スクレイパー不在の現状、prometheus_client の主価値(registry / multiprocess /
  histogram / exposition server)を使う場面がない。ゼロ依存で済むものに
  3配備すべてへ流れる依存を足さない。
- histogram が必要になった時点(Prometheus 導入時)での置き換えは、
  エンドポイント URL・metric 名を維持したまま内部実装の差し替えで済む。

## How

変更は `services/meeting-api/`(meeting_api/ 本体 + tests/)、
`services/runtime-api/`(runtime_api/ 本体 + tests/)、`deploy/compose/README.md` のみ。

### 1. 新モジュール `meeting_api/metrics.py`

- `render_prometheus_text(samples) -> str`: 純関数。各メトリクスに `# HELP` /
  `# TYPE`(counter|gauge)行 + `name{label="v"} value` 行を出す。ラベル値は
  `\` `"` 改行をエスケープ。末尾改行。数値は float/int をそのまま。
- DB 集計(raw SQL、sweeps.py の流儀に合わせる。すべて呼び出し時実行 =
  スクレイプ時オンデマンド。バックグラウンド収集は作らない):
  - `query_join_stats(db)`: 直近24h(`created_at >= now() - interval '24 hours'`)の
    meetings を status 別に count + `data #>> '{completion_reason}'` 別に count。
  - `query_final_transcription_stats(db)`:
    (a) backlog — sweeps.py:725-733 と同型の条件で status バケット
    (queued / running / failed_retryable)別 count(窓なし・全件)。
    (b) 直近24hの final_transcription 完了/失敗件数と、完了ジョブの
    `completed_at - queued_at` 秒の p50 / p95(行数は高々数百なので Python 側
    `statistics.quantiles` で可。SQL percentile_cont でも可、実装者選択)。
- カウンタ読み出しは**関数内で `from . import sweeps` して属性参照**
  (テストの monkeypatch を効かせるため。import 時束縛にしない)。
- collector lag: /health/collector(main.py:204-262)と同じ `xinfo_groups` 読みで
  group lag を gauge 化。Redis エラー時は -1。

### 2. metric 名(この名前が契約。実装で変えない)

| metric | type | 中身 |
|---|---|---|
| `meeting_api_sweep_iterations_total` | counter | sweeps.sweep_iterations |
| `meeting_api_sweep_last_iteration_timestamp_seconds` | gauge | sweep_last_iteration_at(unix秒) |
| `meeting_api_final_transcription_worker_iterations_total` | counter | 同名カウンタ |
| `meeting_api_final_transcription_worker_last_iteration_timestamp_seconds` | gauge | 同上 last_at |
| `meeting_api_collector_stream_lag` | gauge | transcription_segments group lag(エラー時 -1) |
| `meeting_api_meetings_24h{status="..."}` | gauge | 直近24h status 別会議数(参加成功率の分母分子) |
| `meeting_api_meetings_by_reason_24h{reason="..."}` | gauge | 直近24h completion_reason 別(join_failure 等の内訳) |
| `meeting_api_final_transcription_backlog{status="queued"\|"running"\|"failed_retryable"}` | gauge | sweep滞留 |
| `meeting_api_final_transcription_jobs_24h{result="completed"\|"failed"}` | gauge | 直近24h 完了/失敗件数 |
| `meeting_api_final_transcription_latency_seconds{quantile="0.5"\|"0.95"}` | gauge | queued_at→completed_at 秒 |
| `runtime_api_idle_loop_iterations_total` | counter | lifecycle.idle_loop_iterations |
| `runtime_api_idle_loop_last_iteration_timestamp_seconds` | gauge | 同上 last_at |
| `runtime_api_containers{status="..."}` | gauge | state の container 一覧を status 別 count |

成功「率」はゲージにしない(分母分子を出す。率の計算は読む側。窓は24h固定、
env ノブは足さない)。

### 3. meeting-api `GET /metrics`(main.py)

- 認証なし(既存 /health 系と同等。上記のとおり外部到達経路なし)。
- `Response(content=..., media_type="text/plain; version=0.0.4; charset=utf-8")`。
- **部分故障で 500 にしない**: DB 到達不可なら DB 由来 gauge を省略、Redis 不可なら
  lag=-1。プロセス内カウンタは常に返し、エンドポイント自体は 200
  (監視endpoint が監視対象より先に死なない)。

### 4. runtime-api `GET /metrics`(main.py)

- render は同型の小さな純関数をローカル実装(runtime-api は独立配布 package の
  ため meeting-api とのコード共有はしない)。
- `runtime_api_idle_loop_*` + `runtime_api_containers{status=...}`(state モジュールの
  既存一覧関数を使用。Redis エラー時は containers 系を省略し 200)。
- 認証 middleware の skip list(main.py:150)に `/metrics` を追加。

### 5. テスト

- `services/meeting-api/tests/test_metrics.py`(新設):
  - render 純関数: HELP/TYPE 行、ラベルエスケープ、counter/gauge 型注記。
  - TestClient で /metrics: DB クエリ関数を名前 patch(既存テストの流儀)して
    200 + 期待 metric 行。sweeps カウンタ値の反映(monkeypatch で値を仕込む)。
  - DB クエリ関数が raise しても 200 + プロセス内カウンタは出る。
  - Content-Type 検証。
- `services/runtime-api/tests/test_metrics.py`(新設):
  - fakeredis(既存 dev extras)で /metrics 200、認証ヘッダなしで通ること、
    idle_loop カウンタ行の存在。

### 6. deploy/compose/README.md へ観測手順の節を追加

- `docker compose exec meeting-api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/metrics').read().decode())"`
  相当の curl 手順と、3指標の読み方(どの metric が何か)を10行程度で記載。

### 7. 変更しないもの

- 既存 /health・/readyz・/health/collector、sweeps のカウンタ更新ロジック。
- compose / helm の scrape 配線・annotations(スクレイパー不在のため。Prometheus
  導入時の advisory として残す)。
- transcription-service・その他サービス。

## Why(実装者に渡さない)

- スクレイパー不在でも /metrics に価値がある根拠: (1) lifecycle.py:172 の設計意図
  (カウンタは既に「/metrics から読む」前提で置かれている)の完成、(2) text format は
  curl で人間可読なので「ログ掘りより速い一次切り分け」が今日から成立、(3) 障害時に
  sweep 停止・backlog 蓄積を1コマンドで確認できるのは P1-2 で入れたワーカー分離
  (#54)の検証手段にもなる、(4) 将来の Prometheus 導入が「サーバ足すだけ」になる。
  対案「ログベースに全振り」を退けたのは、率・滞留数・分位点はログ行から再構成する
  コストが高く、既に DB/カウンタに正規化された状態があるから。
- 率をゲージにしない理由: 窓・分母の定義論争をサービス側に持ち込まない。
  分母分子があれば curl 目視でも Prometheus 式でも自由に計算できる。
- DB クエリのスクレイプ時オンデマンド実行が安全な根拠: 同型クエリを sweeps が
  既に30秒ごとに回している(sweeps.py:725)。/metrics は curl 時のみ実行で
  既存負荷を下回る。
- 24h 窓固定・env なしの理由: #57 の教訓(ノブを増やすほど設定検証・文書・
  テストが増える)。最小合格ラインは固定窓で満たせる。
