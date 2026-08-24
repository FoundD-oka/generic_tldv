# 検証契約: p24-docker-resource-guard

対象: `base-commit (d5c324f)..HEAD` の差分。CI が最終権威。エージェントの自己申告は証拠にならない。
各要求は「最低合格ライン」を満たせば完了。超過した作り込みはしない。

## 変更スコープ(R0)

- **合格ライン**: `git diff --name-only d5c324f7e75a8662f743de087a08242e33716828..HEAD` から `.hw/plans/p24-docker-resource-guard/**` を除外した残りが次のファイルだけで構成される:
  `deploy/compose/docker-compose.yml`, `deploy/compose/scripts/verify_observability_config.py`,
  `deploy/compose/scripts/docker_resource_audit.py`(新規), `deploy/compose/scripts/tests/`(新規),
  `Makefile`(root), `.hw/verify.sh`。`.env` および他のデプロイ資材の変更はゼロ。
- **除外規則**: `.hw/plans/p24-docker-resource-guard/**` は hw ハーネスの必須成果物であり、本判定の対象外。それ以外の `.hw/**` は `.hw/verify.sh` だけ許可する。
- **証拠**: 生の差分一覧と除外後の残余リストを `.hw/gates/p24-docker-resource-guard/scope.txt` に保存。

## R1: healthcheck 偽 unhealthy の修正

- **合格ライン**:
  - `deploy/compose/docker-compose.yml` の dashboard / kabosu-dashboard の healthcheck が `http://127.0.0.1:3000/api/health` を使用。
  - `deploy/compose/` 配下に `localhost:3000/api/health` が残存しない。
  - `verify_observability_config.py` の `REQUIRED_TEST_FRAGMENTS` が `127.0.0.1` 版に更新済みで、同スクリプトが exit 0。
- **証拠**:
  - `rg -n 'localhost:3000/api/health' deploy/compose/` → ヒット 0 件
  - `rg -n '127.0.0.1:3000/api/health' deploy/compose/docker-compose.yml` → 2 件
  - `python3 deploy/compose/scripts/verify_observability_config.py; echo $?` → 0
- **evidence gate(ローカル、CI 必須にしない)**: compose 再起動後
  `docker inspect --format '{{.State.Health.Status}}'` が両 dashboard コンテナで `healthy`。
  出力を `.hw/gates/p24-docker-resource-guard/healthcheck-live.txt` に保存。

## R2: 監査 CLI が読み取り専用であること

- **合格ライン**: スクリプトが発行する docker サブコマンドが allowlist
  (`system df`, `stats --no-stream`, `ps -q`, `inspect`)のみ。`prune` / `rm` / `rmi` / `volume rm` を含むコマンド文字列を組み立てるコードが存在しない。
- **証拠**:
  - unit test: FakeRunner が受け取った全コマンドを記録し、allowlist 外があれば fail。
  - `rg -n 'prune|docker rm|rmi' deploy/compose/scripts/docker_resource_audit.py` → 実行コードとしてのヒット 0 件(表示文言中の `docker system prune` 名称提示は可。テストは subprocess 引数側で判定)。

## R3: Make target

- **合格ライン**: root `Makefile` に `docker-audit` ターゲットが存在し、`DOCKER_AUDIT_ARGS` を監査スクリプトへ透過する。
- **証拠**: `make -n docker-audit DOCKER_AUDIT_ARGS=--json` の出力に `docker_resource_audit.py --json` が含まれる。

## R4: fail-closed(異常を正常扱いしない)

- **合格ライン**(すべて unit test、FakeRunner で再現):
  - docker コマンド不在(`FileNotFoundError`)→ exit 3, `status: "error"`
  - docker CLI 非ゼロ終了 → exit 3
  - JSON 行破損 → exit 3
  - 未知の単位文字列(例 `"12XB"`)→ exit 3
  - 不正な CLI 引数 → exit 3
