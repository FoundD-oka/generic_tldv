---
generated_by: fable
task_id: st14-bot-spawn-retry
base-commit: 5cae3a05550e8679b156e88652b0dfa2193d30f1
size: M
---

# ST-1: ボット起動失敗時のリトライ導入(P0-4)

## ゴール

依頼の文字通りの内容: 「ボット(コンテナ)起動失敗時にリトライを入れる。
3回・指数バックオフ」(計画書 P0-4 / 監査 ST-1)。

再設計後のゴール(reframe): リトライは**一時的(transient)な失敗にのみ**適用し、
**二重起動を絶対に起こさない**ことを不変条件とする。具体的には
「runtime-api にリクエストが到達しなかった、または runtime-api が状態を
クリーンアップ済みと確定できる失敗」だけをリトライし、応答途絶
(ReadTimeout 等。コンテナが実は作成されている可能性がある)はリトライしない。
「3回・指数バックオフ」の枠は維持しつつ、リトライ対象の分類を確定させた。
乖離は小さく、依頼の意図(瞬断で会議を取り逃さない)と完全に整合する。

## 対象(変更してよいファイルはこの3つのみ)

- `services/meeting-api/meeting_api/meetings.py` — `_spawn_via_runtime_api`
  (base-commit 時点 :403-435)
- `services/meeting-api/meeting_api/retry.py` — `with_retry` にオプション引数追加
- `services/meeting-api/tests/test_bot_spawn_retry.py` — 新規テストファイル

runtime-api・compose・helm・ダッシュボードは変更しない。

## 設計判断(確定事項。実装者の裁量に委ねない)

1. **リトライの実装位置**: `_spawn_via_runtime_api` の内部。呼び出し元3箇所
   (meeting bot :1205 / agent :744 / browser-session :823)の契約
   (成功→dict、失敗→None、429→HTTPException(429))は一切変えない。
   これにより3経路すべてが同一ポリシーでリトライされ、呼び出し元の
   FAILED 遷移(:1214-1218 ほか)は「リトライ枯渇後の最終結果」になる。

2. **リトライ回数・間隔**: 既存 `retry.py` の既定と同一。
   `max_retries=3`(初回含め最大4試行)、`base_delay=1.0` 秒、
   倍率2の指数バックオフ + `random.uniform(0, 0.5)` ジッタ、上限 `MAX_DELAY=10.0` 秒。
   追加待機の合計は最悪 1+2+4+ジッタ ≒ 8.5 秒以内。試行ごとの HTTP タイムアウトは
   現行の 30.0 秒を変更しない。

3. **リトライ対象の分類**(二重起動防止の中核):
   - **リトライする**:
     - `httpx.ConnectError` / `httpx.ConnectTimeout` — 接続未確立。リクエストは
       runtime-api に到達していないため再送は安全。
     - HTTP **500** — runtime-api は作成失敗時に Redis state を削除してから
       500 を返す(runtime_api/api.py:274-279 で確認済み)。コンテナ名は毎回
       uuid サフィックスで新規生成されるため名前衝突もない。再送は安全。
     - HTTP **502 / 503 / 504** — runtime-api の create_container 自体は
       これらを返さない(返すのは 400/429/500 系のみ)。これらは中間層
       (プロキシ・過負荷拒否)由来でリクエスト本体は未処理。再送は安全。
   - **リトライしない**:
     - HTTP **429** — 同時実行上限。現行どおり即 `HTTPException(429)` を送出
       (ユーザー起因の確定的失敗。1度も再送しない)。
     - `httpx.ReadTimeout` / `WriteTimeout` / `PoolTimeout` /その他
       `httpx.RequestError` — 応答途絶。runtime-api がコンテナを作成済みの
       可能性があり、再送すると同一会議に2体目のボットが入る。即 None を返す
       (現行の失敗経路と同じ)。
     - HTTP 400 等その他のステータス — 確定的失敗。現行どおりログして None。

4. **冪等性の方針**: runtime-api POST /containers に冪等キーは追加しない
   (クロスサービス変更で影響範囲が跳ねるため)。代わりに上記 3. の
   「未処理と確定できる失敗のみ再送」でスポーン重複ゼロを担保する。

