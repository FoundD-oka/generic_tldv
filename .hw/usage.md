# hw v2 の使い方

v2.1.3以降、検証コマンドには `HW_VERIFY_ROOT`、`HW_VERIFY_HEAD` と
`HW_VERIFY_BASE` を渡す。gateでは実際に選んだ比較先commit、単独verifyでは空文字を渡す。
独自の差分チェックはこの比較先を使い、main固定で別の範囲を検査しない。
これらは検証範囲の伝達であり、実行権限の証明ではない。

## 主担当と分担

主担当が調査・計画・分担を選ぶ。未知の導入では調査→小さな実験→計画の更新を使い、
小変更には不要な工程を課さない。モデル名を成果物の合格条件にしない。
Claude の実装・修復役 hw-builder はOpus 5.5（`claude-opus-5-5`）を指定する。
hw-researcher / hw-verifier のモデル指定は従来どおり inherit。
任意の意図レビュー用 hw-intent-reviewer はfableを既定候補とする（後述）。
Codex には AGENTS.md の案内と共通の役割文書を使う。利用中のランタイムの分担機能で
新しいコンテキストを開始し、必要な資料を明示して渡す。特定の subagent API は再実装しない。
ツールがない環境では自分の再読を独立レビューと呼ばず、レビュー待ちを報告する。

設計探索では、同じ会話を続けるか、原要求と確認済み資料から別案を作るかを主担当が選ぶ。
毎回の分岐や2案生成は必須ではない。探索の分岐と、レビュー初回からBuilderの自己評価を
除くことは別の目的を持つ。別案が要求を満たしていても、誤案を検出した証明にはならない。
実行時のモデル・役割文・自動読込みされる指示やスキルはランタイムでも確認する。
manifestのrole.mdのhashだけでは、それ以外の実際の入力や独立性を保証しない。

Claude の tools に Bash を含めると、Write/Edit を外してもファイル書き換えは可能。
手引き・役割名・worktree・Context Manifest はOSの権限制限ではない。検証コードも
信頼できない場合は、書き込み可能な一時領域のみを持つ別コンテナ等で実行する。
本番認証情報や保護設定の変更権限は、Builder/Verifier の環境に渡さない。

### Builderの起動

Claude Code 2.1.280以上を使う。`claude --version` を確認し、古ければ `claude update`。
既存の認証・課金経路を維持し、`claude auth status` で確認する。macOSのsandbox内だけで
未認証になる場合は通常環境で再確認し、認証値をログや文書へ転記しない。

導入先の `.claude/agents/hw-builder.md` のfrontmatterがモデルを指定する。
Claudeからの分担は `hw-builder` を選び、呼出時のmodel指定で別モデルへ上書きしない。
Codex等からCLIで渡す場合も、導入先ルートで同じagentを使う:

```bash
claude --model claude-opus-5-5 --agent hw-builder
```

