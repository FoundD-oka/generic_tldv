---
generated_by: fable
task_id: p24-docker-resource-guard
base-commit: d5c324f7e75a8662f743de087a08242e33716828
created: 2026-08-24
---

# p24-docker-resource-guard 実装プラン

## 依頼の文字通りの内容と再設計後のゴール

- 文字通り: 「dashboard の誤った unhealthy 表示を直す」「Docker がメモリ・容量を無駄に圧迫していないかチェックする機能を持たせる」
- 再設計後のゴール(reframe は最小限。合意済み方針はそのまま採用):
  1. compose healthcheck の `localhost` → `127.0.0.1` 修正により dashboard / kabosu-dashboard の偽 unhealthy を解消し、既存静的検証(`verify_observability_config.py`)で回帰を恒久的に防ぐ。
  2. **読み取り専用**の Docker 資源監査 CLI を新設する。実使用メモリと limit を区別し、reclaimable を「削除候補の目安」として提示する(自動削除・prune は実装しない)。root Makefile から明示実行でき、日本語サマリと機械可読 JSON の両方を出す。fail-closed。

## 変更ファイル(このリスト外は変更禁止)

| ファイル | 変更内容 |
|---|---|
| `deploy/compose/docker-compose.yml` | dashboard / kabosu-dashboard の healthcheck URL 2箇所を `http://127.0.0.1:3000/api/health` へ |
| `deploy/compose/scripts/verify_observability_config.py` | `REQUIRED_TEST_FRAGMENTS` の該当2件を `127.0.0.1` へ更新(compose 側と同時変更しないと検証が落ちる構造を回帰防止として利用) |
| `deploy/compose/scripts/docker_resource_audit.py` | **新規**。監査 CLI 本体(Python stdlib のみ) |
| `deploy/compose/scripts/tests/test_docker_resource_audit.py` | **新規**。unit test(stdlib `unittest`、fake runner + fixture) |
| `deploy/compose/scripts/tests/fixtures/` | **新規**。docker CLI 出力の canned fixture(正常系/異常系) |
| `Makefile`(root) | `docker-audit` ターゲット追加。`DOCKER_AUDIT_ARGS` で引数を透過 |
| `.hw/verify.sh` | 監査 unit test の実行を追加(`python3 -m unittest discover -s deploy/compose/scripts/tests`) |

**allowlist 判定からの明示除外**: `.hw/plans/p24-docker-resource-guard/**` は hw ハーネス必須成果物であり、製品・設定・テストの allowlist 判定対象外とする。それ以外の `.hw/**` は `.hw/verify.sh` だけ許可する。

**禁止事項**: `.env`・本番デプロイ資材の変更、destructive な docker サブコマンド(prune / rm / rmi / volume rm 等)の実装・実行、`.github/workflows/` の変更(既存 `verify-compose-config.yml` がフラグメント更新を自動でカバーするため不要)。

## Part 1: healthcheck 修正(how)

1. `deploy/compose/docker-compose.yml` 内の `wget -qO- http://localhost:3000/api/health` を 2 サービス分 `http://127.0.0.1:3000/api/health` に置換。
2. `verify_observability_config.py` の `REQUIRED_TEST_FRAGMENTS` の対応 2 エントリを同一文字列に更新。
3. `python3 deploy/compose/scripts/verify_observability_config.py` が exit 0 になることを確認。
4. リポジトリ内の compose healthcheck に `localhost:3000/api/health` が残っていないことを grep で確認(`deploy/compose/` 配下のみが対象。他ディレクトリの同名文字列はスコープ外)。

## Part 2: 資源監査 CLI(how)

### CLI 仕様

- 実行: `python3 deploy/compose/scripts/docker_resource_audit.py [flags]` / `make docker-audit DOCKER_AUDIT_ARGS="[flags]"`
- 依存: Python 3.11+ stdlib のみ。docker CLI 呼び出しは `subprocess.run` 経由。
- **読み取り専用コマンド allowlist**(これ以外の docker サブコマンドをランナーに渡したらテストで落とす):
  - `docker system df --format {{json .}}`
  - `docker stats --no-stream --format {{json .}}`
  - `docker ps -q`
  - `docker inspect --format {{.Name}}\t{{.HostConfig.Memory}} <ids...>`
- docker 呼び出し時は `env` に `LC_ALL=C`, `LANG=C` を明示設定(locale 差の排除)。
- 出力:
  - デフォルト: 人間向けの簡潔な日本語サマリ(stdout)
  - `--json`: JSON のみを stdout に出力(サマリは出さない)
- reclaimable 表示の文言規律: 「削除候補の目安。削除可否は再取得コスト等を確認のうえ各自判断」とする。**「安全に削除可能」という断定表現は使用禁止**(テストで文言不在を検査)。cleanup コマンドの提案は `docker system prune` の名称提示までに留め、実行はしない。

### 終了コード(fail-closed)

| code | 意味 |
|---|---|
| 0 | ok(全チェック合格) |
| 1 | warn(WARN が1件以上、CRITICAL なし) |
| 2 | critical(CRITICAL が1件以上) |
| 3 | error(daemon 不在 / CLI 非ゼロ終了 / JSON 破損 / 未知の単位 / 引数不正)。正常扱いしない |

argparse のデフォルト exit(2) は critical と衝突するため、`ArgumentParser.error` をオーバーライドして 3 で終了させる。

### JSON schema(v1、キーは原語)

