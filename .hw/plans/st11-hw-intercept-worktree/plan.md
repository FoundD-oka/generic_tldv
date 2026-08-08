---
generated_by: fable
task_id: st11-hw-intercept-worktree
size: M
base-commit: 5cae3a05550e8679b156e88652b0dfa2193d30f1
---

# st11: pr-create-intercept の worktree 盲点修正

## 依頼の文字通り / 再設計

- 文字通り: `.hw/hooks/pr-create-intercept.sh`(ローカル)と
  `~/.claude/skills/hw-init/template/.hw/hooks/pr-create-intercept.sh`(テンプレート)
  を修正し、「セッション cwd = メインリポジトリのまま worktree ブランチの PR を
  作る」ケースで正しい worktree に対して pr-ready-gate を再実行させる。
  ゲートは緩めない(解決不能時は fail-closed で block)。
- reframe: 不要。引き継ぎ文書で合意済みの要求であり、成果と一致している。

## ゴール

1. `gh pr create` のインターセプト時に、ゲートを実行すべきディレクトリを
   次の優先順位で解決する:
   `--head` のブランチが載る worktree > コマンド内 `cd` 連鎖の到達先 >
   payload の `cwd` > フックプロセスの cwd。
2. 解決先が確定できない・git リポジトリでない場合は exit 2 で block(fail-closed)。
3. 従来ケース(メインリポジトリ実装 / EnterWorktree セッション)の挙動は不変。
4. `gh pr create` を含まないコマンドは従来どおり必ず exit 0(全 Bash コマンドで
   走るフックなので、この経路の退行は全エージェントを止める。最重要の不変条件)。
5. ローカルとテンプレートは byte-identical を維持する(現状も同一)。

## How

### 1. `.hw/hooks/pr-create-intercept.sh` を書き換える

構造(bash + python3 のみ。python3 は .hw 全域で既に前提):

1. `payload="$(cat)"` を最初に読む(現行は cd 後に読んでいるが、先に読む)。
2. python3 ヒアドキュメント1回で payload を解析し、標準出力に1行で判定を返す:
   - `SKIP` … `tool_input.command` が取れない、または `gh pr create` を含まない
     → bash 側は exit 0。JSON 解析失敗も `SKIP`(現行と同じ挙動。gh 判定経路は
     fail-open のままにし、無関係コマンドを絶対に殺さない)。
   - `DIR <絶対パス>` … ゲートを実行すべき git toplevel。
   - `FAIL <理由>` … 解決不能。bash 側は理由を stderr に出して exit 2。
3. python3 内の解決ロジック:
   - `base` = payload の `cwd`(存在するディレクトリの場合)、なければ
     `os.getcwd()`(Codex 等、`cwd` フィールドが無い payload への互換)。
   - `command` の最初の `gh pr create` 出現位置より前の部分から `cd` 連鎖を抽出:
     正規表現 `(?:^|&&|\|\||;|\n)\s*cd\s+("([^"]*)"|'([^']*)'|([^\s;&|]+))`。
     各トークンは `~` を expanduser し、`base` から順に
     `os.path.normpath(os.path.join(...))` で適用 → `exec_dir`。
     トークンに未展開の `$` / バッククォートを含む場合は `FAIL`
     (シェル変数は解決できない。fail-closed。エージェントは絶対パスで書き直せる)。
     `cd` が無ければ `exec_dir = base`。
   - `gh pr create` 以降から `--head` / `-H` の値を抽出
     (`=` 区切り・空白区切り・引用符の3形態)。値に `$` / バッククォートが
     あれば `FAIL`。`owner:branch` 形式は `:` 以降をブランチ名とする。
   - `--head` が取れた場合: `git -C <exec_dir> worktree list --porcelain`
     (exec_dir が git 外なら base で再試行)を読み、
     `branch refs/heads/<head>` を持つ worktree があればそれを gate 対象にする。
     無ければ `exec_dir` のまま(push 済みで checkout していないブランチは
     現行挙動と同じく cwd 側で検査。緩和ではない)。
   - 最終検証: `git -C <対象> rev-parse --show-toplevel` が成功したら
     `DIR <toplevel>`、失敗したら `FAIL`。
4. bash 側: `DIR` を受けたら `cd` して現行どおり
   `.hw/current/task-id` を読み、`bash .hw/hooks/pr-ready-gate.sh "$TASK_ID"
   > .hw/state/intercept.log 2>&1` を実行。block 時の stderr メッセージに
   解決したディレクトリを含める(デバッグ用)。