- **証拠**: `python3 -m unittest discover -s deploy/compose/scripts/tests -v` の該当テスト green。

## R5: 出力形式

- **合格ライン**:
  - デフォルト実行: 日本語サマリを stdout に出力。
  - `--json`: schema_version=1 の JSON のみ stdout。`json.loads` 可能で、`schema_version` / `generated_at` / `status` / `exit_code` / `checks[].id` / `checks[].status` / `checks[].summary_ja` / `checks[].metrics` / `checks[].thresholds` / `notes_ja` を含む。
  - `status` と exit code の対応: ok=0 / warn=1 / critical=2 / error=3。info は集計に影響しない。
- **証拠**: fixture ベースの unit test で JSON を parse し、キー存在・status/exit code 対応表を検証。

## R6: 閾値のデフォルトと上書き

- **合格ライン**:
  - デフォルト: build cache 20/40 GB、images reclaimable 20/40 GB、volumes reclaimable 5/20 GB、disk 80/90 %、mem 85/95 %(`値 >= 閾値` で発火、GB は SI)。
  - 各閾値が CLI フラグで上書き可能。
  - 境界テスト: 閾値ちょうどで発火、閾値未満で非発火。
  - 実測 fixture(build cache 49.62GB)がデフォルト閾値で critical / exit 2 になる。
- **証拠**: unit test green(境界値ケースと実測 fixture ケースを含む)。

## R7: 未制限 limit を「無駄」と断定しない

- **合格ライン**:
  - `docker inspect` の `HostConfig.Memory == 0` を未制限と判定(stats の limit 欄は使用しない)。
  - 未制限コンテナは `unlimited_memory` チェックに **info** で列挙され、warn/critical・exit code に影響しない。
  - limit 有りコンテナのみ mem 使用率閾値の対象。
- **証拠**: 未制限+高使用コンテナを含む fixture で exit 0(他チェック ok 時)となる unit test。

## R8: エッジケースと文言規律

- **合格ライン**(unit test):
  - 稼働コンテナ 0 件 → container 系チェックは ok/info、exit は df 等の結果に従う(0 になり得る)。
  - 単位変換: SI(`kB/MB/GB/TB`=10^3系)と binary(`KiB/MiB/GiB/TiB`=2^10系)の混在 fixture が正しく bytes 換算される(例: `1.5GiB` = 1610612736)。
  - docker 呼び出しの `env` に `LC_ALL=C` と `LANG=C` が設定されている(FakeRunner で受領 env を検査)。
  - 日本語サマリおよび JSON の全文言に「安全に削除可能」という文字列が含まれない。reclaimable は「削除候補の目安」等の非断定表現。
- **証拠**: `python3 -m unittest discover -s deploy/compose/scripts/tests -v` green。

## R9: 依存とゲート配線

- **合格ライン**:
  - `docker_resource_audit.py` とテストが Python stdlib のみに依存(新規 pip 依存ゼロ)。
  - `.hw/verify.sh` が unit test を実行し、`bash .hw/verify.sh` が commit 済み clean tree で exit 0。
  - CI(既存 workflow)はライブ Docker daemon を前提としない(unit test は FakeRunner のみ)。
- **証拠**: `rg -n '^import|^from' deploy/compose/scripts/docker_resource_audit.py`(stdlib のみ)、`bash .hw/verify.sh; echo $?` → 0。

## evidence gate(ローカルのみ、CI 必須にしない)

- `make docker-audit DOCKER_AUDIT_ARGS=--json` のライブ実行結果と exit code を
  `.hw/gates/p24-docker-resource-guard/live-audit.json` に保存する。
  現行環境では build cache 49.62GB により exit 2(critical)が期待値。

## 完了条件

R0〜R9 の全証拠が commit 済み状態で再現可能であり、`bash .hw/hooks/pr-ready-gate.sh p24-docker-resource-guard` と Fable 契約レビューを通過すること。
