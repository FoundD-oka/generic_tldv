---
generated_by: fable
task_id: st10-ci-postgres-service
base-commit: 5cae3a05550e8679b156e88652b0dfa2193d30f1
size: M
runtime: inline
---

# st10: CI に postgres service を追加し、辞書統合テストを opt-in ゲート化する

## ゴール

- 文字通りの依頼: `.github/workflows/test-meeting-api.yml` に postgres service が
  無く常時 fail する統合テストへの対処方針を決めて実装する。
- 本当の成果(reframe): 「CI を完全 green にする」と「advisory lock の実DB検証を
  失わない」を両立する。CI では postgres service + migration の上でテストを必ず
  実走させ、ローカルの素の `pytest tests/` では決定的に skip させる。
  skip のみの案は検証を CI から消すため不採用(fail-closed)。

## 方針(確定)

両方採用:
1. workflow に postgres:16-alpine service を追加し、migration 適用後に統合テストを
   実走する。
2. テストに opt-in ゲート `RUN_POSTGRES_INTEGRATION_TESTS=1` を追加する。
   未設定時は skip。CI と run script は変数を設定するので skip されない。

## 変更対象(3ファイルのみ。st9 の pyproject.toml には触れない)

### 1. services/meeting-api/tests/integration/test_transcription_dictionary_postgres.py

import 群の直後にモジュールレベルで追加:

    pytestmark = pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_INTEGRATION_TESTS") != "1",
        reason=(
            "実PostgreSQLが必要。RUN_POSTGRES_INTEGRATION_TESTS=1 で有効化"
            "(tests/run_transcription_dictionary_postgres.sh 経由が正規手順)"
        ),
    )

os は既に import 済み(2行目)。テスト本体・アサーションは変更しない。

### 2. services/meeting-api/tests/run_transcription_dictionary_postgres.sh

23行目の `export DB_HOST=...` 行の付近に追加:

    export RUN_POSTGRES_INTEGRATION_TESTS=1

他は変更しない。

### 3. .github/workflows/test-meeting-api.yml

test ジョブに service を追加:

    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: test_user
          POSTGRES_PASSWORD: test_pass
          POSTGRES_DB: test_db
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U test_user -d test_db"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 20

Install dependencies ステップに `psycopg2-binary` を追加
(`pip install pytest pytest-asyncio httpx psycopg2-binary`)。

既存の "Run unit tests" ステップはコマンド・env とも一切変更しない
(env 未設定なので統合テストは skip 表示になるだけ)。

その後に2ステップ追加:

    - name: Apply transcription dictionary migration
      env:
        DATABASE_URL: postgresql://test_user:test_pass@127.0.0.1:5432/test_db
      run: python scripts/migrations/20260712_add_transcription_dictionary.py up

    - name: Run postgres integration tests
      env:
        RUN_POSTGRES_INTEGRATION_TESTS: "1"
        DB_HOST: 127.0.0.1
        DB_PORT: "5432"
        DB_NAME: test_db
        DB_USER: test_user
        DB_PASSWORD: test_pass
        DB_SSL_MODE: disable
      run: |
        set -o pipefail
        pytest services/meeting-api/tests/integration/test_transcription_dictionary_postgres.py -v | tee itest.log
        grep -E "1 passed" itest.log

注意点:
- `DB_SSL_MODE: disable` は必須。database.py は既定 "prefer" で SSLContext を
  asyncpg に渡すため、SSL 無効の postgres コンテナに接続できない。
- `grep "1 passed"` はサイレント skip を fail に変えるガード。削らないこと。
- migration ステップはリポジトリルート(既定 workdir)で実行する。

## 実装手順

1. main(base_commit)から branch `hw/st10-ci-postgres-service` を切る。
2. 上記3ファイルを変更し commit。
3. ローカル検証(検証契約 AT-001/AT-002/AT-003)→ `bash .hw/hooks/pr-ready-gate.sh st10-ci-postgres-service`。
4. push・PR 作成後、AT-004(CI 実走)を確認してからマージ。
   注意: workflow ファイル変更を含む push は token の workflow スコープ不足で
   拒否される場合がある(既知リスク。発生したら人間へ報告して止まる)。

## Why(実装者に渡さない)

- st8 契約の「既知失敗8件リスト」は、st9(PR #41)と本タスクが通れば CI 完全
  green になり不要化する。これが直接の動機。
- skip のみ案を退けた理由: pg_advisory_xact_lock による 200件キャップの競合制御は
  PostgreSQL 固有で、このテストが唯一の実DB検証。CI から消すのは CLAUDE.md の
  fail-closed 指向に反する。
- opt-in ゲートを足す理由: run_transcription_dictionary_postgres.sh(docker +
  migration)が正規のローカル実行経路として既に存在し、素の pytest で落ち続ける
  状態は設計意図と乖離した騒音。test_integration_live.py の「接続不能なら skip」
  と同型の前例に揃える。
- CI 側で env を設定した上で grep ガードを置くことで、将来誰かが env 名を変えて
  もサイレント skip にならず fail する(fail-closed の担保)。
