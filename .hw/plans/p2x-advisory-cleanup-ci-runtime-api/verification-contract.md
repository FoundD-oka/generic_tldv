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

- (実装者が着手時に実測ベースラインをここへ記入する。合格ラインは「新規 fail 0 /
  ruff 増加 0」で不変)
