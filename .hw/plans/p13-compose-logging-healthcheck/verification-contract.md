# Verification Contract — p13-compose-logging-healthcheck

対象: `base-commit..HEAD` の差分(base-commit はコーディネータが着手時に
plan.md frontmatter へ書き込んだ値)。検証は commit 済み clean tree に対して実行する。

## ベースライン取得手順(FP-004 の前提。転記値の使用禁止)

1. 実装着手前に base-commit をチェックアウトした clean tree で mcp テストを実行:
   `python3.11 fresh venv`(`~/.cache/hw-venvs/p13-compose-logging-healthcheck` 等
   /tmp 以外)に pytest + fastapi + httpx を入れ、
   `python -m pytest services/mcp/tests` を実行(fastapi_mcp は既存テストが stub 済み)。
2. サマリ全文を `.hw/gates/p13-compose-logging-healthcheck/pytest-baseline-<commit>.txt`
   に保存する。**この実測値が本契約のベースライン**。転記値は無効。
3. 併せて base-commit で
   `docker compose -f deploy/compose/docker-compose.yml config --quiet` が通ることを
   確認し記録する(compose 構文の事前状態)。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | **ログローテーション全数付与(ST-19 の本質要求)**: 全プロファイル展開時の全サービスが `logging: json-file + max-size + max-file` を持つ | `python3 deploy/compose/scripts/verify_observability_config.py` が exit 0(検査1) | スクリプト出力を `.hw/gates/p13-compose-logging-healthcheck/` に保存 |
| AT-002 | **意味のある healthcheck 全数付与(ST-20 の本質要求)**: 除外リスト(minio-init / wake-orchestrator / [curl 非搭載確認時のみ minio])以外の全サービスに `healthcheck.test` があり、かつ meeting-api の test に `readyz` を含む(検査2・3) | 同上スクリプト exit 0 | スクリプト出力 |
| AT-003 | compose の構文・展開が全プロファイルで妥当: `docker compose -f deploy/compose/docker-compose.yml --profile kabosu --profile tts --profile wake-stt --profile wake --profile voiceprint --profile calendar config --quiet` が exit 0 | コマンド実行 | 実行ログ |
| AT-004 | mcp /health の実在: GET /health ルートが app に登録されている | `python -m pytest services/mcp/tests -v`(新設 test_health.py を含む) | pytest ログ |
| AT-005 | healthcheck が実在 endpoint を指す: compose の各 healthcheck URL(ポート・パス)がコード上の実在エンドポイントと一致する(meeting-api:8080/readyz、runtime-api:8090/health、dashboard:3000/api/health、mcp:18888/health、tts:8002/health、wake-stt:8058/health、calendar:8050/health) | Fable レビュー(diff とコードの突合)+ スクリプトの test 文字列検査 | レビュー verdict + スクリプト出力 |
| AT-006 | minio の扱いが確定している: curl 搭載を実測確認して healthcheck を付けたか、非搭載の証拠(`docker run --rm --entrypoint sh minio/minio:latest -c 'command -v curl'` の出力)を残して見送り+除外リスト記載か、いずれか | 実測コマンド出力 | `.hw/gates/` に保存 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | healthcheck / logging 以外の compose 差分ゼロ: depends_on・restart・mem_limit・environment・volumes・ports が無変更 | base-commit と HEAD の両方で `docker compose config --format json` を取り、healthcheck / logging キーを除去した JSON が一致することを確認(スクリプトまたは jq) | 比較出力 |
| FP-002 | 既存 healthcheck(redis / postgres / admin-api / api-gateway / voiceprint)の test 内容が不変(検査4) | verify スクリプト exit 0 | スクリプト出力 |
| FP-003 | 変更ファイルが `deploy/compose/docker-compose.yml`・`deploy/compose/scripts/verify_observability_config.py`(新規)・`services/mcp/main.py`・`services/mcp/tests/` のみ | `git diff --name-only base-commit..HEAD` | diff 出力 |
| FP-004 | mcp 既存テスト: ベースラインに対し新規 fail 0・passed 増は新設テスト分のみ | 同一 venv・同一コマンドでサマリ比較 | 両サマリ全文 |
| FP-005 | mcp の変更が /health 追加のみ(既存ルート・fastapi_mcp 配線・auth に差分なし) | `git diff base-commit..HEAD -- services/mcp/main.py` のハンクレビュー(Fable) | diff + レビュー verdict |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | healthcheck コマンドが各 image 内で実在するツールのみを使う(python:slim → python urllib、node:alpine → busybox wget。curl を slim/alpine 系に対して使っていない) | compose diff のレビュー | レビュー verdict |
| NFT-002 | verify スクリプトが docker daemon 以外の外部依存を持たない(pip 依存なし、stdlib のみ。CI へそのまま載せられる) | スクリプトのソースレビュー | レビュー verdict |
| NFT-003 | 除外リスト(スクリプト内定数)に除外理由コメントがあり、compose 側にも対象外サービスへの理由コメントがある | ソースレビュー | レビュー verdict |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p13-compose-logging-healthcheck/` へ)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-001 | minio/minio:latest に curl が搭載されているか(リリースにより変動歴あり) | 実装時に `docker run --rm --entrypoint sh minio/minio:latest -c 'command -v curl'` で実測(AT-006 と同一) | `.hw/gates/` の実測出力 |