5. `set -uo pipefail` 維持。`pr-ready-gate.sh` は無改変。

### 2. 単体テスト `.hw/tests/pr-create-intercept.test.sh` を新設

引数でフックのパスを取る: `bash .hw/tests/pr-create-intercept.test.sh <hook-path>`。
mktemp 下に fixture を作る:

- git リポジトリ R(初期 commit 済み)+ `git worktree add` で ブランチ `b1` の
  worktree W。
- R と W の両方に stub の `.hw/hooks/pr-ready-gate.sh` を(未追跡ファイルとして)
  置く。stub は `$(pwd)` を結果ファイルへ記録し、環境変数
  `HW_TEST_GATE_EXIT`(既定 0)で exit する。
- payload JSON は python3 で生成して stdin に流し、exit code と記録された pwd を
  検証する。ケースは検証契約 AT-001〜AT-011 のとおり。
- 1件でも不一致なら exit 1。全通過で `ok` を出力し exit 0。

### 3. `.hw/verify.sh` にフック自己テストを追加

`make smoke` ブロックの後に追記(既存ロジックは無改変):

```bash
if [ -f .hw/tests/pr-create-intercept.test.sh ]; then
  bash .hw/tests/pr-create-intercept.test.sh .hw/hooks/pr-create-intercept.sh || exit 1
fi
```

これで pr-ready-gate と CI(最終権威)がフックの退行を毎回検査する。

### 4. テンプレート側の同期(リポジトリ外)

- `~/.claude/skills/hw-init/template/.hw/hooks/pr-create-intercept.sh` を
  ローカルの新版で上書き(byte-identical を維持)。
- `~/.claude/skills/hw-init/template/.hw/tests/pr-create-intercept.test.sh` を
  新設し、ローカルのテストと同一内容を置く(将来の hw-init 先にも配布される)。
- テンプレートは git 管理外のため hw ゲート・Fable レビューの差分対象に
  入らない。担保は検証契約 AT-101〜AT-103(diff の同一性検査 + テンプレート側
  パスを引数にした同一テストの実行)で行う。実装者はこの3コマンドの実出力を
  評価証拠として残すこと。

### 5. 実装順序

1. テストを先に書く(現行フックに対して AT-004/005/006 は通り、
   AT-002/003/007 系は落ちることを確認 = 盲点の再現)。
2. フックを書き換えて全ケースを通す。
3. verify.sh 追記 → `bash .hw/verify.sh` 通過確認。
4. テンプレートへコピー → AT-101〜AT-103 実行。
5. commit → `python3 .hw/fable_review.py st11-hw-intercept-worktree`。

### 既知の限界(スコープ外、緩和ではない)

- コマンド文字列の静的解析なので、引用符内の `cd` 風文字列やコマンド置換を
  誤検出し得る。誤検出時の帰結は「別ディレクトリでゲートが走り block」で
  あり、誤って通す方向には倒れない。
- `--repo owner/name` のリモート指定はローカル検査対象の解決に使わない
  (ローカル worktree と対応しないため)。

## Why(実装者に渡さない)

- 盲点の実害: worktree で実装したブランチを、cwd がメインリポジトリの
  セッション(または Codex)から PR しようとすると、(a) メイン側 tree が別
  セッションの未コミット変更で dirty、(b) verdict がブランチ側 worktree にしか
  無い、の両方で必ず block される。st9/st10 では EnterWorktree だったため
  顕在化しなかった。
- `--head` を最優先にするのは、PR の対象ブランチを決めるのは実行ディレクトリ
  ではなく `--head` だから。`cd` より意味的に強い。
- gh 判定経路(JSON 解析→ SKIP)を fail-open のまま残すのは、このフックが
  全 Bash コマンドで走るため。ここを fail-closed にすると payload 形式の揺れで
  全エージェントの Bash が死ぬ。ゲート本体(gh pr create と判定した後)だけを
  fail-closed にするのが正しい非対称。
- verify.sh に入れるのは「CI > ローカルゲート > エージェント」の序列に
  フック自身を載せるため。契約の一回性検証だけだと将来の退行を捕まえられない。
- payload の `cwd` フィールドは Claude Code のフック共通入力
  (session_id / transcript_path / cwd / hook_event_name / tool_name /
  tool_input)として文書化されている。ただし本リポジトリ内に実キャプチャは
  無く、Codex 側の payload 形式は未確認。確信度: Claude Code については高、
  Codex については低 — だから `cwd` 欠落時はプロセス cwd へフォールバック
  する設計にしてある(欠落しても現行と同じ挙動で、覆っても壊れない)。
