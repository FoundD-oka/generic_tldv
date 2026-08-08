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

最低合格ライン: 上記フルスイートが exit 0。加えて下記 AT/FP の各テストが
`services/meeting-api/tests/test_final_transcription.py` に実在し pass すること。

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
| FP-004 | Gemini・レーン・sweep 系の既存テストが無変更で pass | フルスイート exit 0 | pytest ログ |

## アンチゲーミング条項(レビューで機械的に確認)

- `git diff 3964a5e9d464cbe57b8bc14f7f4c1bf7538b3ad1..HEAD -- services/meeting-api/tests/` に
  既存テストの削除・`skip`/`xfail` マーカー追加・assert の期待値緩和が含まれて
  いたら契約違反。許されるのはテストの**追加**のみ。
- AT-001 の「Delete 不実行」検証は `db.execute.call_args_list` の型走査で行う
  こと。`db.execute.call_count` の数合わせや `assert True` 相当は契約違反。
- 製品コードの変更は `services/meeting-api/meeting_api/final_transcription.py`
  1ファイルに限る。他ファイルへの変更が diff に現れたら理由なき限り契約違反。

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | `bash .hw/hooks/pr-ready-gate.sh st8-replace-zero-guard` が pass(内部で .hw/verify.sh = make smoke を実行) | ゲート実行 | ゲート出力 |
| NFT-002 | 新規 status/error_code 値は `failed` / `empty_transcription_result` のみで、ダッシュボード側変更が不要なこと(`retranscription-status.ts` が failed を正規化済み) | source check | diff に services/dashboard 変更が無いこと |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(pytest フルスイートの実行ログ)
- hash-bound approval required: yes
- research brief required: no(外部API仕様に非依存。全て repo 内コードで確定)
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
