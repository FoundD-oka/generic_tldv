# Verification Contract — st11-hw-intercept-worktree

前提: 各 AT の「stub gate」は `.hw/tests/pr-create-intercept.test.sh` が fixture
(temp git リポジトリ R + ブランチ b1 の worktree W、双方に pwd を記録する stub の
`.hw/hooks/pr-ready-gate.sh`)内に設置するもの。テストは
`bash .hw/tests/pr-create-intercept.test.sh <hook-path>` で実行し、
AT-001〜AT-011 を全件検査して不一致が1件でもあれば exit 1 とする。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | `gh pr create` を含まないコマンド(例 `ls`)は payload の内容・cwd に関係なく exit 0、stub gate は呼ばれない | unit(テストスクリプト内ケース) | テスト出力 |
| AT-002 | cwd=R で `cd <W> && gh pr create --title x` → stub gate が W で実行され、stub 成功時フックは exit 0 | unit | stub が記録した pwd = W の toplevel |
| AT-003 | cwd=R で `gh pr create --head b1` → `git worktree list` 解決で stub gate が W で実行される | unit | stub 記録 pwd = W |
| AT-004 | cwd=W・素の `gh pr create` → stub gate が W で実行される(EnterWorktree セッションの回帰なし) | unit | stub 記録 pwd = W |
| AT-005 | cwd=R・素の `gh pr create`(R のブランチ)→ stub gate が R で実行される(従来ケースの回帰なし) | unit | stub 記録 pwd = R |
| AT-006 | payload に `cwd` フィールドが無い場合(Codex 互換)、プロセス cwd=R で stub gate が R で実行される | unit | stub 記録 pwd = R |
| AT-007 | `cd /存在しないパス && gh pr create` → exit 2、stub gate は呼ばれない(fail-closed) | unit | exit code + stub 未実行 |
| AT-008 | `gh pr create --head 存在しないブランチ` cwd=R → stub gate が R で実行される(現行同等のフォールバック、緩和ではない) | unit | stub 記録 pwd = R |
| AT-009 | stub gate が exit 1 のとき、フックは exit 2 で block する | unit(`HW_TEST_GATE_EXIT=1`) | exit code |
| AT-010 | `cd "$VAR" && gh pr create` のように未展開シェル変数を含み解決不能 → exit 2(fail-closed) | unit | exit code |
| AT-011 | cwd が git リポジトリ外・素の `gh pr create` → exit 2 | unit | exit code |
| AT-020 | 実リポジトリ経路: 本リポジトリ直下で `printf '%s' '{"tool_input":{"command":"echo hi"}}' | bash .hw/hooks/pr-create-intercept.sh; echo $?` が `0` | smoke | コマンド実出力 |
| AT-101 | ローカルとテンプレートのフックが同一: `diff .hw/hooks/pr-create-intercept.sh ~/.claude/skills/hw-init/template/.hw/hooks/pr-create-intercept.sh` が空で exit 0 | command | diff 実出力 |
| AT-102 | テンプレート側フックも全ケース通過: `bash .hw/tests/pr-create-intercept.test.sh ~/.claude/skills/hw-init/template/.hw/hooks/pr-create-intercept.sh` が exit 0 | command | テスト実出力 |
| AT-103 | テンプレート側にテストが配布済み: `diff .hw/tests/pr-create-intercept.test.sh ~/.claude/skills/hw-init/template/.hw/tests/pr-create-intercept.test.sh` が空で exit 0 | command | diff 実出力 |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | 非 `gh pr create` コマンドが exit 非0 になる退行(全エージェントの Bash が止まる)を入れない | AT-001 / AT-020 | テスト出力 |
| FP-002 | 解決不能ケースを exit 0 で素通しする(ゲート緩和)退行を入れない | AT-007 / AT-010 / AT-011 | テスト出力 |
| FP-003 | pr-ready-gate.sh 本体・hd-gate.sh の無改変(このタスクの差分に含めない) | `git diff 5cae3a0..HEAD --stat` に当該2ファイルが無いこと | diff 実出力 |
| FP-004 | 既存 verify(`make smoke` + baseline 比較)のロジック無改変。verify.sh への追記はテスト実行ブロックの追加のみ | `git diff 5cae3a0..HEAD -- .hw/verify.sh` のレビュー | diff 実出力 |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | 非 `gh pr create` 経路のフック実行が体感遅延を生まない(1秒未満/実測) | `time`(AT-020 のコマンド) | 実測出力 |
| NFT-002 | 依存は bash / python3 / git / coreutils のみ(新規依存なし) | フックとテストのソース確認 | ソース |
| NFT-003 | `bash .hw/verify.sh` がフック自己テストを含んで通過する | command | 実出力 |

## KPI Checks

該当なし(kpi-backcast-roadmap.md 不在)。

## Gate Requirements

- preflight result required: yes(commit 済み clean tree で検証)
- evidence pack required: yes(AT-020 / AT-101〜103 / NFT-001 はコマンド実出力を残す)
- hash-bound approval required: yes(M のため Fable READY 必須)
- research brief required: no
- option matrix required: no
- kpi backcast roadmap required: no
- external consultation required: no
- external consultation provider: not needed

## Research Freshness Checks

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-001 | Claude Code PreToolUse payload に `cwd` が含まれる、という前提。将来の形式変更や Codex payload では欠落し得る | 設計側で吸収済み: `cwd` 欠落時はプロセス cwd へフォールバック(AT-006 が恒久検査) | テスト出力 |