`opus` は変動するエイリアスなので使わない。一般のCLIモデル選択は `--model`、
`ANTHROPIC_MODEL`、settingsの `model` の順。subagentでは呼出時のmodel指定がfrontmatterより
優先され、`CLAUDE_CODE_SUBAGENT_MODEL_FORCE` はfrontmatterも上書きするため、呼出元も確認する。
計画役・レビュー役に影響する全体設定や環境変数を、この変更のために書き換えない。
詳細は[モデル設定](https://code.claude.com/docs/en/model-config)と
[subagent設定](https://code.claude.com/docs/en/sub-agents)を参照する。

設定だけで選択されることは `--model` を付けず、短い接続確認で検証する:

```bash
claude -p --agent hw-builder --output-format json --tools "" \
  --strict-mcp-config --permission-mode dontAsk --no-session-persistence \
  '接続確認です。資料の読取や実装は不要です。OKとだけ返してください。'
```

JSONの `is_error: false` と `modelUsage` の実モデルID `claude-opus-5-5` を確認する。
モデルの自己申告は証拠にしない。利用不可や別モデルへの切替があれば成功扱いせず、原因を報告する。
配布元の更新だけでは導入先コピーは変わらない。既存のplan/applyで個別の差分を確認して適用する。

## 要求意図の合意（v2.1.4）

新しい開発案件では、主担当が現状と元の要求を確認した後、設計方法を決める前に
「利用者が得たい結果」「判断で優先すること」「必須制約」を短く具体的に提示する。
この理解を軸に実装することと、違いがあれば訂正してほしいことを伝え、返答を待つ。
抽象的な目的への同意だけで済ませず、ユーザーが違いを指摘できる内容にする。
既に同じ軸への明示的な合意がある場合や、仕様が閉じた単純変更では同じ質問を繰り返さない。
待機中は事実調査を進められるが、未合意の解釈に依存する設計・実装には進まない。

例（音楽アプリの場合の提示文であり、全案件への必須条件ではない）:

> スマホで説明なしに触れ、適当に触ってもメロディーやフレーズが音楽としてつながり、
> 自分が演奏していると感じる体験、と理解しています。触れた瞬間の音と動き、
> 音楽としてのまとまり、次も触りたくなる楽しさを軸に実装します。
> この理解で合っていますか？ 違うところや優先したい部分があれば教えてください。

合意内容は既存の要求文書の独立した節、または `docs/requirements/intent.md` 等に保存する。
設計案・実装ログ・レビュー結果をこの節へ混ぜない。残す内容は次のとおり。

- 元の要求の出典と、実際に提示した文面。
- ユーザーの実際の返答と出典（会話の日時・識別子等）、訂正を反映した判断軸。
- 合意が及ぶ範囲と、未解決の点。提示のみ・未返答は合意済みと記録しない。

「はい」「進めて」も、何の提示への返答かを対にして残す。機能追加への許可を、
別の目的への合意に流用しない。明示的に「確認不要・任せる」と委任された場合は
委任の範囲と主担当の仮定を区別して残し、ユーザーが内容を確認済みとは記録しない。
会話の場所を参照できない引継ぎでは、必要な提示文と返答を原文で保存する。

通常の修正は合意した軸と最新の追加要求を一緒に渡す。目的・優先順位・必須制約を
変える場合だけ、変更前後と影響をユーザーへ提示し、新しい返答を得る。
過去の合意を上書きで消さず、どの訂正で何が変わったかを追えるようにする。
実装方法の選択や、合意済み目的を満たす不具合修正は再確認の理由にしない。

Builder/Verifierへの手渡しでは原要求と合意記録を明示する。taskを使う場合は
既存の `sources` に両方の参照を載せる。技術contextのrequest.jsonは参照先本文を
同梱しないので、主担当は該当文書も閲覧可能な形で渡し、受け手は本文を読む。
意図レビューでは、確認の提示文と返答を含む追跡済みの原文ファイルを
`intent_review.sources` にも列挙する。baseline/design/deliveryの全段階へ同じ原文が届く。
合意を変えたら原文ファイルも更新し、source_bindingの失効に従って基準・レビューを作り直す。

計画・受入条件・成果物を合意した軸に照らす。数や応答時間等の代理指標だけで、
体験の達成まで確認済みとしない。ユーザーの意図確認も、完成品の品質保証ではない。
これは主担当と各担当の運用指示。ランナーはユーザーの本人性・合意の真実性を認証せず、
新しい承認JSONやBuilder起動を強制するフックは追加しない。既存task/CIの形式は維持する。

## プロジェクトが所有する設定

`.hw/project.json` と `.hw/verify.sh` は更新時に保持する。
`verify` は shell 文字列でなく argv 配列の配列。例:

```json
{
  "schema": 1,
  "base_ref": "origin/main",
  "verify": [["npm", "test"]],
  "timeout_seconds": 900,
  "review": {"paths": ["src/auth/*", "migrations/*"], "check_name": null, "app_id": null}
}
```

これは例。base_ref と review.paths はプロジェクトの実物に合わせる。
`fnmatch` でパス全体を照合するため `*` は `/` も含む。認証・課金・データ変更など、
固有の重要経路を指定する。共通の .hw/.github/.claude/.codex と AGENTS.md/CLAUDE.md、
REVIEW.md（どの階層でも）の変更は設定によらずレビュー対象。パス判定は要求の危険度を完全に検出しない。
REVIEW.md もプロジェクト所有。更新処理は作成も上書きもしない（後述「REVIEW.md」）。
主担当が独立検証を追加すべきと判断した作業は task.review_required を true にする。
`.hw/tasks/` は実行中の要求だけを置く場所。CIも全ファイルを読み、CLIの --task 省略では
必須レビューを外せない。`review_paths` を指定すればその範囲の変更に適用する。
省略時は全変更に適用する。完了済みの要求は `docs/hw-tasks/` 等へ移す。
要求の追加・変更・削除自体も .hw の変更なので独立レビューの対象になる。
プロジェクトの verify が内部で検証をスキップして exit 0 を返すことまでは一般的な
ランナーでは検出できない。初回移行で既存 verify の実際のチェック内容を点検する。

## 検証

```bash
python3 .hw/runtime/hw.py doctor
python3 .hw/runtime/hw.py verify
python3 .hw/runtime/hw.py gate --base origin/main
```

verify は未commitの作業にも使える。gate は対象 checkout の clean な HEAD に対し、
比較先との merge-base からの変更全体を調べて検証を再実行する。
成功は checks_passed。merge・deploy・削除の許可は発行しない。
実行ログと前後の状態を `.hw/evidence/` に保存する。エラー時はJSONまたはログを確認する。
検証でソースやHEADが変われば失敗。ignoreされた依存・設定・外部DBは完全な状態固定の
対象ではないため、その環境に関する必要な確認はプロジェクトの verify に含める。
対象外のworktreeへ .hw をコピーしたり、planの文字列だけを根拠に別checkoutへ移動しない。

利用者に見える振る舞いは、単体テストだけでなく実際に動かした証拠で確かめる。
再現できる確認（E2Eテスト、Playwright等の画面操作、APIへのリクエスト）はプロジェクトの
verify に入れる。人の目や一時的な環境が必要な確認は、Verifier が実行して review JSON の
checks に方法・結果・スクリーンショットや応答の保存先を記録する。観測できない項目は unverified。

## Claude Code による強制（v2.4.0）

手引きに書くだけでなく、Claude Code のフックとサブエージェント設定で次を強制する。
導入・更新は `update_hw.py` が行い、既存の `.claude/settings.json` の他の設定は保持する。

| 仕組み | 設定場所 | 効果 |
|---|---|---|
| PR作成前のゲート | `.claude/settings.json` の PreToolUse（`if: "Bash(gh *)"`。v2.5.1 で `gh pr create *` から広げた） | その commit で `hw.py gate` が通っていない、または未commitの変更があると `gh pr create` / `gh pr new`（`-R` / `--repo` 付きも含む）を止める（`hw.py pr-check`）。gh のほかのコマンドはフック内で判定して通す |
| Builder の完了前 verify | `.claude/settings.json` の SubagentStop（matcher: `hw-builder`） | verify が失敗すると完了させず差し戻す。3回差し戻したら止め、失敗として報告させる。先頭行が `HW-ESCALATE:` の報告（判断待ち）と、verify 未設定のときは止めない |
| 考える深さ | frontmatter の `effort` | Builder と Verifier は `high`（Opus 5.5 の既定は medium）。長い作業は `xhigh` も候補 |
| レビューの文脈 | `hw-verifier` / `hw-intent-reviewer` の `omitClaudeMd: true` | CLAUDE.md を読まずに起動し、渡した要求・資料と自分の実行結果で判断させる |

gate の記録は `.git` 内（worktree ごと）に置くため、作業ツリーは変更扱いにならない。
PR作成のフックは手元の確認で、承認ではない。CI の gate とマージの判断が引き続き権威になる。
Codex 等、Claude Code 以外の実行環境ではこのフックは働かないため、同じ手順を手で守る。

フックは frontmatter ではなく `.claude/settings.json` に置く。サブエージェント定義の frontmatter の
フックは、フォルダを信頼済み（trust dialog を承認）にしないと実行されず、`-p` では承認扱いにならない。
`omitClaudeMd` は Claude Code 2.1.271 以上。QA・意図レビューを fork（`/subtask` や
`context: fork` のスキル）で起動すると会話全体を引き継ぐため、定義済みのサブエージェントを使う。
非 fork のサブエージェントは親の会話履歴を受け取らないが、ファイルやツールの権限は別途制限が要る。

## 要求と独立レビュー

複雑な作業では、目的・元の要求を参照できる sources・受入条件を
`.hw/tasks/<task-id>.json` に保存する。Builderの自己評価を混ぜない。

```json
{
  "schema": 1,
  "purpose": "利用者が自分の申込履歴を確認できる",
  "acceptance": ["本人の履歴を表示する", "他人の履歴は表示しない"],
  "constraints": ["既存ログインを使う"],
  "sources": ["docs/requirements.md#history"],
  "review_required": true
}
```

仕様を含めてcommitした対象から、明示的な入力セットを作る:

```bash
python3 .hw/runtime/hw.py context --task history --role verifier \
  --base origin/main --output /tmp/history-review
```

request.json、role.md、changes.patch、manifest.json を出力する。manifest の binding は
比較先・HEAD・全差分・有効な方針のハッシュをまとめた値。parent の全履歴を渡さず、
新しいセッションへこの入力を渡す。ソースを調べる環境も別途用意する。
ソース差分には変更された文書も含む。入力セット生成だけで他文書や上位指示への
アクセスが禁止されるわけではない。manifest の isolation_enforced は false と明示する。

Verifier は元の要求と条件の抜け、変更全体、必要な実テストから独自に判断する。
結果のローカル形式:

```json
{
  "schema": 1,
  "binding": "manifest.json の binding",
  "reviewer": "実際に使った検証セッションの識別子",
  "context_mode": "fresh",
  "verdict": "pass",
  "checks": [{"method": "本人と別利用者のアクセスを実行", "result": "期待どおりの可否"}],
  "findings": [],
  "unverified": []
}
```

指摘は findings に `blocking`（boolean）、`scenario`（再現条件）、説明を記録する。
v2.5.0から、指摘に `origin`（`introduced` / `pre_existing`）と `class`（問題の種類）、結果に
`round`（manifest の値）を付ける。どれも省略できるが、付けた値は gate が形式を確かめる。
未実施・無応答・確認失敗は verdict を pass にしない。重大な欠陥は契約項目の有無と
無関係に blocking。修復後は新しい binding で全体を再確認する。
技術レビューのunverifiedは合否に必要だが未確認の項目。非空・null・不正な型のままのpassは拒否する。
従来の技術レビュー記録ではこの項目の省略を許すが、省略は確認済みであることの証明ではない。
reviewerには実際のセッションを識別する空でない文字列を記録する。これは身元の認証ではない。

```bash
python3 .hw/runtime/hw.py gate --base origin/main --task history \
  --review .hw/evidence/history-review.json
```

このJSONの存在・形式はチェックできるが、誰が作ったかや実際の独立性は証明できない。
ローカルの支援用記録であり、CIはこのJSONを合格根拠として受け付けない
(`review.authority` が `human_merge` でも同じ。CIはこのJSONを読まず、人が見る)。
過去のHD記録は原因調査に利用する。新方式は再発カテゴリの文字列で解除する仕組みを
持たず、必要な回帰検証を通常のテストへ組み込む。旧記録だけでは現在の欠陥は閉じない。

## レビューの収束（v2.5.0）

AI の作者と AI のレビュアーは、止める条件がないと往復を続ける。Claude Code チームでは
一晩で50周を超えたPRがあり、繰り返し指摘されたコードの約59%は初回のレビュー時点から
存在していた（[出典](https://claude.dev/blog/solving-code-review-at-anthropic/)、2026-10-17 確認）。
hw では次の4つで往復を収束させる。

- **3周まで。** 2周目以降の context は前回のレビュー結果から round を数える。4周目の作成は
  拒否されるので、主担当は1件ずつの修正を止め、方針を見直してユーザーへ返す。方針を変えて
  再開するときは `--approach-reset "<何を変えたか>"` を付ける。round は1に戻り、理由が manifest に残る。
- **全指摘に答える。** 前回の各指摘に `response` を付けてから次の周へ進む。`action` は
  `fixed`（直した）・`incorrect`（誤り）・`intended`（意図どおり）・`not_needed`（直す価値がない）。
  fixed 以外は `reason` が要る。この記録は、後でレビューのノイズを数える材料にもなる。
- **同じ指摘を繰り返さない。** 次の周の Verifier には前回の指摘と対応だけが届き、前回の
  verdict と checks は渡らない。理由付きで不採用の指摘は、新しい根拠がない限り再掲しない。
- **種類で直す。** Verifier は同じ原因の問題を `class` にまとめて全箇所を挙げ、Builder は
  同じ種類の箇所をすべて直す。

```bash
python3 .hw/runtime/hw.py context --task history --role verifier --base origin/main \
  --previous-review /tmp/history-review-1.json --output /tmp/history-review-2
```

前回の結果ファイルは、Verifier が返した JSON の findings に主担当が `response` を足したもの:

```json
{
  "round": 1,
  "findings": [
    {"blocking": true, "origin": "introduced", "class": "テナント外の検索",
     "scenario": "別テナントのIDで履歴APIを呼ぶと結果が返る",
     "response": {"action": "fixed"}},
    {"blocking": false, "origin": "introduced", "scenario": "変数名が曖昧",
     "response": {"action": "not_needed", "reason": "既存コードの命名に合わせた"}}
  ]
}
```

比較先から存在する問題は `origin: pre_existing` にし、blocking にしない。gate は
pre_existing の blocking 指摘を形式不正として拒否し、通った場合は `review_follow_up` に
一覧を返す。主担当はそれを報告に載せ、後続の対応にする。今回の変更で悪化した問題や
新たに到達できるようになった問題は `introduced` として扱う。

再レビューの範囲は狭めない。レビュー結果は従来どおり merge-base からの変更全体に結び付く。
2周目以降の Verifier は前回からの変更を重点的に見てよいが、binding は全体のまま。
v1 の「末尾の commit だけをレビュー」に戻さないため。
この上限は主担当への案内であり、前回の結果を渡さずに context を作れば数え直せる。
manifest に round と approach_reset が残るので、回避は記録から見える。

## REVIEW.md（v2.5.0）

REVIEW.md は、レビュアーに「このリポジトリで何を指摘し、何を指摘しないか」を伝える
プロジェクト所有のファイル。hw-verifier は CLAUDE.md を読まずに起動するため、
プロジェクトのレビュー基準はここに書く。リポジトリのルートに置き、特定の領域だけの基準は
そのディレクトリに置く（例: `services/billing/REVIEW.md`）。

`hw.py context --role verifier` は、ルートと変更したファイルの上位ディレクトリにある
REVIEW.md を**比較先のcommitから**読み、`review-guidance/` に同梱する。レビュー対象の変更が
自分の審査基準を書き換えられないようにするため。今回の差分で変えた REVIEW.md は
manifest の `review_guidance.changed_in_diff` に載り、merge 後に効力を持つ。REVIEW.md の
変更自体も独立レビューの対象になる。REVIEW.md は Nit の上限や報告しない対象を決められるが、
重大な欠陥の blocking は外せない。

短く書く。そのリポジトリでの失敗の形、Nit の上限、見なくてよいパス、根拠の求め方、
要約の書き方を入れる。同じ指摘が繰り返し出たら、ここか lint・テストへ移す。

```markdown
# Review instructions

## 何を重大とするか
挙動を壊す、データを漏らす、戻せなくする変更を blocking 候補にする。
スタイル・命名・リファクタの提案は Nit まで。

## Nit の上限
1回のレビューで Nit は5件まで。

## 報告しない
- CI が既に強制しているもの（lint、整形、型）
- 生成物: `src/gen/`、`*.lock`

## 必ず確認する
- 新しい API ルートに結合テストがある
- ログにメールアドレス・利用者ID・リクエスト本文を出さない

## 根拠
挙動の主張には file:line を付け、名前からの推測で指摘しない。
要約は指摘の件数から始め、直すものがなければそう書く。
```

同じファイルを GitHub の Code Review（Team / Enterprise 向けサービス）も読む。
ローカルの `/code-review` は REVIEW.md を読まず CLAUDE.md だけを読むため、両方で使う規則は
CLAUDE.md に書く（[Customize Code Review](https://code.claude.com/docs/en/code-review-customize)、
2026-10-17 確認）。

## PR本文と報告（v2.5.0）

完了報告とPR本文には次の項目を書く。Claude が下書きしてよいが、変更を依頼した人が
内容を確認する。

- **検証:** 実行したコマンドと結果、実際に動かした証拠の場所。
- **リスク:** 低・中・高と理由。書いた人（AIか人か）でなく変更内容で決める。
  - 低: 文書、文言、機能フラグで無効にできる追加など、利用者の挙動を変えないもの
  - 中: 通常の機能変更、キャッシュなど影響範囲の広い仕組みの変更
  - 高: 認証・権限・課金・データ移行や削除・並行処理・CI・リリース・インストーラ
- **戻し方:** revert だけで戻るか。データ移行・外部設定・配布済みの成果物など、revert で
  戻らないものとその手順。
- **独立レビュー:** 実施の有無、round、blocking の有無、後続対応にした pre_existing の指摘。
- **未確認事項:** 観測できなかったことと、その理由。

高リスクの変更には `instructions.md` の条件どおり反証型のレビューを足す。
リスクの分類は人が確認する深さを決める材料であり、承認の代わりにはならない。

## 別系統モデルによる確認（v2.3.0）

主担当と別の系統のモデルに、反証型のレビューや、主担当の仮説に引きずられない調査を頼む。
必須工程ではない。使う場面は `.hw/instructions.md` の条件に合う作業だけ。
Claude Code が主担当なら、OpenAI公式のCodexプラグイン（`codex@openai-codex`）を使う。
未導入・未認証の環境では使わず、実施しなかったことを報告する。導入は `/codex:setup`。
プラグインとCodex CLIの版で使えるコマンドが変わる。使う前に導入済みの版を確認する。

`/codex:review` と `/codex:adversarial-review` はモデルから呼べない設定のため、
主担当はプラグインのスクリプトを直接実行する（スラッシュコマンドも中身は同じ）:

```bash
CODEX_COMPANION="$(python3 -c 'import json, os; d = json.load(open(os.path.expanduser("~/.claude/plugins/installed_plugins.json"))); print(d["plugins"]["codex@openai-codex"][0]["installPath"])')/scripts/codex-companion.mjs"
# 反証型のレビュー（設計・前提・失敗条件を疑う）。末尾に疑ってほしい観点を書く
node "$CODEX_COMPANION" adversarial-review --wait --base origin/main "認証の境界と権限昇格を疑う"
# 通常のコードレビュー（観点の指定はできない）
node "$CODEX_COMPANION" review --wait --base origin/main
```

時間がかかる場合は Bash のバックグラウンド実行を使い、完了後に結果を読む。
レビューは読み取り専用で、コードを変更しない。

別経路の調査は、原要求・再現手順・失敗ログだけをファイルに書いて渡す。主担当の仮説・
試した修正・Builderの自己評価は入れない。`--write` を付けなければ読み取り専用で動く:

```bash
node "$CODEX_COMPANION" task --fresh --prompt-file /tmp/hw-rescue-request.md
```

修正までCodexに任せる場合は `--write` を付け、主担当やBuilderと同じファイルを同時に
変更しない（別worktreeか、担当範囲を分ける）。会話ごとCodexへ引き継ぐ場合は、
ユーザーが `/codex:transfer` を実行する。
停止時に自動レビューさせる `/codex:setup --enable-review-gate` は、往復が続いて使用量が
増えることがある。リリース前やデータ移行など、必要な案件の間だけ有効にする。

Codexは `~/.codex/skills/` の手順書と `AGENTS.md` を自動で読む。古い手順書が残っていると
レビューの観点が偏るため、結果の冒頭で読み込まれたファイルを確認する。
指摘は採否と理由を作業記録か報告に残す。high の指摘は修正するか、採用しない理由を示す。
Codexの出力はローカルレビューJSONではなく、`hw.py gate` の独立レビューの記録として扱わない。
Codexが主担当の環境では、この節をClaude等の別系統モデルへの依頼に読み替える。

## CI と外部の権限境界

CIは pull_request のHEADをcheckoutし、比較先のcommitに保存されたランナーと方針を使う。

独立レビューが必要な変更で、その実施を誰が判定するかは `.hw/project.json` の
`review.authority` で選ぶ。この設定はプロジェクト全体に効き、技術QAと意図レビューの
両方に適用される。片方だけ機械で強制する構成にはならない。

- `human_merge`: CIは外部チェックを読まない。差分とレビュー記録を人が見て merge を
  判断する。CIの結果には `review_authority: human_merge` と
  `merge_decision: human_required` が出て、workflow はそれをジョブサマリへ書く。
  誰がレビューしたか、独立していたか、bindingが一致するか、比較先が新しいかは
  CIでは確認しない
- `external_check`: 下記のとおり GitHub App の Check Run を読む

省略時は `external_check`。既存プロジェクトがランナー更新だけで緩まないようにするため。
ただし **hw-init のテンプレートは `human_merge` を書いた状態で配る**。新規プロジェクトは
人の判断で始まり、必要になったら上げる。導入時にどちらを選んだかを報告する。

`human_merge` を選ぶときは、次を運用側で用意する。設定を書くだけでは、機械の強制を
外した分がどこにも移らない。

- **レビュー記録の提示経路。** `.hw/evidence/` は通常 Git 管理外なので、レビューJSONは
  PRに現れない。主担当が、実施の有無・verdict・blockingの有無・確認した内容・採取した
  証拠の場所を PR 本文かコメントに書く（「PR本文と報告」の項目）。書かれていない PR は
  「レビューされていない」と同じ扱いにする
- **merge の抑止。** 緑のチェックはレビュー済みを意味しない。branch protection の
  必須レビュー承認を有効にする。有効にできないプラン・設定の場合は、その事実と
  「誰が merge を承認するか」を運用文書に書く
- **何を人に委ねたかの記録。** 独立レビューの実施・bindingの一致・比較先の鮮度の3つが
  機械的に強制されなくなる。型検査・テスト・evidence検査は従来どおりCIが守る

`external_check` では、設定した GitHub App の Check Run をGitHubから読み取る。
ローカルの verdict は受け付けない。最新の該当チェックが次をすべて満たす必要がある:

- `name` = review.check_name、`app.id` = review.app_id
- `head_sha` = PRのHEAD、`external_id` = このPR範囲の binding
- `status` = completed、`conclusion` = success

チェックを発行する側は、同じ比較先・HEAD・方針で binding を計算し、独立した検証を
完了してから発行する。未構成・無応答・古い比較先・古いチェック・API失敗はblock。
ローカルのCLIがチェックを書き込む機能はない。reviewerの結論待ちで先にCIが失敗した
場合はチェック発行後にGitHubの Re-run jobs で再実行する。

重要: これはGitHub Appの接続契約までの実装。独立したAppの配備・鍵・ポリシーは
プロジェクト側で用意する。同じPRの自由に変更可能な処理から成功を発行するAppでは
独立性が成立しない。GitHub Actions全体という広い発行主体だけで独立と判断しない。
workflow自体の保護、必須チェック、保護変更や迂回の権限分離はGitHub側で設定する。
比較先が更新された後も古い成功結果でmergeできないよう、保護側で最新の比較先との
検証を要求する。bindingの照合だけでGitHubのmerge許可設定までは強制できない。
本番変更は別の実行主体で、操作・対象・上限・期限を制限し、実行時にも条件を再確認する。
履歴は監査、承認は業務上の許可、テストは動作の証拠であり、相互に代用しない。

初回導入PRはbase側にv2ランナーがないため自動の最終ゲートを通らない。
保護された経路を初めて設置する変更として、人間が別途差分・ローカル検証・外部設定を
確認する。自動fallbackで全チェックを省略して成功にはしない。
テンプレートは依存インストールや環境起動を推測しない。既存CIの環境準備を保持して接続する。
merge queue/非GitHub CIにはこのPR用アダプターをそのまま使わず、対象SHAと信頼元を明示した
接続を別途実装する。doctorは外部保護や本番権限を設定済みとは報告しない。

## 意図・体験レビュー（v2.1、案件単位で有効化）

新しい体験、要件解釈の幅が大きい変更、技術チェックを通っても利用者の目的からずれた
案件で使う。調査や技術QAとは別軸。小さな確定済みの変更に一律の工程を増やさない。
主担当は .hw/roles/intent-reviewer.md を読み、段階ごとの新しいセッションを用意する。
Fableを既定候補とし、Claude入口 hw-intent-reviewer のmodelもfable。projectの
intent_review.modelは候補をmanifestへ記録する設定であり、CLIを自動起動しない。
別モデルを選んだときは、呼び出し側でmodel引数も変える。実行ログの実モデル名を記録する。

既存taskのスキーマに次を追加する。review_required（技術QA）とは独立に有効化できる。
下記はtask全体の例。すべての入力はリポジトリ内の追跡済み通常ファイルを指定する。
URLや見出し参照は直接コピーできないため、取得日・URL付きの資料ファイルを作る。
秘密や不要な全会話を格納しない。引用の出典と省略範囲を残す。

```json
{
  "schema": 1,
  "purpose": "初見の人が触りながら音楽の仕組みを発見する",
  "acceptance": ["スマホのタッチで遊べる"],
  "constraints": ["最初は窓3つと猫1匹"],
  "sources": ["docs/night/user-request.md"],
  "review_required": true,
  "review_paths": ["app/night/*", "lib/night-town/*", "docs/night/*"],
  "intent_review": {
    "required": true,
    "sources": ["docs/night/user-request.md"],
    "baseline": "docs/night/intent-baseline.json",
    "design": ["docs/night/design.md"],
    "evidence": ["docs/night/observations.md", "app/night/page.tsx"]
  }
}
```

intent_review.sourcesはユーザーの元の発言・参考資料と、意図確認の提示文・返答の原文。
合意されていない主担当の要約で置き換えない。purposeや
acceptanceはBuilder向けにも残すが、意図レビューのbaseline入力にはコピーしない。
この機構はファイル内容が本当に原文か、選別が公正かまで検証しない。

v2.1.1から、入力セットのrole.mdは対象HEADの `.hw/roles/intent-reviewer.md` と同じバイト列。
別の短縮プロンプトへ置換しない。manifestのreview_protocol_sha256とdesign/deliveryのbindingに
その内容を含める。更新前のdesign/delivery receiptは再利用できない。原文が同じならbaselineの
source_bindingは保たれるが、baseline自身の解釈は原文から点検する。旧checkoutに役割ファイルが
なければ更新計画で導入し、不在を短縮版へのfallbackで隠さない。


### 1. 設計前に原文だけで基準を作る

原文とtaskをcommitし、cleanな対象checkoutで実行する。この段階では指定した
baseline/design/evidenceのファイルはまだ存在しなくてもよい。

```bash
python3 .hw/runtime/hw.py context --task night --role intent-reviewer \
  --phase baseline --base origin/main --output /tmp/night-baseline
```

出力はrole.md、manifest.json、inputs/sources/以下の原文のコピーだけ。全差分・
主担当の受入条件・設計・既存レビューは含まない。意図確認の提示文は返答と対にして含める。
新しいセッションはmanifestにある
ファイルだけを読み、独自の期待を作る。親の会話をforkしない。
返された内容を次の形式で指定のbaselineファイルへ保存する。

```json
{
  "source_binding": "baseline manifest の source_binding",
  "expectations": [
    {"id": "E1", "statement": "触った対象から音と動きが返る", "basis": "元発言の箇所", "verification": "初回・反復タッチの音付き観測"}
  ],
  "assumptions": [],
  "unresolved": []
}
```

基準は主担当が都合よく書き換えない。原文に照らした訂正は理由を残して可能。
source_bindingはtask識別子と原文のパス・バイト列に結びつく。実装commitが増えても
変わらず、原文が変わると失効する。意味的な独立性を証明する署名ではない。

### 2. 設計を別セッションで照合する

baselineと設計をcommitしてから生成する。

```bash
python3 .hw/runtime/hw.py context --task night --role intent-reviewer \
  --phase design --base origin/main --output /tmp/night-design
```

原文・baseline・設計だけが入る。実装者の弁明や他レビュアーの結論を設計ファイルに
混ぜない。Reviewerは基準自体も原文で確認し、設計の不一致または必要な確認を返す。

レビュー記録の例（値とchecksは実際の結果に置き換える）:

```json
{
  "schema": 1,
  "axis": "intent",
  "phase": "design",
  "task": "night",
  "binding": "この段階の manifest.binding",
  "source_binding": "manifest.source_binding",
  "reviewer": "実際の独立セッション識別子・実モデル名",
  "context_mode": "fresh",
  "verdict": "pass",
  "checks": [{"method": "原文と設計の照合", "result": "対象ごとの応答と小規模試作の確認方法が設計されている"}],
  "findings": [],
  "unverified": []
}
```

```bash
python3 .hw/runtime/hw.py intent-gate --task night --phase design \
  --base origin/main --intent-review .hw/evidence/night-design-review.json
```

これは主担当が使う設計の確認点。Builder起動を強制的に制御するフックではない。
設計を変えたら再評価する。設計のpassは実際の楽しさを立証しない。

### 3. 小さな実物で確認し、納品判定へつなぐ

最小試作を動かし、必要な操作・画面・音・利用者観察を証拠として残す。どこで、誰が、
何を実施したか、取得対象と未確認範囲を書く。証拠をcommit後、delivery入力を作る。

```bash
python3 .hw/runtime/hw.py context --task night --role intent-reviewer \
  --phase delivery --base origin/main --output /tmp/night-delivery
```

原文・baseline・設計・明示したevidenceが入る。role省略は不可、phase省略時はdelivery。
コードや静止画だけで音・タッチ・初見の楽しさを確認したことにしない。
動画や実機が必要なのにアクセスできなければinsufficient_evidenceとして具体的に残す。
unverifiedには当該段階の合否に必要な未確認だけを入れ、範囲外や任意の留意点は
limitationsに書く。必須の未確認をlimitationsへ移して合格にすることは認めない。
同じ形式でphase=deliveryの結果を保存する。段階間の判定流用は拒否される。

```bash
python3 .hw/runtime/hw.py gate --base origin/main \
  --review .hw/evidence/technical-review.json \
  --intent-review .hw/evidence/night-delivery-review.json
```

gateは--taskの選択にかかわらず、変更に適用される全必須taskのdelivery判定を要求する。
複数なら--intent-reviewを繰り返す。技術レビューへ意図レビューを渡しても通らない。
revise / insufficient_evidence、unverifiedが残るpass、blocking指摘、別task、古い
bindingは失敗。status=checks_passedは従来どおりで、intent_review.status/tasksが加わる。
実行承認は発行しない。ローカルJSONの偽造や実際の閲覧順まではランナーで防げない。

### CIで必須化する場合

プロジェクト所有のproject.jsonに任意で追加する。既存ファイルは更新処理で自動変更しない。

```json
"intent_review": {
  "paths": ["app/night/*", "lib/night-town/*"],
  "check_name": "hw-intent",
  "app_id": 123456,
  "model": "fable"
}
```

pathsに合う変更には有効なtaskと意図レビューが必要。既存のreview.pathsとは別軸。
baseで有効なtaskの削除・無効化・関連範囲の縮小で、同じ変更から要件を外すことはできない。
CIは保護されたbase方針を用い、taskごとに次のCheck Runを要求する。

- name = `<intent_review.check_name>/<task-id>`（例: hw-intent/night）
- app.id = intent_review.app_id、head_sha = 対象HEAD
- external_id = そのtaskのdelivery manifest.binding
- status = completed、conclusion = success

技術側と同じApp・同じcheck名の組合せは拒否する。ただし別名だけでは独立性の証明に
ならない。発行側は意図レビューを独立に実施し、未確認や不一致があればsuccessにしない。
ローカルJSONへのfallbackはない。Appの配備・資格情報・必須チェック・変更保護は別途必要。
doctorのconfigured_not_verifiedは設定値の存在だけを示し、外部接続済みを意味しない。
