# Verification Contract — p15-compose-deps-limits

対象: `base-commit..HEAD` の差分(base-commit はコーディネータが着手時に plan.md
frontmatter へ書き込んだ値)。検証は commit 済み clean tree に対して実行する。

判定主体の凡例: **Fable** = base-commit..HEAD の差分と本契約のみで判定
(gitignore 領域の証跡には到達できない)/ **CI** = GitHub Actions(最終権威)/
**ゲート** = pr-ready-gate 実行者が `.hw/gates/p15-compose-deps-limits/` の証跡を確認。

## ベースライン取得手順(転記値の使用禁止)

1. 着手時の base-commit の clean tree で
   `python3 deploy/compose/scripts/verify_observability_config.py` を実行し exit 0 を確認、
   出力を `.hw/gates/p15-compose-deps-limits/verify-baseline-<commit>.txt` に保存する。
2. 同 tree で全プロファイル付き `docker compose ... config --format json` を取得し
   `.hw/gates/p15-compose-deps-limits/config-baseline-<commit>.json` に保存する
   (FP-101 の比較元)。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-101 | **ST-24**: 展開後 config で runtime-api に memory 制限(512MiB 相当)がある | 拡張後 `verify_observability_config.py` exit 0(検査5) | Fable(スクリプト差分+compose 差分)+ ゲート(実行出力) | スクリプト出力を `.hw/gates/` に保存 |
| AT-102 | mem_limit の値 512m に helm 由来(values.yaml runtimeApi limits.memory: 512Mi)のコメントが付く | compose 差分のレビュー | Fable | diff |
| AT-103 | **ST-25**: depends_on 条件が plan §2 の期待マップと完全一致(昇格8件+非昇格の据え置き) | 同スクリプト exit 0(検査6) | Fable + ゲート | スクリプト出力 |
| AT-104 | **構造デッドロックなし**: service_healthy 条件の依存先すべてに healthcheck が定義され、`restart: "no"` のサービスに healthy/completed 条件がない | 同スクリプト exit 0(検査7・8) | Fable + ゲート | スクリプト出力 |
| AT-105 | 昇格しない依存(meeting-api→runtime-api、api-gateway→meeting-api、*→api-gateway)に理由コメントがある | compose 差分のレビュー | Fable | diff |
| AT-106 | 全プロファイル展開が妥当: `docker compose -f deploy/compose/docker-compose.yml --profile kabosu --profile tts --profile wake-stt --profile wake --profile voiceprint --profile calendar config --quiet` が exit 0 | コマンド実行 | ゲート + CI(workflow 内で config 展開が走る) | 実行ログ |
| AT-107 | **検査の実効性(sabotage)**: 一時ファイルへ compose を複製し (a) runtime-api の mem_limit を除去 (b) meeting-api→redis を service_started へ戻す (c) redis の healthcheck を削除したまま service_healthy 依存を残す、の3変異それぞれでスクリプトが exit 1 になる | 変異ごとに実行し出力を保存(リポジトリ内ファイルは変更しない) | ゲート | 3変異の実行出力 |
| AT-108 | CI workflow が本 PR 上で実行され、verify スクリプトのステップが成功している | PR の checks 欄 | CI | PR の check run(URL を PR 本文に記載) |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-101 | depends_on と runtime-api mem_limit 以外の compose 差分ゼロ: healthcheck・logging・environment・volumes・ports・restart が無変更 | base と HEAD の `config --format json` から depends_on / mem_limit(および memory 表現)キーを除去した JSON の一致確認(jq またはスクリプト) | ゲート(比較出力)+ Fable(diff のハンクが該当キーのみか) | 比較出力 |
| FP-102 | #60 の既存検査1〜4(logging 全数 / healthcheck 全数 / meeting-api readyz / 既存 healthcheck 不変)が引き続き exit 0 | 拡張後スクリプトの実行(検査1〜4を削除・緩和していないこと) | Fable(スクリプト差分)+ CI | スクリプト出力 |
| FP-103 | wake-orchestrator の depends_on と restart: "no" が不変 | 検査8 + diff | Fable + ゲート | スクリプト出力 |
| FP-104 | **実装ファイル**(プロダクトコード・設定・workflow)が `deploy/compose/docker-compose.yml`・`deploy/compose/scripts/verify_observability_config.py`・`.github/workflows/verify-compose-config.yml` の3つのみ。`.hw/plans/` 配下のハーネス成果物(プラン・契約・decision・レビュー verdict)は本 FP の対象外(ハーネス規約上コミット必須のため)。**`.hw/plans/` 以外**の `.hw/`(hooks / rules / verify.sh 等)への差分は従来どおり violation | `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` の出力が上記3ファイルに一致 | Fable | diff |
| FP-105 | minio-init のスクリプト本文(entrypoint)が不変 | diff | Fable | diff |

