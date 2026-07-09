# Vexa

## Harness Identity

This project uses the AI Delivery Harness as a Managed Agent Harness. Claude Code,
Codex CLI, Codex App, and Codex GitHub Action are runtime profiles over the same
core flow. Runtime reports are not the source of truth; deterministic gates and
outcome cards are.

Read `.ai/HARNESS.md` before planning non-trivial changes.
Read `docs/managed-agent-harness-architecture.md` before changing runtime profile behavior.
Read `docs/agent-coding-best-practices.md` before Plan Relay or any task that
depends on Claude Code, Codex, agentic coding, browser/computer-use, or current
AI-tool behavior.

## Core Rules

1. Build a plan before implementation unless the task is **clearly S**.
   **S fast-path** — skip full Plan Relay when ALL of these are true:
   - <= 2 files and <= ~30 lines of product code changed
   - No new external dependencies
   - No schema, migration, auth, payment, or PII paths
   - Implementation path is unambiguous after reading the code
   - Change can be described in one sentence
   When S fast-path applies: write a short `plan.md` (intent + approach, <= 10 lines) and `sml-decision.json`. If any criterion is in doubt, run the full relay.
2. Decide S/M/L after the plan and verification contract exist.
3. Use GitNexus first when `.gitnexus/` exists.
4. Run Research Scout before planning when the task depends on current UX
   patterns, libraries, APIs, regulations, security guidance, AI-tool behavior,
   or market/user expectations. Store `research-brief.md` and
   `option-matrix.md`, or record why research was skipped.
5. Research Scout must not be generic research. It should reframe the request
   when the literal ask would miss the real outcome, write hypotheses before
   source checks, search first for disconfirming evidence, and record confidence
   plus overturning conditions.
6. Use KPI Backcast when the task needs future-state KPIs, multi-category
   delivery, scheduling, or stable checkpoints. For contract work where
   requirements arrive as a feature list rather than KPIs, use
   `docs/feature-list-backcast-template.md` instead, or record a one-line
   skip reason in `research-brief.md`.
7. The planner must record an Agent Coding Best-Practice Check in the plan when
   Claude Code, Codex, subagents, skills, hooks, MCP, browser/computer-use, or
   other agent runtime behavior affects the work.
8. Advisory instructions are not proof. Anything that must happen with zero
   exceptions belongs in a hook, script, gate, or verification contract.
9. For L work, create Fable CLI external consultation evidence. For M/L work,
   call Fable at phase review, repeated-failure, plan-deviation, or final-audit
   points when the review would reduce risk.
10. Store plan, gate, evidence, and approval artifacts under `.pipeline/`.
11. Use `scripts/harness/worktree.sh` and `scripts/harness/build.sh` for non-trivial build work.
12. Do not treat an implementing agent's self-report or Fable output as proof.
13. Verify harness residency before PR readiness.
14. Write every human-facing report or publication in Japanese. This includes
   user replies, progress/completion reports, plan summaries, delivery reports,
   GitHub Issue titles/bodies/comments, GitHub PR titles/bodies/comments,
   review notes, and user-facing clean summaries. Machine-readable JSON/schema
   fields, file paths, commands, logs, and quoted external source text may stay
   in their required/original language, but explain them in Japanese.
15. Do not create br/cm/dcg/ubs workflow artifacts.

## Routing

| Situation | Read |
|---|---|
| Planning | `.ai/HARNESS.md`, `.pipeline/config.json`, existing context pack, research brief, option matrix |
| KPI Backcast | `docs/kpi-backcast-roadmap-template.md`, `docs/backcast-contracts.md`, verification contract |
| Agent coding planning | `docs/agent-coding-best-practices.md`, official vendor docs when current behavior matters |
| Implementation | `AGENTS.md`, approved plan, verification contract |
| Codex runtime profile | `.pipeline/agents/*.agent.json`, `.pipeline/environments/*.environment.json`, `.pipeline/adapters/*.adapter.json` |
| Codex session ledger | `.pipeline/sessions/<task-id>/events.jsonl` |
| Outcome judgment | `.pipeline/outcomes/<task-id>/outcome-card.json`, then `scripts/harness/outcome-judge.sh <task-id>` |
| Worktree build | `scripts/harness/worktree.sh create <task-id>`, then `scripts/harness/build.sh <task-id> --worktree <path> -- <command>` |
| Codex build unlock | `scripts/harness/codex-build.sh <task-id> --worktree <path>` |
| Operational smoke | `scripts/harness/full-loop-smoke.sh` |
| QA judgment | verification contract + evidence only |
| High-risk or L review | `.claude/agents/bug-tribunal.md` or `.claude/agents/sidechain-review.md` |
| Review finding recorded / recurrence | `.claude/skills/hd-log/SKILL.md` |
| External tool contract | `.claude/skills/adapter-contract/SKILL.md` |
| Fable consultation | `docs/fable-consultation.md`, then `.claude/hooks/external-consultation-validate.sh` |
| Harness rule growth | `.claude/skills/feedback-ledger/SKILL.md` |
| PR readiness check | run `bash .claude/hooks/pr-ready-gate.sh <task-id>` |
| Documentation update | `.ai/DOCS.md`, `.claude/skills/doc-update/SKILL.md` |

## Build And Test

See `.ai/BUILD.md`.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **generic_tldv** (15377 symbols, 27116 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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
