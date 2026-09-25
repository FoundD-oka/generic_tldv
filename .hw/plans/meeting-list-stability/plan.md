---
generated_by: fable
task_id: meeting-list-stability
base-commit: 67ea03210c2de4c8723780402d302948b138d939
size: M
runtime: inline
---

# meeting-list-stability 実装プラン

## ゴール

- 文字通りの依頼: (A) 一覧APIをslim+pagedに統一、(B) dashboard proxyのfallback廃止と初回二重fetch解消、
  (C) unfinalized sweep / finalizer のトランザクション分割。
- 再設計: reframe なし(クライアント合意済み)。4修正を1タスク、実装順 A→B→C。各段階で対象suiteを緑にしてからcommit。

## 前提(確定事実・実装者は再調査不要)

- `collector/endpoints.py:get_meetings` は limit/offset 任意・デフォルト全件・full JSONB(41件24MB)。
- `meetings.py:list_user_bots` は limit=50 / limit+1 / has_more / `_meeting_list_data_summary` / `?include=data` 済み。
- api-gateway `GET /meetings` はクエリをそのまま collector へ forward(変更不要)。
- Postgres は idle-in-transaction 60秒で切断。sweep/finalizer は I/O 中に tx と row lock を保持している。

## A. 一覧契約(API + SDK)

### A1 summary の単一実装化
- 新規 `services/meeting-api/meeting_api/meeting_summary.py` に `meeting_list_data_summary(d)` を置く
  (本体は現 `meetings._meeting_list_data_summary` をそのまま移動。ロジック変更禁止)。
- `meetings.py` は `from .meeting_summary import meeting_list_data_summary as _meeting_list_data_summary`
  で置換し、既存参照名 `_meeting_list_data_summary` を維持する。関数本体は repo 内に1つだけ。
- `collector/endpoints.py` は `from ..meeting_summary import meeting_list_data_summary` を使う
  (`..meetings` を import しない: `meetings` → `.collector.auth` の循環を避ける)。

### A2 collector `GET /meetings`
- シグネチャ: `limit: int = Query(50, ge=1, le=100)`, `offset: int = Query(0, ge=0)`,
  `status`, `platform` は現状維持, 追加 `include: Optional[str] = Query(None)`。
- クエリ: `.order_by(created_at desc).offset(offset).limit(limit + 1)` → `has_more = len(rows) > limit` → `rows[:limit]`。
- `include == "data"` のときのみ既存どおり `MeetingResponse.model_validate(m)`(full JSONB)。
  それ以外は同じ列で `data` を `meeting_list_data_summary(m.data)` に差し替えた item を返す。
- OpenAPI description に「既定 limit=50(最大100)、`include=data` で従来の full data」を明記。

### A3 schemas
- `schemas.py` の `MeetingListResponse` に `has_more: bool = False` を追加。
- `MeetingResponse.data` の型を確認する。dict 互換(`Optional[Dict[str, Any]]` 等)なら追加変更なし。
  厳密モデル型なら `class MeetingListItem(MeetingResponse)` で `data: Optional[Dict[str, Any]] = None` に緩め、
  `MeetingListResponse.meetings: List[MeetingListItem]` に変更する。`data` の redaction serializer は継承で維持。
- api-gateway `main.py` はコード変更なし(Response passthrough)。

### A4 Python SDK(`packages/vexa-client/vexa_client/vexa.py`)
- 新規 `get_meetings_page(limit=None, offset=None, status=None, platform=None, include_data=False) -> Dict[str, Any]`
  を追加。`None` でない引数のみ `params` に載せ、`include_data=True` のとき `params["include"]="data"`。
  戻り値は `{"meetings": [...], "has_more": bool}`(`data` 欠落時の `{}` 補完は現行どおり)。
- `get_meetings(...)` は同じキーワード引数を受け、`get_meetings_page(...)["meetings"]` を返す(戻り値 List 維持、
  引数なし呼び出しは従来どおり動作=サーバ既定 slim 50件)。docstring を summary 形状に更新。
