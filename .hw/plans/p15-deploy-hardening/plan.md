---
generated_by: fable
task_id: p15-deploy-hardening
base-commit: b6abdd74a9b15960902545272d1ab408b6af5b84
size: M
type: umbrella
subtasks:
  - p15-compose-deps-limits   # ST-24 + ST-25(先行)
  - p15-docker-events-timeout # ST-23 + #57 advisory(後続)
---

# P1-5 デプロイ堅牢化(ST-23 / ST-24 / ST-25)— 親プラン

## 依頼の文字通りの内容と再設計後のゴール

文字通りの依頼: 「ST-24 runtime-api の mem 制限・healthcheck、ST-25 depends_on 条件付与、
ST-23 Docker event ストリームの read timeout」。

reframe(現物再監査に基づく精密化。監査 2026-08 時点から状況が動いている):

1. **ST-24 の「healthcheck」は #60 で解消済み**(compose の runtime-api に
   healthcheck あり、`deploy/compose/docker-compose.yml:192-199`)。
   **helm 側の mem 制限も解消済み**(`deploy/helm/charts/vexa/values.yaml:180-186` に
   `limits.memory: 512Mi` あり。監査後の `46fa31d` で整備された)。
   残る実体は **compose の runtime-api に `mem_limit` がない**ことだけ。
2. **ST-25** は「起動直後の参加失敗の増幅」が本質だが、増幅元の ST-1 は P0-4(#46 の
   3回指数バックオフ)で既に緩和済み。したがって service_healthy への全面昇格は不要で、
   達成すべき成果は「**インフラ依存(redis/postgres/minio)への昇格 + 昇格しない判断の
   明文化 + 構造デッドロックの機械検証**」。アプリ間依存を healthy で縛ると、
   bot サブシステム単独の故障が全製品停止(文字起こし閲覧も不可)へ増幅するため縛らない。
3. **ST-23** は「timeout を付ける」ことではなく「**半開き接続でも bot の die イベントを
   取り逃さない**」が成果。bot profile は `idle_timeout: 0` のため、die イベントを
   取り逃すと会議が永久 active のまま残る(イベント経路が唯一の exit 通知)。
   read timeout 単体では正常時に定期例外が出るだけなので、
   再接続 + `since` リプレイ(切断中のイベント再取得)までを1セットで設計する。

ゴール(3点とも機械検証可能):

- compose: runtime-api に mem_limit(512m、helm 実測由来)が付き、depends_on の
  インフラ依存が service_healthy へ昇格し、両方が静的検証スクリプト + CI で固定される。
- runtime-api: Docker event ストリームが read timeout + since リプレイ付き再接続になり、
  半開き検知不能・イベント消失のモードが閉じる。正常時のログは汚れない。
- #57 advisory: runtime-api の `TRANSCRIPTION_SERVICE_URL` 空既定に startup warning が付く。

## タスク分割の判断: 2タスク(2PR)へ分割する

| task-id | スコープ | 着手順 |
|---|---|---|
| `p15-compose-deps-limits` | ST-24(compose mem_limit)+ ST-25(depends_on 昇格)+ 静的検証拡張 + CI workflow | 1(先行) |
| `p15-docker-events-timeout` | ST-23(event stream read timeout + since リプレイ)+ #57 advisory(env warning) | 2(後続) |

根拠:

- **変更面が完全に分離している**: 前者は `deploy/compose/` + workflow のみ、後者は
  `services/runtime-api/` のみ。検証手段も別物(compose config 静的検査 vs pytest)。
- **revert 粒度**: event stream はコア経路(bot exit 検知)の挙動変更。問題発生時に
  deploy 設定の堅牢化まで巻き戻さずに済むよう PR を分ける。
- **P1-2 / P1-3 の分割前例と整合**(1監査テーマ=変更面ごとに1タスク)。
- 順序は compose 先行: コード変更なしの低リスク側を先に出し、後続タスクの
  ベースライン実測(runtime-api テスト)を compose 変更と混ぜない。
- 各サブタスクのプラン・契約は `.hw/plans/<sub-task-id>/` に作成済み。
  **base-commit は各サブタスクの着手時にコーディネータが `git rev-parse HEAD` で
  更新すること**(p13 以降の運用と同じ。特にタスク2はタスク1マージ後の HEAD にする。
  さもないと Fable レビュー対象差分に他タスクの変更が混入する)。

