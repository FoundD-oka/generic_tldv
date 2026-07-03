# Agent Coding Best Practices for Planner

Last reviewed: 2026-06-29

This document gives the planner a compact, current baseline for Claude Code,
Codex, and agentic coding work. It is guidance for planning, not proof of
delivery. Anything that must always happen belongs in scripts, hooks, gates, or
the verification contract.

## Source Baseline

Use official or primary sources when a task depends on current agent behavior,
AI tool capability, API usage, security, UX patterns, model selection, or vendor
limits.

| Source | Planner takeaway |
|---|---|
| Anthropic Claude Code best practices | Keep `CLAUDE.md` focused, use hooks for zero-exception behavior, create skills for reusable workflows, and use subagents for isolated exploration/review. |
| Anthropic Claude Code subagents docs | Use read-only planning/exploration agents to preserve main context and restrict tools; custom subagents can enforce focus and capability boundaries. |
| OpenAI Codex AGENTS.md docs | Codex reads `AGENTS.md` before work; repo guidance should encode stable expectations, setup, and verification commands. |
| OpenAI Codex skills docs | Skills are reusable workflows with explicit inputs/outputs; keep each skill focused and use scripts when deterministic behavior or external tooling is needed. |

## Planner Rules

1. Do not implement during planning. First translate the request into outcome,
   scope, constraints, unknowns, and non-goals.
2. Use Context Scout before planning when repository structure, ownership, entry
   points, or tests are not already obvious.
3. Use Research Scout before choosing an approach when current external
   knowledge could change the plan.
4. Prefer official documentation, primary sources, or live local repo evidence
   over memory and blog summaries.
5. Research Scout must start by asking what problem the request is really
   trying to solve, then write two or three hypotheses and search first for
   evidence that would break them.
6. Treat instructions as advisory and hooks/gates as deterministic. If failure
   must be impossible, add or reuse a gate instead of only writing a rule.
7. Keep implementation agents narrow: pass `plan_how`, allowed paths, forbidden
   paths, and verification commands; do not pass QA-only reasoning as truth.
8. Decide S/M/L after `plan.md` and `verification-contract.md` exist. Size is
   residual uncertainty and required control, not line count.
9. Make every claim testable. A plan is not complete until it can produce an
   evidence manifest, QA judgment when required, and an outcome card.

## Research Triggers

Research is required when the task touches any of these:

- OpenAI, Anthropic, model, or agent-product behavior
- Browser/computer-use automation
- security, secrets, auth, permission, sandbox, or data exfiltration risk
- current APIs, SDKs, libraries, framework versions, or package choices
- UX patterns, accessibility, responsive design, or visual quality
- external service limits, pricing, policy, or integration behavior
- legal, compliance, medical, finance, or other high-stakes domains

If research is skipped, the plan must say why the relevant knowledge is stable
enough to proceed without browsing/source checks.

## Plan Shape

The planner must produce:

- `research-brief.md`: reframed question, critical questions, hypotheses,
  disconfirming evidence, sources checked, findings, confidence, and stale
  assumptions
- `option-matrix.md`: at least two viable approaches when there is a meaningful
  choice
- `kpi-backcast-roadmap.md`: future KPIs by category, current baselines,
  checkpoint conversion, deliverable destinations, dependency graph, and
  schedule when the task needs stable implementation KPIs
- `plan.md`: chosen implementation path, blast radius, risks, non-goals
- `verification-contract.md`: commands, artifacts, browser checks, and failure
  criteria
- `sml-decision.json`: size and residual uncertainty after the plan exists

## Anti-Patterns

- Treating a model self-report as evidence
- Letting "latest best practice" live only in a prompt with no plan artifact
- Sizing from issue title or diff size
- Researching broadly without recording the decision impact
- Searching only for support after choosing a favorite approach
- Accepting the literal request when a reframe is needed to satisfy the actual
  outcome
- Passing planner uncertainty into the implementation agent as if it were fact
- Adding long rule prose when a deterministic hook or script is the real need
