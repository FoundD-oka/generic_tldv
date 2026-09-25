# Verification Contract — meeting-list-stability

対象: base-commit `67ea03210c2de4c8723780402d302948b138d939` .. 実装 HEAD。証拠はすべてコマンド出力または差分で示す。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | collector `GET /meetings` は引数なしで slim(summary `data`)・最大50件・`has_more` を返す | unit (pytest, 既存 collector list fixture 方式) | 51件 fixture で `len(meetings)==50`, `has_more is True`, 各 `data` が `meeting_list_data_summary(m.data)` と等価 |
| AT-002 | `?include=data` で従来 full data、`limit/offset/status/platform` が反映される | unit (pytest) | `include=data` の `data` が `MeetingResponse.model_validate(m).data` と等価、`limit=2&offset=1` で該当件、`limit=101` は 422 |
| AT-003 | summary 実装は単一 | unit + source check | `meetings._meeting_list_data_summary is meeting_summary.meeting_list_data_summary` が True。`rg -n "participants_count" services/meeting-api/meeting_api` のヒットが `meeting_summary.py` の1箇所 |
| AT-004 | SDK `get_meetings()` は List を返し、引数なしではクエリを送らない。`get_meetings_page` は `has_more` を返し、指定引数のみ params 化、`include_data=True` で `include=data` | unit (pytest, `requests.Session.request` mock) | 3ケースの `params` 引数と戻り値型の assert |
| AT-005 | SDK `get_meeting_by_id` は `has_more` を追ってページ走査し full data の一致を返す | unit (pytest) | 2ページ目にある meeting を返し、呼び出し params に `include=data` と `offset=100` を含む |
| AT-006 | proxy `GET /api/vexa/meetings` は上流 status を保持: 500→500, 401→401, timeout→504, network error→502。ボディに `error` と `retryable` | unit (vitest) | 4ケースで `response.status` と body を assert。`fetch` 呼び出し1回、URL が `/bots?` で始まる |
| AT-007 | store `fetchMeetings` 失敗時に `meetings` を保持し `error`/`errorRetryable` を設定。silent 失敗時は state 不変 | unit (vitest) | 事前 `meetings` 2件 → 失敗後も2件、`errorRetryable` が 502 で true / 404 で false |
| AT-008 | 一覧 hook は mount で fetch 1回、status/platform 変更で +1、検索は 300ms 後に1回、refresh で +1 | unit (vitest + testing-library `renderHook`, fake timers) | `fetchMeetings` mock の呼び出し回数と引数 |
| AT-009 | sweep は storage I/O 前に `rollback` し、書き込み前に `refresh(with_for_update=True)`、commit 後に finalize | unit (pytest, 呼び出し順記録) | 順序配列が `rollback < list_objects_bounded < refresh < commit < finalize` |
| AT-010 | sweep 書き込み相は session_uid で冪等マージ | unit (pytest) | refresh 側で同 session が既に存在する場合 `commit` 未呼び出し・recordings 長さ不変 |
| AT-011 | finalizer は `to_thread` 実行時点で tx 終了済み、write 相で再ロックし id 一致で更新、commit 1回 | unit (pytest) | `_finalize_one_media_file_sync` 呼び出し時に `rollback` 済み、data の順序を入れ替えた mock でも正しい media_file が更新、`commit` 1回 |
| AT-012 | UI: `error` かつ `meetings.length>0` で一覧を残しバナー+再試行、0件で `ErrorState` | unit (vitest) または source check | 条件分岐とバナー描画のテスト、または `page.tsx` 差分に両分岐が存在 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | `/bots/status` fallback で 200 空配列を返さない | unit (vitest) | 上流 500 時に `fetch` が `/bots/status` を含む URL で呼ばれない、status≠200 |
| FP-002 | 履歴→active-only の意味変化なし(proxy が status を注入しない、collector 既定に status 条件なし) | unit + source check | 既存 `test_vexa_sensitive_proxy_auth` の URL 一致テストが無変更で緑、collector 既定 SQL に status where なし |
| FP-003 | 既存 race / single-flight / sweep テストが無変更で緑 | 既存 suite | `test_meetings_store_refresh_race.test.ts`, `test_single_flight_polling.test.ts`, `test_sweeps_unfinalized_recordings.py` の差分が0行 |
| FP-004 | 既定一覧のサイズ | unit (pytest) | 41件×`data` 500KB 相当の fixture で既定レスポンス JSON < 100KB、`include=data` で > 1MB |
| FP-005 | テスト削除/skip/緩和なし | source check | `git diff base..HEAD --stat -- '**/tests/**'` に削除行のみのファイルなし、追加行に `skip|xfail|\.only|\.skip` が0 |
| FP-006 | Redis lock / fail-open / never-raise(`finalize_recording_master_job`)無変更 | source check | 当該関数の差分0行 |
| FP-007 | api-gateway の forward 挙動無変更 | source check | `services/api-gateway/main.py` の差分0行 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | 対象 suite 全緑 | command | `pytest services/meeting-api/tests -q`, `npx vitest run`(dashboard), SDK pytest の終了コード0 |
| NFT-002 | 型/静的検査 | command | dashboard `npx tsc --noEmit` 終了コード0(`npm run lint` が定義済みなら同様) |
| NFT-003 | `.hw/verify.sh` と pr-ready-gate | command | 両方の終了コード0 |
| NFT-004 | (任意・手動)実 Postgres で `SET idle_in_transaction_session_timeout='2s'` + 3秒遅延 storage stub で sweep が切断されない | manual | evidence pack にログ添付。ゲート条件ではない |

## KPI Checks

`kpi-backcast-roadmap.md` なし。適用外。

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

外部ライブラリ/API の最新挙動に依存する判断なし。適用外。
