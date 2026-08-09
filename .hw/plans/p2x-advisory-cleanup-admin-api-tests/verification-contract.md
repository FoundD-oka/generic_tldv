# Verification Contract — p2x-advisory-cleanup-admin-api-tests

対象: `base-commit..HEAD` の差分(base-commit =
`0fe5ea5a4473312412d4ab5c5f48c605668d0949`、origin/main)。
検証は commit 済み clean tree に対して行う。

## ベースライン取得手順(転記値の使用禁止)

1. 実装着手前に base-commit の clean tree で fresh venv(python3.11、
   `~/.cache/hw-venvs/p2x-advisory-cleanup-admin-api-tests`。/tmp 禁止)を作成:
   `pip install -e libs/admin-models/ && pip install -e services/meeting-api/ &&
   pip install -r services/admin-api/requirements.txt &&
   pip install pytest pytest-asyncio httpx`
   → リポジトリルートから `pytest services/admin-api/tests/ -v` を実行し、
   出力全文を
   `.hw/gates/p2x-advisory-cleanup-admin-api-tests/pytest-baseline-<commit>.txt`
   に保存する。
2. **実測したサマリ(collected / failed / passed / skipped or errors)を下の
   欄に実装者が記入する**(プラン設計時の参考値 15 failed / 40 passed の転記は
   無効。自分の実測で上書きすること):
   - ベースライン実測値: **collected 59 / 15 failed / 40 passed / 4 errors**
     (2026-08-10、base-commit `0fe5ea5a` の clean tree、fastapi 0.141.1 /
     starlette 1.6.0 / pytest 9.1.1。内訳: TestRouteDefinitions 13 failed、
     test_g5_gate 2 failed + 4 errors)
   - 修正後実測値: **collected 59 / 0 failed / 53 passed / 6 skipped / 0 errors**
     (同一 venv。skipped 6 = test_g5_gate 全件。collected 数はベースラインと同一)
3. 修正後の合格ラインは「failed 0 かつ error 0、テスト collected 数が
   ベースラインから減っていない、g5_gate は skip 表示」。

## 検証の権威分担(3層。各 AT/FP に判定主体を明記)

- **Fable 差分レビュー**: 契約と `base-commit..HEAD` の差分だけで完結する項目
  (テスト書き換えの内容・opt-in ゲートの型・conftest 修正・workflow 差分・
  禁止パターン不在・変更ファイル限定)。**Fable は `.hw/gates/`(gitignore
  領域)に到達できない**ため、evidence 突合が必要な合否判定を Fable に置かない。
  ベースライン/修正後の実測値は本契約の上欄(コミットされる契約本文)で読む。
- **CI(最終権威)**: `Test Admin API` の緑(failed 0)・sabotage 赤・
  キャッシュ効果。
- **ゲート(pr-ready-gate / コーディネータ)**: `.hw/gates/<task>/` の
  ベースライン・修正後ログ・run URL の保存確認、ローカルと CI のテスト数突合。

## Acceptance Tests

