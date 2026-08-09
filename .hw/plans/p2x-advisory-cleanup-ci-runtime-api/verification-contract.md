# Verification Contract — p2x-advisory-cleanup-ci-runtime-api

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体の凡例: **Fable** = 差分と本契約のみで判定(`.hw/gates/` に到達できない。
実測値は本契約の改訂履歴に記入されたものと照合)/ **CI** = GitHub Actions /
**ゲート** = pr-ready-gate 実行者が `.hw/gates/p2x-advisory-cleanup-ci-runtime-api/`
の証跡を確認。

## ベースライン取得手順(転記値の使用禁止。handoff の「18件 fail / ruff 119」を転記しない)

1. base-commit の clean tree で python3.11 fresh venv を
   `~/.cache/hw-venvs/p2x-advisory-cleanup-ci-runtime-api` に作成し
   `pip install -e "services/runtime-api/[dev]"`。
2. `python -m pytest services/runtime-api/tests` のサマリ全文(passed/failed/skipped
   と failed の node-id 一覧)と `ruff check services/runtime-api --output-format json`
   の rule 別件数を `.hw/gates/p2x-advisory-cleanup-ci-runtime-api/baseline-<commit>.txt`
   に保存し、**本契約末尾の改訂履歴にも記入して commit する**(Fable が差分のみで
   判定できるようにするため)。
3. CI(PR 実走)での failed 集合がローカルと異なる場合、**commit するベースラインは
   CI 実測値**とし、その値も改訂履歴に記入する(RF-501)。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-501 | `.github/workflows/test-runtime-api.yml` が存在し、push/pull_request 両方の paths に `services/runtime-api/**` と自ファイルを含む | diff + 静的検査 | Fable + ゲート | diff / grep 出力 |
| AT-502 | 本 PR で workflow「Test Runtime API」が実走して緑 | `gh run list --branch <branch>` で workflow 名を確認(job 名ではない) | CI + ゲート | gh run list 出力 |
| AT-503 | pytest ベースライン比較: ベースライン外の fail/error で exit 非0、ベースライン内のみなら exit 0 | 比較スクリプトのユニットテスト(合成レポートで両方向を固定) | Fable(テストソース)+ ゲート(pytest ログ) | pytest ログ |
| AT-504 | collection error は無条件 exit 非0(「収集0件で緑」の禁止、#64 の教訓) | 比較スクリプトのユニットテスト | Fable + ゲート | pytest ログ |
| AT-505 | ruff ラチェット: rule 別件数がベースライン超過で exit 非0、同数以下で exit 0 | 比較スクリプトのユニットテスト | Fable + ゲート | pytest ログ |
| AT-506 | サボタージュ実証: 一時的に必ず fail するテストを加えた状態でベースライン比較が exit 非0 になる(commit しない。ローカル実行の記録のみ) | ローカル実行ログ | ゲート | 実行ログを `.hw/gates/` へ |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-501 | 変更ファイルが `.github/workflows/test-runtime-api.yml` と `services/runtime-api/tests/ci/` 配下のみ: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` で確認 | diff | Fable | diff |
| FP-502 | `services/runtime-api/runtime_api/`・既存テストファイル・conftest・`pyproject.toml` に差分なし(CI 整備の名目でテスト対象を書き換えない) | diff | Fable | diff |
| FP-503 | 既存テストの実行結果がベースラインどおり(新規 fail 0。redis service 追加で fail→pass に転じるのは可、その場合ベースラインから除外して commit) | 同一 venv・同一コマンドのサマリ比較 | ゲート + Fable(改訂履歴と照合) | 両サマリ全文 |
| FP-504 | 比較スクリプトは stdlib のみ(新規 python 依存なし) | ソースレビュー | Fable | diff |

## Non-Functional Checks

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| NFT-501 | workflow が既存流儀に従う: action SHA ピン、permissions: contents: read、timeout-minutes 指定 | diff レビュー | Fable | diff |

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-501 | CI 環境(ubuntu runner)での failed 集合はローカル(macOS)と異なり得る。ベースラインは CI 実測で確定する | PR 実走のログから failed 集合を採取し、改訂履歴に記入 | 改訂履歴 + `.hw/gates/` のログ |
| RF-502 | redis service コンテナで解消する fail の集合(推測禁止。実測で確定) | CI 実走(redis あり)との比較 | 改訂履歴 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p2x-advisory-cleanup-ci-runtime-api/`)
- hash-bound approval required: yes(M のため Fable 契約レビュー必須)
- research brief required: no(RF は契約本文の改訂履歴で代替)

## 改訂履歴

### 2026-08-10 base-commit 更新

プラン作成後に PR #69/#70 が main へマージされたため、base-commit を
`0fe5ea5a4473312412d4ab5c5f48c605668d0949` → `09375ac204bc1173496546dd63830d2128a7b7c8`
へ更新した(`plan.md` frontmatter と `base-commit` の両方)。

### 2026-08-10 実測ベースライン(base-commit 09375ac、clean tree)

測定コマンド(共通): `pip install -e "services/runtime-api/[dev]"` の後
`python -m pytest services/runtime-api/tests` / `ruff check services/runtime-api --output-format json`。
証跡は `.hw/gates/p2x-advisory-cleanup-ci-runtime-api/`(gitignore のため非コミット)。

**pytest — 環境別に結果が割れた。これが本タスクの要点。**

