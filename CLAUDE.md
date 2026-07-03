# Vexa

## Harness Identity

This project uses the AI Delivery Harness as a Managed Agent Harness. Claude Code,
Codex CLI, Codex App, and Codex GitHub Action are runtime profiles over the same
core flow. Runtime reports are not the source of truth; deterministic gates and
outcome cards are.

Read `.ai/HARNESS.md` before planning non-trivial changes.
Read `docs/managed-agent-harness-architecture.md` before changing runtime profile behavior.

## Core Rules

1. Build a plan before implementation unless the task is clearly S.
2. Decide S/M/L after the plan and verification contract exist.
3. Use GitNexus first when `.gitnexus/` exists.
4. Store plan, gate, evidence, and approval artifacts under `.pipeline/`.
5. Do not treat an implementing agent's self-report as evidence.
6. Verify harness residency before PR readiness.
7. Do not create br/cm/dcg/ubs workflow artifacts.

## Routing

| Situation | Read |
|---|---|
| Planning | `.ai/HARNESS.md`, `.pipeline/config.json`, existing context pack |
| Implementation | `AGENTS.md`, approved plan, verification contract |
| Codex runtime profile | `.pipeline/agents/*.agent.json`, `.pipeline/environments/*.environment.json`, `.pipeline/adapters/*.adapter.json` |
| Codex session ledger | `.pipeline/sessions/<task-id>/events.jsonl` |
| Outcome judgment | `.pipeline/outcomes/<task-id>/outcome-card.json`, then `scripts/harness/outcome-judge.sh <task-id>` |
| QA judgment | verification contract + evidence only |
| High-risk or L review | `.claude/agents/bug-tribunal.md` or `.claude/agents/sidechain-review.md` |
| Review finding recorded / recurrence | `.claude/skills/hd-log/SKILL.md` |
| External tool contract | `.claude/skills/adapter-contract/SKILL.md` |
| Harness rule growth | `.claude/skills/feedback-ledger/SKILL.md` |
| PR readiness check | run `bash .claude/hooks/pr-ready-gate.sh <task-id>` |
| Documentation update | `.ai/DOCS.md`, `.claude/skills/doc-update/SKILL.md` |

## Build And Test

See `.ai/BUILD.md`.

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
