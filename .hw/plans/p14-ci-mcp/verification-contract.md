# Verification Contract — p14-ci-mcp

対象: `base-commit..HEAD` の差分(base-commit はコーディネータが着手時に
plan.md frontmatter へ書き込んだ値)。検証は commit 済み clean tree に対して行う。

## ベースライン取得手順(転記値の使用禁止)

1. 実装着手前に base-commit の clean tree で fresh venv(python3.11、
   `~/.cache/hw-venvs/p14-ci-mcp` 等 /tmp 以外)を作成:
   `pip install -e libs/admin-models/ -e services/meeting-api/ pytest`
   → `python -m pytest services/mcp/tests/ -v` を実行し、**収集エラーで
   失敗すること**(conftest が存在しない packages/meeting-api を参照)を含む
   出力全文を `.hw/gates/p14-ci-mcp/pytest-baseline-<commit>.txt` に保存する。
2. 本タスクのベースラインは「壊れている」ことの記録であり、修理後の合格値は
   **修理後の実測**(全収集・全pass のテスト数)で確定する。転記値は無効。
3. meeting-api 側の回帰がないことの確認用に、meeting-api テストのベースライン
   は不要(本タスクは meeting-api に触れない。FP-004 の diff 確認で足りる)。

## 検証の権威分担(3層。各 AT/FP に判定主体を明記)

- **Fable 差分レビューで判定するもの**: 契約と `base-commit..HEAD` の差分だけで
  完結する項目(conftest 修正内容・重複ファイル削除・workflow 構文・禁止
  パターン不在・変更ファイル限定・skip/xfail 不追加)。**Fable は `.hw/gates/`
  (gitignore 領域)に到達できない**ため、合否判定を evidence pack との突合に
  依存する AT/FP を置いてはならない。
- **CI 実行結果に委ねるもの(最終権威)**: Linux での install + 全テストの
  収集・実行成功(収集エラーがあれば pytest は非0 exit で赤になるため、
  「収集の修理」の機械検証は CI 緑そのもの)・sabotage 赤・キャッシュ効果。
- **ゲート(pr-ready-gate / コーディネータ)が evidence pack で確認するもの**:
  `.hw/gates/p14-ci-mcp/` のベースライン(壊れている記録)・修理後ローカル
  実測・run URL の保存と内容確認(ローカルと CI のテスト数の突合を含む)。
  evidence はレビューの合否根拠ではなく、ゲート通過の証跡。

## Acceptance Tests

