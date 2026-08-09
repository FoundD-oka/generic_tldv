---
generated_by: fable
task_id: p15-compose-deps-limits
base-commit: b6abdd74a9b15960902545272d1ab408b6af5b84
size: M
parent: p15-deploy-hardening
---

# ST-24 / ST-25: compose の runtime-api mem_limit と depends_on 昇格(P1-5 第1弾)

base-commit はコーディネータが着手時に `git rev-parse HEAD` で更新すること。

## ゴール

- `deploy/compose/docker-compose.yml` の runtime-api に `mem_limit: 512m` が付く(ST-24)。
- インフラ依存(redis / postgres / minio)への depends_on が `service_healthy`
  (minio-init は `service_completed_successfully`)へ昇格し、昇格しないアプリ間依存には
  理由コメントが付く(ST-25)。
- 上記が `verify_observability_config.py` の拡張検査と新規 CI workflow で恒久的に
  機械検証される。

## 現状(現物確認済み、行番号は base b6abdd7 時点)

- runtime-api(compose:143-207): mem_limit なし。healthcheck は #60 で付与済み。
  helm 側は `limits.memory: 512Mi` 整備済み(values.yaml:180-186)→ helm は触らない。
- depends_on の現状: minio-init→minio(短縮形)、runtime-api→redis,postgres(短縮形)、
  meeting-api→redis,postgres,runtime-api(短縮形)、api-gateway→admin-api(healthy),
  meeting-api(started),redis(started)、mcp/dashboard/kabosu-dashboard/wake-orchestrator
  →api-gateway(短縮形)、calendar-service→postgres,meeting-api(短縮形)。
