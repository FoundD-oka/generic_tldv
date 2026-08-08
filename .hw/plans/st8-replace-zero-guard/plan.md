---
generated_by: fable
task_id: st8-replace-zero-guard
base-commit: 3964a5e9d464cbe57b8bc14f7f4c1bf7538b3ad1
---

# ST-8: replace モードの0セグメント成功時に既存文字起こしを全削除するバグの修正

## ゴール

依頼の文字通りの内容: 「STTプロバイダが0セグメントを返したとき、replace モードが
既存 Transcription を DELETE して stored=0 のまま succeeded になるのを防ぐ」。

再設計後のゴール(reframe): 守るべき不変条件は「**replace は、新規に保存できる
実質セグメントが1件以上あるときだけ既存行を削除してよい**」。0セグメントだけで
なく「全セグメントが空白のみのテキスト」(保存ループが全件 skip して stored=0 に
なるケース)も同一の欠陥なので同時に塞ぐ。乖離は小さく、依頼の意図(既存データの
保持)と完全に整合する。

## 対象

- ファイル: `services/meeting-api/meeting_api/final_transcription.py`
- 関数: `run_deferred_transcription` (該当箇所は base-commit 時点で :1440-1445 の
  `if mode == "replace":` ブロック)
- テスト: `services/meeting-api/tests/test_final_transcription.py`(追記)

## 設計判断(確定事項。実装者の裁量に委ねない)

1. **「空」の定義**: `str(seg.get("text", "")).strip()` が空でないセグメントを
   「実質セグメント」とする。これは直後の保存ループ(:1464-1469)が行を保存する
   条件と同一。ガード条件は「実質セグメント数 == 0」。
   `_parse_segments` は空白のみのテキストを既に落とすが、レーン経路
   (`_transcribe_lanes`)の出力にも同じ不変条件を適用するため、判定は
   **delete 実行地点で** segments リストに対して行う(経路に依存しない)。
2. **ガードの発火条件**: `mode == "replace"` かつ 実質セグメント数 == 0 かつ
   既存 Transcription 行数 > 0。既存行が 0 件なら失うものがないので現行どおり
   succeeded(segment_count=0)で完了させる(無音会議を永久リトライさせない)。
3. **force との関係**: `force=True` でもガードは適用する。force は「話者ラベルを
   Unknown で上書きしてよい」の意思表示であり、「全データを削除して何も残さなく
   てよい」の意思表示ではない。
4. **status の扱い**: skip ではなく **failed** にする。
   `_set_final_transcription_state` で
   `status="failed"`, `failed_at`, `updated_at`, `last_error`(プロバイダが実質
   0セグメントを返した旨), `error_code="empty_transcription_result"`,
   `retryable=(not _is_gemini_requested())`, `segment_count=0`,
   `lease_expires_at=None`, `triggered_by=triggered_by` を設定して commit し、
   `HTTPException(status_code=502, detail=...)` を raise する。
   - retryable=True(非Gemini)の根拠: 空応答は一過性のプロバイダ不調があり得る。
     sweep(`sweeps.py:696-834`)が `failed AND retryable=true` を拾い、
     `FINAL_TRANSCRIPTION_MAX_ATTEMPTS`(既定24)で打ち止めるため無限ループに
     ならない。Gemini は既存の失敗経路(:1408-1425)と同じく retryable=False
     (コスト暴走防止の既存ポリシー踏襲)。
   - 502 の根拠: sweep の except 分岐(:821-827)が retryable failure として
     warning ログを出す既存分類に一致。手動API経路
     (`meetings.py:_execute_manual_transcription`)は HTTPException を catch して
     ログするだけなので background task は安全。
5. **webhook / UI / 副作用**: ガード発火時は delete しない・
   `_clear_live_transcript_cache` を呼ばない・`_publish_transcript_finalized` を
   publish しない・voiceprint followup を実行しない・
   `queue_drive_export_if_needed` を呼ばない(既存の失敗経路と同じ扱い)。
   ダッシュボードは `failed` を既知 status として表示できる
   (`services/dashboard/src/lib/retranscription-status.ts:9` が failed を正規化
   済み)ため、フロント変更は不要。
6. **既存ガード(:1325-1332 の no-speaker-events skip)は変更しない。**

## 実装手順(How)

