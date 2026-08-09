# Verification Contract — p15-docker-events-timeout

対象: `base-commit..HEAD` の差分(base-commit はコーディネータが着手時に plan.md
frontmatter へ書き込んだ値)。検証は commit 済み clean tree に対して実行する。

判定主体の凡例: **Fable** = base-commit..HEAD の差分と本契約のみで判定
(gitignore 領域の証跡には到達できない)/ **CI** = GitHub Actions(runtime-api の
CI は未整備のため本タスクでは不使用。pytest 判定はゲート+改訂履歴の実測値記載で代替)/
**ゲート** = pr-ready-gate 実行者が `.hw/gates/p15-docker-events-timeout/` の証跡を確認。

## ベースライン取得手順(転記値の使用禁止。handoff の「18件 fail」を転記しない)

1. 着手時の base-commit の clean tree で python3.11 fresh venv を
   `~/.cache/hw-venvs/p15-docker-events-timeout`(/tmp 以外)に作成し、
   `pip install -e "services/runtime-api/[dev]"`(dev extra が無ければ
   `pip install -e services/runtime-api/ pytest pytest-asyncio`)。
2. `python -m pytest services/runtime-api/tests` を実行し、サマリ全文を
   `.hw/gates/p15-docker-events-timeout/pytest-baseline-<commit>.txt` に保存する。
   **この実測値(passed/failed/skipped と failed のテスト名一覧)が本契約の
   ベースライン**。併せて本契約末尾の「改訂履歴」へ実測サマリ(数値と failed 名)を
   記入して commit する(Fable が差分のみで FP-201 を判定できるようにするため)。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-201 | **ST-23 本体**: /events GET が `timeout=(5, DOCKER_EVENTS_READ_TIMEOUT)` で発行される(`timeout=None` の消滅)。既定値 300、env で変更可 | ユニットテスト(plan §4-1)+ config.py の差分 | Fable(diff)+ ゲート(pytest ログ) | pytest ログを `.hw/gates/` に保存 |
| AT-202 | **正常時の静粛性**: timeout 系例外(Timeout / ReadTimeoutError をラップした ConnectionError)で `_stream_events` が例外なく return し、debug ログのみ(warning・traceback なし)で即時再接続する | ユニットテスト(plan §4-2)+ diff レビュー | Fable + ゲート | pytest ログ |
| AT-203 | **イベント消失の閉塞(since リプレイ)**: 再接続時に `since` が「最後に観測したイベントの timeNano(未観測なら前回接続開始時刻)」由来のナノ秒精度文字列 `<sec>.<9桁>` で渡される。初回接続は since なし | ユニットテスト(plan §4-3) | Fable + ゲート | pytest ログ |
| AT-204 | die イベントの処理セマンティクス不変: filters(container/die/MANAGED_LABEL)不変、`on_exit(name, exit_code)` ディスパッチ不変、timeout 判別不能の例外は従来どおり `_event_loop` の warning + 2s backoff | ユニットテスト(plan §4-4)+ diff レビュー | Fable | pytest ログ + diff |
| AT-205 | **#57 advisory**: `TRANSCRIPTION_SERVICE_URL` 未設定で startup 時に warning が1回出る。設定済みなら出ない。fail-fast しない(exit しない) | ユニットテスト(plan §4-5) | Fable + ゲート | pytest ログ |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-201 | 既存テスト非退行: ベースラインの failed 集合に対し**新規 fail 0**(passed 増は新設テスト分のみ。ベースラインで fail していたものの扱いは現状維持でよい) | 同一 venv・同一コマンドでサマリ比較 | ゲート(両サマリ全文)+ Fable(改訂履歴の実測値と照合) | 両サマリ全文 |
| FP-202 | 変更ファイルが `services/runtime-api/runtime_api/backends/docker.py`・`runtime_api/config.py`・`runtime_api/main.py`・`services/runtime-api/tests/` のみ | `git diff --name-only base-commit..HEAD` | Fable | diff |
| FP-203 | lifecycle.py(callback 配送・reconcile)・k8s/process backend・`listen_events` シグネチャに差分なし | diff | Fable | diff |
| FP-204 | main.py の差分が `warn_missing_bot_env` の追加と startup 内の呼び出し1行のみ(env validation の fail-fast 化・必須 env 追加をしていない) | diff のハンクレビュー | Fable | diff |
| FP-205 | 新規テストが docker daemon・実ネットワークに依存しない(fake session 注入のみ。CI 未整備環境でも将来そのまま CI に載る) | テストソースレビュー | Fable | diff |

