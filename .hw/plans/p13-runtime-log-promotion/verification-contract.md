# Verification Contract — p13-runtime-log-promotion

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
S タスクのため機械検証のみ(Fable レビューなし)。

## ベースライン取得手順(FP-001 の前提。転記値の使用禁止)

1. 実装着手前に base-commit をチェックアウトした clean tree で python3.11 fresh venv
   (`~/.cache/hw-venvs/p13-runtime-log-promotion` 等 /tmp 以外)を作成し、
   `pip install -e "services/runtime-api/[dev]"` →
   `python -m pytest services/runtime-api/tests` を実行する。
2. サマリ全文を `.hw/gates/p13-runtime-log-promotion/pytest-baseline-<commit>.txt` に保存する。
3. **この実測値が本契約のベースライン**。handoff や過去契約からの転記値は無効。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | **障害ログの既定可視化(本質要求)**: 例外握り潰し系 debug が非テストコードに残っていない — `grep -rEn "logger\.debug\(.*exc_info" services/runtime-api --include='*.py'` のうち tests/ 以外が 0件 | grep 実行 | 出力を `.hw/gates/p13-runtime-log-promotion/` に保存 |
| AT-002 | 残存 logger.debug が state.py の状態遷移トレース1箇所のみ: `grep -rn "logger\.debug" services/runtime-api --include='*.py'` の非テスト残存が state.py の1件 | grep 実行 | grep 出力 |
| AT-003 | 昇格がレベル変更のみ: 差分が plan の9箇所に対する `debug`→`warning` 置換(+必要ならテスト側のレベル追随)だけで、メッセージ文字列・exc_info・制御フローに変更がない | `git diff base-commit..HEAD` の目視確認(機械補助: diff 行数が小さいこと) | diff 出力 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | runtime-api テスト: ベースラインに対し新規 fail 0・skipped 増加なし(caplog レベル assert の追随修正があれば passed 数同一のまま) | 同一 venv・同一コマンドでサマリ比較 | 両サマリ全文 |
| FP-002 | 変更ファイルが `services/runtime-api/runtime_api/lifecycle.py`・`backends/docker.py`・`backends/process.py`・`backends/kubernetes.py`・(必要時)`services/runtime-api/tests/` のみ | `git diff --name-only base-commit..HEAD` | diff 出力 |
| FP-003 | warning 洪水の防止: 昇格した各箇所を含むループに sleep/バックオフがあることを実装者が確認済み(ない箇所は据え置き+advisory 記録) | 該当ループのソース確認メモを evidence に残す | 確認メモ |

## Non-Functional Checks

なし(レベル変更のみ)。

## KPI Checks

なし。

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p13-runtime-log-promotion/` へ)
- hash-bound approval required: no(S のため機械検証のみ)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

なし(外部依存のない logging レベル変更)。