1. `run_deferred_transcription` 内、heartbeat cancel(:1427-1428)と Gemini の
   lease 再確認(:1430-1438)より後、現行の `replaced_count = 0` /
   `if mode == "replace":` ブロック(:1440-1445)を次の構造に書き換える:

   ```python
   replaced_count = 0
   if mode == "replace":
       existing_final_count = (await db.execute(
           select(func.count(Transcription.id)).where(Transcription.meeting_id == meeting_id)
       )).scalar() or 0
       has_effective_segments = any(
           str(seg.get("text", "")).strip() for seg in segments
       )
       if not has_effective_segments and existing_final_count > 0:
           _set_final_transcription_state(
               meeting,
               status="failed",
               failed_at=_utcnow_iso(),
               updated_at=_utcnow_iso(),
               last_error=(
                   "transcription provider returned no non-empty segments; "
                   f"existing {existing_final_count} transcription row(s) preserved"
               ),
               error_code="empty_transcription_result",
               retryable=not _is_gemini_requested(),
               segment_count=0,
               lease_expires_at=None,
               source_recording_path=source.storage_path,
               source_recording_backend=source.storage_backend,
               language=resolved_language,
               triggered_by=triggered_by,
           )
           await db.commit()
           logger.warning(
               "Deferred transcription for meeting %s returned zero effective "
               "segments in replace mode — existing %d row(s) preserved, marked failed",
               meeting_id, existing_final_count,
           )
           raise HTTPException(
               status_code=502,
               detail="Transcription provider returned an empty transcript; existing transcription preserved",
           )
       replaced_count = existing_final_count
       await db.execute(delete(Transcription).where(Transcription.meeting_id == meeting_id))
   ```

   注意: この raise は :1264-1425 の try/except の外側なので二重処理されない。
   `attributes.flag_modified` は `_set_final_transcription_state` 内で処理される
   前提を既存失敗経路と同様に踏襲する(同関数の既存実装を確認して同じ作法で)。

2. `services/meeting-api/tests/test_final_transcription.py` に、既存の
   `test_run_deferred_transcription_replace_replaces_existing_rows_after_success`
   と同じモック様式(AsyncMock db / MockResult / patch 群)でテストを追加する。
   ケースは verification-contract.md の AT-001〜AT-005 に列挙。delete 不実行の
   検証は `db.execute.call_args_list` を走査し
   `sqlalchemy.sql.dml.Delete` インスタンスが1つも無いことを assert する
   (呼び出し回数だけの assert は不可)。

3. 既存テスト(特に上記 replace 成功テストと
   `tests/test_final_transcription_lanes.py` の replace 系)は**一切変更しない**。

## スコープ外

- ST-9〜ST-12(OOM・ffmpeg タイムアウト等)には触れない。
- `_parse_segments` / `_transcribe_lanes` / sweep のリトライ上限は変更しない。
- ダッシュボード側の変更なし。

## Why(実装者に渡さない)

- 監査 ST-8(.pipeline/plans/product-quality-gap-2026-08/current-state.md:22)。
  本製品の主任務は「要約のための文字起こし」であり、確定文字起こしの全削除+
  succeeded 確定は主機能の復旧不能な欠陥(出荷ブロッカー)。
- 既存ガード(:1325-1332)は「speaker_events 無しで有意な話者ラベルを潰さない」
  という別の不変条件を守るもので、speaker_events が存在する会議・話者ラベルが
  Unknown のみの会議では発火しない。0セグメント問題とは直交。
- delete を「保存できるものがあるときだけ」に遅延させる案(delete と insert の
  順序入替)も検討したが、segment_id の一意性や既存トランザクション構造への影響
  が読みにくく、ガード+早期 raise の方が blast radius が小さい。
- 既存行 0 件時に succeeded(0件)を維持するのは、録音が本当に無音のケースを
  リトライ地獄にしないため。データ損失が起きるのは既存行がある場合だけ。
- status を skip 系ではなく failed にしたのは、(a) 一過性のプロバイダ不調なら
  sweep の有限リトライ(24回・指数バックオフ)で自己回復させたい、(b) UI が
  failed を既に表示できる、(c) skipped_no_speaker_events は retryable=False で
  「再実行しても無意味」の意味論を持ち、本ケース(再実行に意味がある)と合わない
  ため。
- GitNexus MCP は本セッションで未接続のため、影響範囲は Grep で裏取りした。
  `run_deferred_transcription` の製品コード呼び出し元は `meetings.py`(手動API、
  background task で HTTPException を握り潰しログ化)と `sweeps.py`(502 を
  retryable failure として warning ログ)の2箇所のみで、いずれも 502 raise に
  安全に耐える。
