# Verification Contract — st8-replace-zero-guard

対象: `services/meeting-api/meeting_api/final_transcription.py` の
`run_deferred_transcription`(replace モードの0セグメントガード)。

## テスト実行方法(CI と同一手順)

CI(`.github/workflows/test-meeting-api.yml`)の再現。Python 3.11 必須:

```bash
python3.11 -m venv /tmp/st8-venv && source /tmp/st8-venv/bin/activate
pip install -e libs/admin-models/
pip install -e services/meeting-api/
pip install pytest pytest-asyncio httpx
pytest services/meeting-api/tests/ -v --ignore=services/meeting-api/tests/test_integration_live.py
```

## 最低合格ライン(改訂1)

base-commit(3964a5e)時点で上記手順は既に 8 failed であり「フルスイート exit 0」は
達成不能(mainのCIも同状態)。よって合格ラインはベースライン比較方式に改訂する
(.hw/verify-baseline の「新規回帰のみ block」哲学と整合):

1. **新規 failed ゼロ**: 実装 HEAD で上記手順を実行し、failed が下記
   「既知失敗ベースライン」8件と**完全一致**すること。既知失敗リスト以外の
   failed(または error / collection error)が**1件でもあれば契約違反**。
   既知8件のうち一部が pass に転じるのは違反ではない(その場合は報告する)。
2. **AT-001〜AT-005 が全て PASSED** であること(テスト名の PASSED 行を証跡に)。
3. **既存テスト無変更**: アンチゲーミング条項の diff 検査を満たすこと。
4. 比較は同一 venv・同一手順で行う。ベースライン側の実測
   (base tree: 8 failed / 580 passed / 10 skipped)との突き合わせを証跡に含める。

### 既知失敗ベースライン(base-commit 3964a5e 時点、8件)

いずれも本タスクのスコープ外(final_transcription.py と無関係の環境起因):

| # | テスト | 原因 | スコープ外の理由 |
|---|---|---|---|
| 1 | `tests/test_voiceprint_matching.py::test_followup_writes_suggestion_when_similarity_above_threshold` | numpy 未インストール(requirements.txt:14 にあるが pyproject.toml 依存に無く、契約手順の `pip install -e` では入らない) | 依存宣言の乖離。別タスク候補 |
| 2 | `tests/test_voiceprint_matching.py::test_followup_matches_unconfirmed_gemini_cluster_from_mixed_master` | 同上 | 同上 |
| 3 | `tests/test_voiceprint_matching.py::test_followup_discards_embedding_when_below_threshold` | 同上 | 同上 |
| 4 | `tests/test_voiceprint_matching.py::test_followup_write_preserves_concurrent_edit_via_fresh_reselect` | 同上 | 同上 |
| 5 | `tests/test_voiceprint_matching.py::test_followup_write_does_not_resurrect_concurrently_rejected_entry` | 同上 | 同上 |
| 6 | `tests/test_voiceprint_matching.py::test_followup_write_does_not_resurrect_concurrently_confirmed_entry` | 同上 | 同上 |
| 7 | `tests/test_voiceprint_matching.py::test_followup_write_preserves_untouched_old_entry_when_no_concurrent_change` | 同上 | 同上 |
| 8 | `tests/integration/test_transcription_dictionary_postgres.py::test_real_postgres_advisory_lock_enforces_200_term_cap` | 実 PostgreSQL 接続が必要。CI(test-meeting-api.yml)に postgres service が無く main の CI も既に red | インフラ欠落。別タスク候補 |

