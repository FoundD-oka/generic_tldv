---
generated_by: fable
task_id: p12-final-transcription-worker
base-commit: 2da98bbab812d4d12941a47b126645809444fb13
size: M
---

# ST-6: final-transcription sweep を独立ワーカーループへ分離(P1-2 第1弾)

## ゴール

依頼の文字通りの内容: 「全 sweep が単一タスク直列で、長時間会議1件の文字起こし
(リース最大35分)中に全ユーザーの救済・Drive出力等が停止する。final-transcription を
独立ワーカー化する」。

reframe: 不要(監査 ST-6 の指摘・改善方針をコード裏取りした結果、両方正しい)。
達成すべき成果を明確化すると:
- final-transcription の実行時間(1件最大 DEFERRED_TRANSCRIPTION_TIMEOUT_SECONDS=1680秒、
  1イテレーション最大10件)が、stale-stopping 救済 / aggregation retry / unfinalized
  recordings 修復 / drive export / voiceprint retention / container-stop outbox の
  実行周期に一切影響しなくなる。
- final-transcription 自体の処理セマンティクス(選定クエリ・リース・リトライ上限・
  skip_locked)は一切変えない。変わるのは「どのループから呼ばれるか」だけ。

## 現状分析(2da98bb で現物確認済み)

- `services/meeting-api/meeting_api/sweeps.py` `start_sweeps`(:1025-1148)が唯一の
  周期ループ。1イテレーション内で7種の sweep を **直列に await** し、その4番目が
  `_sweep_final_transcription_jobs`(:1091)。
- `_sweep_final_transcription_jobs`(:683-843)は最大 `FINAL_TRANSCRIPTION_SWEEP_LIMIT`
  (=10)件の meeting を直列処理し、1件ごとに `run_deferred_transcription` を await。
  この中で STT 呼び出し(`DEFERRED_TRANSCRIPTION_TIMEOUT_SECONDS` 既定1680秒 =28分、
  `final_transcription.py:883`)まで待つため、**最悪ケースで1イテレーションが数時間**。
  その間、後続の drive export・voiceprint retention・container-stop と、次イテレーションの
  stale-stopping 救済・unfinalized 修復がすべて停止する(監査 ST-6 の実害どおり)。