| 環境 | 結果 |
|---|---|
| macOS arm64 / python3.11 fresh venv(`~/.cache/hw-venvs/p2x-advisory-cleanup-ci-runtime-api`) | **18 failed, 107 passed, 7 skipped** |
| linux/arm64 `python:3.11-slim` コンテナ(runner 相当) | **0 failed, 105 passed, 27 skipped** |
| linux/amd64 `python:3.11-slim` コンテナ(ubuntu-latest と同アーキ) | **0 failed, 105 passed, 27 skipped** |
| linux/arm64 + redis 7 サービスコンテナ(`REDIS_URL` 指定) | **0 failed, 105 passed, 27 skipped** |

macOS で失敗した 18 件の内訳と原因:

- `tests/test_integration.py` 12 件(`TestHealth::test_profiles_loaded`,
  `TestContainerLifecycle::test_01_create_container`〜`test_07_list_after_stop`,
  `TestErrors::test_unknown_profile` / `test_inspect_nonexistent` / `test_delete_nonexistent`,
  `TestCallback::test_callback_url_accepted`)
- `tests/test_integration_process.py` 5 件(`TestProcessLifecycle::test_01_create_process`〜`test_05_process_stopped`)
- `tests/test_backends.py::test_process_backend_inspect` 1 件

原因は**開発機固有**。前者 17 件は開発機で稼働中の runtime-api が `localhost:8090` を
listen しているため `check_service` の skip 分岐(`httpx.ConnectError` 捕捉)に入らず、
API キー未設定で 403 を返して落ちる。runner では何も listen していないので
`ConnectError` → skip になる(linux 実測の skipped 27 = macOS の 7 +
`test_integration.py` 13 + `test_integration_process.py` 7。この 20 件は macOS では
17 failed / 3 passed だった)。後者 1 件は macOS で `sleep 100` の子プロセスが inspect
時点で `exited` と判定されるプラットフォーム差で、linux では pass する。

したがって **CI(ubuntu-latest)で凍結すべき既知 fail は 0 件**であり、
`services/runtime-api/tests/ci/pytest-baseline.json` の `known_failures` は `[]` で commit した。
handoff の「18件 fail」を転記していたら、runner では起きない失敗を恒久的に免罪する
過大ベースラインになっていた(RF-501)。

**ruff — 環境非依存。macOS / linux とも完全一致で total 117 件(ruff 0.16.2)。**

rule 別: ASYNC220 1 / ASYNC222 1 / ASYNC230 1 / B023 1 / BLE001 17 / F401 20 / F841 1 /
G201 4 / I001 12 / PLW0602 1 / PLW1509 1 / RUF013 4 / S110 7 / SIM102 1 / SIM115 2 /
UP017 1 / UP035 5 / UP041 1 / UP045 36。これを `ruff-baseline.json` に固定した。
ruff の既定 rule セットはバージョンで変わるため、workflow では `ruff==0.16.2` を
install してベースラインの意味を固定している。

### 2026-08-10 RF-502(redis サービスコンテナで解消する fail 集合)

**実測結果: 0 件**。linux コンテナ + redis 7 サービス(`REDIS_URL` を注入)で
`0 failed, 105 passed, 27 skipped` となり、redis なしの実測と完全に一致した。
runtime-api のユニットテストは fakeredis / 自前 FakeRedis を使っており、実 redis を
要求するものはない(実 redis を要求する `test_integration*.py` は runtime-api 本体の
HTTP 起動も要求するため、どちらにせよ runner では skip される)。
よって workflow に redis の service コンテナは置いていない(置いても解消する fail が
無く、CI 時間だけ増えるため)。合格ラインは「新規 fail 0」で不変。

### 2026-08-10 実装後の実測(PR 実走前、runner 相当のコンテナで workflow 手順を再現)

- `python -m pytest services/runtime-api/tests` → **122 passed, 27 skipped, 0 failed**
  (105 + 本 PR で追加したラチェットのユニットテスト 17 件)。
  `check_pytest_baseline.py` → `collected=149 failed=0 errored=0` で exit 0。
- `ruff check services/runtime-api` → **total 117**(追加ファイルの新規指摘 0)。
  `check_ruff_baseline.py` → exit 0。

### 2026-08-10 AT-506 サボタージュ実証(ローカル、commit しない)

| 仕掛けた故障 | pytest exit | ラチェット exit | 検出内容 |
|---|---|---|---|
| 必ず fail するテストを1件追加 | 1 | **1** | `tests.test_zz_sabotage::test_sabotage_always_fails` を baseline 外 fail として指摘 |
| import 不能なテストモジュールを追加(収集エラー) | 2 | **1** | collection error 1 件を baseline 非依存で fail |
| 収集0件 | 5 | **1** | 「収集0件を緑にしない」で fail |
| 未使用 import を1件追加 | 1(ruff) | **1** | `F401 21 > baseline 20 (+1)` |
| 構文エラーを追加 | 1(ruff) | **1** | `invalid-syntax 2 > baseline 0` |
| すべて撤去(コントロール) | 0 | **0** | 緑に戻る |

pytest exit>=2(収集エラー・収集0件)は workflow の `Run tests` ステップ自体でも
即 fail する二重化になっている。

**残る不確定性**: 上記はすべて runner 相当のコンテナでの実測であり、GitHub Actions
実走ではない。PR 実走で追加の failed が出た場合は、その値で
`pytest-baseline.json` を確定し直す(RF-501)。ベースラインの運用規律は
dashboard lint ラチェット(#63)と同じ「下げるのは可・上げるのは禁止」。