- `get_meeting_by_id` は `get_meetings_page(limit=100, offset=n, include_data=True)` を `has_more` が False になるまで
  走査し最初の一致を返す(full data の意味を維持)。

### A5 テスト(A段階)
- collector: 既存 list テストと同じ fixture 方式で「既定 slim+50+has_more」「`include=data` で full」「limit/offset/status/platform」
  「summary 関数が単一オブジェクトである(`meetings._meeting_list_data_summary is meeting_summary.meeting_list_data_summary`)」。
- SDK: `requests.Session.request` を mock し、params の組み立て・List 戻り値・`get_meeting_by_id` のページ走査を検証。

## B. Dashboard(proxy エラー保持 + 初回1回fetch)

### B1 `route.ts` の `meetings` 分岐
- `/bots/status` fallback ブロックを削除。`/bots` 1回のみ呼ぶ(クエリ組み立て・env key fallback は現状維持)。
- 上流 2xx: 現状どおり `{ meetings, has_more }` を 200 で返す。
- 上流 非2xx: ボディを text で読み JSON なら parse。`{ error: <detail か text>, upstream_status: <n>, retryable: <n>>=500||n===429 }`
  を **上流と同じ status** で返す(`Cache-Control: no-store`)。
- 例外: `AbortError`(5s timeout)→ 504 `{ error: "Request timeout", retryable: true }`、その他 → 502
  `{ error: "Failed to connect to API: <msg>", retryable: true }`。空配列 200 は返さない。

### B2 `meetings-store.ts`
- state に `errorRetryable: boolean`(初期 false)を追加。`fetchMeetings` 非silent失敗時は
  `meetings` を **変更せず**、`error` と `errorRetryable = isTransientRefreshError(error) || status in {429,500..599}` を set。
  402 分岐・silent 分岐・generation ガードは現状維持。`clearError` で `errorRetryable=false`。

### B3 effects の一本化(`page.tsx`)
- `src/hooks/use-meeting-list-query.ts` を新設し、`filtersRef` / `applyFilters` / mount+filter effect / 300ms debounce /
  `handleRefresh` をそこへ移す。effect は「`[statusFilter, platformFilter]` 依存の1本」だけにし、mount 時にこれが1回走る。
  `useEffect(() => { fetchMeetings(); }, [fetchMeetings])` は削除。
- 戻り値: `{ searchQuery, setSearch(handleSearchChange), platformFilter, setPlatformFilter, statusFilter, setStatusFilter, refresh }`。
  page.tsx は hook を呼ぶだけに変更。silent polling(`startSingleFlightPolling`)と infinite scroll は page.tsx に残す。
- エラー表示: `error && meetings.length > 0` のときは一覧を残したままインラインバナー(メッセージ + 再試行ボタン)、
  `meetings.length === 0` のときは既存 `ErrorState`。`onRetry` は `() => fetchMeetings()` で包む(引数混入防止)。

### B4 テスト(B段階)
- proxy: 上流 500/401/timeout/network error の各ケースで status・`error`・`retryable` を検証し、fetch が1回のみ・
  `/bots/status` を呼ばないことを検証。既存 `test_vexa_sensitive_proxy_auth.test.ts` は無変更で通す。
- store: 失敗時に `meetings` 保持・`errorRetryable` の値、silent 失敗時に state 不変。
- hook: `@testing-library/react`(devDependency に無ければ追加、jsdom 環境)で `renderHook` し、mount で fetch 1回、
  status 変更で +1、search 入力は 300ms 後に1回、refresh で +1。既存 race / single-flight テストは無変更で通す。

## C. トランザクション分割(sweep / finalizer)

### C1 `_sweep_unfinalized_recordings`
- 候補 id の SELECT は現状維持。各 meeting について以下の3相に分割:
  1. **claim/read tx**: `FOR UPDATE SKIP LOCKED` で行を取り、`meeting_id, user_id, data(dict copy)`,
     sessions を `(session_uid, session_start_time)` にスナップショット。`has_unfinalized_jsonb` と欠落 session を計算後、
     `await db.rollback()` でロックと tx を即解放(commit はしない)。
  2. **I/O(tx なし)**: storage list / chunk key 解析 / recovered recording 構築(既存ロジック、DB 触らず)。
  3. **write tx**: `await db.refresh(meeting, with_for_update=True)` で再ロック・再読込し、現在の `data.recordings` に
     `session_uid` が無いものだけ append(冪等マージ)、`flag_modified` → `commit`。追加が無ければ commit しない。