| ID | Requirement | Method | Evidence / 判定主体 |
|---|---|---|---|
| AT-001 | **収集の修理(本質要求)**: `python -m pytest services/mcp/tests/ -v` が収集エラー0で全テスト実行・全pass(テスト数は修理後実測で確定し evidence に記録) | 機械検証は AT-005 の CI 緑(収集エラー・fail があれば pytest 非0 で赤)。ローカルはベースラインと同一 venv で HEAD を実行 | 判定主体: CI(最終権威)。ローカル実行ログ全文は `.hw/gates/p14-ci-mcp/pytest-after.txt` に保存(ゲート確認)。レビュー層は AT-002/AT-003 の差分確認 + CI 緑で判定 |
| AT-002 | conftest.py から存在しないパス(packages/meeting-api)への sys.path 挿入が除去され、docstring に実行前提(meeting-api の editable install)が記載されている | conftest.py のレビュー + `grep -rn "packages/meeting-api" services/mcp/` が空 | 判定主体: Fable(差分)。grep 出力は evidence へ |
| AT-003 | 重複ファイル `services/mcp/test_parse_meeting_url.py`(サービス直下の旧版)が削除され、`pytest services/mcp/` でも旧版が収集されない | `git diff --name-status` で削除確認 + `python -m pytest services/mcp/ --collect-only -q` | 判定主体: Fable(差分で削除確認)。collect 出力は evidence へ(ゲート確認) |
| AT-004 | workflow が有効な YAML で、trigger paths が push: `services/mcp/**` + `services/meeting-api/meeting_api/schemas.py` + workflow 自身 / pull_request: `services/mcp/**` + `services/meeting-api/**` + workflow 自身(test-api-gateway.yml の流儀) | pyyaml でのパース + 目視レビュー | 判定主体: Fable(差分)。パース出力は evidence へ |
| AT-005 | **CI 実測緑**: PR 上で `Test MCP` が install → テストまで成功する(収集エラー0・failed 0 をログのサマリ行で確認) | `gh run list --workflow=test-mcp.yml` + `gh run view <id> --log` | 判定主体: CI(最終権威)。run URL とログ抜粋を `.hw/gates/p14-ci-mcp/ci-green.txt` に保存。ローカル実測(AT-001)とのテスト数突合は**ゲート**が evidence で行う |
| AT-006 | **sabotage 検証(常に緑でないことの実証)**: テスト期待値を壊す一時 commit で「テストステップの失敗で」赤 → revert で緑 | 一時 commit → `gh run view --log-failed` → revert | 判定主体: CI。赤/緑 run URL・ログ抜粋を `.hw/gates/.../ci-sabotage.txt` に保存(ゲート確認) |
| AT-007 | pip キャッシュ設定があり、2回目以降の run で cache restore が記録される | revert 後 run のログ | 判定主体: CI。ログ抜粋は evidence へ(ゲート確認) |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence / 判定主体 |
|---|---|---|---|
| FP-001 | 握り潰しなし: workflow に `continue-on-error` / `\|\| true` がなく、テストに新規の skip / xfail マーカーを追加していない | grep(workflow + `git diff base-commit..HEAD -- services/mcp/tests/` 内の skip/xfail) | 判定主体: Fable(差分) |
| FP-002 | `services/mcp/main.py` に差分なし(実バグ発見時は停止・報告し、本タスクで製品コードを直さない) | `git diff base-commit..HEAD -- services/mcp/main.py` が空 | 判定主体: Fable(差分) |
| FP-003 | 変更ファイルが `services/mcp/tests/**`(conftest 修正 + §3 の範囲のテスト期待値修正)・`services/mcp/test_parse_meeting_url.py`(削除)・`.github/workflows/test-mcp.yml`(新規)のみ | `git diff --name-only base-commit..HEAD` | 判定主体: Fable(差分) |
| FP-004 | meeting-api / libs/admin-models に差分なし | `git diff base-commit..HEAD -- services/meeting-api/ libs/` が空 | 判定主体: Fable(差分) |
| FP-005 | テスト期待値を修正した場合、各修正に「main.py のどの実装を正としたか」の根拠が PR 本文に記載されている | PR 本文レビュー | 判定主体: Fable(PR 本文) |
| FP-006 | 既存 workflow が無変更 | `git diff base-commit..HEAD -- .github/workflows/ ':!.github/workflows/test-mcp.yml'` が空 | 判定主体: Fable(差分) |

## Non-Functional Checks

| ID | Requirement | Method | Evidence / 判定主体 |
|---|---|---|---|
| NFT-001 | actions は既存 test-*.yml と同一の SHA ピンを使用 | uses: 行レビュー | 判定主体: Fable(差分) |
| NFT-002 | secrets を要求しない(contents: read のみ)。fastapi-mcp / mcp を CI で install していない(スタブ設計の維持) | workflow レビュー | 判定主体: Fable(差分) |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p14-ci-mcp/` へ。ゲート確認用であり、Fable レビューの合否根拠には使わない)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

- 外部ライブラリの最新動向への依存なし(fastapi-mcp はスタブで遮断、
  meeting-api は同リポジトリの editable install)。

## 改訂履歴

- **2026-08-09 改訂1**(Fable。p14-ci-dashboard の Fable レビュー NEEDS_HUMAN
  で判明した契約欠陥の横展開点検による。実装コードの変更なし)
  - **原因**: 旧 AT-001(修理後の全pass)の合否がローカル実測の evidence にのみ
    置かれ、旧 AT-005 が「AT-001 の実測と同数」という evidence 間突合を要求して
    おり、Fable レビュー層(差分のみ・`.hw/gates/` 到達不能)では判定不能な
    条件だった。
  - **改訂後**: 検証の権威分担を3層(Fable差分 / CI / ゲート)に明確化し、
    全 AT/FP に判定主体を明記。AT-001 の機械検証を CI 緑(pytest は収集エラー
    でも非0 = 修理の成否が CI で直接判定される)へ寄せ、AT-005 の合格条件を
    CI ログ単独で判定可能な「収集エラー0・failed 0」に変更。ローカル/CI の
    テスト数突合はゲートの evidence 確認へ付け替え。要求内容・合格ラインは不変。
  - **注記(復元)**: 改訂作業時、`.hw/plans/p14-ci-mcp/` 一式(未コミット)が
    ローカルから消失していることを検出した(消失原因は本セッション外)。
    プラン設計セッションの原本内容から plan.md / sml-decision.json /
    runtime-decision.json を復元し、本契約は改訂1を適用した版として再作成した。
    mcp タスク自体の採否はコーディネータ判断(plan.md の Why 参照)。
