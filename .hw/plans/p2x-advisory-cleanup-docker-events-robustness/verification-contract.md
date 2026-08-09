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

- (実装者が着手時に pytest 実測ベースラインと RF-601 の確認結果をここへ記入する。
  合格ラインは不変)
