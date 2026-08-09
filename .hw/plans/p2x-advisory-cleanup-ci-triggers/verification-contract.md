# Verification Contract — p2x-advisory-cleanup-ci-triggers

対象: `base-commit..HEAD` の差分。検証は commit 済み clean tree に対して実行する。
判定主体: **CI** = GitHub Actions / **ゲート** = pr-ready-gate 実行者が
`.hw/gates/p2x-advisory-cleanup-ci-triggers/` の証跡を確認。S のため Fable レビューなし。

## Acceptance Tests

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| AT-401 | test-admin-api / test-api-gateway / test-meeting-api / test-packages の各 `pull_request.paths` に `.github/workflows/<自ファイル名>` が含まれる | 静的検査: `grep -A8 'pull_request:' .github/workflows/<f> \| grep '<自ファイル名>'` を4本分実行しログ保存 | ゲート | grep 出力を `.hw/gates/` へ |
| AT-402 | verify-compose-config.yml の `push.branches` が `[main, feature/*, hw/**]` を含む | 静的検査(grep) | ゲート | grep 出力 |
| AT-403 | deploy-dashboard-gcp.yml の `push.paths` が `services/dashboard/**`・`packages/transcript-rendering/**`・`.github/workflows/deploy-dashboard-gcp.yml` の3件ちょうど | 静的検査(該当節の全文をログ保存) | ゲート | 節の全文 |
| AT-404 | 本 PR 上で4本の test workflow(自パス追加により発火)が**実際に走って緑** | `gh run list --branch <branch>` で **workflow 名**を確認(job 名ではない) | CI + ゲート | gh run list 出力 |
| AT-405 | 本 PR で deploy-dashboard-gcp が走らない(push main 限定のまま) | `gh run list --branch <branch>` に同 workflow が現れない | ゲート | 同上 |

## Failure Patterns

| ID | Must Not Regress | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| FP-401 | 変更ファイルが上記6 workflow のみ: `git diff --name-only base-commit..HEAD -- ':(exclude).hw/plans'` の結果が `.github/workflows/` の6件と一致 | diff | ゲート | diff 出力 |
| FP-402 | 各 workflow の差分が `on:` 節のみ(jobs / steps / permissions / concurrency に差分なし) | `git diff base-commit..HEAD -- .github/workflows/` のハンク確認 | ゲート | diff 出力 |
| FP-403 | 既存 paths エントリの削除がない(追加のみ) | diff に `-` 行の paths エントリがない(YAML 整形での並び替えも不可) | ゲート | diff 出力 |

## Non-Functional Checks

| ID | Requirement | Method | 判定主体 | Evidence |
|---|---|---|---|---|
| NFT-401 | YAML として妥当(構文破壊なし) | AT-404 の実走が証明(GitHub が parse できなければ run が現れない/エラーになる)。加えて手元に actionlint があれば実行 | CI | gh run list |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes(`.hw/gates/p2x-advisory-cleanup-ci-triggers/`)
- hash-bound approval required: no(S・機械検証のみ)
- research brief required: no
