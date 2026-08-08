---
generated_by: fable
task_id: p11-finalizer-async-job
base-commit: b547429380effc854b6b271e087364a41d771b54
size: M
---

# ST-13: master生成を exit コールバックから外し非同期ジョブ化(冪等ガード付き)

## ゴール

依頼の文字通りの内容: 「finalize_recording_master が exit コールバック HTTP ハンドラ内で
同期実行され、呼び出し側(runtime-api)の10秒タイムアウトで永久再送・多重実行になる。
コールバックから外して非同期ジョブ化(冪等ガード付き)する」。

reframe: 不要(監査の改善方針をコード裏取りした結果、方針は正しい)。達成すべき成果を
明確化すると:
- bot exit コールバックが master 構築の所要時間に関係なく即時(storage/ffmpeg I/O なしで)
  2xx を返し、runtime-api の再送が構造的に発生しない。
- master 構築は meeting ごとに高々1実行(冪等ガード)で、失敗時は既存 sweep が回復する。
- 「Preparing audio...」の表示時間が「master 構築1回分の実時間」に収束する
  (現状: 再送嵐 + 多重実行 + status flip 遅延で数分〜十数分)。

## 現状分析(b547429 で現物確認済み)

- runtime-api `runtime_api/lifecycle.py:256` — コールバック POST は
  `httpx.AsyncClient(timeout=10)`。2xx が返らない限り pending レコードを Redis に残し、
  burst リトライ(:254-271)+ idle_loop sweep(:99-112)で**永久再送**する設計。
- meeting-api `meeting_api/callbacks.py` `bot_exit_callback`(:323-617)— 3分岐すべてで
  `await finalize_recording_master(meeting.id, db)` を**応答前に同期実行**:
  - exit_code==0 分岐 :380-388
  - stopping 分岐 :440-448
  - else(非stopping)分岐 :549-557
- 重複ガード `exit_callback_processed_at` は :585-592 の commit 後に初めて有効。
  **処理中(finalize 実行中)に届いた再送は素通り**し、finalize が並行多重実行される。
  finalize 自体の冪等(`recording_finalizer.py:441-447` の master HEAD チェック)は
  アップロード完了前の並行実行を防げない。
- 長い会議では finalize(全チャンクDL + ffmpeg duration注入 + UL)が10秒を超え、
  クライアント側タイムアウト → 再送 → 多重実行、かつ status flip(:389/:449/:558)が
  finalize 完了まで遅延 → dashboard は非terminal のまま「準備中」。
- 既存の耐久リトライ: `meeting_api/sweeps.py` `_sweep_unfinalized_recordings`(:504-680)が
  terminal かつ playback_url 欠落の meeting に対し finalize を再実行する(:666)。
  → ジョブが失敗しても回復経路は既にある。
- 後続タスク: `background_tasks.add_task(run_all_tasks, meeting.id)`(callbacks.py:610)。
  final transcription は queue のみ(post_meeting.py:408-418)で重い処理は sweep 側
  (sweeps.py:683-)。Starlette の BackgroundTasks は**応答送信後に追加順で直列実行**
  されるため、finalize ジョブを run_all_tasks の前に add_task すれば
  「master 構築 → 文字起こし queue」の順序は現状と同じに保てる。
- GitNexus impact: `finalize_recording_master` の caller は `bot_exit_callback` と
  `_sweep_unfinalized_recordings` の2つのみ。blast radius は限定的。
- UI 側の前提: `/recordings/{id}/master` は master 未構築時 404 を返し
  (recordings.py:687-688)、dashboard は 404 → 「finalizing」表示を実装済み
  (dashboard `src/app/meetings/[id]/page.tsx:210,219`)。従来も finalize 失敗時は
  「status=completed かつ master 不在」状態が発生しており(callbacks.py の
  try/except が失敗を握り潰して status 更新を続行するため)、この状態は既知・処理済み。

## 仮説と確信度(反証を優先して確認した結果)

1. 「10s timeout → 再送 → 多重実行が Preparing audio 長期化の主因」— 確信度: 高。
   裏取り: timeout=10(lifecycle.py:256)、pending保持+永久再送(:99-112, :273-279)、
   processed_at ガードが応答直前 commit であること(callbacks.py:585-592)。
   覆る条件: 本番の finalize が常に10秒未満なら効果は限定的(その場合も構造是正として有効)。
2. 「BackgroundTasks は応答送信後・追加順に直列実行」— 確信度: 高(Starlette 仕様)。
   注意: FastAPI 0.106+ では yield 依存(DB セッション)は background 実行前に閉じる。
   → ジョブは request の db を使わず自前セッション必須(run_all_tasks と同じパターン、
   post_meeting.py:399)。
3. 「status flip 先行でも UI は finalizing 表示で壊れない」— 確信度: 中〜高。
   裏取り: 上記 UI 前提。覆る条件: 404 を error 扱いする経路が detail page にあった場合。
   実装時に page.tsx の master 404 ハンドリングを目視確認し、error 扱いなら planner に
   差し戻す(UI 変更はこの契約のスコープ外)。

