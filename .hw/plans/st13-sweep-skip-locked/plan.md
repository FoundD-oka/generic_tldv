---
generated_by: fable
task_id: st13-sweep-skip-locked
base-commit: 5cae3a05550e8679b156e88652b0dfa2193d30f1
size: M
---

# ST-7: sweep の FOR UPDATE に skip_locked が無く複数レプリカで相互ブロックする欠陥の修正

## ゴール

依頼の文字通りの内容: 「sweep の skip_locked 対応」(監査 ST-7、計画 P0-3:
skip_locked=True + sweep リーダー選出(env ガードで可))。

再設計後のゴール(reframe はほぼ不要、範囲の明確化のみ): 守るべき不変条件は
「**sweep はロック取得で待たされない。他者(別レプリカの sweep・exit callback・
手動 API)がロックを保持する行はスキップし、次イテレーションに委ねる**」。
これを (1) sweeps.py 内の 3 箇所の `with_for_update()` への `skip_locked=True`
付与と、(2) レプリカ単位で sweep を無効化できる env ガード
(`MEETING_API_SWEEPS_ENABLED`)の 2 点で実現する。フルのリーダー選出
(advisory lock 等)は計画文書が「env ガードで可」と明記しているため実装しない。

## 対象

- ファイル: `services/meeting-api/meeting_api/sweeps.py`(製品コードはこの1ファイルのみ)
  - `_sweep_unfinalized_recordings`(base-commit 時点 :540)
  - `_sweep_final_transcription_jobs`(同 :732)
  - `_sweep_drive_export_jobs`(同 :886)
  - `start_sweeps`(同 :1020-1042)
- テスト: `services/meeting-api/tests/test_sweeps_skip_locked.py`(新規1ファイルのみ)

## 設計判断(確定事項。実装者の裁量に委ねない)

1. **skip_locked の付与箇所**: :540 / :732 / :886 の `.with_for_update()` を
   `.with_for_update(skip_locked=True)` に変える。**この3箇所だけ**。
   sweeps.py 外の `with_for_update`(meetings.py, final_transcription.py,
   drive_export.py, outbound_events.py, recordings.py, voiceprint系)は
   正当性のためのロック(TOCTOU防止)であり、スキップしてよい性質ではないので
   触らない。`_sweep_stale_stopping` / `_sweep_aggregation_retry` は
   `with_for_update` を使っていないため対象外。
2. **スキップ時の挙動は既存コードで完結**: 3箇所とも直後に
   `if meeting is None: continue` があり、skip_locked でロック中の行が
   落とされたケース(scalar が None)をそのまま吸収する。追加の分岐・ログは
   書かない(sweep は周期実行で次イテレーションが拾う)。
3. **env ガードの仕様**:
   - 変数名 `MEETING_API_SWEEPS_ENABLED`、既定 `"true"`(未設定=有効。
     現行挙動を変えない fail-open)。
   - 判定ヘルパを sweeps.py にモジュールレベルで追加する:
     ```python
     def _sweeps_enabled() -> bool:
         raw = os.environ.get("MEETING_API_SWEEPS_ENABLED", "true")
         return raw.strip().lower() not in {"0", "false", "no", "off"}
     ```
   - `start_sweeps` の先頭(`_stop_event = asyncio.Event()` より前)に:
     ```python
     if not _sweeps_enabled():
         logger.warning(
             "[sweeps] disabled via MEETING_API_SWEEPS_ENABLED=%r — "
             "this replica will not run idle sweeps",
             os.environ.get("MEETING_API_SWEEPS_ENABLED"),
         )
         return
     ```
     早期 return で `_stop_event` は None のままだが、`stop_sweeps()`(:1141)は
     `if _stop_event is not None` で防御済みなので安全。`main.py` の
     `asyncio.create_task(start_sweeps(...))`(:333)は**変更しない**
     (タスクは即終了するだけ)。sweep_iterations 等のヘルス用モジュール状態は
     main.py から未参照であることを裏取り済みのため、無効時に 0 のままでよい。
4. **`os` は sweeps.py で import 済み**(:30 付近)。新規 import は不要。

## 実装手順(How)

