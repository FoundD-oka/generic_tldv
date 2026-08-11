---
generated_by: fable
task_id: hw-prime-worktree-guard
created: 2026-08-11
size: M
runtime: inline
---

# hw-prime-worktree-guard: ensure_worktree の無検査再利用を fail-closed の明示モードに置き換える

## ゴール

`.hw/prime_run.py` の `ensure_worktree()` が既存 worktree / branch を状態確認なしに
再利用する挙動をやめ、既存状態があるときは停止して人間に
`HW_PRIME_WORKTREE_MODE=resume|fresh` の明示を求める。初回実行の挙動は不変。

## 制約(先に読むこと)

- 初回(worktree も branch `hw/prime/<task>` も無い)の経路は現行と完全同一に保つ。
- 成功時に worktree を自動削除しない(ready 後の worktree は人間が成果確認して
  PR を作る場所)。
- 未登録ディレクトリ(path に在るが `git worktree list --porcelain` に無い)は
  どのモードでも削除しない・使わない。停止のみ。
- 不正なモード値は黙って既定に落とさず停止する。

## How

### 1. 状態検査ヘルパー(新関数)

`prime_run.py` に追加:

- `worktree_state(root, path, branch) -> dict`
  返すもの: `registered`(`git worktree list --porcelain` に path が載るか)、
  `branch_exists`(`git branch --list <branch>`)、`head_branch`(worktree の
  `git symbolic-ref --short HEAD`、detached なら None)、`dirty_count`
  (worktree の `status --porcelain --untracked-files=all` の行数)、
  `ahead_count`(`git rev-list --count <start>..<branch>`。start は起動時 HEAD
  ではなく branch 作成時点が不明のため、`base-commit..branch` で近似してよい。
  表示用であり判定には使わない)。

### 2. ensure_worktree の分岐

`mode = os.environ.get("HW_PRIME_WORKTREE_MODE", "").strip()` として:

- **既存状態なし**(path 不在 かつ branch 不在): 現行どおり作成。mode の値は
  参照しない(fresh/resume が指定されていても初回作成でよい)。
- **既存状態あり**(path 存在 または branch 存在):
  - mode 未設定 → `PrimeRunError`。メッセージに状態レポート(registered /
    head_branch / dirty_count / ahead_count)と、取るべき選択肢を明記:
    `HW_PRIME_WORKTREE_MODE=resume`(前回の続きから再開)/
    `HW_PRIME_WORKTREE_MODE=fresh`(worktree と branch を作り直す)/
    手動確認用に `git worktree list` と worktree の path。
  - `mode == "resume"` → 次の全条件で再利用、1つでも欠けたら `PrimeRunError`
    (欠けた条件を明示):
    1. path が存在し `registered` である
    2. `head_branch == "hw/prime/<task>"`(detached・別ブランチは拒否)
    3. `dirty_count == 0`
    branch だけ在って path が無い場合は resume 不成立として停止
    (worktree を branch 先端から再作成しない。それは fresh でも resume でもない
    中間状態で、現行の黙認経路そのもの)。
  - `mode == "fresh"` →
    1. path が存在して `registered` なら `git worktree remove --force <path>`。
    2. path が存在して未登録なら停止(削除しない。人間が処分する)。
    3. branch が在れば `git branch -D hw/prime/<task>`。
    4. 現行の新規作成経路で現在の HEAD から作り直す。
  - その他の値 → `PrimeRunError`(有効値を提示)。

### 3. 記録と後始末案内

- `run_prime()` が書く `prime-run.json` に `"worktree_mode"` を追加
  (未設定時は "initial" / 使用時は "resume" or "fresh")。
- `status == "ready"` の出力に後始末コマンドを追記:
  「PR マージ後: `git worktree remove <path> && git branch -d <branch>`」。

### 4. テストと配線

- `.hw/tests/prime-worktree-guard.test.sh` を新設(既存
  `pr-create-intercept.test.sh` の様式)。mktemp 一時リポジトリに
  runtime=prime の plan 一式(frontmatter に generated_by: fable を含む plan.md、
  契約、base-commit、sml-decision)を commit し、`HW_PRIME_CLI` に「実行時に
  マーカーファイルを書いて exit 0 する」stub、`HW_PRIME_WORKTREE_ROOT` に
  一時ディレクトリを差して `prime_run.py` を実行する。検証契約 AT-101..AT-110 を
  検査する。stub 実行の有無はマーカーファイルで判定する。
- `.hw/verify.sh` に本テストの実行を追加。
- 注意: `hw-fable-chunk-review` タスクも verify.sh に1行足す。両タスクは直列に
  実施し、後発がリベースする。

## Why(実装者に渡さない)

現行は `path.is_dir()` だけで再利用を宣言し、branch が残っていれば worktree を
その先端から黙って再作成する。RF-71R 第2回の前に手動削除しなければ第1回の迂回
成果(コミット済み=clean 検査では防げない)の上に積み、実験が汚染されるところ
だった。「再開」と「作り直し」は機械に区別できないため、自動判定(clean なら
再利用)は棄却し、既定 fail-closed + 明示 env(HW_PRIME_GATE と同型)を採用した。
成功時自動削除は ready 後の worktree が PR 作成場所そのものなので棄却。放置対策は
ready 時の案内出力と fresh モードの掃除兼用に留める。選択肢比較の全文は
`.pipeline/plans/hw-harness-prime-gaps-2026-08/design.md` §2。
