# Verification Contract — hw-prime-worktree-guard

対象: `.hw/prime_run.py` の `ensure_worktree` fail-closed 化。
検証はすべて機械実行(mktemp 一時リポジトリ + `HW_PRIME_CLI` stub +
`HW_PRIME_WORKTREE_ROOT` 差し替え)。stub は実行時にマーカーファイルを書いて
exit 0 する。「Prime が起動したか」はマーカーの有無で判定する(出力文字列では
判定しない)。以下 `<b>` = `hw/prime/<task>`。

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-101 | 初回(worktree も branch も無い): mode 未設定で worktree が作成され、HEAD=起動時 HEAD、ブランチ=`<b>`、stub が起動する(現行挙動の維持) | unit | `.hw/tests/prime-worktree-guard.test.sh` の該当ケース |
| AT-102 | 既存 worktree あり + mode 未設定: exit 非0 で停止し、stub は起動せず、stderr に状態(dirty 数・ブランチ)と `resume`/`fresh` の指示が含まれる | unit | exit code + マーカー不在 + stderr |
| AT-103 | mode=resume + 登録済み・`<b>` に attach・clean な worktree: 再利用され stub が起動する | unit | マーカー存在 + worktree HEAD 不変 |
| AT-104 | mode=resume + dirty(未追跡ファイル含む)な worktree: exit 非0、stub 起動なし | unit | exit code + マーカー不在 |
| AT-105 | mode=resume + clean だが detached HEAD または別ブランチに attach: exit 非0、stub 起動なし | unit | 同上 |
| AT-106 | path に未登録ディレクトリが居座る場合: mode 未設定・resume・fresh のいずれでも exit 非0 で停止し、ディレクトリは削除されず内容も不変 | unit | exit code + ディレクトリ内容の before/after 一致 |
| AT-107 | mode=fresh + 先行コミットと dirty を持つ登録済み worktree: 旧 worktree が除去され、branch `<b>` が現在の HEAD から作り直され、新 worktree の HEAD = リポジトリ HEAD、旧コミットは `<b>` の履歴に含まれない | unit | `git rev-parse` 突合 + `git rev-list <b>` に旧 SHA 不在 |
| AT-108 | mode に不正値(例 `HW_PRIME_WORKTREE_MODE=yolo`): exit 非0、stub 起動なし、stderr に有効値の提示 | unit | exit code + マーカー不在 + stderr |
| AT-109 | branch `<b>` のみ残存(worktree ディレクトリ無し)+ mode 未設定: exit 非0 で停止し、branch 先端からの黙った worktree 再作成が起きない | unit | exit code + worktree 不在 |
| AT-110 | stub が exit 0(status=ready)のとき、出力に後始末コマンド(`git worktree remove` と `git branch -d`)が含まれ、`prime-run.json` に `worktree_mode` が記録される | unit | stdout + prime-run.json の内容 |
| AT-111 | `.hw/verify.sh` が本テストを実行しており、テストを故意に失敗させると verify.sh が非0で終わる | integration | verify.sh の変更行 + 故意失敗時の exit code |

## 想定迂回と対策(各ATが潰す抜け道)

| 迂回シナリオ | 潰す検査 |
|---|---|
| 警告を print するだけで従来どおり再利用を続行する | AT-102(exit 非0 **かつ** stub マーカー不在。文字列だけでは通らない) |
| dirty だけ検査して branch/detached を見ない | AT-105(clean だが別ブランチで拒否) |
| `path.is_dir()` のままで git 登録を確認しない | AT-106(未登録ディレクトリで停止) |
| fresh が未登録ディレクトリまで rm -rf する | AT-106(内容 before/after 一致) |
| fresh が worktree だけ消して branch を残し、旧先端から再作成する | AT-107(旧コミットが `<b>` 履歴に不在) |
| branch のみ残存の経路(現行 :241-244)を検査から漏らす | AT-109 |
| 不正な mode 値を黙って既定(または fresh)に落とす | AT-108 |
| 初回経路にまで停止を広げて prime を使用不能にする | AT-101(初回は現行どおり無停止) |
| 検査を ensure_worktree に書くが run_prime から呼ばない | 全 AT が `prime_run.py` を丸ごと実行して判定(関数単体を直接呼ばない) |
| テストを書くが CI から呼ばれない(死んだテスト) | AT-111(verify.sh 配線と故意失敗の伝播) |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-101 | `HW_PRIME_WORKTREE=0`(worktree 無効)のとき root をそのまま返す現行挙動 | unit | 該当 env でモード検査に入らず stub が root で起動 |
| FP-102 | clean-tree 必須・runtime=prime 必須・plan-by-fable 必須など `run_prime`/`load_run_context` の既存ガードが先に効く | unit | dirty tree fixture で worktree 分岐に入らず exit 2 |
| FP-103 | 成功時に worktree を自動削除しない | unit | AT-110 後に worktree が存在すること |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-101 | 停止時のエラーメッセージだけで人間が次の一手を打てる(worktree の絶対 path・状態・両モードの指定例・手動確認コマンドを含む) | unit | AT-102 の stderr 内容検査(必須トークンの grep) |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: yes
- research brief required: no
- option matrix required: no(設計書 `.pipeline/plans/hw-harness-prime-gaps-2026-08/design.md` §2 に記録済み)
- kpi backcast roadmap required: no
- external consultation required: no
