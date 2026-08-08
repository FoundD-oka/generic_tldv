---
generated_by: fable
task_id: p05-collector-liveness
base-commit: 5cae3a05550e8679b156e88652b0dfa2193d30f1
size: M
---

# ST-21: 実装済み /health/collector を meeting-api の livenessProbe へ配線する

## ゴール

依頼の文字通りの内容: P0-5 のうち ST-21「実装済みの /health/collector が
liveness 未配線」を解消する(監査根拠: current-state.md ST-21、
main.py:203-260 / helm deployment-meeting-api.yaml:155-160)。

reframe なし: main.py:203 の docstring 自体が「kubelet livenessProbe に載せて
consumer 停滞時に pod を再起動させる」ことを設計意図として明記しており、
依頼と意図が一致している。配線だけを行う。

## 対象

- 変更: `deploy/helm/charts/vexa/templates/deployment-meeting-api.yaml` のみ
- 不変更(禁止): readinessProbe ブロック(/readyz、:147-153)/
  `Chart.yaml`(version bump しない。ローカルチャートパスからのデプロイ運用
  のため不要で、bump すると chart-release.yml が公開リリースを発火する)/
  `services/meeting-api/**`(main.py 含む)/ `deploy/compose/docker-compose.yml`
  (Codex 並行変更中。compose healthcheck は ST-20/Phase 1 の責務)/
  st9〜st14 対象ファイル / p05-detail-wiring 対象ファイル / vexa-lite チャート

## 実装 How(設計判断。実装者の裁量に委ねない)

base-commit 時点の :154-160 を次のとおり書き換える(それ以外の行は不変更)。

1. :154 のコメント
   `# /health = HTTP-loop liveness (always 200 unless event loop wedges)` を
   以下へ差し替える:
   ```
          # v0.10.5 Pack C.3 — lag-aware liveness (main.py /health/collector).
          # 503 when consumer stalled (lag>100 AND idle>60s); kubelet restarts
          # the pod within failureThreshold × periodSeconds (worst case ~60s).
   ```
2. livenessProbe ブロックを以下の逐語形へ(path 変更 + failureThreshold: 3 を
   明示。initialDelaySeconds 45 / periodSeconds 20 は据え置き):
   ```
          livenessProbe:
            httpGet:
              path: /health/collector
              port: http
            initialDelaySeconds: 45
            periodSeconds: 20
            failureThreshold: 3
   ```
   インデントは既存ブロックと同一(10/12/14スペース)。failureThreshold: 3 は
   k8s 既定値と同値であり挙動を変えず、readinessProbe との対称性と検知時間
   (20s×3=60s)の明示のためだけに書く。

フラッピング安全性(確認済み・実装不要の前提):
`/health/collector` は「lag > 100 かつ max idle > 60s」でのみ 503。Redis の
一時エラーは 200 を返す(main.py:258-264)。起動直後の redis_client 未初期化
503 は initialDelaySeconds: 45 が吸収する。/health エンドポイント自体は
main.py に残る(他利用者向け。削除・変更しない)。

## 段取り

- main `5cae3a0` から新規 worktree。p05-detail-wiring(PR #47)と非交差のため
  マージ待ち不要。コミット前に変更ファイルが1つだけであることを機械確認
  (検証契約 FP-002)。

## Why(実装者に渡さない)

- Pack C.3 の実装者は「C.1/C.2 が拾えない停滞モード(DB書き込み中のハング等)
  の最終防衛線」として liveness 配線まで含めて設計したが、配線だけが漏れた。
  現行の /health は HTTP ループ死のみ検知し、consumer 停滞では pod が再起動
  されず文字起こしセグメントが Redis stream に滞留し続ける。P0 の主目的
  「確定文字起こしを消さない・取り逃さない」に直結する低コスト・高効果の穴埋め。
- Chart.yaml を bump しないのは、リリース発火という本番影響作用を「配線」タスク
  の副作用にしないため。デプロイ経路(lke-setup-helm.sh)はローカルパス参照
  なので bump なしで効く。
