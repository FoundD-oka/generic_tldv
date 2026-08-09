# Verification Contract — p12-audio-disk-streaming

対象: `base-commit..HEAD` の差分(base-commit は PR #54 マージ後の main を
コーディネータが `.hw/plans/p12-audio-disk-streaming/base-commit` に記録した値)。
テストは commit 済み clean tree に対して実行する。
meeting-api テストは python3.11 fresh venv
(`pip install -e libs/admin-models/ -e services/meeting-api/` + `pytest pytest-asyncio httpx`。
/tmp の venv は劣化するため毎回作り直す)。

## ベースライン取得手順(FP-001 の前提。転記値の使用禁止)

1. 実装着手前に base-commit をチェックアウトした clean tree で上記 venv を作成し、
   `python -m pytest services/meeting-api/tests` を実行する。
2. サマリ全文を `.hw/gates/p12-audio-disk-streaming/pytest-baseline-<commit>.txt` に保存する。
3. **この実測値が本契約のベースライン**である。handoff や過去契約からの転記値は無効。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | **mixed 経路のピークメモリが音声長に比例しない**(本タスクの本質要求。緩和禁止): 48MiB / 192MiB の合成 WAV(LocalStorage + streaming stub、実 httpx + request.stream を消費する DrainTransport、DB は既存流儀の AsyncMock)で run_deferred_transcription を実行し tracemalloc 計測。**peak(192MiB) ≤ 64MiB かつ peak(192)−peak(48) ≤ 16MiB**。DrainTransport 受信総バイト ≥ WAV サイズ(送信実行の証明) | `python -m pytest services/meeting-api/tests/test_final_transcription_memory.py -k mixed -v`(テスト内で両サイズを実測し assert。実測ピーク値をログ出力すること) | pytest ログ(ピーク実測値を含む)を `.hw/gates/p12-audio-disk-streaming/` に保存 |
| AT-002 | lane 経路のピークメモリが lane 合計サイズに比例しない: 3 lane × 64MiB(合計192MiB)で tracemalloc peak ≤ 64MiB | 同上 `-k lane` | pytest ログ |
| AT-003 | 静的ガード: `final_transcription.py` のソースに bytes 全読みの storage API 呼び出しがない(`download_file(` の全出現が `download_file_to_path(` であり、`_audio_duration_seconds` 経路に `BytesIO(` がない) | unit(inspect.getsource による assert) | pytest ログ |
| AT-004 | ST-10: ffmpeg タイムアウトが入力サイズ比例+クランプ: (i) 小サイズ → floor(既定120)、(ii) 線形域(例 84MiB×4.0秒/MiB=336秒)、(iii) 巨大サイズ → cap(既定1800)、(iv) `DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS` / `_MAX_SECONDS` / `_SECONDS_PER_MIB` の env override が効く、(v) cap < floor の誤設定では floor 優先 | unit(`_ffmpeg_timeout_seconds` パラメトライズ) | pytest ログ |
| AT-005 | `_convert_audio_to_wav` が算出タイムアウトを実際に `subprocess.run(timeout=...)` へ渡す | unit(subprocess.run を patch し、入力ファイルサイズから期待値を計算して kwarg を assert) | pytest ログ |
| AT-006 | 一時ファイルが残存しない: mixed 成功 / STT 失敗(500)/ lane fallback の各経路の実行後、run 用 temp ディレクトリが削除されている | unit(tempfile.TemporaryDirectory の親を tmp_path に向け、実行後の残存エントリ 0 を assert) | pytest ログ |
| AT-007 | 変換不要フォーマット(wav)は無変換で素通りし、変換失敗・タイムアウト時の HTTPException(500、detail 文言)が現状と同一(retryable 分類の回帰なし) | unit(ffmpeg 失敗/TimeoutExpired を patch して detail を assert) | pytest ログ |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | meeting-api テスト: 上記手順で実装者が実測したベースラインに対し、**新規 fail 0・passed 増は本タスクの新規テスト分のみ・skipped 増加なし** | ベースラインと変更後を同一 venv・同一コマンドで実行しサマリ比較 | ベースライン/変更後の pytest サマリ全文(`.hw/gates/p12-audio-disk-streaming/`) |
| FP-002 | 既存 mock 境界テスト(test_final_transcription_lanes / test_final_transcription_gemini_dictionary / test_voiceprint_hook_integration / test_speaker_clusters / test_final_transcription)の修正は最小限: 許可済みは `test_call_transcription_service_marks_deferred_tier` の入力 path 化のみ。それ以外の修正は「実測で fail したもの」に限り、assertion の意図を変えない。修正一覧と理由を evidence に記録 | `git diff base-commit..HEAD -- services/meeting-api/tests/` のレビュー + 修正一覧 | diff 出力 + 修正一覧メモ |
| FP-003 | 変更ファイルが `services/meeting-api/meeting_api/final_transcription.py` と `services/meeting-api/tests/` 配下のみ(sweeps.py / storage.py / meetings.py / transcription-service / deploy は無変更) | `git diff --name-only base-commit..HEAD` が上記のみ | diff 出力 |
| FP-004 | deploy のメモリ上限が据え置き(compose `mem_limit: 1g`、helm memory 1Gi。上限引き上げによる誤魔化し禁止) | `git diff base-commit..HEAD -- deploy/` が空 | diff 出力 |
| FP-005 | 処理セマンティクス不変: `_set_final_transcription_state` の状態キー・エラー分類・retryable 判定・lane all-or-nothing・duration budget(BUG-010)・skip_no_speaker_events ガード・Gemini 分岐に差分がない | 既存テスト green(FP-001 内)+ `git diff base-commit..HEAD -- services/meeting-api/meeting_api/final_transcription.py` の該当ハンクレビュー(Fable) | pytest ログ + レビュー verdict |
| FP-006 | 既存 env `DEFERRED_TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS` の後方互換: 値を設定した環境では算出タイムアウトがその値を下回らない | AT-004(iv) に含めて assert | pytest ログ |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | メモリ検証テストの CI 安定性: 閾値はチャンクバッファ+固定費に対し 4 倍以上の余裕(64MiB/16MiB)を持ち、GC 状態に依存しない(計測前に gc.collect + tracemalloc reset) | テスト実装のレビュー + ローカル3回連続 green | pytest ログ(3回)+ レビュー verdict |
| NFT-002 | 新規 env(`..._MAX_SECONDS` / `..._SECONDS_PER_MIB`)は既定値で挙動が成立し、deploy への配線は不要(任意) | AT-004 の既定値ケース + FP-004 | pytest ログ |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p12-audio-disk-streaming/` へ。`.hw/plans/` に後 commit しない)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

- httpx の multipart ファイルオブジェクト・ストリーミング挙動(64KiB チャンク遅延読み)
  に依存するが、AT-001 が実 httpx + DrainTransport の実測で経験的に固定するため、
  仕様理解が誤っていればテストが fail する(文書調査への依存なし)。