| ID | Requirement | Method | Evidence / 判定主体 |
|---|---|---|---|
| AT-001 | **テスト修理(本質要求)**: fresh venv で `pytest services/admin-api/tests/ -v` が failed 0・error 0(g5_gate は skip)。テスト collected 数はベースラインから減っていない(実測値は上欄に記入) | ベースラインと同一 venv で HEAD を実行 | 判定主体: CI(最終権威 = AT-005)。ローカルログ全文は `.hw/gates/<task>/pytest-after.txt`(ゲート確認)。Fable は上欄の実測値記入と AT-002〜004 の差分で判定 |
| AT-002 | `_route_paths_and_methods()` が `app.openapi()["paths"]` ベース(公開 API)になり、`app.routes` の平坦化・`_IncludedRouter`・`original_router` 等の FastAPI 私有表現に依存していない。TestRouteDefinitions の期待値14件(パス・メソッド)は無変更 | test_crud.py の差分レビュー + `grep -n "app.routes\|_IncludedRouter\|original_router" services/admin-api/tests/test_crud.py` が空(または openapi ベースのみ) | 判定主体: Fable(差分) |
| AT-003 | test_g5_gate.py にモジュールレベル `pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE_STACK_TESTS") != "1", ...)` があり(meeting-api の RUN_POSTGRES_INTEGRATION_TESTS と同型)、テスト本体・期待値は無変更 | 差分レビュー | 判定主体: Fable(差分)。CI ログの skip 表示確認はゲート |
| AT-004 | test-admin-api.yml: (1) `pip install -e services/admin-api/ 2>/dev/null \|\|` の欺瞞フォールバックが除去され requirements.txt インストールへ一本化 (2) `timeout-minutes: 10` (3) `cache: 'pip'` + `cache-dependency-path: services/admin-api/requirements.txt` (4) pull_request paths への workflow 自身の追加を**していない**(PR #69 との衝突回避) | workflow 差分レビュー + pyyaml パース | 判定主体: Fable(差分) |
| AT-005 | **CI 実測緑**: PR 上で `Test Admin API` が install → テスト実行まで成功し、ログのサマリ行で failed 0・error 0 を確認 | `gh run list --workflow=test-admin-api.yml` + `gh run view <id> --log` | 判定主体: CI(最終権威)。run URL とサマリ抜粋を `.hw/gates/<task>/ci-green.txt` に保存(ゲート確認) |
| AT-006 | **sabotage 検証(常に緑でないことの実証)**: TestRouteDefinitions の期待値を壊す一時 commit で `Test Admin API` が**テストステップの失敗で**赤 → revert で緑 | 一時 commit → `gh run view --log-failed` → revert | 判定主体: CI。赤/緑の run URL・ログ抜粋を `.hw/gates/<task>/ci-sabotage.txt` に保存(ゲート確認) |
| AT-007 | conftest.py から存在しないパス `packages/meeting-api` への sys.path 挿入が削除され(p14-ci-mcp と同処置)、docstring に実行前提が記載されている | 差分レビュー + `grep -rn "packages/meeting-api" services/admin-api/` が空 | 判定主体: Fable(差分) |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence / 判定主体 |
|---|---|---|---|
| FP-001 | **製品コード無変更**: `git diff base-commit..HEAD -- services/admin-api/app/ services/admin-api/requirements.txt services/admin-api/Dockerfile libs/ services/meeting-api/` が空(稼働中の admin-api を壊す修正を入れない。fastapi ピン留めによる隠蔽もしない) | diff 確認 | 判定主体: Fable(差分) |
| FP-002 | **見かけの緑の禁止**: テストファイルの削除なし。AT-003 の opt-in ゲート以外に新規 skip / skipif / xfail マーカーを追加していない。テストメソッドの削除・リネームなし | `git diff base-commit..HEAD -- services/admin-api/tests/` のレビュー | 判定主体: Fable(差分) |
| FP-003 | 変更ファイルが `services/admin-api/tests/**` と `.github/workflows/test-admin-api.yml` のみ | `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` | 判定主体: Fable(差分)。`.hw/plans/<task>/` は許容リスト |
| FP-004 | 他の workflow(test-admin-api.yml 以外)が無変更 | `git diff base-commit..HEAD -- .github/workflows/ ':!.github/workflows/test-admin-api.yml'` が空 | 判定主体: Fable(差分) |
| FP-005 | workflow に `continue-on-error` / `\|\| true` / エラー握り潰しリダイレクト(`2>/dev/null` 等)が存在しない | grep | 判定主体: Fable(差分) |
| FP-006 | TestUserEndpoints / TestTokenEndpoints 等の HTTP 経由テスト(ベースラインで pass していた40件相当)が修正後も pass(ルート内省の書き換えが実挙動テストを巻き込まない) | AT-001/AT-005 のサマリで failed 0 + collected 数維持 | 判定主体: CI |

## Non-Functional Checks

| ID | Requirement | Method | Evidence / 判定主体 |
|---|---|---|---|
| NFT-001 | actions の SHA ピン(checkout / setup-python)が既存と同一のまま | uses: 行レビュー | 判定主体: Fable(差分) |
| NFT-002 | `permissions: contents: read` 維持。secrets を要求しない | workflow レビュー | 判定主体: Fable(差分) |
| NFT-003 | revert 後の run で pip キャッシュ restore がログに記録される | run ログ | 判定主体: CI。抜粋を evidence へ(ゲート確認) |

## KPI Checks

なし(kpi-backcast-roadmap.md 不使用)。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p2x-advisory-cleanup-admin-api-tests/` へ。ゲート確認用であり Fable レビューの合否根拠には使わない)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no(実測検証を RF-001 に記録済み)
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-001 | FastAPI の `include_router` が `app.routes` を平坦化しない新挙動(`_IncludedRouter`)と、`app.openapi()["paths"]` 内省が新旧両版で成立すること | 実測済み(2026-08-09): venv fastapi 0.141.1/starlette 1.6.0 で `app.routes`=9件・`_IncludedRouter`×3 を確認。期待14ルートの openapi 判定は 0.141.1(venv)と 0.136.3(稼働コンテナ `vexa-admin-api-1`)の両方で missing=[]。覆る条件: fastapi の将来版が openapi paths の形式を変えた場合(公開契約のため可能性低) | 本契約と plan.md の記録 |
| RF-002 | 稼働中 admin-api の健全性(テスト欠陥であって製品バグではないという判定の前提) | 実測済み(2026-08-09): `vexa-admin-api-1` 内で `('/admin/users','POST') in app.routes` == True、`GET /admin/users` が HTTP 200 で実データ返却。実装者は着手時に同コンテナが Up (healthy) であることを `docker ps` で再確認する | `.hw/gates/<task>/` へ docker ps 出力を保存(ゲート確認) |

## 改訂履歴

### 2026-08-10 — 実装者によるベースライン実測と Research Freshness 再検証

上欄「ベースライン実測値 / 修正後実測値」を実測で記入した(転記ではなく、
CI と同一手順の python3.11 fresh venv を作り直して自分で実行)。

**venv パスの差異(記録)**: 本契約は `~/.cache/hw-venvs/p2x-advisory-cleanup-admin-api-tests`、
plan.md §5 と実装指示は `~/.cache/hw-venvs/p2x-admin-api-tests` を指定していた。
後者(plan.md 側)を採用。venv 名の違いのみで手順・結果に影響しない。

**RF-001 再実測(実装前、2026-08-10)** — 新旧両版で確認:

| 環境 | fastapi / starlette | `len(app.routes)` | `app.routes` の型内訳 | 期待14ルートの missing |
|---|---|---|---|---|
| fresh venv (CI 相当) | 0.141.1 / 1.6.0 | 9 | Route 4 / APIRoute 2 / `_IncludedRouter` 3 | `app.routes` 内省: **13件 missing** / `openapi()["paths"]` 内省: **[] (missing なし)** |
| 稼働コンテナ `vexa-admin-api-1` | 0.136.3 / 1.2.1 | 21 | Route 4 / APIRoute 17 | `app.routes` 内省: **[]** / `openapi()["paths"]` 内省: **[] (missing なし)** |

`openapi()["paths"]` から得られる (path, METHOD) は両版とも 16 件で一致。
プラン設計時の判定(FastAPI 新版が `include_router()` を平坦化しないこと、
openapi ベース内省が新旧両版で成立すること)は覆っていない。覆る条件も変更なし。

**RF-002 再確認(実装前、2026-08-10)**: `docker ps` で `vexa-admin-api-1` が
`Up 4 days (healthy)`(image `vexaai/admin-api:latest`)であることを確認。
稼働中コンテナへの破壊的操作は行っていない(読み取りの内省のみ)。

**ローカル sabotage 予備確認(AT-006 の CI 実測はコーディネータが実施)**:
`("/admin/users", "POST")` の期待値を壊すと
`test_create_user_route_exists` が 1 failed になり、復元すると 27 passed に戻ることを確認。
