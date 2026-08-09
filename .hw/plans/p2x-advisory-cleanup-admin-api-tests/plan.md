---
generated_by: fable
task_id: p2x-advisory-cleanup-admin-api-tests
base-commit: 0fe5ea5a4473312412d4ab5c5f48c605668d0949
size: M
---

# admin-api: 一度も CI 検証されなかったテストを修理し「意味のある緑」にする

## ゴール

依頼の文字通りの内容: PR #69 で史上初めて発火した `Test Admin API` が
15 failed / 40 passed で赤になった。P1-4 が3サービス(+mcp)に対してやったこと
(常時未検証状態の解消)を admin-api に対して行う。

reframe: 不要。依頼の文字通りの内容と本当の成果が一致している。ゴールは
**「admin-api のテストが CI で実行され、壊れたら赤くなり、正常なら緑になる」**。
テストの削除・skip による見かけの緑は不可。

## Root cause(実測で確定済み。推測ではない — 2026-08-09、プラン設計時)

CI と同一手順(python3.11 fresh venv `~/.cache/hw-venvs/p2x-admin-api-tests`、
`pip install -e libs/admin-models/ -e services/meeting-api/` →
admin-api は requirements.txt へフォールバック → リポジトリルートから
`pytest services/admin-api/tests/ -v`)で **15 failed / 40 passed を完全再現**した。

### (A) TestRouteDefinitions 13件 — テスト側の欠陥(FastAPI 内部表現への依存)

- 名前衝突ではない。`app.main.__file__` は
  `services/admin-api/app/main.py` を指すことを実測確認(conftest が
  admin-api ルートを sys.path[0] に挿入するため正しく解決される)。
- 真因: requirements.txt の `fastapi>=0.128.8` が unpinned のため CI/venv では
  **fastapi 0.141.1 / starlette 1.6.0** が入る。この版では `include_router()` が
  ルートを `app.routes` へ平坦化せず、**`_IncludedRouter` オブジェクト
  (path=None, methods=None)を1個置くだけ**になった。実測:
  `app.routes` = 9件(既定4 + APIRoute 2 + `_IncludedRouter` 3)。
  ルータ配下のルートは `_IncludedRouter.original_router.routes` の中にいる。
- 製品は健全: 同 venv で `TestUserEndpoints` 等の **HTTP 経由テストは全て pass**
  (ルーティング自体は機能している)。稼働中コンテナ `vexa-admin-api-1`
  (fastapi 0.136.3 / starlette 1.2.1 = 旧・平坦化挙動)でも
  `('/admin/users','POST')` が `app.routes` に存在し、
  `GET /admin/users` が **HTTP 200 で実データを返すことを実測確認**。
- 結論: **製品バグではなくテスト側の欠陥**。`_route_paths_and_methods()` が
  FastAPI のバージョン依存の内部表現(`app.routes` の平坦化)に依存していた。

### (B) test_g5_gate 2件 — 実スタック前提テストが素の pytest で走る

- `test_invalid_token_fail_open` / `test_no_token_fail_open` はフィクスチャを
  使わないため `_skip_if_no_gateway()` を通らず、CI では localhost:8056 への
  接続拒否で fail する(他4件はフィクスチャ内で skip/error)。
- `requests` は meeting-api の依存として venv に入るため
  `pytest.importorskip("requests")` では止まらない。