## Non-Functional Checks

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| NFT-201 | ログ方針の整合(#58): 正常周期の read timeout は debug、接続失敗・パース失敗は warning。warning を debug へ格下げした箇所がない | diff レビュー | Fable | diff |
| NFT-202 | 新規依存の追加なし(pyproject.toml 無変更。requests / stdlib のみで実装) | diff | Fable | diff |

## KPI Checks

なし。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p15-docker-events-timeout/` へ)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-201 | requests の streaming read timeout が実際にどの例外型で表面化するか(requests/urllib3 のバージョン依存。Timeout か ReadTimeoutError ラップの ConnectionError か) | 実装時に venv の requests/urllib3 バージョンで小さな再現スクリプトか単体確認を行い、判別ヘルパーが両型を覆うことをテストで固定 | テストコード(両型のケース)+ `.hw/gates/` に確認メモ |
| RF-202 | Docker Engine API の `since` がナノ秒精度文字列 `<sec>.<nano>` を受理し境界イベントを重複配送しないこと | 実装時にローカル docker daemon で `curl --unix-socket ... '/events?since=...'` を1回実測 | `.hw/gates/` に実測出力 |

## 改訂履歴

### base-commit(着手時実測)

- `d691ff5b917cf48beae47fb62031394e539f65d7`(plan.md frontmatter と
  `base-commit` ファイルを同値へ更新済み。プラン記載の `b6abdd7...` は第1弾
  マージ前の古い値だった)

### pytest ベースライン実測(base-commit d691ff5 / clean tree)

環境: python3.11.15 fresh venv `~/.cache/hw-venvs/p15-docker-events-timeout`、
`pip install -e "services/runtime-api/[dev]"`、
`python -m pytest services/runtime-api/tests -q`。

- **ベースライン: 18 failed, 94 passed, 7 skipped**
- **実装後: 18 failed, 107 passed, 7 skipped**(新規 fail 0。passed +13 は
  新設 `tests/test_docker_events.py` の13件のみ)
- failed 集合はベースライン・実装後で完全一致(環境依存の既存 fail、本タスク非対象):
  - `test_backends.py::test_process_backend_inspect`
  - `test_integration.py::TestHealth::test_profiles_loaded`
  - `test_integration.py::TestContainerLifecycle::test_01_create_container`
  - `test_integration.py::TestContainerLifecycle::test_02_list_containers`
  - `test_integration.py::TestContainerLifecycle::test_03_inspect_container`
  - `test_integration.py::TestContainerLifecycle::test_04_touch_container`
  - `test_integration.py::TestContainerLifecycle::test_05_stop_container`
  - `test_integration.py::TestContainerLifecycle::test_06_container_gone`
  - `test_integration.py::TestContainerLifecycle::test_07_list_after_stop`
  - `test_integration.py::TestErrors::test_unknown_profile`
  - `test_integration.py::TestErrors::test_inspect_nonexistent`
  - `test_integration.py::TestErrors::test_delete_nonexistent`
  - `test_integration.py::TestCallback::test_callback_url_accepted`
  - `test_integration_process.py::TestProcessLifecycle::test_01_create_process`
  - `test_integration_process.py::TestProcessLifecycle::test_02_list_shows_process`
  - `test_integration_process.py::TestProcessLifecycle::test_03_inspect_process`
  - `test_integration_process.py::TestProcessLifecycle::test_04_stop_process`
  - `test_integration_process.py::TestProcessLifecycle::test_05_process_stopped`
- 証跡全文: `.hw/gates/p15-docker-events-timeout/pytest-baseline-d691ff5.txt`、
  `pytest-after-impl.txt`(gitignore 領域)

### RF-201 実測(streaming read timeout の例外型)

確認方法: ヘッダのみ返して本文を送らない TCP サーバと unix socket サーバを立て、
`session.get(..., stream=True, timeout=(5,1))` + `iter_lines()` で read timeout を
実際に発生させ、例外型を出力した(requests 2.34.2 / urllib3 2.7.0 / python 3.11.15)。

- **本文ストリーミング中の read timeout** → `requests.exceptions.ConnectionError`
  (`args[0]` と `__cause__` が `urllib3.exceptions.ReadTimeoutError`)。
  `isinstance(exc, requests.exceptions.Timeout)` は **False**。
  TCP・unix socket(requests_unixsocket)とも同一挙動。
- **ヘッダ到着前(`session.get` 内)の read timeout** → `requests.exceptions.ReadTimeout`
  (`requests.exceptions.Timeout` のサブクラス)。
- したがって判別ヘルパー `_is_read_timeout` は「`Timeout` サブクラス」に加えて
  「`ConnectionError` の例外チェーンに `ReadTimeoutError` を含むもの」も timeout 扱い
  にする必要がある。両型をテストで固定した(`test_docker_events.py`)。
- 証跡: `.hw/gates/p15-docker-events-timeout/rf-201-read-timeout-repro.txt`

### RF-202 実測(Docker API の since ナノ秒精度)

確認方法: ローカル docker daemon(Server API 1.54)で `runtime.managed=true` ラベル付き
コンテナを2つ die させ、`curl --unix-socket /var/run/docker.sock '/v1.43/events?since=...'`
を実行して境界イベントの再配送有無を確認した。

- 観測した die イベント: `1786281088293639700`(rf202-a)/ `1786281088546786600`(rf202-b)
- `since=1786281088.293639700`(rf202-a の timeNano ちょうど)→ **rf202-b の1件のみ**。
  境界イベントは再配送されない(ナノ秒精度 `<sec>.<9桁>` は受理され、`since` は排他的)。
- 参考: `since=1786281088`(秒精度)→ 2件とも配送され rf202-a が重複する。
  ナノ秒精度が必須であることを実測で確認。
- 証跡: `.hw/gates/p15-docker-events-timeout/rf-202-since-nano.txt`
