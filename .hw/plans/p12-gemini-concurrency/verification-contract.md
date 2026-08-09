# Verification Contract — p12-gemini-concurrency

対象: `base-commit..HEAD` の差分(base-commit はコーディネータが着手時に
plan.md frontmatter へ書き込んだ値)。テストは commit 済み clean tree に対して実行する。

このサービスは CI 未整備(ST-26/P1-4 スコープ)のため、本契約の pytest 実行は
すべてローカル fresh venv で行い、ログを evidence として保存する。
venv 構築: python3.11 で
`pip install -r services/transcription-service/requirements.txt pytest pytest-asyncio`
(/tmp の venv は劣化するため毎回作り直す)。

## ベースライン取得手順(FP-001 の前提。転記値の使用禁止)

1. 実装着手前に base-commit をチェックアウトした clean tree で上記 venv を作成し、
   `python -m pytest services/transcription-service/tests` を実行する。
2. サマリ全文を `.hw/gates/p12-gemini-concurrency/pytest-baseline-<commit>.txt` に保存する。
3. **この実測値が本契約のベースライン**である。handoff や過去契約からの転記値は無効。
   (integration マーカー等で既存 skip/fail があってもそのまま記録し、比較基準とする。)

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | 既定同時実行数が 3: env 未設定で `MAX_CONCURRENCY == 3` かつ semaphore 初期容量 3。`GEMINI_MAX_CONCURRENCY=5` で 5、`0`/`-2` で `max(1,...)` ガードにより 1(importlib.reload + 復元 fixture) | `python -m pytest services/transcription-service/tests/test_gemini_adapter.py -k concurrency -v`(以下同 venv) | pytest ログを `.hw/gates/p12-gemini-concurrency/` に保存 |
| AT-002 | semaphore が実際に同時実行を制限する: concurrency=2 で 3 リクエスト並行実行時、fake Client 内の同時在圏カウンタの最大値 ≤ 2 | unit(async、fake genai Client + 在圏カウンタ) | pytest ログ |
| AT-003 | **429 で自滅しない(本タスクの前提条件。緩和禁止)**: generate_content が 429 を 2 回返した後成功するケースで、正常な文字起こし結果が返り、`time.sleep`(monkeypatch)への待機が指数的(上限 60 秒クランプ)である | unit(fake Client が status_code=429 属性付き例外を送出) | pytest ログ |
| AT-004 | 429 枯渇時は手動 reconcile でなく自動リトライ可能な失敗になる: 常時 429 で `GeminiError.code == "admission_timeout"` かつ `status_code == 503`(`unknown_manual_reconcile` でないこと) | unit | pytest ログ |
| AT-005 | 429 再試行ループが deadline/stop_event を尊重する: 過去の deadline_monotonic を渡すと全試行を消費せず即中断される | unit(`time.sleep` 呼び出し回数と経過を assert) | pytest ログ |
| AT-006 | 非 429 のエラー分類が不変: generate_content の一般例外 → `unknown_manual_reconcile`、401/403 → `auth_failed`、404 → `model_not_found`、ValueError → `config_invalid` | unit(既存テスト + 必要な追加) | pytest ログ |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | transcription-service テスト: ベースライン実測に対し**新規 fail 0・passed 増は本タスクの新規テスト分のみ・skipped 増加なし** | ベースラインと変更後を同一 venv・同一コマンドで実行しサマリ比較 | ベースライン/変更後の pytest サマリ全文 |
| FP-002 | compose/env 配線の `:-1` フォールバックがコード既定を潰さない: `grep -rn "GEMINI_MAX_CONCURRENCY" services/transcription-service/docker-compose.yml services/transcription-service/docker-compose.cpu.yml services/transcription-service/.env.example` の全出現が既定 3(`:-3` または `=3`)である | 上記 grep コマンドの出力 | grep 出力 |
| FP-003 | 変更ファイルが `services/transcription-service/` 配下のみ(meeting-api / deploy/compose / deploy/helm は無変更) | `git diff --name-only base-commit..HEAD` が上記のみ | diff 出力 |
| FP-004 | file API のリトライ(`_retryable_file_call`)と `HttpRetryOptions(attempts=1)` に差分がない(SDK 側リトライを有効化しない) | `git diff base-commit..HEAD -- services/transcription-service/gemini_adapter.py` の該当ハンクレビュー(Fable) | diff + レビュー verdict |
| FP-005 | チャンク処理・マージ・admission(semaphore 待ち→admission_timeout)の既存セマンティクスが不変(429 経路の追加のみ) | 既存テスト green(FP-001 内)+ 差分レビュー | pytest ログ + レビュー verdict |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | 429 バックオフのテストが実時間 sleep しない(`time.sleep` monkeypatch 必須。テストスイート全体が従来と同オーダーの実行時間) | テスト実装レビュー + pytest 実行時間 | pytest ログ |
| NFT-002 | 429 発生時に attempt 番号・待機秒を含む warning ログが出る(運用観測性) | unit(caplog) | pytest ログ |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p12-gemini-concurrency/` へ。`.hw/plans/` に後 commit しない)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no(plan.md 内の仮説・確信度記録で足りる)
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

- Gemini API の Tier 別レート制限値(RPM/TPM)は外部依存で変動し得るが、本契約は
  数値でなく**機構**(429 バックオフ → 枯渇時 admission_timeout/503 → 上流自動
  リトライ)を固定する。実レート制限が仮説より低くても自滅せず実効直列化に退行する
  ことが AT-003/AT-004 で保証されるため、レート制限値の文書調査に依存しない。
