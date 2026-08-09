# Verification Contract — p15-deploy-hardening(親契約)

本タスクは2サブタスクへ分割して実装する。契約の実体は各サブタスクの契約であり、
本親契約の完了条件は次の2点のみ。

1. `p15-compose-deps-limits` の契約(同ディレクトリの verification-contract.md)が
   PASS し PR がマージされていること。
2. `p15-docker-events-timeout` の契約が PASS し PR がマージされていること。

## 監査 ID → 検証項目の対応(判定はすべてサブタスク契約側で行う)

| 監査 ID | 要求 | 検証先 |
|---|---|---|
| ST-24 | compose の runtime-api に mem_limit(healthcheck は #60 済、helm は 46fa31d 済) | p15-compose-deps-limits AT-101/102 |
| ST-25 | depends_on のインフラ依存を service_healthy へ昇格し、昇格しない依存の判断を明文化・機械検証 | p15-compose-deps-limits AT-103〜105 / FP-103 |
| ST-23 | Docker event ストリームの半開き検知(read timeout + since リプレイ + 再接続) | p15-docker-events-timeout AT-201〜204 |
| #57 advisory | runtime-api の TRANSCRIPTION_SERVICE_URL 空既定に startup warning | p15-docker-events-timeout AT-205 |

## Gate Requirements

- preflight result required: yes(各サブタスク側)
- evidence pack required: yes(各サブタスク側)
- hash-bound approval required: yes(両サブタスクとも M、Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed
