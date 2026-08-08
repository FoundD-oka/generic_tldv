# Verification Contract — p11-finalizer-async-job

対象: `b547429..HEAD` の差分。テストは commit 済み clean tree に対して実行する。
meeting-api テストは python3.11 fresh venv
(`pip install -e libs/admin-models/ -e services/meeting-api/` + `pytest pytest-asyncio httpx`)。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | `services/meeting-api/meeting_api/callbacks.py` に同期呼び出し `await finalize_recording_master(` が残存0件 | `grep -c "await finalize_recording_master(" services/meeting-api/meeting_api/callbacks.py` が 0 | コマンド出力を `.hw/gates/p11-finalizer-async-job/` に保存 |
| AT-002 | bot_exit_callback の3分岐(exit_code==0 / stopping / else)すべてで `finalize_recording_master_job` が `run_all_tasks` より**先に** background_tasks に登録される | unit(BackgroundTasks をモックし add_task の呼び出し順を検証) | pytest ログ |
| AT-003 | ジョブの冪等ガード: Redis ロック非取得時(SET NX=False)、finalize_recording_master を呼ばず return する | unit | pytest ログ |
| AT-004 | ジョブは request セッションではなく自前セッション(async_session_local)を使用し、finalize の例外を呼び出し元へ伝播しない | unit(finalize を例外送出モックにしてもジョブ呼び出しが例外にならない) | pytest ログ |
| AT-005 | Redis 不在(get_redis が None)時は fail-open で finalize を実行する | unit | pytest ログ |
| AT-006 | duplicate-terminal 早期 return 経路(既 terminal + processed_at 済)ではジョブが登録されない | unit | pytest ログ |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | meeting-api テスト: ベースライン 591 passed / 11 skipped / 0 failed に対し新規 fail 0(既存テストの patch 先変更は同等セマンティクスでの更新のみ許容) | `python -m pytest services/meeting-api/tests` 全件実行しベースライン比較 | pytest サマリ全文 |
| FP-002 | exit コールバックの status 分類(Pack J / st15 admission 分類)・webhook スケジュール・chat 永続化の挙動が不変 | 既存 test_callbacks.py 系が green | pytest ログ |
| FP-003 | sweeps.py `_sweep_unfinalized_recordings` の finalize 経路が無変更(diff に sweeps.py の当該関数変更が含まれない) | `git diff b547429..HEAD -- services/meeting-api/meeting_api/sweeps.py` が空 | diff 出力 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | exit コールバックの応答生成経路(response return まで)に storage I/O・ffmpeg 実行が存在しない | AT-001 の grep + コード構造レビュー(Fable レビューで確認) | レビュー verdict |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p11-finalizer-async-job/` へ。`.hw/plans/` に後commit しない)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

なし(外部 API・ライブラリの最新仕様に依存しない。Starlette BackgroundTasks の
実行順序はリポジトリ内の既存利用実績 run_all_tasks で裏取り済み)。
