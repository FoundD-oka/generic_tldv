# Verification Contract — p2x-advisory-cleanup-docker-events-robustness

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体の凡例: **Fable** = 差分と本契約のみで判定(`.hw/gates/` に到達できない)/
**CI** = GitHub Actions(test-runtime-api がマージ済みの場合)/ **ゲート** =
pr-ready-gate 実行者が `.hw/gates/p2x-advisory-cleanup-docker-events-robustness/` を確認。

## ベースライン取得手順(転記値の使用禁止)

1. base-commit の clean tree で python3.11 fresh venv
   (`~/.cache/hw-venvs/p2x-advisory-cleanup-docker-events-robustness`)に
   `pip install -e "services/runtime-api/[dev]"`。
2. `python -m pytest services/runtime-api/tests` のサマリ全文を `.hw/gates/` に保存し、
   実測値(passed/failed/skipped と failed 名)を本契約末尾の改訂履歴へ記入して commit。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-601 | **since アンカー保持**: `session.get` が raise した場合、`_next_since()` の返り値が試行前と不変(アンカーが失敗試行の開始時刻へ進まない) | ユニットテスト(fake session) | Fable + ゲート | pytest ログ |
| AT-602 | **アンカー更新は成功後**: get 成功後の read timeout では従来どおり静粛 return し、次回 since が「最終観測イベントの timeNano(未観測なら**今回接続の開始時刻**=get 呼び出し前の時刻)」になる | ユニットテスト | Fable + ゲート | pytest ログ |
| AT-603 | **resp クローズ**: read timeout 経路・正常終了経路の両方で `resp.close()` が呼ばれる | ユニットテスト(fake resp の close 記録) | Fable + ゲート | pytest ログ |
| AT-604 | **ConnectTimeout の分類**: `requests.exceptions.ConnectTimeout` は静粛 return せず raise として伝播する(`_event_loop` の warning + 2s backoff 経路) | ユニットテスト | Fable + ゲート | pytest ログ |
| AT-605 | **isinstance 第一判定**: 実 urllib3 `ReadTimeoutError` を cause に持つ ConnectionError が timeout 扱い(既存挙動維持)、かつ同名の自作偽クラスでも名前マッチのフォールバックが機能する | ユニットテスト | Fable + ゲート | pytest ログ |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-601 | #66 セマンティクス不変: filters(container/die/MANAGED_LABEL)、`on_exit(name, exit_code)` ディスパッチ、since のナノ秒書式 `<sec>.<9桁>`、read timeout の debug ログ静粛性(warning なし)に差分なし | diff + 既存テスト green 維持 | Fable | diff + pytest ログ |
| FP-602 | 変更ファイルが `services/runtime-api/runtime_api/backends/docker.py` と `services/runtime-api/tests/` のみ: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` | diff | Fable | diff |
| FP-603 | 既存テスト非退行: ベースラインの failed 集合に対し新規 fail 0(test-runtime-api CI がマージ済みなら CI 緑がこれを兼ねる) | CI または同一 venv でのサマリ比較 | CI / ゲート + Fable(改訂履歴と照合) | CI ログ or 両サマリ全文 |
| FP-604 | lifecycle.py・k8s/process backend・`listen_events` シグネチャ・config.py に差分なし | diff | Fable | diff |
| FP-605 | 新規テストが docker daemon・実ネットワークに依存しない(fake session 注入のみ) | テストソースレビュー | Fable | diff |

## Non-Functional Checks

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| NFT-601 | 新規依存なし(pyproject.toml 無変更。urllib3 は requests の推移的依存として import し、ImportError 時のフォールバックがある) | diff | Fable | diff |
| NFT-602 | close 例外の安全性: `resp.close()` が例外を投げても timeout 判定・raise 経路を破壊しない | ユニットテストまたはソースレビュー(contextlib.closing の挙動確認) | Fable | pytest ログ or diff |

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-601 | `ConnectTimeout` が実際に requests からどう raise されるか(#66 で例外型の推測が2回覆った経路。プランの想定を鵜呑みにしない) | 実装時に requests の該当バージョンで発生経路を確認し、確認結果(バージョンと発生型)を改訂履歴へ記入 | 改訂履歴 |
| RF-602 | `contextlib.closing` + `iter_lines` の組で read timeout 時に close が二重例外を出さないこと | ユニットテストで実 requests/urllib3 の型を使って確認 | pytest ログ |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p2x-advisory-cleanup-docker-events-robustness/`)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no(RF は改訂履歴で代替)

## 改訂履歴

合格ラインは不変。以下は実装者が実測して記入した値。

### base-commit の是正(2026-08-10)

プラン記載の `0fe5ea5` は着手時点の HEAD ではなかったため、`git rev-parse HEAD` の
実測値 `03436054d2e1d8a2e0d918bf53ea704cdb4612c0` へ plan.md frontmatter と
`base-commit` ファイルの両方を更新した。