```json
{
  "schema_version": 1,
  "generated_at": "<ISO8601 UTC>",
  "status": "ok|warn|critical|error",
  "exit_code": 0,
  "checks": [
    {
      "id": "build_cache|reclaimable_images|reclaimable_volumes|host_disk|container_memory|unlimited_memory",
      "status": "ok|warn|critical|info|error",
      "summary_ja": "<日本語1行>",
      "metrics": {},
      "thresholds": {}
    }
  ],
  "notes_ja": []
}
```

- `status` は checks の最悪値(error > critical > warn > ok。info は集計に影響しない)。
- `container_memory` の `metrics` には `containers: [{name, usage_bytes, limit_bytes, percent}]` を入れる。
- `unlimited_memory` は `HostConfig.Memory == 0` のコンテナ名リスト。**severity は常に info**(未制限=無駄と断定しない。docker stats の limit 表示はホスト総量になるため limit 判定には使わず、inspect の値を正とする)。

### 閾値(デフォルト、CLI で上書き可)

| チェック | warn | critical | フラグ |
|---|---|---|---|
| build cache 総量 | 20 GB | 40 GB | `--build-cache-warn-gb` / `--build-cache-crit-gb` |
| images reclaimable | 20 GB | 40 GB | `--images-reclaimable-warn-gb` / `--images-reclaimable-crit-gb` |
| volumes reclaimable | 5 GB | 20 GB | `--volumes-reclaimable-warn-gb` / `--volumes-reclaimable-crit-gb` |
| ホストディスク使用率(`shutil.disk_usage("/")`) | 80% | 90% | `--disk-warn-percent` / `--disk-crit-percent` |
| limit 有りコンテナの mem 使用率(usage/inspect limit) | 85% | 95% | `--mem-warn-percent` / `--mem-crit-percent` |

判定は `値 >= 閾値` で発火。GB は SI(10^9 bytes)で統一。

### 単位変換

- `docker system df` / `docker stats` の値は文字列(例 `"82.03GB"`, `"683MiB / 1.5GiB"`)。パーサは SI(`B, kB, KB, MB, GB, TB` = 10^3 系)と binary(`KiB, MiB, GiB, TiB` = 2^10 系)の両方を bytes に正規化する。未知の単位・parse 不能は例外 → exit 3。
- `docker stats` の MemUsage は「usage / limit」形式だが limit 側は使わない(前述のとおり inspect を正とする)。usage 側のみ採用。

### エッジケースの扱い

- **daemon 不在 / docker コマンド不在**: `FileNotFoundError` または非ゼロ rc → `status: "error"`, exit 3。
- **稼働コンテナ 0 件**: `docker ps -q` 空 → container 系チェックは「対象コンテナなし」の ok/info。df 系チェックは通常どおり評価。exit 0 になり得る(エラーではない)。
- **df の一部行欠損 / JSON 行破損**: exit 3(部分成功を ok と偽らない)。

### テスト設計

- 監査ロジックは `run_audit(runner, args) -> AuditResult` の形で subprocess 注入可能にし、`FakeRunner`(fixture 文字列を返す/例外を投げる)で検証。ライブ daemon を CI 前提にしない。
- fixture: 事実確認済みの実測値(images 82.03GB / reclaimable 22.29GB、build cache 49.62GB 等)を正常系 fixture に採用。デフォルト閾値で critical(build cache 49.62 ≥ 40)になることを固定ケースとして検証。
- テストケース一覧は verification-contract.md の R4〜R9 に対応させる。

## 実装順序

1. healthcheck 修正(compose + fragments)→ `verify_observability_config.py` 通過確認 → commit
2. 監査スクリプト + テスト + fixture → unit test 通過 → commit
3. Makefile / `.hw/verify.sh` 配線 → `bash .hw/verify.sh` 通過 → commit
4. ライブ evidence を `.hw/gates/p24-docker-resource-guard/` へ採取(ローカルのみ)
5. `python3 .hw/fable_review.py p24-docker-resource-guard`

## リサーチ記録

- 仮説: docker CLI の `--format json` 出力(df / stats)は行単位 JSON で安定しており stdlib で parse 可能。確信度: 高(公式ドキュメント 2026-08-24 確認、Docker Desktop 29.5.3 で実測済み)。
- 覆る条件: Docker メジャーアップデートで JSON フィールド名や単位表記が変わった場合。その際も fail-closed 設計により exit 3 で検知される(誤って ok を返さない)。

## Why(実装者に渡さない)

- healthcheck の根因はコンテナ内 `localhost` が `[::1]` に解決され IPv6 で Connection refused になること。アプリは IPv4 の 3000 で listen しており `127.0.0.1` なら 200。fragments を同時更新するのは、compose 側だけ直して静的検証と乖離する事故と、将来 `localhost` に戻す退行の両方を CI(`verify-compose-config.yml`)で防ぐため。
- 監査を読み取り専用に限定するのは、build cache 49.62GB・reclaimable 22.29GB という実態に対し「自動 prune」が最短に見えるが、Docker Desktop 上の prune はデータ損失リスクがあり、hw ハーネスの権威序列上もエージェントに破壊的操作を持たせない方針のため。「未制限 limit ≠ 無駄」の区別は、transcription-worker(limit 7.75GiB / 実使用 675MiB)のような意図的な余裕を誤検知しないため。
- 閾値デフォルトは現状実測(build cache 49.62GB)が critical に落ちる水準に置き、導入直後から実際の圧迫を可視化する意図。inspect を limit の正とするのは、docker stats が未制限時にホスト総量を limit 欄に出す仕様で、これを閾値判定に使うと未制限コンテナを誤って warn にするため。
- R軸 inline の理由: 全作業が1コンテキスト・1セッションに収まり、並行委譲も反復資産化も不要。Prime はサンドボックスでないため既定どおり inline に倒す。
