# Managed Agent Harness Architecture

作成日: 2026-06-17

## 結論

このハーネスは Claude Code 専用ではなく、複数 runtime を差し替えられる
Managed Agent Harness として扱う。

Claude Code / Codex CLI / Codex App / Codex GitHub Action は、同じ core model
を実行する runtime profile である。runtime ごとの違いは profile と adapter に
閉じ込め、Done 判定は deterministic gate と outcome judgment で行う。

## Core Model

```text
Task
  -> Context Scout / Research Scout
  -> KPI Backcast Roadmap when future KPIs or multi-step checkpoints are needed
  -> Plan Relay
  -> S/M/L Sizing
  -> Agent Profile
  -> Environment Profile
  -> Session Ledger
  -> Adapter Contract
  -> Deterministic Gates
  -> Outcome Judgment
  -> Approval / PR Ready
```

### Task

作業単位。Issue、ユーザー依頼、計画ファイル、PR 修正依頼のいずれかから
作られる。

最低限持つ情報:

- task id
- goal
- acceptance tests
- scope / non-scope
- size: S / M / L
- target runtime profile

### Agent Profile

どの agent が、どの目的で、どの context と tools を持つかを定義する。
prompt そのものではなく、実行者の契約である。

保存先:

```text
.pipeline/agents/*.agent.json
```

例:

- `codex-executor.agent.json`
- `codex-reviewer.agent.json`
- `claude-orchestrator.agent.json`

### Environment Profile

agent が作業する実行境界を定義する。

保存先:

```text
.pipeline/environments/*.environment.json
```

最低限定義するもの:

- sandbox mode
- network policy
- writable paths
- allowed commands
- secret policy
- expected setup commands

### Session Ledger

agent 実行の append-only event log。人間向け run journal ではなく、再開・監査・
outcome 判定・HD 判定の入力になる機械可読ログとして扱う。

保存先:

```text
.pipeline/sessions/<task-id>/events.jsonl
.pipeline/sessions/<task-id>/session.json
```

Codex runtime では `codex exec --json` の JSONL stream をそのまま ledger にする。

### Adapter Contract

外部 tool / MCP / CLI / GitHub / browser / screenshot / benchmark runner を
曖昧な「使えるツール」ではなく契約付き adapter として扱う。

保存先:

```text
.pipeline/adapters/*.adapter.json
schemas/harness-adapter.schema.json
```

adapter は以下を持つ:

- entrypoints
- input / output contract
- evidence contract
- safety boundaries
- validation checks

### Deterministic Gates

agent の自己申告ではなく、shell script / tests / schema validation / review result
で判定する。

例:

- `harness-doctor`
- `adapter-validate`
- `external-consultation-validate`
- `feedback-prune`
- `outcome-judge`
- `pr-ready-gate`

### Outcome Judgment

AT / FP / NFT / HD / review findings をまとめた task 完了判定。

保存先:

```text
.pipeline/outcomes/<task-id>/outcome-card.json
```

Outcome は PR ready の前提であり、agent の最終メッセージより強い。

## Runtime Profiles

| Runtime | 主用途 | Session ledger | Outcome | 注意点 |
|---|---|---|---|---|
| `claude-code` | orchestration / plan / QA | run journal + tool transcript | QA judgment | 品証 context を実装 context から分離する |
| `codex-cli` | repo-local implementation / verification | `codex exec --json` | `--output-schema` or outcome card | sandbox / approval を environment profile に固定する |
| `codex-app` | interactive planning / review | app thread + exported evidence | local outcome card | 人間の介入を ledger に残す |
| `codex-github-action` | CI auto-fix / PR checks | action logs + patch artifact | CI outcome card | API key を untrusted code と同じ job に置かない |

## Codex Runtime Design

Codex は clone runtime ではなく、repo-local execution runtime として設計する。

対応関係:

| Harness concept | Codex implementation |
|---|---|
| Durable guidance | `AGENTS.md` |
| Runtime config | `.codex/config.toml` |
| Lifecycle extension | `.codex/hooks.json` / `.codex/hooks/*` |
| Session ledger | `codex exec --json` JSONL |
| Structured final output | `codex exec --output-schema` |
| Continuation | `codex exec resume` |
| Execution boundary | cwd / git worktree / sandbox |
| Gate | `scripts/harness/*.sh` + project tests |
| PR automation | Codex GitHub Action / `gh` / app workflow |

## S/M/L Enforcement

| Size | Required core | Required artifacts |
|---|---|---|
| S | AGENTS + task brief + mandatory verification | optional session summary |
| M | Agent profile + environment profile + adapter validation + outcome card | `events.jsonl`, `outcome-card.json` |
| L | M + sidechain review + Fable consultation + feedback / HD governance | sidechain synthesis, external consultation summary, feedback prune report |

L task では `plan_why` を実装 runtime に渡さない。実装 runtime は `plan_how` と
task contract だけを見る。QA runtime は `plan_why` と evidence / outcome を見る。

## Security Invariants

- credentials は sandbox 内の generated code から直接読める場所に置かない。
- adapter が credentials を扱う場合は、entrypoint と evidence contract を明示する。
- Fable consultation uses bounded `claude -p --model fable` review and must not mutate repository state.
- hooks は補助 enforcement として使い、最終判定は deterministic gate に置く。
- session ledger は append-only とし、後から都合よく上書きしない。
- runtime profile は task ごとに選び、profile をまたいだ責務混在を避ける。

## Implementation Targets

この repo ではまず Codex runtime profile を実体化する。

```text
.pipeline/
  agents/
  environments/
  adapters/
  sessions/
  outcomes/
scripts/harness/
  codex-session-ledger.sh
  outcome-judge.sh
  backcast-state.sh
  worktree.sh
  build.sh
  codex-build.sh
  full-loop-smoke.sh
  validate-runtime-profile.sh
schemas/
  harness-agent.schema.json
  harness-environment.schema.json
  harness-adapter.schema.json
  outcome-card.schema.json
  codex-build-result.schema.json
```

Claude Code runtime は既存の `.claude/agents` と `.claude/skills` を継続利用する。
今後は同じ core model に沿って `.pipeline/agents/claude-*.agent.json` を追加する。