- その後 `finalize_recording_master(meeting_id, db)`(引数は int のスナップショット)。例外時 `rollback` は現状維持。
- rollback/commit 後は ORM 属性へアクセスしない(expire 対策。スナップショット値のみ使う)。
- `UNFINALIZED_RECORDINGS_LIMIT`・SKIP LOCKED・storage singleton・戻り値 `swept` の意味は変えない。

### C2 `finalize_recording_master` と `recover_recordings_jsonb_from_storage`
- finalizer: (R) meeting を読み `rec_list` を deep copy → `await db.rollback()`(tx 終了)。
  (I/O) 各 media file の `_finalize_one_media_file_sync` を `to_thread` で実行し `[(recording_id, media_file_id, master_key)]` を集める。
  (W) `db.refresh(meeting, with_for_update=True)` で再ロック・再読込し、`recording.id` と `media_file.id`(id 欠落時は
  元 `storage_path`)で対象を特定して `storage_path/finalized_at/finalized_by/is_final` と `playback_url` を書き、1回 commit。
  idempotent(`storage_path == master_key`)/no-fallback(chunk 0件は None)/raise 契約は維持。
- recover: sessions 読込後に `rollback`、storage I/O、`refresh(with_for_update=True)` → session_uid 冪等マージ → commit。
- `finalize_recording_master_job` の Redis lock・fail-open・never-raise は変更しない。

### C3 テスト(C段階)
- 既存 `test_sweeps_unfinalized_recordings.py` 4件は無変更で通す(execute 呼び出し列は3件のまま)。
- 追加: 呼び出し順記録で「`rollback` → `list_objects_bounded` → `refresh(with_for_update=True)` → `commit` → `finalize`」、
  refresh 後の data に同 session が既にある場合は重複 append しない、finalizer は `to_thread` 実行時点で tx 終了済み
  (rollback 済み)かつ write 相で id 一致により更新、`db.commit` 1回。

## 検証手順

- meeting-api: `services/meeting-api` で py3.11 venv → `pytest tests -q`(最低限 `tests/test_sweeps_unfinalized_recordings.py`,
  finalizer/collector list 関連, 新規テスト)。
- dashboard: `services/dashboard` で `npx vitest run` と `npx tsc --noEmit`(あれば `npm run lint`)。
- SDK: `packages/vexa-client` の既存 test runner(pytest)。
- `.hw/verify.sh` が定義する既存入口を最後に実行し、`bash .hw/hooks/pr-ready-gate.sh meeting-list-stability`。

## 制約

- テスト削除・skip/xfail・期待値緩和禁止。`.env`/deploy 変更なし。summary 実装の重複禁止。
- 履歴一覧を active-only に変える挙動(status 注入等)禁止。変更は上記に列挙した範囲に限定。

## Why(実装者に渡さない)

- 24MB 一覧は帯域と DoS 面の問題で、`list_user_bots` に既に正解があるため collector を同型に寄せる。summary を共有モジュールに
  出すのは重複禁止と循環 import 回避の両立のため。
- fallback→200 空配列は「履歴消失」を成功に見せる沈黙障害。上流 status を保持し UI がデータを残して再試行を示すのが正しい失敗。
- 初回二重 fetch は mount effect と filter effect の重複。hook 抽出はテスト可能性のため(page.tsx 直接 render は依存 mock が重い)。
- idle-in-transaction 60秒切断は I/O 中の tx 保持が原因。rollback で read tx を閉じ、write 相で再ロック+再読込+冪等マージにすると
  既存 mock テストの execute 列を崩さず(refresh は mock で吸収)ロスト更新も抑えられる。Redis lock 追加はスコープ外。
- S/M/L=M: 要求は明確だが `MeetingResponse.data` 型・finalizer 既存テスト形・jsdom 有無が未読で残る。
