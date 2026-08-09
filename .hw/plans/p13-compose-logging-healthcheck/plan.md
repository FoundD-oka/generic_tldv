---
generated_by: fable
task_id: p13-compose-logging-healthcheck
base-commit: c748fecb0ac1af8866515fece3769fa112b950bd
size: M
---

# ST-19 / ST-20: compose 全サービスにログローテーションと意味のある healthcheck を付与する(P1-3 第2弾)

## ゴール

依頼の文字通りの内容: 「ST-19 compose のログローテーションがない。ST-20 healthcheck が
全サービスに付いていない」。

reframe(ST-20 の精密化): 監査の「healthcheck なし」は部分的に古い。meeting-api /
runtime-api / admin-api / transcription-service は **Dockerfile の image レベル
HEALTHCHECK を持つ**(compose には現れないが有効)。ただし meeting-api のそれは
`/health`(常時200のliveness)を叩くため、**readyz 劣化(consumer 不在・起動未完了)を
検知できない**(#57 レビュー advisory と同根)。達成すべき成果は:

- compose 常駐全サービスのコンテナログが json-file の回転付きになり、ディスク枯渇で
  全滅するモードが消える(ST-19)。
- compose 常駐全サービスが**意味のある endpoint**(劣化を実際に検知できるもの)を叩く
  healthcheck を compose レベルで持ち、`docker ps` で劣化が見える(ST-20)。
  特に meeting-api は `/readyz` を叩く。
- 上記が**静的に機械検証できる**(`docker compose config` の展開結果を検査する
  スクリプトを commit し、将来 CI に組める形にする)。

lite(Dockerfile.lite に HEALTHCHECK あり、ログは docker run 側責務)と
helm(kubelet がログ管理、meeting-api probes は #48 で配線済み)はスコープ外。

## 現状分析(現物確認済み、deploy/compose/docker-compose.yml)

- `logging:` は全サービスで 0件(ST-19 は未解消・監査どおり)。
- compose レベル healthcheck あり: redis(redis-cli ping)、postgres(pg_isready)、
  admin-api(`/`)、api-gateway(`/`)、voiceprint-service(`/health`)。
- image レベル HEALTHCHECK あり(compose 記載なし): meeting-api(`/health`)、
  runtime-api(`/health`)。
- どちらも無し: minio、minio-init(one-shot)、mcp、dashboard、kabosu-dashboard、
  tts-service、wake-stt、wake-orchestrator、calendar-service。
- health endpoint の実在(コード確認済み): meeting-api `/health` `/readyz`
  `/health/collector`、runtime-api `/health`(main.py:150 で認証 skip 済み)、
  dashboard `/api/health`(常時 200 の設定検査 JSON、内部 fetch 5s×2 を含む)、
  tts-service `/health`、wake-stt `/health`、calendar-service `/health`。
  **mcp には health endpoint がない**(FastAPI、business ルートのみ)。
  **wake-orchestrator は HTTP サーバを持たない**(client ループのみ)。
- base image: python:3.11/3.12-slim(curl なし・python urllib は既存 healthcheck で
  使用実績あり)、dashboard は node:20-alpine(busybox wget あり)。

## How

変更は `deploy/compose/docker-compose.yml`、`deploy/compose/scripts/`(新規スクリプト)、
`services/mcp/main.py` + `services/mcp/tests/` のみ。

### 1. mcp に `/health` を追加(services/mcp/main.py)

- `@app.get("/health")` → `{"status": "ok"}`(依存なしの3行)。
- `services/mcp/tests/test_health.py`(新設): 既存 test_parse_meeting_url.py の流儀
  (fastapi_mcp を stub して main を import)で、`app.routes` に GET /health が
  登録されていることを assert(TestClient 起動は不要。route 存在検査で足りる)。

### 2. compose: ログローテーション(ST-19)

- ファイル先頭に extension field を定義:
  ```yaml
  x-default-logging: &default-logging
    driver: json-file
    options:
      max-size: "10m"
      max-file: "3"
  ```
- **定義済み全サービス**(minio-init・プロファイル付き含む)に
  `logging: *default-logging` を付与。

### 3. compose: healthcheck(ST-20)

追加・是正(interval 30s / timeout 5s / retries 3 を基本。例外は明記):

| サービス | test | 備考 |
|---|---|---|
| meeting-api | python urllib で `http://localhost:8080/readyz` | **是正の本体**。urlopen は 503 で HTTPError → 非0 exit。start_period 60s(startup の Redis bounded retry 最悪 ~60s を考慮) |
| runtime-api | python urllib で `http://localhost:8090/health` | image HEALTHCHECK を compose に明示(一覧性と静的検証のため) |
| dashboard / kabosu-dashboard | `wget -qO- http://localhost:3000/api/health` | busybox wget。timeout 15s(内部 fetch 5s×2 を考慮)、start_period 30s |
| mcp | python urllib で `http://localhost:18888/health` | 手順1が前提 |
| tts-service | `http://localhost:8002/health` | 実装時に image 内の到達手段(python/curl)を確認して選ぶ |
| wake-stt | `http://localhost:8058/health` | 同上 |
| calendar-service | python urllib で `http://localhost:8050/health` | |
| minio | `curl -f http://localhost:9000/minio/health/live` | **実装時に必ず確認**: `docker run --rm --entrypoint sh minio/minio:latest -c 'command -v curl'`。curl が無ければ healthcheck は見送り、advisory として記録 |

対象外(理由を compose コメントに残す): minio-init(one-shot)、
wake-orchestrator(HTTP サーバなし)。既存 healthcheck(redis / postgres /
admin-api / api-gateway / voiceprint)は変更しない。

**depends_on の条件は一切変更しない**(service_healthy への昇格は ST-25 / P1-5 の
スコープ。healthcheck 追加だけでは既存の起動順序・再起動挙動は変わらない)。

### 4. 静的検証スクリプト `deploy/compose/scripts/verify_observability_config.py`(新規・commit する)

- `docker compose -f deploy/compose/docker-compose.yml --profile kabosu --profile tts
  --profile wake-stt --profile wake --profile voiceprint --profile calendar config
  --format json` を subprocess 実行(IMAGE_TAG 等の必須 env はスクリプト内で
  ダミー値を注入)し、以下を assert して exit 0/1:
  1. 展開された**全サービス**に `logging.driver == "json-file"` かつ
     `max-size` / `max-file` オプションがある。
  2. 除外リスト(minio-init、wake-orchestrator、および minio を curl 非搭載で
     見送った場合のみ minio)以外の全サービスに `healthcheck.test` がある。
  3. meeting-api の healthcheck test 文字列に `readyz` を含む(`/health` への
     退行を機械的に禁止)。
  4. 既存 healthcheck の test が変わっていないこと(redis=redis-cli ping、
     postgres=pg_isready を含む)。
- 除外リストはスクリプト冒頭の定数とし、理由コメントを付ける。

### 5. 変更しないもの

- depends_on・restart・mem_limit・環境変数(healthcheck / logging 以外の compose 差分ゼロ)。
- deploy/lite・deploy/helm・各サービスの Dockerfile(image HEALTHCHECK は据え置き。
  compose レベルが優先されるため衝突しない)。
- vexa-bot コンテナ(runtime-api が docker run で起動する動的コンテナ)のログ設定は
  compose 管理外。advisory として記録(P1-5 か別タスクで runtime-api の
  LogConfig 注入を検討)。

## Why(実装者に渡さない)

- compose レベルで healthcheck を明示する理由(image 継承に任せない): 監査・検証の
  一覧性。verify スクリプトが compose config だけで全数検査でき、Dockerfile ごとの
  確認が不要になる。image レベルと二重定義になるサービスは compose 側が優先される
  仕様なので衝突しない。
- meeting-api /readyz を選ぶ理由: docker compose は unhealthy でも自動再起動しない
  ため、compose healthcheck の価値は「可視化」と「depends_on: service_healthy の
  判定材料」。可視化なら常時200の /health は無価値で、consumer 不在=機能不全を映す
  /readyz が唯一意味を持つ。/health/collector(lag 検知)は再起動を意図した
  liveness 設計(K8s 用)であり、再起動しない compose では unhealthy 表示が
  誤解を招くため readyz に留める。
- depends_on を触らない理由: service_healthy への昇格は起動ブロックの副作用があり
  (healthcheck の誤設定が全スタック起動不能に化ける)、影響分析が別途必要。
  ST-25 として P1-5 に割当済み。本タスクで healthcheck を全数化しておくことが
  その前提整備になる。
- 実起動スモークを契約の最低ラインにしない理由: full stack build は GPU 系・
  ビルド時間の制約でローカル検証が重く、フレークが契約を汚す。compose config の
  展開結果検査+endpoint 実在のコード確認(mcp はテスト)で「設定として正しい」を
  固定し、実起動は次回デプロイの運用確認(handoff に記載)へ回す。
