# Verification Contract — p05-collector-liveness

前提: ローカル・CI とも helm バイナリでのテンプレートレンダリングは不可。
機械検証はソース逐語契約で行う(probe ブロックは Go テンプレートを含まない
純 YAML 区間であることを確認済み)。コマンドはすべてリポジトリルートで実行。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | meeting-api の livenessProbe が /health/collector を逐語形(下記スクリプト内の期待ブロック: path=/health/collector, port=http, initialDelaySeconds=45, periodSeconds=20, failureThreshold=3, インデント10/12/14スペース)で指す | 下記「機械検証スクリプト」実行で `PROBE-CONTRACT OK` | コマンド出力 |
| AT-002 | チャート構造検証が通る(helm 不在時は構造チェックのみで pass) | `bash deploy/helm/tests/test_helm_lint.sh` | 実行ログ(exit 0) |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | readinessProbe ブロックが base-commit と逐語一致で不変(path=/readyz, 15/10/3) | 機械検証スクリプト(readiness 側の逐語断言を含む) | AT-001 と同一出力 |
| FP-002 | 変更ファイルが `deploy/helm/charts/vexa/templates/deployment-meeting-api.yaml` の1つ(+ `.hw/plans/p05-collector-liveness/` 配下)のみ。禁止リスト(Chart.yaml / services/meeting-api/** / deploy/compose/docker-compose.yml / pyproject.toml / .github/workflows/** / sweeps.py / meetings.py / retry.py / src/types/vexa.ts / test_meeting_status_display.test.ts / meeting-card.tsx / login/page.tsx / test_meeting_cards_ui.test.ts / p05-detail-wiring 対象2ファイル / vexa-lite チャート)との交差が空 | `git diff --name-only 5cae3a0..HEAD` | コマンド出力 |
| FP-003 | `/health/collector` と `/health` の両エンドポイントが main.py に存続(受け口ロック。ファイルは変更しない) | `grep -n '@app.get("/health' services/meeting-api/meeting_api/main.py` が /health と /health/collector の2行を返す | コマンド出力 |
| FP-004 | Chart.yaml の version が `0.10.6+3` のまま(公開リリース非発火) | `grep '^version:' deploy/helm/charts/vexa/Chart.yaml` | コマンド出力 |
| FP-005 | `make smoke`(.hw/verify.sh)で verify-baseline に無い新規失敗0件 | `bash .hw/verify.sh` | 実行ログ |

## 機械検証スクリプト(AT-001 / FP-001。この形で固定)

```bash
python3 - <<'EOF'
FILE = "deploy/helm/charts/vexa/templates/deployment-meeting-api.yaml"
src = open(FILE, encoding="utf-8").read()

LIVENESS = (
    "          livenessProbe:\n"
    "            httpGet:\n"
    "              path: /health/collector\n"
    "              port: http\n"
    "            initialDelaySeconds: 45\n"
    "            periodSeconds: 20\n"
    "            failureThreshold: 3\n"
)
READINESS = (
    "          readinessProbe:\n"
    "            httpGet:\n"
    "              path: /readyz\n"
    "              port: http\n"
    "            initialDelaySeconds: 15\n"
    "            periodSeconds: 10\n"
    "            failureThreshold: 3\n"
)
assert LIVENESS in src, "livenessProbe block mismatch"
assert READINESS in src, "readinessProbe block regressed"
assert src.count("livenessProbe:") == 1, "unexpected extra livenessProbe"
assert src.count("readinessProbe:") == 1, "unexpected extra readinessProbe"
assert "path: /health\n" not in src, "old /health liveness path still present"
print("PROBE-CONTRACT OK")
EOF
```

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | 追加コメントが probe の発火条件と検知時間を誤りなく記述(main.py:203-260 の実装と矛盾しない: lag>100 AND idle>60s、20s×3=60s) | Fable 契約レビューでの目視照合 | レビュー記録 |

## KPI Checks

対象外(kpi-backcast-roadmap.md なし)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: yes
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

対象外(k8s livenessProbe の標準セマンティクスのみに依存し、外部APIの現行仕様
に依存しない)。

## 既知の検証限界(advisory、契約外)

- 実クラスタでの probe 発火(consumer 停滞→pod 再起動)はローカル検証不能。
  次回 LKE デプロイ時に `kubectl describe pod` で livenessProbe path を確認する
  こと(P0-9 相当の運用確認に委ねる)。
- 公開チャートリポジトリ利用者への伝播は次回の通常 Chart.yaml version bump に
  相乗りする(本タスクでは bump しない)。
