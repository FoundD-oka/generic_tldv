---
generated_by: fable
task_id: p2x-advisory-cleanup-triage
base-commit: 0fe5ea5a4473312412d4ab5c5f48c605668d0949
size: S
---

# advisory 一掃の選別記録(実装なし・PR なし・記録のみ)

## ゴール

依頼の文字通りの内容: 「advisory 19件を潰す小タスク群を行ってから Phase 3 に進む」。

reframe: advisory は契約外の指摘であり修正義務はない(CLAUDE.md)。19件を機械的に
消化するのではなく、**「直す価値がある少数を選び、残りは理由つきで棄却する」**ことが
本当の成果。棄却理由の記録自体が成果物であり、次スレッドが同じ triage を繰り返さない
ためにこのファイルへ固定する。正本は
`.pipeline/plans/product-quality-gap-2026-08/handoff-2026-08-09-p15.md` の
「未解決の advisory」節(番号1〜19 + P1-2 由来4件 + P1-4 由来2件 = 計25件)と、
各 `review-verdict.json` の原文 detail。

## 選別の物差し(この順で判定)

1. **黙って壊れるか**: 失敗が検知できない・データが漏れる類は最優先(CI/検知系)。
2. **時限爆弾か**: 現構成では無害だが、想定内の構成変更(TCP DOCKER_HOST、直 push
   運用、py3.12 移行)で顕在化するもの。窓を閉じるコストが小さいなら今閉じる。
3. **コストと壊すリスクの釣り合い**: 修正が実測を要する(mem_limit)・中規模リファク
   (テスト書き換え)なら、advisory 消化の名目では着手しない。
4. **実測済みの現規模**: 2026-08-09 実測で Transcription 5,949 行(手元の
   稼働 vexa-postgres-1)。「本番 ~507K 行」を前提にした懸念は格下げする。
5. **しかるべき別の席があるか**: Phase 3(UI 磨き込み)や「並列度を上げる」等の
   将来タスクに自然な席があるものはそこへ送る(このタスク群で拾わない)。

## 採否一覧(25件)

採用9件(番号 1,2,3,4,5,9,11,13,18)+ P1-4 由来2件 → 5タスクに集約。棄却14件。

| # | 出所 | 内容(要約) | 採否 | 行き先 / 棄却理由 |
|---|---|---|---|---|
| 1 | #66 | since アンカーが接続失敗時に進む | 採用 | docker-events-robustness。TCP 化で踏む時限爆弾。修正は小(リセットを get 成功後へ) |
| 2 | #66 | read timeout 後に resp.close() なし | 採用 | 同上。contextlib.closing で1行級 |
| 3 | #66 | _is_read_timeout が型名文字列マッチ | 採用 | 同上。isinstance 第一判定へ。同名別例外の誤判定窓を閉じる |
| 4 | #66 | ConnectTimeout が「正常扱い即再接続」に入る | 採用 | 同上。TCP 構成で backoff なし再接続ループになる時限爆弾 |
| 5 | #65 | verify-compose push トリガーに hw/* なし | 採用 | ci-triggers。1行。直 push 運用が入った時の検証抜けを閉じる |
| 6 | #65 | verify スクリプトの compose パス env 上書き不可 | 棄却 | ディレクトリ複製の回避策が確立済み・利用者はゲート実行者のみ。次に compose 検査を触るタスクへ同乗させる |
| 7 | #65 | postgres 等 5 サービスに mem_limit なし | 棄却 | 限界値は実測が前提(#55/#65 の思想)。推測値で postgres に上限を張ると OOM-kill の実害を新造する。負荷実測タスクとして別途起票が筋 |
| 8 | #65 | meeting-api が minio-init を待つ副作用 | 棄却 | 設計上の想定どおりと #65 で明文化済み。作業なし |
| 9 | #66 | runtime-api の CI 未整備(base 18F、ruff 119) | 採用 | ci-runtime-api。負債可視化の基盤で、タスク docker-events-robustness の検証土台。最優先群 |
| 10 | #67 | migration status が INVALID を present と報告 | 棄却 | 実測 5,949 行で CONCURRENTLY 構築は瞬時、途中失敗の窓は実質ゼロ。migration は一度適用すれば status の出番がない。→ 代わりに「稼働 DB へ migration を今適用する」運用アクションをユーザーへ提案 |
| 11 | #67 | get_user_and_token の二重宣言 | 採用 | search-hygiene。1行削除・挙動不変。ついで消化の粒 |
| 12 | #67 | postgres 統合テストが HTTP 層をバイパス | 棄却 | unit(HTTP経由・モックDB)+統合(実DB・サービス層)の二層で経路は覆えており残る隙間は狭い。中コストの統合テスト再構築に見合わない |
| 13 | #67 | テストの datetime.utcnow() deprecated | 採用 | search-hygiene。py3.12 移行前に9箇所置換。naive/aware の罠は RF で実測 |
| 14 | #68 | 配線テストがソース文字列一致依存 | 棄却 | 壊れれば CI が赤くなる=silent でない。rendering ベースへの置換は中コスト。壊れた時に直す |
| 15 | #68 | AbortController 未配線 | 棄却 | 世代カウンタで結果の正しさは担保済み。ネットワーク節約は Phase 3 の UI 磨き込みへ |
| 16 | #68 | 検索結果が会議一覧を押し下げる | 棄却 | UI 磨き込みそのもの。Phase 3(UI-12〜18)へ送る |
| 17 | #68 | 更新ボタンで再検索しない | 棄却 | 軽微な UX 不整合で入力変更では再検索される。Phase 3 へ送る |
| 18 | #68 | tsc --noEmit に既存エラー、CI 型チェックなし | 採用 | dashboard-typecheck。「常時緑でないことの証明」を塞ぐ基盤系。修正1件+CI 1ステップ |
| 19 | #67 | 統合テスト teardown の engine.dispose() fixture 化 | 棄却 | YAGNI。postgres 統合テストを次に増やすタスクで共通 fixture 化する(その時の受け入れ条件に入れる) |
| P1-2a | 引継 | LocalStorageClient.download_file_to_path が read-all | 棄却 | dev 専用経路(compose=MinIO、本番=GCS)。下記 P1-2 まとめ方針 |
| P1-2b | 引継 | Soniox 分岐 await file.read() 全量メモリ | 棄却 | P1-2 で意図的に受容したトレードオフ(並列上限とセット)。部分修正の価値が薄い |
| P1-2c | 引継 | Gemini 経路の音声全量 bytes(400MB×3) | 棄却 | 同上。「ディスク化+並列度4以上」は需要が出た時に1タスクでまとめて実施する単位 |
| P1-2d | 引継 | GEMINI_RATE_LIMIT_RETRY_ATTEMPTS compose 未配線 | 棄却 | 既定6で成立と明記済み。必要になった時に1行足せばよい |
| P1-4a | 引継 | 4 workflow の paths に自分自身なし | 採用 | ci-triggers。**現物確認の結果、push 側は修正済みで pull_request 側のみ欠落**(handoff の記述より狭い)。PR 運用の本線に穴 |
| P1-4b | 引継 | deploy-dashboard-gcp が paths なし即デプロイ | 採用 | ci-triggers。compose だけの PR で本番ダッシュボードが再デプロイされる実害。paths 絞りは安全な最小修正 |

## タスク一覧と着手順

| 順 | task-id | 対象 advisory | S/M/L | R軸 | レビュー |
|---|---|---|---|---|---|
| 1 | p2x-advisory-cleanup-ci-triggers | 5, P1-4a, P1-4b | S | inline | 機械検証のみ |
| 2 | p2x-advisory-cleanup-ci-runtime-api | 9 | M | inline | Fable |
| 3 | p2x-advisory-cleanup-docker-events-robustness | 1,2,3,4 | M | inline | Fable |
| 4 | p2x-advisory-cleanup-dashboard-typecheck | 18 | S | inline | 機械検証のみ |
| 5 | p2x-advisory-cleanup-search-hygiene | 11,13 | S | inline | 機械検証のみ(最後尾・価値最小。落とす判断も可) |

依存: 1 が先(workflow 変更 PR で CI が走る状態を先に作る。2 と 4 は workflow を
足す/触るため)。3 は 2 の後(runtime-api CI を検証土台に使う)。4 と 5 は独立。
4 は 1 と同じ test-dashboard.yml を触るため直列にする(順序は 1 → 4)。

## Why(実装者に渡さない)

- 「19件潰してから Phase 3」というユーザー指示の真意は「未解決指摘の山を放置して
  先へ進まない」こと。全件消化ではなく、山を「採用済み/理由つき棄却済み」に二分して
  ゼロにするのが達成条件。棄却理由をここに固定することで山は消える。
- 実測(5,949 行)の反映で 10 を落とし、代わりに未適用 migration の即時適用を
  運用側へ返す。コード修正より適用の方が価値が高い。
- 本タスク自体はコード変更なし。PR 対象ではなく、後続タスクの最初のコミットに
  `plan(hw):` として同乗させる(untracked プランが clean-tree ゲートを塞ぐため)。