## How

### 1. `meeting_api/recording_finalizer.py` — ジョブラッパー追加

`async def finalize_recording_master_job(meeting_id: int) -> None` を追加:

- 冪等ガード(Redis ロック):
  - 関数内 import で `from .meetings import get_redis`(モジュール循環回避。
    既存の関数内 import パターン :585 に倣う)。
  - key `finalizer:master:{meeting_id}`、value は uuid4().hex トークン、
    `SET key token NX EX 900`。
  - 取得失敗(既保持)→ info ログして即 return(保持者が完了させる。失敗時は
    `_sweep_unfinalized_recordings` が耐久リトライ)。
  - redis が None、または SET が例外 → warning ログして**ロックなしで続行**
    (fail-open。storage 側の master HEAD チェックが下段ガード)。
- 本体: `from .database import async_session_local` を用い
  `async with async_session_local() as db: await finalize_recording_master(meeting_id, db)`。
- 例外は log.error で吸収し伝播しない(sweep が回復経路)。
- finally でロック解放: GET の値が自トークンと一致する場合のみ DELETE(best-effort)。

### 2. `meeting_api/callbacks.py` — 同期呼び出しの除去とジョブ登録

- :380-388、:440-448、:549-557 の3つの try/`await finalize_recording_master`/except
  ブロックを削除。
- :610 の `background_tasks.add_task(run_all_tasks, meeting.id)` の**直前**に
  `background_tasks.add_task(finalize_recording_master_job, meeting.id)` を1回追加
  (3分岐の共通合流点。登録順により finalize → run_all_tasks の直列順序を維持)。
- duplicate-terminal 早期 return 経路(:345-362)にはジョブを追加**しない**。
- import(:36)を `finalize_recording_master` → `finalize_recording_master_job` に変更。

### 3. コメント・docstring の整合

- recording_finalizer.py ヘッダ(:18-22)と `finalize_recording_master` docstring
  (:549-551)、callbacks.py の Pack U.7 コメント(「BEFORE status flip」)を新契約に改訂:
  「master は terminal status flip 後に非同期構築。未構築の間は
  /recordings/{id}/master が 404 を返し dashboard が finalizing を表示する」。

### 4. 変更しないもの

- sweeps.py の finalize 呼び出し(:666)— 既存の耐久経路のまま(FOR UPDATE
  skip_locked 内)。sweep とジョブの稀な並行は storage 冪等で実害なし
  (advisory: sweep もジョブラッパー経由にする改善は将来タスク)。
- runtime-api は一切変更しない(応答が速くなれば再送は自然に止まる)。
- `_persist_chat_messages_from_redis`、webhook、分類ロジックは不変。

### 5. テスト

- `tests/test_callbacks.py` の既存 patch(:102, :133 の
  `meeting_api.callbacks.finalize_recording_master`)を新関数名に更新し、
  アサーションを「ハンドラ内で inline await されない + background_tasks に
  run_all_tasks より先に登録される」に変更。
- 新規(test_recording_finalizer_job.py など):
  (a) ロック取得成功時に自前セッションで finalize が呼ばれる、
  (b) ロック非取得(SET NX が False)時は finalize を呼ばず return、
  (c) finalize 例外時もジョブが例外を伝播しない、
  (d) redis 不在(get_redis が None)時は fail-open で finalize 実行、
  (e) 3分岐(exit_code==0 / stopping / else)すべてでジョブ登録、
  (f) duplicate-terminal 早期 return でジョブ未登録。
- 環境: python3.11 fresh venv(/tmp の venv は劣化するため作り直す)。
  ベースライン: 591 passed / 11 skipped / 0 failed。

## Why(実装者に渡さない)

- 「非同期ジョブ化」に Celery 等の新基盤を入れない理由: このリポジトリの既存資産は
  BackgroundTasks(run_all_tasks)+ 周期 sweep(耐久リトライ)の二段構えで、
  final transcription が既にこのパターンで動いている。同型に揃えるのが最小リスク。
  新規キュー基盤は D1-D7 配備境界にも触れかねずスコープ外。
- ロックを finalize_recording_master 本体でなくラッパーに置く理由: sweep 経路の
  トランザクション構造(FOR UPDATE + 呼び出し側 rollback)を触らないため。
  sweep 側は skip_locked で複製間相互排他済み(#45)であり、残る並行窓は
  「ジョブ実行中に sweep が同一 meeting を拾う」だけ。cutoff 年齢条件があるため稀で、
  結果も同一バイトの二重アップロードに留まる。
- fail-open(redis 不在時にロックなし実行)の理由: ロック取得不能で finalize を
  止めると、redis 障害時に再生が永久に準備中になる。多重実行の害(無駄な計算)より
  再生不能の害が大きい。
