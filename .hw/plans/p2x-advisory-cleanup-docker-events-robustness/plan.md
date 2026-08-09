---
generated_by: fable
task_id: p2x-advisory-cleanup-docker-events-robustness
base-commit: 03436054d2e1d8a2e0d918bf53ea704cdb4612c0
size: M
---

# docker イベントストリームの堅牢化(#66 advisory 1〜4)

## ゴール

`services/runtime-api/runtime_api/backends/docker.py` の `_stream_events` /
`_is_read_timeout` に残る4つの窓を閉じる。現行の unix socket 構成では実害が
ほぼないが、TCP / プロキシ経由の `DOCKER_HOST` を将来入れた瞬間に
「イベント取りこぼし」「backoff なし再接続ループ」に化ける時限爆弾。
**#66 で確立したセマンティクス(半開き検知・since ナノ秒リプレイ・exit 二重発火
なし)は一切変えない。**

## How

対象は `docker.py` と `services/runtime-api/tests/` のみ。

1. **since アンカー(advisory 1)**: `_stream_events` 冒頭の
   `self._last_event_nano = 0` / `self._stream_started_nano = time.time_ns()`
   (現行 157-158 行)を廃し、`attempt_started = time.time_ns()` を
   `session.get` **前**に取得、`session.get` が**成功した後**に
   `self._last_event_nano = 0; self._stream_started_nano = attempt_started` を
   コミットする。接続試行が raise した場合は前回アンカーが無傷で残り、次回
   `_next_since()` が最終観測イベント(または前回接続開始時刻)を指し続ける。
   `attempt_started` を get 前に取るのは「接続確立〜ヘッダ到着の間のイベント」を
   リプレイ窓から漏らさないため。
2. **resp のクローズ(advisory 2)**: `resp` 取得後の iter_lines ループ全体を
   `contextlib.closing(resp)` で囲む。read timeout・正常終了のどちらの経路でも
   close される。close 自体が投げる例外で timeout 判定を壊さないこと。
3. **_is_read_timeout の厳密化(advisory 3)**: ConnectionError の内部例外判定を
   `isinstance(inner, urllib3.exceptions.ReadTimeoutError)` を第一判定にし、
   既存の型名文字列マッチ(`ReadTimeoutError` / `ReadTimeout`)はフォールバック
   として残す(vendoring 差異対策)。urllib3 の import は module top で行い、
   ImportError 時は名前マッチのみに落ちる try/except にする。
4. **ConnectTimeout の分類是正(advisory 4)**: `requests.exceptions.ConnectTimeout`
   は `Timeout` のサブクラスのため現行では「正常な keepalive 満了」扱いで即時
   再接続に入る。`isinstance(exc, requests.exceptions.Timeout) and not
   isinstance(exc, requests.exceptions.ConnectTimeout)` として、接続タイムアウトは
   従来どおり raise → `_event_loop` の warning + 2s backoff 経路へ戻す。
5. **テスト**(fake session 注入。docker daemon・実ネットワーク非依存):
   - get が raise した場合、`_next_since()` の返り値が試行前と不変。
   - get 成功後に read timeout した場合、従来どおり静粛 return + since 更新。
   - `resp.close()` が read timeout 経路・正常経路の両方で呼ばれる。
   - `ConnectTimeout` が raise として伝播する(静粛 return しない)。
   - 実 urllib3 の `ReadTimeoutError` を `__cause__` に持つ `ConnectionError` が
     引き続き timeout 扱い(既存テスト維持)+ 同名の自作偽例外クラスでも
     フォールバックが機能する。

## 前提・順序

- p2x-advisory-cleanup-ci-runtime-api(CI 新設)の**後**に着手。paths に
  `services/runtime-api/**` が入るため、本タスクの PR で CI が回帰判定する。
  CI が未マージの場合は fresh venv での実測比較で代替(契約参照)。

## Why(実装者に渡さない)

- #66 のレビューで「違反ではないが窓が残る」と明示された4点。単体では小粒だが、
  すべて同一関数群・同一テスト基盤なので1タスクに束ねるのが revert 粒度として正しい。
- この経路は bot の exit 検知=会議完了通知の中核。誤実装は「会議が永久 active」
  「exit callback 二重発火」に化ける。#66 で推測が実測に2回覆された経路でもあり、
  セマンティクス固定のテストを厚めに要求している。
