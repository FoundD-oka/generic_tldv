# meeting-api が Bot 退出のたびに数分止まる件（2026-11-10）

## 元の要求

2026-11-10 の会話で、主担当が次の修正案を提示した。

> 修正の中身は、一覧取得を別スレッドに移して止まらないようにすることと、
> 一覧の範囲を該当の会議だけに絞ること。修正はコード変更と本番反映が要るから、やるなら言って。

ユーザーの返答は「やって」。

## 確認した事実

- 2026-11-10 04:14〜04:31 UTC、本番の meeting-api のログが 88〜168 秒ずつ途切れた（6回）。
  途切れている間はヘルスチェックが失敗し（unhealthy）、停止 API は 503 を返した。
- どの回も、録音のない会議（参加できずに終わった Bot）の退出処理で起きていた。
  `recording_finalizer.finalize_recording_master` から `sweeps.recover_recordings_jsonb_from_storage` が呼ばれ、
  `storage.list_objects_bounded("recordings/<user_id>/")` をイベントループ上で同期実行していた。
  本番は STORAGE_BACKEND=gcs で、ユーザーの全録音を列挙する。
- 同じ処理の定期スイープ版（`_sweep_unfinalized_recordings`）は、すでに `asyncio.to_thread` で実行し、
  一覧も prefix ごとに1回だけ取っている。

## 範囲の判断

録音キーは `recordings/<user_id>/<recording_id>/<session_uid>/<media_type>/...` の並びで、
recording_id が session_uid より前に来る。会議とセッションの情報だけでは prefix を絞れないため、
「範囲を会議に絞る」は実施せず、会議ごとに1回だけ列挙する形にした。ユーザーへはこの点を報告する。