注意: この8件が**別の要因**で失敗内容を変えた場合(例: numpy を入れても落ちる、
エラー種別が変わる)も「既知失敗」とは認めない。既知扱いは上記の原因のまま
失敗している場合に限る。疑義があれば numpy 追加後の再実行(7件が pass に転じる
こと)で切り分ける。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | replace + プロバイダ応答 `{"segments": []}` + 既存 Transcription 2件 のとき: (a) `db.execute` に `sqlalchemy.sql.dml.Delete` インスタンスが一度も渡されない(call_args_list 走査で assert)、(b) `db.add` が呼ばれない、(c) `HTTPException` (status_code=502) が raise される、(d) `meeting.data["final_transcription"]` が `status=="failed"`, `error_code=="empty_transcription_result"`, `retryable is True`, `segment_count==0` | unit: 新規テスト `test_replace_zero_segments_preserves_existing_rows` | pytest 実行ログ(該当テスト名の PASSED 行) |
| AT-002 | replace + 全セグメントが空白のみテキスト(例 `[{"start":0,"end":1,"text":"   "}]`)+ 既存2件 でも AT-001 と同じガードが発火する | unit: 新規テスト `test_replace_whitespace_only_segments_preserves_existing_rows` | 同上 |
| AT-003 | replace + 0セグメント + 既存 Transcription 0件 のとき: raise せず `status=="succeeded"`, `segment_count==0` で完了する(無音会議を失敗ループに入れない) | unit: 新規テスト `test_replace_zero_segments_without_existing_rows_succeeds` | 同上 |
| AT-004 | replace + `force=True` + 0セグメント + 既存2件 でもガードが発火し既存行が保持される(Delete 不実行 + 502) | unit: 新規テスト `test_replace_zero_segments_guard_applies_even_with_force` | 同上 |
| AT-005 | AT-001 のガード発火時、`_clear_live_transcript_cache` と `_publish_transcript_finalized` が呼ばれない(AsyncMock の assert_not_called / assert_not_awaited) | unit: AT-001 のテスト内 assert で可 | 同上 |

## Failure Patterns(回帰禁止)

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 実質セグメントが1件以上ある通常の replace は従来どおり delete→insert→succeeded で動く | 既存テスト `test_run_deferred_transcription_replace_replaces_existing_rows_after_success` が**無変更で** pass | pytest ログ + `git diff 3964a5e..HEAD -- services/meeting-api/tests/` に既存テストの削除・変更が無いこと |
| FP-002 | 既存の no-speaker-events skip ガード(:1325-1332, :1194-1209)の挙動が変わらない | 既存 `tests/test_final_transcription.py` / `tests/test_final_transcription_lanes.py` の skip 系テストが無変更で pass | pytest ログ |
| FP-003 | reject_if_exists モードの挙動が変わらない | 既存 reject_if_exists 系テスト(lanes / voiceprint_hook)が無変更で pass | pytest ログ |
| FP-004 | Gemini・レーン・sweep 系の既存テストが無変更で pass(既知失敗ベースライン8件を除く) | 最低合格ライン1(新規 failed ゼロ)で担保 | pytest ログ |

## アンチゲーミング条項(レビューで機械的に確認)

- `git diff 3964a5e9d464cbe57b8bc14f7f4c1bf7538b3ad1..HEAD -- services/meeting-api/tests/` に
  既存テストの削除・`skip`/`xfail` マーカー追加・assert の期待値緩和が含まれて
  いたら契約違反。許されるのはテストの**追加**のみ。
- 既知失敗ベースラインへの**追加**は契約違反(この表を増やして failed を既知化
  する形での回避は不可)。ベースラインは base-commit 3964a5e の実測に固定する。
- AT-001 の「Delete 不実行」検証は `db.execute.call_args_list` の型走査で行う
  こと。`db.execute.call_count` の数合わせや `assert True` 相当は契約違反。
- 製品コードの変更は `services/meeting-api/meeting_api/final_transcription.py`
  1ファイルに限る。他ファイルへの変更が diff に現れたら理由なき限り契約違反。
  (既知失敗の原因である pyproject.toml / CI workflow の修正も本タスクでは
  行わない。別タスクで対応する。)

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | `bash .hw/hooks/pr-ready-gate.sh st8-replace-zero-guard` が pass(内部で .hw/verify.sh = make smoke を実行) | ゲート実行 | ゲート出力 |
| NFT-002 | 新規 status/error_code 値は `failed` / `empty_transcription_result` のみで、ダッシュボード側変更が不要なこと(`retranscription-status.ts` が failed を正規化済み) | source check | diff に services/dashboard 変更が無いこと |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(pytest フルスイートの実行ログ。base/HEAD 両方の failed 一覧突き合わせを含む)
- hash-bound approval required: yes
- research brief required: no(外部API仕様に非依存。全て repo 内コードで確定)
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no

## 改訂履歴

| 改訂 | 日付 | 改訂者 | 理由 |
|---|---|---|---|
| 1 | 2026-08-08 | planner (Fable) | 初版の「フルスイート exit 0」が base-commit 3964a5e 時点で達成不能(環境起因の既知失敗8件: numpy 依存宣言乖離7件+実Postgres要求1件、main CI も同状態)と実測で判明したため、合格ラインをベースライン比較方式(既知失敗8件を固定列挙し、リスト外 failed 1件でも違反)へ改訂。AT/FP・アンチゲーミング条項は緩めず、ベースライン追加禁止条項を追加。 |
