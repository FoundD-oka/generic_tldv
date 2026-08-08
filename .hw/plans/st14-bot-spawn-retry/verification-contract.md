# Verification Contract — st14-bot-spawn-retry

実行環境: py3.11 venv(fresh。numpy / cryptography を追加インストール)。
実行ディレクトリ: `services/meeting-api`。
ベースライン(base-commit 5cae3a0、同一環境の実測): **591 passed / 10 skipped / 1 failed**
(failed はローカル未起動 Postgres に依存する統合テスト1件のみ、既知)。

## Acceptance Tests

すべて `services/meeting-api/tests/test_bot_spawn_retry.py`(新規)。
`_spawn_via_runtime_api` を直接呼び、`meeting_api.meetings._get_httpx_client` を
patch した mock client の `post` を `side_effect` 列で駆動する。
`meeting_api.retry.asyncio.sleep` は AsyncMock で patch(実スリープ禁止)。

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | `httpx.ConnectError` が2回続いた後 201 → 戻り値は 201 の JSON dict、`post` は3回呼ばれる | unit | `python -m pytest tests/test_bot_spawn_retry.py -v` の合格ログ |
| AT-002 | HTTP 500 の後 201 → 戻り値は dict、`post` は2回呼ばれる(500 はリトライ対象) | unit | 同上 |
| AT-003 | HTTP 502 / 503 / 504(parametrize)の後 201 → リトライされ dict を返す | unit | 同上 |
| AT-004 | `ConnectError` が4回連続 → 戻り値 None、`post` はちょうど4回(初回+3リトライ)、例外は外へ伝播しない | unit | 同上 |
| AT-005 | HTTP 429 → `HTTPException`(status_code=429)が即送出、`post` は1回のみ、`sleep` 呼び出しゼロ | unit | 同上 |
| AT-006 | `httpx.ReadTimeout` → 戻り値 None、`post` は1回のみ(二重起動防止: 応答途絶は非リトライ) | unit | 同上 |
| AT-007 | HTTP 400 → 戻り値 None、`post` は1回のみ(確定的失敗は非リトライ) | unit | 同上 |
| AT-008 | バックオフ系列: patch した sleep の第 i 回(i=0,1,2)引数が `[1.0*2^i, 1.0*2^i + 0.5]` の範囲内かつ 10.0 以下 | unit | 同上 |
| AT-009 | `with_retry` の新引数 `is_retryable` 未指定時は既存述語で動作(後方互換)。指定時はカスタム述語が使われる | unit | 同上 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | スポーン最終失敗時の呼び出し元契約: `_spawn_via_runtime_api`→None で meeting が FAILED になり POST /bots が 500 を返す(既存 `tests/test_meetings.py::TestCreateMeeting::test_create_meeting_runtime_failure`) | unit | フルスイート実行ログ内の当該テスト合格 |
| FP-002 | ベースライン比較: `python -m pytest tests/ -q` の結果が **(591 + 新規テスト数) passed / 10 skipped / 1 failed** で、failed は既知の Postgres 統合テスト1件と同一(新規失敗ゼロ・新規スキップゼロ) | full suite | pytest 末尾サマリ行 + failed テストの同定 |
| FP-003 | `webhook_delivery.py` の `with_retry` 利用挙動が不変(`tests/test_webhooks.py` 全合格。FP-002 に包含されるが明示確認) | unit | フルスイート実行ログ |
| FP-004 | 429 の即時伝播(バックオフで遅延させない)— AT-005 が兼ねる | unit | AT-005 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | 追加待機の上限: 定数が max_retries=3 / base_delay=1.0 / 倍率2 / ジッタ≤0.5 / MAX_DELAY=10.0 で、最悪追加待機 ≈8.5 秒以内。試行毎 HTTP timeout は 30.0 のまま | source check + AT-008 | diff 該当行の引用 |
| NFT-002 | 変更ファイルが `meetings.py` / `retry.py` / `tests/test_bot_spawn_retry.py` の3つ(+ `.hw/plans/st14-bot-spawn-retry/` 配下)のみ(`git diff --stat base-commit..HEAD`) | command | diff --stat 出力 |
| NFT-003 | テスト実行に実スリープが混入しない(新規テストファイルの実行が数秒オーダーで完了) | command | pytest の duration 表示 |

## アンチゲーミング条項

- 既存テストの削除・変更・skip/xfail 追加は契約違反。許されるのは新規ファイル
  `test_bot_spawn_retry.py` の追加のみ。
- 製品コードの変更は `meetings.py` / `retry.py` の2ファイルに限る。
  sweeps.py・pyproject.toml・`.github/workflows/`・deploy/ への変更は
  理由の如何を問わず契約違反(st9/st10/st13 の PR と衝突するため)。
- ベースライン既知失敗(postgres 統合テスト1件)への追加は契約違反。

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

該当なし(外部 API・ライブラリの最新仕様に依存しない。httpx の例外階層は
リポジトリ内既存コード `retry.py` の実績に準拠)。