- P1-4 の前例(meeting-api の実PostgreSQL統合テストを
  `RUN_POSTGRES_INTEGRATION_TESTS=1` opt-in ゲート化、PR #42)と同型の問題。

### 修正方針の実証(両バージョンで確認済み)

`app.openapi()["paths"]` は**公開 API 契約**であり、期待14ルート
(path, method)全てが fastapi 0.141.1(venv)と 0.136.3(稼働コンテナ)の
**両方で missing = [] になることを実測確認済み**。openapi ベースの内省へ
書き換えれば、バージョン非依存でルート登録を検証できる。

### ベースライン凍結ではなく根絶を選ぶ根拠

修正はヘルパー1関数の書き換え + skipif 1行で、両バージョン成立を実証済み。
根絶コストが凍結の管理コストを下回る。また15件全部を凍結すると
TestRouteDefinitions が実質無効化され「意味のある緑」にならない。

## How

変更は4ファイル: `services/admin-api/tests/test_crud.py`、
`services/admin-api/tests/test_g5_gate.py`、
`services/admin-api/tests/conftest.py`、`.github/workflows/test-admin-api.yml`。
製品コード(`services/admin-api/app/**`)には一切触れない。

### 1. `test_crud.py` — `_route_paths_and_methods()` を openapi ベースへ

- `_route_paths_and_methods()` を `app.openapi()["paths"]` から
  `(path, METHOD大文字)` のタプル列を作る実装に書き換える。
  実装イメージ(プラン設計時に両バージョンで検証した形):
  ```python
  def _route_paths_and_methods():
      paths = app.openapi()["paths"]
      return [(p, m.upper()) for p, ops in paths.items() for m in ops]
  ```
- 14個のテストメソッドの期待値(パス・メソッド)は**変更しない**
  (期待値は稼働コンテナの実挙動と一致していることを確認済み)。
- `_IncludedRouter` / `original_router` などの**私有 API へは依存しない**こと。
- 注意: openapi には HEAD や `include_in_schema=False` のルートは出ない。
  既存の期待14件は全て schema に載ることを確認済みなので影響なし。

### 2. `test_g5_gate.py` — opt-in ゲート化(P1-4 / PR #42 の前例に倣う)

- モジュールレベルに追加(meeting-api の
  `tests/integration/test_transcript_search_postgres.py` と同型):
  ```python
  pytestmark = pytest.mark.skipif(
      os.environ.get("RUN_LIVE_STACK_TESTS") != "1",
      reason="実スタック(gateway localhost:8056)が必要。RUN_LIVE_STACK_TESTS=1 で有効化",
  )
  ```
- 既存の `_skip_if_no_gateway()` は残す(opt-in 実行時の二段目ガード)。
- テスト本体・期待値は変更しない。opt-in 実行時にローカルで
  403(ADMIN_API_TOKEN 不一致)や fail-open 期待の陳腐化が観測されているが、
  それは本タスクのスコープ外(PR 本文に既知事項として記載するに留める)。

### 3. `conftest.py` — 死んでいる sys.path 挿入の削除

- 8行目 `sys.path.insert(0, str(_repo / "packages" / "meeting-api"))` は
  存在しないパス(実体は `services/meeting-api` で、CI では editable install
  される)。p14-ci-mcp の前例と同じく**削除**する(パス修正ではなく削除。
  依存解決は pip に一本化する)。
- `libs/admin-models` の挿入と admin-api ルートの挿入は維持
  (ローカルで install なしでも動く配線として実害がない。admin-api ルート
  挿入は `import app` の解決に必須)。
- docstring に実行前提(fresh venv + editable install + requirements.txt)を明記。

### 4. `.github/workflows/test-admin-api.yml` — 欺瞞フォールバックの除去と P1-4 流儀への整合

- `pip install -e services/admin-api/ 2>/dev/null || pip install -r ...` を
  **`pip install -r services/admin-api/requirements.txt` へ一本化**する。
  admin-api に pyproject.toml は無く `-e` は必ず失敗しており、
  「エラーを握り潰して別の手段に落ちる」行は監査上の欺瞞。
- **pyproject.toml は追加しない**(理由は Why 参照)。
- P1-4 の他 workflow(test-transcription-service.yml 等)に合わせて:
  - `jobs.test` に `timeout-minutes: 10` を追加
  - setup-python に `cache: 'pip'` と
    `cache-dependency-path: services/admin-api/requirements.txt` を追加
- actions の SHA ピン・`permissions: contents: read` は現状維持。
- **pull_request paths への workflow 自身の追加はしない**(それは PR #69 の
  差分。ここで足すと #69 とコンフリクトする)。本タスクの PR は
  `services/admin-api/**` の変更を含むため、既存 paths で CI は発火する。
- `continue-on-error` / `|| true` の追加は禁止。

### 5. 検証手順(PR での実証)

1. **ベースライン実測**(実装着手時、base-commit の clean tree):
   fresh venv(python3.11、`~/.cache/hw-venvs/p2x-admin-api-tests` を作り直し)で
   CI と同一手順を実行し、失敗の再現(15 failed 相当)の出力全文を
   `.hw/gates/p2x-advisory-cleanup-admin-api-tests/pytest-baseline-<commit>.txt`
   に保存。**実測値(collected / failed / passed 数)を検証契約本文に記入**する
   (転記禁止。Fable は `.hw/gates/` に到達できないため契約本文が正)。
2. 修正後、同 venv で `pytest services/admin-api/tests/ -v` → failed 0
   (g5_gate は skip 表示)を確認し `pytest-after.txt` に保存。
3. PR 作成 → `Test Admin API` の緑を確認(failed 0、g5 の skip をログで確認)。
4. **sabotage 検証**: TestRouteDefinitions の期待値を1つ壊す一時 commit →
   `Test Admin API` がテストステップで赤 → revert → 緑。run URL を evidence へ。
5. revert 後 run で pip キャッシュ restore を確認。

### 6. PR の順序(PR #69 のブロック解除)

1. 本タスクの PR を先に main へマージする。
2. その後 `hw/p2x-advisory-cleanup-ci-triggers`(PR #69)を main で更新
   (rebase または merge main)すると、`Test Admin API` が修理済みテストで
   走り緑になる。#69 の担当へ引き継ぐ。
   (test-admin-api.yml は #69 が pull_request paths ブロック、本タスクが
   jobs ブロックを触るため行が離れており、機械的に統合可能な見込み。)

## 変更しないもの

- `services/admin-api/app/**`(製品コード。健全性は実測確認済み。FP-001 で機械検証)
- `services/admin-api/requirements.txt`(fastapi のピン留めで問題を隠さない)
- pyproject.toml の新設(しない。Why 参照)
- `libs/admin-models/` / `services/meeting-api/`
- 他の workflow(test-admin-api.yml 以外)
- g5_gate のテスト本体・期待値(opt-in ゲート追加のみ)

## Why(実装者に渡さない)

- **openapi ベースを選んだ理由**: `app.routes` の平坦化は FastAPI の私有実装で
  0.136→0.141 の間に変わった(_IncludedRouter 導入)。`original_router` を
  たどる修正は次の内部変更でまた壊れる。openapi() は公開契約であり、
  「ルートが登録され schema に露出している」というテストの本来の意図にも近い。
  両バージョンで missing=[] を実測済みなのでロールバック・ロールフォワード
  どちらにも耐える。
- **fastapi をピン留めしない理由**: コンテナ(0.136.3)と CI(0.141.1)の乖離は
  requirements の unpinned 運用に由来するが、ピンで CI だけ旧版に固定すると
  「コンテナ再ビルドで新版が入った瞬間に本番だけ新挙動」という逆転が起きる。
  テストをバージョン非依存にする方が正道。依存更新の統制は別課題。
- **pyproject.toml を追加しない理由**: `app` という同名トップレベルパッケージが
  4サービスに存在する。admin-api を `pip install -e` 可能にする=site-packages に
  `app` を置くことであり、環境を共有した瞬間に他サービスと衝突する時限爆弾に
  なる。正しい解決はパッケージリネーム(admin_api 等)を伴う別タスク。
  本タスクでは「黙って失敗するフォールバック」という欺瞞の除去に留める。
- **g5 の fail-open 期待の陳腐化疑い**: ローカル opt-in 実行で 403 を観測
  (テスト内コメント「Gateway changed to fail-closed — update this test!」に
  該当する可能性)。gateway のセキュリティ挙動の確認は本タスクに混ぜると
  検証契約が崩れるため、PR 本文で既知事項として報告し必要なら issue 化。
- **advisory 由来**: 本タスクは advisory 一掃シリーズの一部だが、実体は
  ST-26〜29(常に緑の CI)と同класс の欠陥修理。P1-4 の完了報告に
  「admin-api は未着手」という漏れがあったことを意味する。
