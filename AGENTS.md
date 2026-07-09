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
Task -> Context Scout -> Research Scout -> KPI Backcast -> Plan Relay -> S/M/L -> Agent Profile -> Environment Profile
  -> Worktree -> Build Runner -> Evidence -> External Consultation -> Gates -> Outcome -> Approval
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
- Planning must include `research-brief.md` and `option-matrix.md` when the task
  depends on current UX patterns, libraries, APIs, regulations, security
  guidance, AI-tool behavior, or market/user expectations. If skipped, the plan
  must say why.
- Research Scout reframes the request when needed, defines critical questions,
  writes hypotheses before source checks, looks first for disconfirming
  evidence, and records confidence plus overturning conditions.
- KPI Backcast is required when the task needs future-state KPIs,
  multi-category delivery, scheduling, or stable checkpoints. It writes
  `kpi-backcast-roadmap.md` and converts KPIs into checkpoint
  `quality_conditions`, deliverable destinations, and verification evidence.
- S work normally does not call Fable. M/L work may call Fable at phase review,
  same-test-failed-twice, plan-deviation, or final-audit points.
- L work requires Fable CLI external consultation evidence unless the task has
  already hit the configured Fable max-call fallback. Use
  `scripts/harness/external-consultation.sh run <task-id> --mode review`.
- Do not pass `plan_why` to implementation runtime. QA runtime reads it later.
- M/L work must preserve `.pipeline/sessions/<task-id>/events.jsonl`.
- M/L work must produce `.pipeline/outcomes/<task-id>/outcome-card.json`.
- Run `scripts/harness/outcome-judge.sh <task-id>` before claiming completion.
- Use `scripts/harness/worktree.sh create <task-id>` before non-trivial implementation.
- Use `scripts/harness/sml-decision.sh <task-id> --size S|M|L` when Plan Relay did not already write `sml-decision.json`.
- Use `scripts/harness/build.sh <task-id> --worktree <path> -- <command>` to bind implementation, verification, manifest, and evidence pack into one run.
- Use `scripts/harness/codex-build.sh <task-id> --worktree <path>` when Codex CLI should perform the implementation.
- Use `scripts/harness/full-loop-smoke.sh` to prove the installed harness can reach PR Ready in a disposable fixture.
- Hooks are early warning. Final state is decided by deterministic gates and outcome cards.
- Keep credentials out of generated-code-readable sandbox state.

## Human-Facing Language

Write every artifact meant for a human reader in Japanese. This includes
progress/completion reports, plan summaries, research summaries, option
matrices, delivery reports, GitHub Issue titles/bodies/comments, GitHub PR
titles/bodies/comments, review notes, and final user replies.

Machine-readable JSON/schema keys, adapter IDs, commands, file paths, logs, and
quoted external source text may stay in their required/original language. When
they appear in a human-facing artifact, explain the meaning or conclusion in
Japanese.

## Done Definition

| Size | Done |
|---|---|
| S | targeted change, relevant test or smoke check, residency + preflight + hd-gate + adapter validation pass |
| M | approved plan, verification contract, tests pass, S gates pass, evidence pack, QA judgment |
| L | M plus tribunal or sidechain synthesis, Fable consultation summary, independent QA judgment, and hash-bound approval before PR |

PR readiness for every size is decided by `bash .claude/hooks/pr-ready-gate.sh`.

## Evidence Paths

Use:

```text
.pipeline/plans/<issue-or-task>/
.pipeline/plans/<issue-or-task>/research-brief.md
.pipeline/plans/<issue-or-task>/option-matrix.md
.pipeline/plans/<issue-or-task>/kpi-backcast-roadmap.md
.pipeline/evidence/<issue-or-task>/
.pipeline/gates/<issue-or-task>/
.pipeline/adapters/
.pipeline/evidence/<issue-or-task>/external-consultation/
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
- Do not claim Fable output is proof; it is advisory consultation until locally verified.
- Do not add or promote harness rules without feedback pruning when core harness files changed.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **generic_tldv** (15564 symbols, 27575 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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