### pytest ベースライン実測(FP-603)

- 環境: python3.11.15 fresh venv `~/.cache/hw-venvs/p2x-docker-events`、
  `pip install -e "services/runtime-api/[dev]"`(requests 2.34.2 / urllib3 2.7.0)、
  macOS arm64。
- コマンド: `python -m pytest services/runtime-api/tests`
- base-commit `03436054` の実測: **18 failed, 124 passed, 7 skipped**。
  failed 集合(すべてローカル環境固有。実サーバ/プロセス起動を前提とする統合テスト):
  `test_backends.py::test_process_backend_inspect`、
  `test_integration.py::TestHealth::test_profiles_loaded`、
  `test_integration.py::TestContainerLifecycle::test_01..test_07`(7件)、
  `test_integration.py::TestErrors::test_unknown_profile / test_inspect_nonexistent /
  test_delete_nonexistent`、`test_integration.py::TestCallback::test_callback_url_accepted`、
  `test_integration_process.py::TestProcessLifecycle::test_01..test_05`(5件)。
- 実装後の実測: **18 failed, 135 passed, 7 skipped**。failed 集合は上記と完全一致
  (`diff` で差分なし)、passed が +11(新規テスト11件)。新規 fail 0 → FP-603 充足。
  なお CI(python:3.11 / linux)では同じ統合テスト群が skip され
  `pytest-baseline.json` の「105 passed, 27 skipped, 0 failed」となる。差は環境依存。
- ruff ラチェット(CI と同じ ruff 0.16.2): 変更前後とも `total=117 rules=19` で
  `check_ruff_baseline.py` が OK。docker.py + test_docker_events.py の内訳も
  変更前後とも `I001=1, UP035=1, UP045=6`(既存負債)で増加なし。

### RF-601 実測(ConnectTimeout の発生型)

requests 2.34.2 / urllib3 2.7.0 で、到達不能アドレスへ
`requests.get(..., timeout=(0.05, 5), stream=True)` を実行して再現した実測結果:

- 送出される型は `requests.exceptions.ConnectTimeout`。
  MRO は `ConnectTimeout → ConnectionError → Timeout → RequestException → OSError`。
  すなわち **`Timeout` と `ConnectionError` の両方のサブクラス**であり、
  修正前の `isinstance(exc, Timeout)` で「正常な keepalive 満了」と誤分類されていた
  (advisory 4 の前提はプランどおりで正しい)。
- 例外チェーン: `ConnectTimeout → urllib3.MaxRetryError → urllib3.ConnectTimeoutError
  → builtins.TimeoutError`。`ReadTimeoutError` / `ReadTimeout` は現れないため、
  ConnectTimeout を Timeout 分岐から除外しても ConnectionError 分岐の名前マッチで
  誤って timeout 扱いに戻ることはない。
- `urllib3.exceptions.ConnectTimeoutError` は `ReadTimeoutError` のサブクラスではない
  (MRO: `ConnectTimeoutError → TimeoutError → HTTPError`)ため、isinstance 第一判定
  でも誤検知しない。
- 併せて #66 の実測を再確認: ボディ読み取り中の read timeout は
  `requests.exceptions.ConnectionError(urllib3.exceptions.ReadTimeoutError)`、
  ヘッダ到着前の read timeout は `requests.exceptions.ReadTimeout`(`Timeout` サブクラス、
  `ConnectTimeout` のサブクラスではない)。どちらも従来どおり timeout 扱いのまま。

### RF-602 実測(close と read timeout の相互作用)

ローカルの実 HTTP ソケットサーバ(ヘッダのみ返して沈黙)に対し
`timeout=(5, 0.5)` で `iter_lines()` を回し、実 requests/urllib3 の型で確認した:

- `contextlib.closing(resp)` で囲んだ場合、read timeout 時に伝播する例外は
  `requests.exceptions.ConnectionError(ReadTimeoutError)` のままで、close 由来の
  二重例外は発生しない。`resp.close()` の二重呼び出しも冪等。
- ただし **close() 自身が例外を投げた場合、`contextlib.closing` はその例外で
  元の streaming 例外を置き換えてしまい、NFT-602(timeout 判定・raise 経路を
  破壊しない)を満たせない**。このため実装は `try/finally` +
  `contextlib.suppress(Exception)` で close し、close 失敗を握り潰す形にした
  (close するタイミング・対象はプラン How 2 と同一。AT-603 / NFT-602 は
  `ExplodingCloseResponse` を使ったユニットテストで検証)。

### 新規テストの非空虚性(参考)

新規11件を修正前の docker.py に対して実行すると 7 件が fail する
(anchor 保持2件・close 2件・ConnectTimeout 2件・isinstance 判定1件)。
残り4件は既存セマンティクスの固定(退行検知)用。
