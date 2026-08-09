# Verification Contract — p12-final-transcription-worker

対象: `2da98bb..HEAD` の差分。テストは commit 済み clean tree に対して実行する。
meeting-api テストは python3.11 fresh venv
(`pip install -e libs/admin-models/ -e services/meeting-api/` + `pytest pytest-asyncio httpx`)。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | `start_sweeps` のループ本体から final-transcription 実行が除去されている | unit(他 sweep を patch して start_sweeps を1イテレーション実行し `_sweep_final_transcription_jobs` の call_count == 0)+ `grep -n "_sweep_final_transcription_jobs" services/meeting-api/meeting_api/sweeps.py` の await 呼び出し出現が新ワーカーループ内の1箇所のみ | pytest ログ + grep 出力を `.hw/gates/p12-final-transcription-worker/` に保存 |
| AT-002 | `start_final_transcription_worker` が周期的に `_sweep_final_transcription_jobs` を db_session_factory 付きで実行する | unit(1イテレーション検証) | pytest ログ |
| AT-003 | `MEETING_API_SWEEPS_ENABLED=false` のとき新ワーカーは sweep を1度も呼ばず return する | unit | pytest ログ |
| AT-004 | イテレーション内の例外でワーカーループが停止しない(次イテレーションで再実行される) | unit(1回目例外 → 2周で call_count == 2) | pytest ログ |
| AT-005 | 並行性: `_sweep_final_transcription_jobs` が長時間 await 中でも、start_sweeps 側の `_sweep_stale_stopping` が並行して実行される | unit(Event 待ち AsyncMock + 2タスク並行起動、タイムアウト付き assert) | pytest ログ |
| AT-006 | main.py startup で `start_final_transcription_worker(async_session_local)` が `asyncio.create_task` 起動される | `grep -n "start_final_transcription_worker" services/meeting-api/meeting_api/main.py` が create_task 行を含む | grep 出力 |
| AT-007 | stop 関数(stop_final_transcription_worker)でワーカーループが終了する | unit | pytest ログ |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | meeting-api テスト: ベースライン **631 passed / 19 skipped / 0 failed**(同一 HEAD で本差分を退避して実測)に対し、新規 fail 0・passed 増は本タスクの新規テスト分のみ許容・**skipped 増加なし** | `python -m pytest services/meeting-api/tests` をベースラインと変更後で同一 venv 実行しサマリ比較 | ベースライン/変更後の pytest サマリ全文(`.hw/gates/p12-final-transcription-worker/pytest-baseline-8bc0374.txt` / `pytest-full-committed-eebdd38.txt`) |
| FP-002 | `_sweep_final_transcription_jobs` 本体(選定クエリ・lease 再取得・リトライ上限・skip_locked)が無変更 | `git diff 2da98bb..HEAD -- services/meeting-api/meeting_api/sweeps.py` の該当関数(683-843 相当)にハンクなし(ループ部・追加関数のみ変更) | diff 出力 |
| FP-003 | `final_transcription.py` が無変更(次タスク p12-audio-disk-streaming との衝突禁止) | `git diff 2da98bb..HEAD -- services/meeting-api/meeting_api/final_transcription.py` が空 | diff 出力 |
| FP-004 | 既存 sweeps テスト(test_sweeps_skip_locked.py / test_sweeps_stopping.py / test_sweeps_unfinalized_recordings.py / test_sweeps_voiceprint_retention.py / test_final_transcription.py)が無修正で green | FP-001 の全件実行内で確認。上記ファイルへの diff が空であること | pytest ログ + diff 出力 |
| FP-005 | 他 sweep(stale-stopping / aggregation retry / unfinalized / drive export / voiceprint retention / container-stops)は start_sweeps 内に残存 | unit(start_sweeps 1イテレーションで各 sweep が呼ばれる既存/新規テスト)or コード構造レビュー | pytest ログ / レビュー verdict |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | 新規必須 env なしで従来と同じ実効周期(60秒)で動作する(compose / helm / lite 無変更で挙動互換) | `git diff 2da98bb..HEAD -- deploy/` が空 + 既定値 60 のコード確認 | diff 出力 + レビュー verdict |
| NFT-002 | 新ワーカーの停止イベント・カウンタが start_sweeps の `_stop_event` / `sweep_iterations` と独立(相互干渉なし) | コード構造レビュー(Fable レビューで確認) | レビュー verdict |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p12-final-transcription-worker/` へ。`.hw/plans/` に後 commit しない)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

なし(外部 API・ライブラリの最新仕様に依存しない。asyncio タスク分離パターンは
リポジトリ内の既存実績 start_sweeps / start_retry_worker で裏取り済み)。

## 改訂履歴

- 2026-08-09 FP-001 改訂(planner Fable、実装者へ差し戻さず契約側を訂正):
  プラン作成時に handoff 記載値「591 passed / 11 skipped / 0 failed」を実測せず転記した
  ことが原因で、実測ベースラインと不一致だった。実装者が python3.11 fresh venv を作り直し、
  本差分を stash 退避した同一 HEAD(8bc0374)で実測した結果は
  **631 passed / 19 skipped / 17 warnings / 0 failed**。改訂前=591/11、改訂後=631/19。
  合格の実質(本タスクが既存テストを退行させないこと)は不変で、合格ラインを
  「新規 fail 0・新規テスト分の passed 増のみ許容・skipped 増加なし」として明文化した。
  変更後(eebdd38)実測は 638 passed / 19 skipped / 0 failed(+7 = 新規テスト7件のみ)で
  改訂後基準を満たす。証跡: `.hw/gates/p12-final-transcription-worker/pytest-baseline-8bc0374.txt`、
  `pytest-full-committed-eebdd38.txt`、`baseline-note.txt`。
  st9・p06 の前例(ベースライン比較方式への改訂)に準拠。
