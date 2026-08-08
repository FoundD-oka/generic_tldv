# Verification Contract — st13-sweep-skip-locked

対象: `services/meeting-api/meeting_api/sweeps.py`(3箇所の
`with_for_update(skip_locked=True)` 化 + `MEETING_API_SWEEPS_ENABLED` env ガード)。

## テスト実行方法(機械検証コマンド。この手順に固定)

Python 3.11 必須。fresh venv を毎回作り直す(/tmp venv は経時劣化するため):

```bash
rm -rf /tmp/st13-venv
python3.11 -m venv /tmp/st13-venv && source /tmp/st13-venv/bin/activate
pip install -e libs/admin-models/ -e services/meeting-api/
pip install pytest pytest-asyncio httpx numpy cryptography
pytest services/meeting-api/tests/ -v --ignore=services/meeting-api/tests/test_integration_live.py
```

numpy / cryptography の追加インストールは PR #41 で宣言済みのため許可する。

## 最低合格ライン(ベースライン比較方式)

base-commit 5cae3a0 のローカル実測(st9 検証時)は上記手順+numpy で
**591 passed / 10 skipped / 1 failed(実Postgres必要な統合テスト)**。

1. **新規 failed ゼロ**: 実装 HEAD での failed が下記「既知失敗ベースライン」
   1件と**完全一致**すること。リスト外の failed / error / collection error が
   1件でもあれば契約違反。既知1件が pass に転じるのは違反ではない(報告する)。
2. **AT-001〜AT-005 が全て PASSED**(テスト名の PASSED 行を証跡に)。
3. **既存テスト無変更**: アンチゲーミング条項の diff 検査を満たすこと。
4. passed 件数は base 591 + 新規テスト件数以上であること(新規テストの
   収集漏れ検知)。

### 既知失敗ベースライン(base-commit 5cae3a0 時点、1件)

| # | テスト | 原因 | スコープ外の理由 |
|---|---|---|---|
| 1 | `tests/integration/test_transcription_dictionary_postgres.py::test_real_postgres_advisory_lock_enforces_200_term_cap` | 実 PostgreSQL 接続が必要(CI にも postgres service なし) | インフラ欠落。st10 系タスクの管轄であり本タスクでは触らない |

この1件が別の要因で失敗内容を変えた場合は「既知失敗」と認めない。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | `_sweep_unfinalized_recordings` を meeting 1件ヒットで駆動したとき、`mock_db.execute.call_args_list` 中に `_for_update_arg` 付き `Select` が **1件以上**存在し、その全件が postgresql dialect でのコンパイル結果に `"FOR UPDATE SKIP LOCKED"` を含む | unit: 新規 `test_unfinalized_recordings_sweep_uses_skip_locked` | pytest ログ(PASSED 行) |
| AT-002 | `_sweep_final_transcription_jobs` について AT-001 と同一の検証 | unit: 新規 `test_final_transcription_sweep_uses_skip_locked` | 同上 |
| AT-003 | `_sweep_drive_export_jobs` について AT-001 と同一の検証 | unit: 新規 `test_drive_export_sweep_uses_skip_locked` | 同上 |
| AT-004 | `MEETING_API_SWEEPS_ENABLED=false` のとき `await asyncio.wait_for(start_sweeps(factory), timeout=1.0)` が完走し(ループに入らず返る)、session factory が一度も呼ばれない | unit: 新規 `test_start_sweeps_disabled_by_env_returns_immediately` | 同上 |
| AT-005 | `_sweeps_enabled()` の真理値表: 未設定→True、`"true"`/`"1"`→True、`"false"`/`"0"`/`"no"`/`"off"`/`"FALSE"`(大文字小文字非依存)→False | unit: 新規 `test_sweeps_enabled_env_parsing` | 同上 |

## Failure Patterns(回帰禁止)

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 既存 sweep 系テスト(`test_sweeps_stopping.py` / `test_sweeps_unfinalized_recordings.py` / `test_sweeps_voiceprint_retention.py` / `test_drive_export.py` / `test_final_transcription.py`)が**無変更で** pass | 最低合格ライン1 + diff 検査 | pytest ログ + git diff |
| FP-002 | sweeps.py 以外のロック取得(meetings.py / final_transcription.py / drive_export.py / outbound_events.py / recordings.py / voiceprint系 の `with_for_update`)に変更が無い | `git diff 5cae3a0..HEAD` に sweeps.py 以外の製品コード変更が無いこと | diff 出力 |
| FP-003 | env 未設定のとき sweep が従来どおり起動する(既定有効。fail-open) | AT-005 の未設定→True で担保 + `start_sweeps` のガードが `_sweeps_enabled()` 経由であることの source check | pytest ログ + diff |

## アンチゲーミング条項(レビューで機械的に確認)

- `git diff 5cae3a05550e8679b156e88652b0dfa2193d30f1..HEAD -- services/meeting-api/tests/`
  に既存テストの削除・変更・`skip`/`xfail` マーカー追加が含まれたら契約違反。
  許されるのは新規ファイル `test_sweeps_skip_locked.py` の**追加のみ**。
- 製品コードの変更は `services/meeting-api/meeting_api/sweeps.py` 1ファイルに
  限る。特に meeting-api の pyproject.toml・`.github/workflows/`・
  `deploy/`(helm / compose)・postgres 統合テスト関連への変更は理由の如何を
  問わず契約違反(st9/st10 の PR と衝突するため)。
- 既知失敗ベースラインへの**追加**は契約違反。ベースラインは base-commit
  5cae3a0 の実測に固定する。
- AT-001〜003 の skip_locked 検証は「`_for_update_arg` 付き Select の抽出件数
  ≥1 の assert + 全件の postgresql コンパイル文字列検査」で行うこと。
  ソースコードの文字列 grep や `assert True` 相当は契約違反。

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | `bash .hw/hooks/pr-ready-gate.sh st13-sweep-skip-locked` が pass | ゲート実行 | ゲート出力 |
| NFT-002 | 新規に導入する運用ノブは `MEETING_API_SWEEPS_ENABLED` の1つのみで、未設定時の挙動が base と同一(マニフェスト変更不要) | source check | diff に deploy/ 変更が無いこと |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(pytest フルスイート実行ログ。failed 一覧と既知失敗ベースラインの突き合わせを含む)
- hash-bound approval required: yes
- research brief required: no(SQLAlchemy の with_for_update(skip_locked=True) は repo 内の既存依存バージョンで確定的に検証可能。外部仕様の鮮度に非依存)
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