1. sweeps.py の 3 箇所を `.with_for_update(skip_locked=True)` へ変更。
2. sweeps.py に `_sweeps_enabled()` を追加し、`start_sweeps` 先頭に無効化ガードを
   追加(上記コードそのまま)。
3. `services/meeting-api/tests/test_sweeps_skip_locked.py` を新規作成。既存の
   `tests/test_sweeps_unfinalized_recordings.py` / `tests/test_drive_export.py` の
   モック様式(conftest の `mock_db` / `MockResult` / `make_meeting`、
   `AsyncMock(side_effect=[...])` で execute の戻りを順に与える)を踏襲し、
   verification-contract.md の AT-001〜AT-005 を実装する。
   - skip_locked の検証方法(AT-001〜003 共通): 各 sweep 関数を1行ヒットする
     ように駆動し、`mock_db.execute.call_args_list` から SQLAlchemy `Select` で
     `_for_update_arg` が設定されている statement を抽出。**抽出件数が 1 以上**
     であることを assert した上で、全件を
     `stmt.compile(dialect=sqlalchemy.dialects.postgresql.dialect())` で文字列化し
     `"FOR UPDATE SKIP LOCKED"` を含むことを assert する
     (`call_count` の数合わせや存在チェックのみは不可)。
   - env ガードの検証(AT-004〜005): `monkeypatch.setenv` で切り替え、
     無効時は `await asyncio.wait_for(start_sweeps(factory), timeout=1.0)` が
     完走し `factory`(MagicMock)が一度も呼ばれないこと。`_sweeps_enabled()` の
     真理値表(既定 True / "false","0","no","off","FALSE" → False /
     "true","1" → True)を直接 assert。
4. 既存テスト・既存製品コード(sweeps.py の上記以外の行を含む)は変更しない。

## スコープ外

- `deploy/helm/charts/vexa/values.yaml`(replicaCount: 2)・compose は変更しない。
  単一 Deployment ではレプリカ別 env を注入できず、skip_locked だけで相互ブロック
  は解消する。ガードは helm の既存 `extraEnv` / compose の environment で運用側が
  必要時に注入できるため、マニフェスト変更は不要。
- `_sweep_stale_stopping` / `_sweep_aggregation_retry` / container-stop outbox /
  voiceprint retention のロジック変更なし。
- リーダー選出(pg advisory lock / Redis lock)は実装しない。
- st9/st10 との衝突回避: meeting-api の pyproject.toml・CI workflow・
  postgres 統合テスト関連には一切触れない。
- メトリクス(ST-18)・sweep 分離(ST-6)は別タスク。

## Why(実装者に渡さない)

- 監査 ST-7(current-state.md:21): FOR UPDATE に skip_locked が無く、helm 既定
  replicaCount=2 で全レプリカが main.py:333 から sweep を起動するため、
  片方の sweep が行ロックを保持したままプロバイダ呼び出し等の長時間処理
  (特に `_sweep_final_transcription_jobs` はロック保持中に
  `run_deferred_transcription` を await し数分かかり得る)をすると、
  もう片方が同じ行の FOR UPDATE で無期限ブロックし片肺化する。
- skip_locked は sweep 同士だけでなく、exit callback や手動 API がロックを持つ
  行への待ちも除去する。sweep は「正史メカニズムの取りこぼし回収」であり、
  誰かが処理中の行はそもそも sweep が触るべきでない — スキップは意味論的に正しい。
- env ガードを start_sweeps 内(main.py でなく)に置いたのは、diff を1ファイルに
  閉じ、asyncio.wait_for で直接テスト可能にするため。既定有効にしたのは、
  ガードは「運用の逃げ道」であり、既定無効にすると全環境で sweep が止まる
  リグレッションになるため。
- SQLite 等の dialect 差異は問題にならない: 本サービスの実 DB は Postgres のみ、
  テストはモックベースで dialect 非依存、検証はキャプチャした statement を
  postgresql dialect でオフラインコンパイルして行う。
- GitNexus MCP は本セッション未接続のため影響範囲は Grep で裏取りした。
  `with_for_update` の sweeps.py 内出現は 3 箇所のみ、`_stop_event` None 時の
  stop_sweeps 安全性、main.py が sweep_iterations を未参照であることを確認済み。