- 各 healthcheck 実測定義(#60): postgres interval 5s/retries 5、redis 10s/3、
  minio start_period 10s/interval 30s、runtime-api start_period 10s/interval 30s、
  meeting-api /readyz start_period 60s、api-gateway start_period 10s/interval 30s。
- wake-orchestrator は `restart: "no"`(依存条件失敗時に再試行されない)。
- minio-init のスクリプトは無条件 `exit 0` で終わる(mc 失敗でも成功扱い)。

## How

変更ファイルは `deploy/compose/docker-compose.yml`、
`deploy/compose/scripts/verify_observability_config.py`、
`.github/workflows/verify-compose-config.yml`(新規)の3つのみ。

### 1. runtime-api に mem_limit(ST-24)

```yaml
    # ST-24: value mirrors the load-tested helm shape (values.yaml
    # runtimeApi.resources.limits.memory: 512Mi). Do not raise without new
    # measurements — a silently raised cap hides memory regressions (#55).
    mem_limit: 512m
```

他サービスへの mem_limit 追加はしない(監査 ID は runtime-api のみ。postgres /
admin-api / wake-stt / calendar-service の未設定は advisory として handoff に記録)。

### 2. depends_on 昇格(ST-25)

昇格するもの(map 構文へ書き換え):

| サービス | 依存 | 条件 | 根拠(最悪起動遅延の見積り) |
|---|---|---|---|
| minio-init | minio | `service_healthy` | minio healthy まで最悪 ~40s(start_period 10s + interval 30s)。現状の `sleep 5` は残す(削除は差分を広げるだけ) |
| runtime-api | redis | `service_healthy` | redis healthy ~10s。startup 直後に `redis.ping()` するため started では crash loop になり得る |
| runtime-api | postgres | `service_healthy` | postgres healthy ~10s(5s×5 retries) |
| meeting-api | redis | `service_healthy` | startup の Redis bounded retry(最悪~60s)を条件側で吸収 |
| meeting-api | postgres | `service_healthy` | 同上 |
| meeting-api | minio-init | `service_completed_successfully` | 初回デプロイでバケット未作成のまま録音アップロードが走るレースを閉じる。minio-init は無条件 exit 0 のため条件が満たされないリスクは実質ゼロ(遅延 ~8s) |
| api-gateway | redis | `service_started`→`service_healthy` | rate limiter が Redis 依存。healthy ~10s |
| calendar-service | postgres | `service_healthy` | healthy ~10s |

昇格しないもの(**compose にコメントで理由を残す**。文言は要旨が同じなら調整可):

- meeting-api → runtime-api: `service_started` 維持。理由: runtime-api の /health は
  Redis 依存を含み、bot サブシステム単独の故障で meeting-api(文字起こし閲覧・API)まで
  起動不能になるのは増幅。起動直後の bot dispatch レースは #46 の3回指数バックオフが吸収。
- api-gateway → meeting-api: `service_started` 維持。理由: /readyz は正当に最大~90s
  かかり得る。gateway は meeting-api 未 ready の間 502 を返すだけで、他ルート
  (admin-api 等)は先に使える方がよい。
- mcp / dashboard / kabosu-dashboard / wake-orchestrator → api-gateway: 現状維持。
  特に wake-orchestrator は `restart: "no"` のため、条件失敗時に永久に起動しない
  副作用があり昇格禁止。
- コメントに「never-healthy の依存先に条件を張ると compose 全体が起動しない。
  昇格先はインフラ純正 healthcheck に限る」の設計原則を1箇所(depends_on を最初に
  変更するサービス付近か冒頭)残す。

最悪起動時間の合計見積り(正常系): meeting-api の起動開始が
max(redis ~10s, postgres ~10s, minio ~40s + init ~8s) ≈ 最悪 ~50s 遅延。
デッドロックなし(昇格先はすべて葉のインフラサービスで循環なし)。

### 3. verify_observability_config.py の拡張(既存検査1〜4は不変)

追加検査(展開後 config JSON に対して):

5. runtime-api に memory 制限がある(`docker compose config --format json` の展開後
   キーを実装時に確認: `mem_limit`(バイト数値化され得る)または
   `deploy.resources.limits.memory` のいずれの表現でも通るように書く)。
6. depends_on 条件の期待マップ一致: 上表の昇格8件+非昇格の据え置き
   (meeting-api→runtime-api = service_started 等)をスクリプト内定数
   `EXPECTED_DEPENDS` に持ち、展開後 config と完全一致を assert。
7. **構造デッドロックガード**: 展開後 config 全体を走査し、`service_healthy` 条件の
   依存先サービスが必ず healthcheck を定義している(かつ `disable: true` でない)
   ことを assert。
8. `restart: "no"` のサービス(wake-orchestrator)に `service_healthy` /
   `service_completed_successfully` 条件が付いていないことを assert。

検査6〜8の定数には理由コメントを付ける(既存スクリプトの流儀に合わせる)。

### 4. CI workflow `.github/workflows/verify-compose-config.yml`(新規)

- `pull_request` の `paths`: `deploy/compose/**` と **workflow ファイル自身**
  (P1-4 で判明した既存4本の盲点を踏まない)。
- job: ubuntu-latest、checkout 後に
  `python3 deploy/compose/scripts/verify_observability_config.py` を実行するだけ
  (ubuntu-latest は docker compose v2 同梱。pip install 不要 = スクリプトは stdlib のみ)。
- 本タスクの PR は compose を変更するため、この workflow が PR 上で実行され green に
  なること自体がトリガー配線の証明になる。

### 5. 変更しないもの

- healthcheck・logging・environment・volumes・ports・restart(depends_on と
  runtime-api の mem_limit 以外の compose 差分ゼロ)。
- deploy/helm・deploy/lite・各 Dockerfile・サービスコード。
- minio-init のスクリプト本文(`sleep 5` 含む)。

## Why(実装者に渡さない)

親プラン `.hw/plans/p15-deploy-hardening/plan.md` の Why セクションを参照
(昇格対象をインフラに限定する理由、512m の由来、OOM 検知導線、advisory 一覧)。