## スコープ判断(申し送り事項への回答)

- **#57 advisory(runtime-api の TRANSCRIPTION_SERVICE_URL 空既定)= 含める**。
  根拠: runtime-api は profiles.yaml(`profiles.yaml:40`)経由でこの値を bot コンテナへ
  注入する。空のまま起動すると bot が空 URL を受け取り実況文字起こしが黙って死ぬ。
  meeting-api 側は #57 で fail-fast 済みだが、逃げ道 `STARTUP_ENV_VALIDATION=warn`
  使用時は素通りする。**warning のみで fail-fast にはしない**(#57 の教訓:
  必須を増やすと開発環境が起動できず warn 常用に堕ちる。ハードケースは meeting-api の
  fail-fast が既に担う)。同一サービス・同一テスト基盤のためタスク2に同梱(+約10行)。
- **runtime-api の CI 整備 = 含めない**。根拠: base 時点から18件 fail(環境依存)の
  triage という独立の不確定性を持ち込む。P1-5 の監査 ID(ST-23/24/25)に含まれず、
  ベースライン固定方式の設計だけで1タスク相当。タスク2の契約は「fresh venv での
  着手時実測ベースライン比の非退行」で CI なしでも成立する。
  → 別タスク候補 `p15x-ci-runtime-api` として handoff へ advisory 記録すること。
- **compose 検証の CI workflow = タスク1に含める**。根拠: #60 が作った
  `verify_observability_config.py` は「将来 CI に組める形」のまま未配線で、
  depends_on/mem_limit の契約を本タスクで足しても CI が最終権威にならない。
  workflow 1本(paths: `deploy/compose/**` + workflow 自身。P1-4 の盲点対策)は
  数十行で、ST-24/25 の退行防止が恒久化する。

## 完了条件

両サブタスクの検証契約が PASS し、それぞれの PR がマージされること。
監査 ID との対応: ST-24 → タスク1 AT-101/102、ST-25 → タスク1 AT-103〜105、
ST-23 → タスク2 AT-201〜204、#57 advisory → タスク2 AT-205。

## Why(実装者に渡さない)

- ST-25 で昇格対象をインフラ(redis/postgres/minio)に限定する理由:
  vendor 純正 healthcheck は「healthy にならない=そのサービス自体が死んでいる」と
  等価で、never-healthy デッドロックのリスクが実質ゼロ。一方アプリの healthcheck
  (meeting-api /readyz、runtime-api /health=Redis 依存)は「劣化しているが他機能は
  生きている」状態を含むため、依存条件にすると部分故障が全体停止へ増幅する。
  依頼側が明示した懸念(never healthy → compose 全体が起動しない)への直接回答。
- runtime-api の mem_limit を 512m にする理由: helm の
  `limits.memory: 512Mi`(「Load-tested shape」コメント付き、実測由来)と一致させる。
  #55 の回帰カナリア思想に従い、「余裕を見て大きく」はしない。新規上限なので
  カナリア据え置きの対象ではないが、根拠なしに上げると退行が隠れる点は同じなので、
  compose コメントに値の由来と「上げるには実測根拠が必要」を明記させる。
- OOMKill 検知導線: compose は `restart: unless-stopped` のため OOM 後に自動再起動し、
  `docker ps`(RestartCount / STATUS)と `docker inspect .State.OOMKilled`、
  ST-19 のログ(起動ログ再出現)で検知可能。runtime-api は再起動時に
  `reconcile_state` で状態を再同期する。ただし reconcile は exit callback を
  発火しない(既存挙動)ため、OOM 再起動の瞬間に die した bot の callback が落ちる
  可能性は既知の残存リスク(advisory。本タスクで直さない)。
- ST-23 で reconcile 定期実行でなく since リプレイを選ぶ理由: 定期 reconcile は
  「stopped をマークするだけで callback を発火しない」既存関数の再利用では
  会議が active のまま残る問題を解かず、callback 発火付き reconcile の新設は
  イベント経路との二重発火設計が必要になり重い。since リプレイは既存の on_exit
  経路をそのまま通るため、重複配送(ナノ秒精度で回避)以外の新規リスクがない。
- Docker daemon のイベントバッファは直近256件。切断が長引き256件を超えて
  溢れた場合のイベントは since でも戻らない(bot 数規模から実質起きないが、
  完全化するなら将来の callback 発火付き reconcile が受け皿。advisory)。
