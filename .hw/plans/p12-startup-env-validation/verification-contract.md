# Verification Contract — p12-startup-env-validation

対象: `base-commit..HEAD` の差分(base-commit はコーディネータが着手時に
plan.md frontmatter へ書き込んだ値)。テストは commit 済み clean tree に対して実行する。
meeting-api テストは python3.11 fresh venv
(`pip install -e libs/admin-models/ -e services/meeting-api/` + `pytest pytest-asyncio httpx`。
/tmp の venv は劣化するため毎回作り直す)。

## ベースライン取得手順(FP-001 の前提。転記値の使用禁止)

1. 実装着手前に base-commit をチェックアウトした clean tree で上記 venv を作成し、
   `python -m pytest services/meeting-api/tests` を実行する。
2. サマリ全文を `.hw/gates/p12-startup-env-validation/pytest-baseline-<commit>.txt` に保存する。
3. **この実測値が本契約のベースライン**である。handoff や過去契約からの転記値は無効。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | **必須 env の fail-fast(本タスクの本質要求。緩和禁止)**: `TRANSCRIPTION_SERVICE_URL` が未設定・空・空白のみのとき `collect_env_issues()` が violation を返し、strict(既定)で `validate_startup_env` が RuntimeError を raise する。非空なら violation なし | `python -m pytest services/meeting-api/tests/test_env_validation.py -v` | pytest ログを `.hw/gates/p12-startup-env-validation/` に保存 |
| AT-002 | ストレージ設定の前倒し検証: `STORAGE_BACKEND=gcs` かつ `GCS_BUCKET` 空 → violation。未知 backend → violation。既定(未設定=minio)・gcs+bucket 設定済み → violation なし | 同上 | pytest ログ |
| AT-003 | 違反の全件集約: 複数の必須違反を同時に仕込んだとき、RuntimeError メッセージに該当 env 名がすべて含まれる(1件目で打ち切らない) | unit | pytest ログ |
| AT-004 | 逃げ道と fail-safe: `STARTUP_ENV_VALIDATION=warn` で raise せず warning ログ(caplog)に降格。未設定・"strict"・不正値(例 "yes")はいずれも strict 挙動 | unit | pytest ログ |
| AT-005 | オプション分類が起動を妨げない: `TRANSCRIPTION_SERVICE_TOKEN` 空 / `VOICEPRINT_SERVICE_URL` 空 / `KABOSU_DRIVE_EXPORT_ENABLED` 有効+Drive 資格情報(GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / KABOSU_GOOGLE_REFRESH_TOKEN / KABOSU_DRIVE_FOLDER_ID)欠落 → violation ではなく warning(caplog、無効化される機能名を含む)。`DEFERRED_TRANSCRIPTION_SERVICE_URL` 空 → warning も出ない | unit | pytest ログ |
| AT-006 | startup() が init_db より前に検証を呼ぶ: `inspect.getsource(main.startup)` 内で `validate_startup_env` の出現位置 < `init_db` の出現位置 | unit(静的ガード) | pytest ログ |
| AT-007 | env は呼び出し時に読まれる(import 時固定でない): 同一プロセス内で monkeypatch により violation あり→なしの両結果が得られる(AT-001〜005 が monkeypatch で成立していること自体を証拠とする) | AT-001〜005 の実行 | pytest ログ |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | meeting-api テスト: 上記手順で実装者が実測したベースラインに対し、**新規 fail 0・passed 増は本タスクの新規テスト分のみ・skipped 増加なし**(既存テストが startup/env に依存していても strict 既定で壊れないこと) | ベースラインと変更後を同一 venv・同一コマンドで実行しサマリ比較 | ベースライン/変更後の pytest サマリ全文 |
| FP-002 | 変更ファイルが `services/meeting-api/meeting_api/env_validation.py`(新規)・`services/meeting-api/meeting_api/main.py`・`services/meeting-api/tests/` 配下のみ(config.py / database.py / deploy / runtime-api は無変更) | `git diff --name-only base-commit..HEAD` が上記のみ | diff 出力 |
| FP-003 | startup() の既存処理順・内容が不変(検証呼び出しの追加のみ。Redis bounded retry・consumer group 作成・supervised task 起動・_startup_complete フリップに差分なし) | `git diff base-commit..HEAD -- services/meeting-api/meeting_api/main.py` の該当ハンクレビュー(Fable) | diff + レビュー verdict |
| FP-004 | 既存の import 時検証(config.py REDIS_URL / database.py DB_*)と重複するチェックを追加していない(REDIS_URL・DB_* が本モジュールの必須リストに含まれない) | env_validation.py のソースレビュー + `grep -n "REDIS_URL\|DB_HOST" services/meeting-api/meeting_api/env_validation.py` が空 | grep 出力 + レビュー verdict |
| FP-005 | deploy 配下が無変更(compose の空既定を「直して」検証を無意味化しない) | `git diff base-commit..HEAD -- deploy/` が空 | diff 出力 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | violation メッセージが運用者に自己完結: 各違反行に env 名・必須である理由(壊れる機能)・設定例を含む | テスト内 assert(メッセージ内容)+ レビュー | pytest ログ |
| NFT-002 | 検証は同期・軽量(ネットワーク接続確認をしない。os.environ の読み取りのみ)— 起動遅延・外部依存フレークを持ち込まない | env_validation.py のソースレビュー(httpx/socket 等の import がない) | レビュー verdict |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p12-startup-env-validation/` へ。`.hw/plans/` に後 commit しない)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

- 外部ライブラリ・API の最新挙動に依存しない(os.environ 読み取りと FastAPI startup
  フックのみ)。K8s CrashLoopBackOff / docker restart backoff の挙動は安定した
  プラットフォーム仕様であり、契約はそれに依存する検証を課さない。
