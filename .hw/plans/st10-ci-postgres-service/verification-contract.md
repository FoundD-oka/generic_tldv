# Verification Contract — st10-ci-postgres-service

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | 素の pytest では統合テストが決定的に skip される(env 未設定・postgres 不在で exit 0) | integration: `RUN_POSTGRES_INTEGRATION_TESTS` を unset した環境で `pytest services/meeting-api/tests/integration/test_transcription_dictionary_postgres.py -v` | 出力に `1 skipped` と skip 理由、exit code 0 |
| AT-002 | opt-in 時は実 postgres でテストが従来どおり合格する(run script 経路が壊れていない) | integration: `bash services/meeting-api/tests/run_transcription_dictionary_postgres.sh`(要 docker。venv 劣化時は `PYTHON_BIN` で有効な python3.11 を指定) | 出力に `1 passed`、exit code 0。docker がローカルで使えない場合のみ AT-004 の CI ログで代替可(その旨を記録) |
| AT-003 | workflow が構文的に妥当で、必須要素(postgres service / migration ステップ / `DB_SSL_MODE: disable` / `grep -E "1 passed"` ガード)を含む | unit: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/test-meeting-api.yml'))"` + 各要素の grep(actionlint があれば併用) | コマンド出力(エラーなし + 各 grep のヒット行) |
| AT-004 | PR の Test Meeting API workflow が green で、統合テストが実走している(skip でない) | CI: PR 上の workflow 実行結果 | "Run postgres integration tests" ステップのログに `test_real_postgres_advisory_lock_enforces_200_term_cap` の PASSED と `1 passed`。マージ前に確認 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 既存 "Run unit tests" ステップのコマンド・env が無変更(`--ignore=…test_integration_live.py` 維持)で、既存テストの結果に影響しない | source check + CI | workflow diff で当該ステップに変更行がないこと + CI unit ステップ green |
| FP-002 | 差分が3ファイル(workflow / 統合テスト / run script)+ .hw/plans/st10-ci-postgres-service/ に収まり、st9 の pyproject.toml・実装コードに触れない | `git diff --stat 5cae3a0..HEAD` | diff --stat の出力が当該ファイルのみ |
| FP-003 | サイレント skip が CI で green にならない(ガードの実在) | source check: workflow の統合テストステップに `set -o pipefail` と `grep -E "1 passed"` が存在 | 該当行の提示 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | `.hw/verify.sh` が新規回帰なしで通る(commit 済み clean tree で実行) | `bash .hw/verify.sh` | 末尾の ok 行(新規回帰なし) |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: yes
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-001 | GitHub Actions の `services:` + health-check 構文(2026-08 時点の標準機能。廃止予兆なし) | AT-004 の CI 実走そのものが鮮度検証を兼ねる | CI green |