- 並行実行の安全性は既に確保済み: 行選択は `with_for_update(skip_locked=True)`
  (:732、PR #45)、run 単位の排他は run_id + lease(`final_transcription.py:65-66,
  1160-1179`)+ ハートビート(:87-107)。**ワーカーを別タスク化しても二重実行は
  この既存機構が防ぐ**(新規の排他機構は不要)。
- ループの env ガードは `_sweeps_enabled()`(:1020-1022、`MEETING_API_SWEEPS_ENABLED`)。
  レプリカ単位で idle sweep を止める運用ノブであり、final-transcription も同じ
  「idle 系」なので新ワーカーも同じガードを共用するのが運用整合的。
- 起動配線は `main.py:332-333` の `asyncio.create_task(start_sweeps(async_session_local))`。
  shutdown(:405-432)は sweep ループを明示停止していない(現行の既知挙動。今回も同型)。
- P1-1(PR #52/#53)は callbacks.py / recording_finalizer.py / recordings.py のみ変更。
  **sweeps.py と final_transcription.py は b547429 以降無変更**で、監査の行番号は有効。
- 既存テスト: `test_final_transcription.py:469-` と `test_sweeps_skip_locked.py:137` が
  `_sweep_final_transcription_jobs` を直接呼ぶ。関数を sweeps.py に残し signature を
  変えなければ既存テストは無修正で通る。
- ドライブ出力の順序依存なし: `drive_export.status=queued` は final transcription 成功時に
  初めて書かれる(`queue_drive_export_if_needed`)ため、ループ分離で順序保証が失われても
  拾い漏れは発生しない(次周期で拾う)。

## 仮説と確信度(反証を優先して確認した結果)

1. 「別 asyncio タスクに分離すれば他 sweep の停止は解消する」— 確信度: 高。
   ブロックの原因は単一タスク内の直列 await であり、重い処理自体は
   `asyncio.to_thread`(ffmpeg)と httpx await でイベントループを塞がない
   (`final_transcription.py:1335,885-905`)。覆る条件: イベントループを塞ぐ同期処理が
   経路上にあった場合(bytes の multipart 構築等)— それは ST-9(次タスク)の領分で、
   本タスクの効果を消すものではない。
2. 「並行化しても二重実行・整合性の問題は出ない」— 確信度: 高。
   skip_locked + run_id/lease で防御済み(上記)。sweep ループと新ワーカーが同一
   meeting を同時に触る経路は分離後は存在しない(final transcription を呼ぶのは
   新ワーカーのみになるため)。覆る条件: なし(構造的に排除)。
3. 「DB コネクション枯渇は起きない」— 確信度: 中〜高。各 sweep はイテレーションごとに
   session factory から新規セッションを取り、並行度は +1 タスク分のみ。プールは
   DB_POOL_SIZE=20 + MAX_OVERFLOW=20(helm values.yaml:147-153)。
   覆る条件: プール縮小運用をしている環境。既定構成では余裕。

## How

### 1. `meeting_api/sweeps.py` — 独立ワーカーループの追加と start_sweeps からの除去

- モジュール定数を追加:
  `FINAL_TRANSCRIPTION_POLL_INTERVAL = int(os.getenv("FINAL_TRANSCRIPTION_POLL_INTERVAL_SECONDS", "60"))`
  (現行実効周期 = STALE_STOPPING_POLL_INTERVAL 60秒 と同じ既定値。挙動据え置き)。
- 可観測性カウンタを追加(既存 `sweep_iterations` / `sweep_last_iteration_at`
  :58-59 と同型): `final_transcription_worker_iterations: int`、
  `final_transcription_worker_last_iteration_at: float`。
- `async def start_final_transcription_worker(db_session_factory) -> None` を追加。
  `start_sweeps`(:1025-)と同型のループ:
  - 冒頭で `_sweeps_enabled()` を判定。false なら warning ログして即 return
    (start_sweeps :1042-1048 と同文体。「final-transcription worker」と明示)。
  - 専用 stop イベント `_ft_stop_event`(モジュールグローバル、start_sweeps の
    `_stop_event` とは**別変数**。共有すると片方の再代入で他方が壊れる)。
  - ループ本体: カウンタ更新 → `try: await _sweep_final_transcription_jobs(
    db_session_factory)` → 件数>0 なら info ログ → `except Exception: logger.error(
    ..., exc_info=True)`(ループ継続)→ `asyncio.wait_for(_ft_stop_event.wait(),
    timeout=FINAL_TRANSCRIPTION_POLL_INTERVAL)` で待機。
- `async def stop_final_transcription_worker() -> None` を追加(stop_sweeps :1151- と同型)。
- `start_sweeps` のループ本体から final-transcription ブロック(:1090-1098)を**削除**。
  docstring(:1030-1037)の「Issue #2: final transcription replacement」行を
  「→ start_final_transcription_worker(独立ループ)へ分離」に改訂。
- `_sweep_final_transcription_jobs` 本体(:683-843)は**一切変更しない**
  (関数の場所・signature・セマンティクスすべて現状維持)。

### 2. `meeting_api/main.py` — ワーカーの起動配線

- startup(:332-334)の `start_sweeps` 起動直後に追加:
  ```python
  from .sweeps import start_final_transcription_worker
  asyncio.create_task(start_final_transcription_worker(async_session_local))
  logger.info("Final-transcription worker loop started (ST-6: sweeps から分離)")
  ```
  (既存 start_sweeps と同じ plain create_task。_spawn_supervised_task はコレクタ専用の
  ため使わない。ループ内 try/except が実質の crash 耐性)。

### 3. 変更しないもの

- `final_transcription.py` 全体(ST-9/ST-10 の次タスク p12-audio-disk-streaming が
  触るため、衝突回避として本タスクでは変更禁止)。
- `_sweep_drive_export_jobs` ほか他の sweep(start_sweeps 側に残す)。
- compose / helm(新規必須 env なし。FINAL_TRANSCRIPTION_POLL_INTERVAL_SECONDS は
  既定値で挙動不変のため配線は任意 → 契約外)。
- shutdown ハンドラ(既存 start_sweeps も明示停止していない。揃える。
  advisory: 両ループの graceful stop 配線は将来タスク)。

### 4. テスト(新規 `tests/test_final_transcription_worker.py`)

既存 sweeps テストの流儀(AsyncMock + patch、`test_sweeps_skip_locked.py` 参照)で:

- (a) start_sweeps を1イテレーション実行しても `_sweep_final_transcription_jobs` が
  呼ばれない(patch して call_count == 0。他 sweep 関数も patch して1周後に
  stop_sweeps する既存パターン)。
- (b) start_final_transcription_worker を1イテレーション実行すると
  `_sweep_final_transcription_jobs` が db_session_factory 付きで呼ばれる。
- (c) `MEETING_API_SWEEPS_ENABLED=false` で start_final_transcription_worker が
  sweep を1度も呼ばず即 return する。
- (d) `_sweep_final_transcription_jobs` が例外を送出してもワーカーループが継続し、
  次イテレーションで再度呼ばれる(2周させて call_count == 2)。
- (e) 並行性の実証(本タスクの本質要求): `_sweep_final_transcription_jobs` を
  「asyncio.Event がセットされるまで待ち続ける」AsyncMock に patch し、
  worker タスクと start_sweeps タスクを並行起動。worker が待機中でも
  start_sweeps 側の `_sweep_stale_stopping` が呼ばれることを assert
  (タイムアウト付き。従来構造では不成立だった性質)。
- (f) stop_final_transcription_worker でループが終了する。

環境: python3.11 fresh venv(`pip install -e libs/admin-models/ -e services/meeting-api/`
+ `pytest pytest-asyncio httpx`。/tmp の venv は劣化するため作り直す)。
ベースライン: 631 passed / 19 skipped / 0 failed(2026-08-09 実測、8bc0374。詳細は契約の改訂履歴参照)。

## Why(実装者に渡さない)

- 別プロセス/別コンテナでなく同一プロセス内の別 asyncio タスクにする理由:
  ST-6 の実害は「単一タスク直列」であり、タスク分離だけで解消する。プロセス分離
  (専用 Deployment 化)はメモリ隔離(ST-9 の OOM 道連れ)にも効くが、compose /
  helm / lite(supervisord)の3配備すべてに新サービス追加が必要で差分が跳ね上がる。
  ST-9 はストリーミング化(次タスク)でメモリ自体を定数化する方針のため、
  プロセス分離の便益は残らない。CLAUDE.md の「契約は最低合格ライン。通ったら止める」
  に従い最小構造変更を選ぶ。
- `_sweep_final_transcription_jobs` を sweeps.py に残す理由: 既存テスト2ファイルが
  `sweeps._sweep_final_transcription_jobs` を直接参照しており、移動すると
  テスト差分が膨らみレビューコストが上がる。関数の責務は不変なので移動は純コスト。
- env ガードを MEETING_API_SWEEPS_ENABLED 共用にする理由: このノブの運用意図は
  「このレプリカで idle 系バックグラウンド処理を止める」(PR #45)。final-transcription
  だけ動き続けると、ノブでレプリカを退役させる運用が壊れる。
- ST-6 を P1-2 の先頭に置く理由: 5項目中で被害範囲が最大(全ユーザーの救済系停止)
  かつ、他タスク(final_transcription.py 内部を触る ST-9/10)とファイルが分離できる。
  逆順にすると同一ファイルの連続改変で rebase/レビューが重くなる。
