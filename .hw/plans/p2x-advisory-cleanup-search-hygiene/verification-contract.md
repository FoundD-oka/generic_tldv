# Verification Contract — p2x-advisory-cleanup-search-hygiene

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体: **CI** = GitHub Actions(Test Meeting API)/ **ゲート** = pr-ready-gate
実行者が `.hw/gates/p2x-advisory-cleanup-search-hygiene/` を確認。S のため Fable
レビューなし。

## ベースライン取得手順(転記値の使用禁止)

着手時に fresh venv で meeting-api の unit 全件と postgres 統合テストを実行し、
サマリ全文を `.hw/gates/p2x-advisory-cleanup-search-hygiene/baseline-<commit>.txt`
へ保存する。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-801 | search.py の差分が `dependencies=[Depends(get_user_and_token)]` の行削除のみ | diff ハンク確認 | ゲート | diff 出力 |
| AT-802 | 未認証リクエストが従来どおり拒否される(既存の認証・2ユーザー分離テストが green) | pytest(unit + postgres 統合) | CI + ゲート | pytest ログ |
| AT-803 | 統合テストに `datetime.utcnow()` が残っていない | `grep -c utcnow` = 0 | ゲート | grep 出力 |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-801 | 変更ファイルが `services/meeting-api/meeting_api/search.py` と `services/meeting-api/tests/integration/test_transcript_search_postgres.py` の2件のみ: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` | diff | ゲート | diff 出力 |
| FP-802 | unit・統合ともベースライン比で新規 fail 0(統合は実 postgres で全件 green) | サマリ比較 + CI | CI + ゲート | 両サマリ |
| FP-803 | search.py のクエリ3本(EXISTS / match_count / window)と `Meeting.user_id == current_user.id` 条件に差分なし | diff | ゲート | diff 出力 |

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-801 | aware datetime と DB カラム(naive か否か)の整合。モデル定義を確認し、統合テストの実 postgres 実行で確定する | モデル定義の source check + 統合テスト green | pytest ログ + ベースラインファイルへの追記 |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p2x-advisory-cleanup-search-hygiene/`)
- hash-bound approval required: no(S・機械検証のみ)
