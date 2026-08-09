---
generated_by: fable
task_id: p2x-advisory-cleanup-ci-runtime-api
base-commit: 09375ac204bc1173496546dd63830d2128a7b7c8
size: M
---

# runtime-api の CI を新設し、既存負債をベースライン固定する(advisory 9)

## ゴール

runtime-api は現在 CI が1本もなく、base 時点から pytest ~18 件 fail・ruff ~119 件
指摘の負債がある(数値は概数。**正確な値は着手時に実測**)。exit 0 必須にすると
常時赤 = 誰も見なくなるため、dashboard lint ラチェット(#63)と同方式で
**既存負債を実測ベースラインとして固定し、新規回帰のみを block する CI** を新設する。

## How

1. **ローカル実測**: python3.11 fresh venv を
   `~/.cache/hw-venvs/p2x-advisory-cleanup-ci-runtime-api` に作成(/tmp 不可)、
   `pip install -e "services/runtime-api/[dev]"`。
   `python -m pytest services/runtime-api/tests` の failed テスト node-id 一覧と、
   `ruff check services/runtime-api` の件数を記録する。
2. **Redis で解消する集合の確認**: fail の主因は Redis 等の実環境要求
   (+ `test_process_backend_inspect`)。GH Actions の `services:` に redis を
   立てて解消するもの(test-meeting-api.yml の postgres service が前例)は
   CI 側で解消し、ベースラインを小さくする。docker daemon 要求など解消しない
   ものはベースラインへ残す。**どちらに転んでも合格ラインは「新規 fail 0」で不変**。
3. **pytest ベースライン機構**: `services/runtime-api/tests/ci/pytest-baseline.json`
   に既知 fail の node-id を列挙。比較スクリプト(stdlib のみの python、
   dashboard の `scripts/ci/lint-ratchet.mjs` と同思想)を
   `services/runtime-api/tests/ci/` に置く。ルール:
   - ベースライン外の fail / error → exit 非0(新規回帰)。
   - ベースライン内が pass に転じたら通知のみ(exit 0)。縮小は任意。
   - collection error は無条件に exit 非0(#64 の教訓: 収集エラーで「0件緑」に
     させない)。
   - 比較スクリプト自体のユニットテストを同ディレクトリに置く(新規 fail 検知・
     collection error 検知の両方を固定)。
4. **ruff ラチェット**: `ruff check --output-format json` の rule 別件数を
   `ruff-baseline.json` に固定し、増加で exit 非0。減少は基準更新を推奨するのみ。
5. **workflow 新設** `.github/workflows/test-runtime-api.yml`: 既存流儀に従う —
   action は SHA ピン、`permissions: contents: read`、`timeout-minutes`、
   push `branches: [main, feature/*]`、paths は push / pull_request 両方に
   `services/runtime-api/**` と `.github/workflows/test-runtime-api.yml` を含める
   (P1-4 の盲点を新設時から踏まない)。ジョブ: install → pytest+ベースライン比較
   → ruff+ラチェット。
6. **CI 実測でベースライン確定**: CI 環境の failed 集合はローカルと異なり得る。
   PR 上の実走で確定した値を最終ベースラインとして commit し、契約の改訂履歴に
   実測値(passed/failed/skipped と failed 名、ruff 件数)を記入する。

## 制約

- `services/runtime-api/runtime_api/`(プロダクションコード)と既存テストの
  **本体を変更しない**(fail を「直して」ベースラインを縮めるのは本タスクの範囲外。
  CI 整備と負債修正を同じ PR に混ぜない)。conftest への追記も不可。
- `pyproject.toml` 変更不可(依存追加なし。redis は CI の service コンテナであって
  python 依存ではない)。

## Why(実装者に渡さない)

- このタスクが docker-events-robustness(次タスク)の検証土台になる。順序が先。
- ベースライン方式は .hw/verify.sh + verify-baseline、dashboard lint ラチェットと
  同じリポジトリ内前例の思想。「常時赤で誰も見ない」ST-27 の逆パターン回避。
- 18件の triage(個別修正)はコストが見合わないと判断した。可視化と単調非増加の
  強制だけをこのタスクの価値とし、負債返済は必要が生じた時に別タスクで行う。
