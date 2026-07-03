# AGENTS.md - Harness Execution Index

## Purpose

This file is the short Codex-facing execution index. It points Codex to the approved task context and harness gates without loading the whole project history.

## Required Inputs

Codex implementation should receive only:

- approved plan or task brief
- verification contract summary
- relevant files
- build/test commands

Do not pass QA-only reasoning, hidden reviewer notes, or implementation self-critique as ground truth.

## Managed Agent Harness Core

This project uses the Managed Agent Harness model. Claude Code, Codex CLI,
Codex App, and Codex GitHub Action are runtime profiles over the same core flow.

```text
Task -> Plan Relay -> S/M/L -> Agent Profile -> Environment Profile
  -> Session Ledger -> Adapter Contract -> Gates -> Outcome -> Approval
```

| profile | role |
|---|---|
| `claude-code` | planning, orchestration, QA judgment |
| `codex-cli` | repo-local implementation, verification, diff review, outcome generation |
| `codex-app` | interactive planning, review, manual intervention |
| `codex-github-action` | CI autofix, PR assistance, patch artifacts |

Read `docs/managed-agent-harness-architecture.md` before changing runtime profile behavior.

## Codex Runtime Rules

- Codex implementation reads `AGENTS.md`, task brief, and approved `plan_how` only.
- Do not pass `plan_why` to implementation runtime. QA runtime reads it later.
- M/L work must preserve `.pipeline/sessions/<task-id>/events.jsonl`.
- M/L work must produce `.pipeline/outcomes/<task-id>/outcome-card.json`.
- Run `scripts/harness/outcome-judge.sh <task-id>` before claiming completion.
- Hooks are early warning. Final state is decided by deterministic gates and outcome cards.
- Keep credentials out of generated-code-readable sandbox state.

## Done Definition

| Size | Done |
|---|---|
| S | targeted change, relevant test or smoke check, residency + preflight + hd-gate + adapter validation pass |
| M | approved plan, verification contract, tests pass, S gates pass, evidence pack, QA judgment |
| L | M plus tribunal or sidechain synthesis, independent QA judgment, and hash-bound human approval before PR |

PR readiness for every size is decided by `bash .claude/hooks/pr-ready-gate.sh`.

## Evidence Paths

Use:

```text
.pipeline/plans/<issue-or-task>/
.pipeline/evidence/<issue-or-task>/
.pipeline/gates/<issue-or-task>/
.pipeline/adapters/
.pipeline/feedback/
.pipeline/sessions/<issue-or-task>/events.jsonl
.pipeline/outcomes/<issue-or-task>/outcome-card.json
```

## Forbidden Shortcuts

- Do not weaken tests to make them pass.
- Do not skip preflight.
- Do not create a PR when evidence is missing.
- Do not mark approval unless the target hash is recorded.
- Do not close a recurring finding category without an HD resolution record.
- Do not skip tribunal or sidechain evidence for L work.
- Do not add or promote harness rules without feedback pruning when core harness files changed.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **generic_tldv** (14938 symbols, 26215 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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
