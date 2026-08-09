---
generated_by: fable
task_id: p13-runtime-log-promotion
base-commit: 5559ff9e57e004805106e10b41da9451b1207923
size: S
---

# ST-22: runtime-api の障害ログ debug→warning 昇格(P1-3 第3弾)

## ゴール

依頼の文字通りの内容: 「障害ログが debug レベルで既定非出力(runtime-api 45箇所)」。

現状の実測(監査からの変化): 監査時 45箇所 → **現在 10箇所**(P1-1 以降の改修で
大幅縮小)。うち 9箇所が例外握り潰し(`exc_info=True` 付き debug)で昇格対象、
1箇所(state.py:23 の状態遷移トレース)は正当な debug で残す。
達成すべき成果: **既定 LOG_LEVEL=INFO(compose 既定)で、runtime-api の障害・
再接続イベントがログに出る**。

## How

変更は `services/runtime-api/runtime_api/` の4ファイルのみ。レベル変更のみで
メッセージ文字列・exc_info・制御フローは変えない。

昇格対象(logger.debug → logger.warning、全9箇所):

| ファイル:行 | 内容 |
|---|---|
| lifecycle.py:107 | pending-callback scan 失敗 |
| lifecycle.py:112 | pending-callback 再配送失敗 |
| lifecycle.py:158 | Pack K.2 reconcile の個別 key エラー |
| lifecycle.py:164 | Pack K.2 reconcile scan 失敗 |
| lifecycle.py:168 | idle check ループエラー |
| backends/docker.py:119 | Docker event stream 再接続(ST-23 の検知点) |
| backends/docker.py:144 | Docker event parse 失敗 |
| backends/process.py:254 | reaper ループエラー |
| backends/kubernetes.py:381 | K8s watch 再接続 |

据え置き: state.py:23(`State set: ...`。正常系トレースで warning 化はノイズ)。

注意(実装時に確認): 各昇格箇所を含むループに sleep/バックオフがあることを確認する
(接続断が続いた場合の warning 洪水防止)。sleep のないタイトループ内の箇所が
あれば、その1箇所のみ据え置きとし advisory として記録する。

検証: (1) `grep -rn "logger\.debug" services/runtime-api --include='*.py'` の非テスト
残存が state.py の1箇所のみ、(2) runtime-api pytest がベースライン比較で退行なし
(caplog でレベルを assert する既存テストが壊れた場合はテスト側をレベル昇格に追随)。

## Why(実装者に渡さない)

- warning(error でなく)を選ぶ理由: 9箇所はすべて「リトライ・再接続で自己回復し得る
  経路」であり、error にすると恒久障害と区別が付かなくなる。運用上必要なのは
  「既定レベルで見えること」であり warning で足りる。
- 45→10 に縮小した経緯を報告に残す理由: 監査値をそのまま契約に書くと実測と食い違い
  差し戻しになる(P1-2 の教訓)。本タスクの契約は現物の10箇所に基づく。