## Non-Functional Checks

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| NFT-101 | verify スクリプトが引き続き stdlib + docker CLI のみに依存(pip 依存なし。CI で pip install 不要) | ソースレビュー + workflow に pip install ステップがないこと | Fable | diff |
| NFT-102 | workflow の `paths` に workflow ファイル自身が含まれる(P1-4 の盲点対策) | workflow のソースレビュー | Fable | diff |

## KPI Checks

なし。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p15-compose-deps-limits/` へ)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-101 | `docker compose config --format json` 展開後の mem_limit / depends_on 条件のキー表現(compose v2 のバージョンで正規化表現が変わり得る) | 実装時にローカルの compose バージョンで実測し、スクリプトは両表現(`mem_limit` / `deploy.resources.limits.memory`)を許容する形にする | スクリプト内コメント + `.hw/gates/` の実測出力 |

## 改訂履歴

- 2026-08-09 実装着手。base-commit = `b6abdd74a9b15960902545272d1ab408b6af5b84`
  (着手時 `git rev-parse HEAD` と plan.md frontmatter が一致、更新不要)。

### ベースライン実測(転記ではなく着手時に実行、証跡は `.hw/gates/p15-compose-deps-limits/`)

- `python3 deploy/compose/scripts/verify_observability_config.py` → exit 0。
  出力: `services checked: 16`、`healthcheck exclusions: minio-init, wake-orchestrator`
  (`verify-baseline-b6abdd7.txt`)。
- 全プロファイル `docker compose config --format json` → exit 0、32,976 bytes
  (`config-baseline-b6abdd7.json`)。ローカル実測環境: Docker Compose v5.1.4 / Python 3.9.6。

### RF-101 実測(`rf101-key-representation-b6abdd7.txt`)

- **mem_limit**: 展開後は**サービス直下の `mem_limit`** に**バイト数の文字列**として現れる。
  実測例 `redis: "536870912"`(=512m)、`minio: "1073741824"`(=1g)、
  `voiceprint-service: "1610612736"`(=1.5g)。`deploy` キーは全サービスで未出力(null)。
  → 本 compose v5.1.4 では `deploy.resources.limits.memory` 表現は出ないが、
  RF-101 に従いスクリプト `memory_limit_bytes()` は両表現+数値/文字列/単位付きを許容する。
- **depends_on**: 短縮リスト形・map 形とも
  `{"<dep>": {"condition": "service_started"|..., "required": true}}` に正規化される
  (短縮形は `service_started` に展開)。
- **restart**: `restart: "no"` は文字列 `"no"` のまま(wake-orchestrator)。
  restart 未指定のサービス(minio-init)は `null`。

### ベースライン時点の depends_on 条件(昇格前)

`minio-init→minio`、`runtime-api→redis,postgres`、`meeting-api→redis,postgres,runtime-api`、
`calendar-service→postgres,meeting-api`、`api-gateway→meeting-api,redis`、
`mcp/dashboard/kabosu-dashboard/wake-orchestrator→api-gateway` がすべて `service_started`。
`admin-api→postgres` と `api-gateway→admin-api` のみ既に `service_healthy`。
runtime-api には memory 制限なし(`mem_limit` キー自体が不在)。
- 2026-08-09 改訂(Fable planner、レビュー実施前): FP-104 の適用範囲を「実装ファイル」に
  明確化し、判定コマンドを `.hw/plans` 除外形へ変更。理由: ハーネス規約上、プラン・契約・
  decision・レビュー verdict は commit 必須であり、旧文言のままでは規約準拠の差分
  (`.hw/plans/p15-compose-deps-limits/` 一式、親プラン `p15-deploy-hardening/`、
  後続プラン `p15-docker-events-timeout/`、および本改訂自身と今後の review-verdict.json)が
  機械的に violation になる。契約文言と運用実態の乖離の是正であり、実装差分を上記3ファイルに
  閉じ込める合格ライン自体は不変(緩和なし。`.hw/plans/` 以外の `.hw/` は violation のまま)。
