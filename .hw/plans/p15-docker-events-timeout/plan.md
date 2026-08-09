---
generated_by: fable
task_id: p15-docker-events-timeout
base-commit: d691ff5b917cf48beae47fb62031394e539f65d7
size: M
parent: p15-deploy-hardening
---

# ST-23: Docker event ストリームの read timeout と since リプレイ(P1-5 第2弾)

base-commit はコーディネータが着手時に `git rev-parse HEAD` で更新すること
(第1弾 `p15-compose-deps-limits` マージ後の HEAD)。

## ゴール

- `services/runtime-api/runtime_api/backends/docker.py` の event ストリームが
  半開き接続を有限時間で検知して再接続する(現状 `timeout=None`、docker.py:130)。
- 再接続の切断窓で発生した die イベントを Docker の `since` パラメータで再取得し、
  イベント消失(= 会議が永久 active)のモードを閉じる。bot profile は
  `idle_timeout: 0` のため、die イベント経路が唯一の exit 通知である。
- 正常時(イベントなしで read timeout に達するだけ)のログを汚さない。
  異常時(接続失敗)は #58 の方針どおり warning。
- 併せて #57 advisory: `TRANSCRIPTION_SERVICE_URL` が未設定のまま起動したとき
  startup warning を1回出す(bot へ空 URL が注入されることの可視化。fail-fast にはしない)。

## 現状(現物確認済み)

- `_stream_events`(docker.py:122-144): `session.get(.../events, stream=True,
  timeout=None)` + `iter_lines()`。半開きで永久ブロック。
- `_event_loop`(docker.py:111-120): 例外時 warning + 2s sleep で再接続(既存)。
  CancelledError で終了。
- イベント JSON には `time`(秒)と `timeNano`(ナノ秒)フィールドがある。
  Docker API の `since` は `<seconds>.<nanoseconds>` 形式の文字列を受け付ける。
- `handle_container_exit` は同名イベントの重複配送で callback を再発火し得るため、
  リプレイの重複はナノ秒精度の since で回避する必要がある。
- 既存テスト: `tests/test_backends.py` ほか。**base 時点から環境依存で複数件 fail する**
  (handoff 記載では18件)。契約は着手時実測ベースライン比の非退行で判定する。

## How

変更ファイルは `services/runtime-api/runtime_api/backends/docker.py`・
`runtime_api/config.py`・`runtime_api/main.py`・`tests/` のみ。

### 1. config.py

```python
# Docker event stream read timeout (seconds). Bounds half-open connection
# detection latency; on expiry the stream reconnects with `since` replay.
DOCKER_EVENTS_READ_TIMEOUT = float(os.getenv("DOCKER_EVENTS_READ_TIMEOUT", "300"))
```

### 2. docker.py — `_stream_events` / `_event_loop`

- インスタンス状態に「最後に観測したイベントの timeNano」と「今回の接続開始時刻
  (ナノ秒)」を持つ(例: `self._last_event_nano: int`。接続開始時に
  `time.time_ns()` を控える)。
- `session.get(f"{url}/events", params=..., stream=True,
  timeout=(5, config.DOCKER_EVENTS_READ_TIMEOUT))` に変更(connect 5s / read は設定値)。
- params に `since` を付ける: 2回目以降の接続では
  `since = f"{nano // 10**9}.{nano % 10**9:09d}"`(nano = 前回接続で最後に観測した
  イベントの timeNano。イベントを1件も観測していなければ前回の接続開始時刻)。
  初回接続は since なし(過去イベントの再生をしない。起動時の取りこぼしは既存の
  `reconcile_state` の責務)。ナノ秒精度により同一イベントの再配送を避ける
  (秒精度だと境界イベントが重複し exit callback が二重発火する)。
- イベント処理ループ内で `timeNano` を `self._last_event_nano` に更新
  (`event.get("timeNano")` が無い場合は更新しない)。
- **read timeout の捕捉**: `requests.exceptions.Timeout` に加え、streaming 中の
  read timeout は `requests.exceptions.ConnectionError`(urllib3 の
  ReadTimeoutError をラップ)として表面化し得る。実装時に実挙動を確認のうえ、
  「timeout 系(正常な keepalive 切れ)」を判別するヘルパーを設け、
  該当時は `_stream_events` から**例外を出さず正常 return** し、
  `logger.debug("Docker event stream read timeout after %ss; reconnecting with since replay", ...)`
  のみ出す(traceback なし)。timeout 判別できない例外は従来どおり raise。
- `_event_loop` は不変の骨格を維持: 正常 return(timeout 再接続)は sleep なしで
  即再接続、例外は既存どおり `logger.warning("Docker event stream reconnecting...",
  exc_info=True)` + 2s sleep。半開きで相手が死んでいる場合、再接続の connect が
  失敗して warning 経路に入るため、異常は warning として自然に可視化される
  (#58 のログ方針と整合。正常周期の timeout を warning にしない)。
- イベント JSON のパース失敗時の既存 warning(docker.py:143-144)は不変。

### 3. main.py — TRANSCRIPTION_SERVICE_URL の startup warning(#57 advisory)

- startup ハンドラの `load_profiles()` 後に、モジュール関数
  `warn_missing_bot_env()`(新設、main.py 内)を呼ぶ:
  `os.getenv("TRANSCRIPTION_SERVICE_URL")` が空・未設定なら
  `logger.warning("TRANSCRIPTION_SERVICE_URL is not set; bot containers will "
  "receive an empty URL and realtime transcription will fail silently "
  "(profiles.yaml injects this value into every bot)")` を1回出す。
- fail-fast にしない(理由は契約外。実装上は warning のみでよい)。

### 4. テスト(tests/test_backends.py へ追加、または tests/test_docker_events.py 新設)

docker daemon 不要のユニットテストにする(`DockerBackend` に fake session を注入):

1. `_stream_events` が `/events` GET に `timeout=(5, DOCKER_EVENTS_READ_TIMEOUT)` を
   渡す(`timeout=None` の退行禁止)。
2. timeout 系例外(`requests.exceptions.Timeout` と、urllib3 ReadTimeoutError を
   ラップした `requests.exceptions.ConnectionError` の両方)で `_stream_events` が
   例外を出さず return する。
3. timeNano 付き die イベントを観測後の再接続で、`since` が
   `"<sec>.<9桁nano>"` 形式・観測値由来で渡される。イベント未観測なら接続開始時刻由来。
   初回接続では since が付かない。
4. die イベントで `on_exit(name, exit_code)` が従来どおりディスパッチされる
   (filters パラメータ不変を含む)。
5. `warn_missing_bot_env()`: env 未設定で warning 1回(caplog)、設定済みで warning なし。

例外を raise させるモックは handoff の教訓どおり `MagicMock(side_effect=<callable>)`
を避け、raise するヘルパー関数か `side_effect=<例外インスタンス>` を使うこと。

### 5. 変更しないもの

- `listen_events` の公開シグネチャ、`_event_loop` の warning + 2s backoff、
  filters の内容(container / die / MANAGED_LABEL)。
- `handle_container_exit` / callback 配送(lifecycle.py)・`reconcile_state`。
- 他 backend(k8s / process)・compose・Dockerfile。

## Why(実装者に渡さない)

親プラン `.hw/plans/p15-deploy-hardening/plan.md` の Why セクションを参照
(since リプレイ選定理由、256件イベントバッファの残存リスク、warning にしない理由)。