5. **`retry.py` の拡張**: `with_retry` に
   `is_retryable: Callable[[Exception], bool] | None = None` を追加し、
   None のとき既存 `_is_retryable` を使う(後方互換。`webhook_delivery.py`
   の既存呼び出しは無変更・挙動不変)。

## 実装手順(how)

1. `retry.py`: `with_retry` にオプション引数 `is_retryable` を追加。
   ループ内の `_is_retryable(e)` を `(is_retryable or _is_retryable)(e)` に差し替え。
   他は一切変更しない。
2. `meetings.py`:
   - モジュールレベルに `class _SpawnRetryableError(Exception)` と
     述語 `_spawn_is_retryable(exc)`(`_SpawnRetryableError` /
     `httpx.ConnectError` / `httpx.ConnectTimeout` のとき True)を定義。
   - `_spawn_via_runtime_api` を次の構造に変更:
     - 内側に `async def _post_once()`: 現行の POST を実行し、
       201→`resp.json()` を返す / 429→`HTTPException(429, detail=...)` を送出
       (現行の detail 生成を踏襲) / 500・502・503・504→
       `_SpawnRetryableError(f"Runtime API returned {status}: {text}")` を送出 /
       その他ステータス→現行どおり `logger.error` して None を返す。
     - 外側: `await with_retry(_post_once, max_retries=3, base_delay=1.0,
       label=f"spawn meeting={metadata.get('meeting_id')}",
       is_retryable=_spawn_is_retryable)` を try で包む。
       `except HTTPException: raise`、
       `except (_SpawnRetryableError, httpx.RequestError) as e:` は
       `logger.error`(試行回数を含める)して None。
   - 関数シグネチャ・戻り値型・呼び出し元3箇所は変更しない。
3. `tests/test_bot_spawn_retry.py` を新規作成。様式は `test_meetings.py` に
   準拠(pytest.mark.asyncio + unittest.mock)。ただし `_spawn_via_runtime_api`
   を関数として直接呼ぶユニットテストとし、`meeting_api.meetings._get_httpx_client`
   を patch して `post` を `AsyncMock(side_effect=[...])` で駆動する。
   実スリープ排除のため `meeting_api.retry.asyncio.sleep` を AsyncMock で patch し、
   バックオフ検証はその call_args で行う。ケース一覧は検証契約 AT-001〜AT-009。
4. 検証契約の全項目を実行し、ベースライン比較(FP-002)を満たすこと。

## やらないこと(スコープ外)

- runtime-api 側の変更(冪等キー、リトライ、クリーンアップ強化)
- meeting.data へのリトライ履歴の記録、UI 表示、通知(ST-4 は別タスク)
- 起動成功後(入室後)の失敗のリトライ(ST-2/ST-3 の領域)
- `sweeps.py` への変更(st13 と衝突するため触らない。本タスクの対象に
  sweeps.py は含まれないことを裏取り済み)
- 契約を超える作り込み(サーキットブレーカ、メトリクス等)

## Why(実装者に渡さない)

- 監査 ST-1(深刻度: 高): runtime-api の瞬断(再起動・一時過負荷)1回で
  会議が即 FAILED 確定し、その会議の記録が丸ごと失われる。Phase 0 の
  「会議を取り逃さない」最小修正が本タスク。
- ReadTimeout をリトライ対象から外すのは、meeting-api 側 30 秒タイムアウトに
  対し K8s のイメージ pull やスケジューリングで作成が 30 秒を超え得るため。
  そこで再送すると会議に2体目のボットが可視参加し、信頼毀損が ST-1 の損失より
  重い。曖昧クラスの救済(存在確認つき再送)は必要になったら別タスクで。
- retry.py を拡張して再利用するのは、st8 以降の方針「既存の失敗経路と既存
  様式を踏襲する」に沿い、レビュー面積を最小にするため。
- 429 を即時伝播に保つのは、同時実行上限がユーザー向けの意図された制御であり、
  バックオフで隠すと上限超過の体感がタイムアウトに化けるため。
