# Vexa

このプロジェクトは hw ハーネス(hw-init 導入、2026-08-08)を使う。
権威の序列: CI > ローカルゲート > エージェント。エージェントの自己申告は証明にならない。
Fable が指示役(プランとレビュー)、Opus 5 が実行役(実装と修復)を担う。
例外ゼロで守らせたいことはフック・ゲート・検証契約に置く。このファイルは助言にすぎない。

## 受付パイプライン(実装前に必ず)

1. 意図を汲み取り、ゴールを再設計する。文字通りの依頼が本当の成果を外すなら
   reframe する。ただしクライアント合意済みの要求は reframe しない。
2. 現在のベストプラクティスに依存するタスクは先にリサーチ。仮説を先に書き、
   反証を優先して探し、確信度と覆る条件を plan.md に記録する。
3. プランは planner subagent(Fable 必須)が作る。メインセッションや他モデルで
   プランを書かない。プランには検証契約(何が通れば完了か)を含める。
   `plan.md` に `generated_by: fable`、`base-commit` に開始時 HEAD を記録する。
4. S/M/L はプラン確定後に判定する。物差しは実装の重さではなく残る不確定性の4軸
   (要求の曖昧さ / 技術的未知 / 影響範囲 / 検証可能性)。1軸でも L 相当なら L、
   迷ったら上へ倒す(fail-closed)。理由を .hw/plans/<task>/sml-decision.json へ記録。

## ルーティング(S/M/L = 不確定性の軸)

| サイズ | レビュー |
|---|---|
| S(不確定性ほぼゼロ) | なし。機械検証のみ。plan.md は10行以内で可 |
| M/L(未知が残る) | Fable 契約レビュー |

## 実行基盤(R軸 = 収まるかの軸)

S/M/L とは独立に判定し .hw/plans/<task>/runtime-decision.json へ記録する。
物差しは「1つのコンテキストと1セッションに収まるか」の4軸:
数時間超 / 状態がコンテキストに載らない量 / 並行委譲が必要 / 反復手順の資産化。
1軸でも該当なら prime。迷ったら inline へ倒す(Prime Agent はサンドボックスでない)。

| 判定 | 実行役 |
|---|---|
| inline(既定) | Opus 5 implementer |
| prime | Prime Agent + Codex。`python3 .hw/prime_run.py <task-id>` |

prime でもプランとレビューは Fable、CI が最終権威。変わるのは実行役だけ。
prime_run.py は worktree を切り、pr-ready-gate を Prime Agent の停止条件として渡す。

## モデル規律

- プラン設計と M/L レビュー = Fable 必須。
- 実装と修復 = Opus 5(implementer)。How と検証契約だけを渡し Why を渡さない。
- Fable のプラン・レビュー層は read-only。Edit・Write・commit・修復を行わない。
- 実行役は Fable READY を自己申告しない。
- Codex は主線から外す。救援(codex:rescue)と R軸 prime の実行役としてのみ使う。

## レビュー規律

- commit 済み clean tree で `python3 .hw/fable_review.py <task-id>` を実行する。
- `claude auth status` は sandbox 内で false になり得る。制限外で再確認する。
- Fable に渡すのは検証契約と `base-commit..HEAD` の差分だけ。
- 契約違反(violations)だけが修正義務。契約外は advisory として保存する。
  人間が明示採用した場合のみ契約かこのファイルを改訂し、次のタスクから基準になる。
- 違反は実行役へ戻し、修復・commit 後に Fable を再実行する。
- READY は対象差分 hash・契約 hash に束縛する。修復や契約変更で自動失効する。
- 契約は最低合格ライン。通ったら止める。要求品質を超えて作り込まない。

## ゲート

- PR 前に `bash .hw/hooks/pr-ready-gate.sh <task-id>` を通す。
  どのエージェントの `gh pr create` も共通 PreToolUse フックがインターセプトする。
- 検証とレビューは commit 済みの状態に対して行う(dirty tree を検証しない)。
- 指摘は .hw/rules/hd-log.tsv に追記(追記専用)。同カテゴリ再発時は
  .hw/rules/hd-resolutions.jsonl に改訂を記録すれば解除。改訂後に再発したら
  その改訂は無効と判定し、エージェントの改訂が2回失敗したら人間へ。
- ビルド/テストは .hw/verify.sh に定義する。CI が最終権威として全て再実行する。

## コンテキスト

- .gitnexus/ があれば planner が優先使用。索引が HEAD から大きく乖離し M/L 相当
  なら analyze を1回実行。更新しないなら概算扱いにして Grep で裏取りする。
- 人間向けの報告・PR・Issue はすべて日本語。機械可読キーは原語のまま意味を補足。

## このファイルの上限

80行以内を維持する。ルールを足すときは削るか統合する(prompt mud 防止)。
下の gitnexus ブロックは自動生成なので行数に数えない。

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **generic_tldv** (17041 symbols, 31305 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/generic_tldv/context` | Codebase overview, check index freshness |
| `gitnexus://repo/generic_tldv/clusters` | All functional areas |
| `gitnexus://repo/generic_tldv/processes` | All execution flows |
| `gitnexus://repo/generic_tldv/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
