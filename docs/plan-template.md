# Plan Template

## Language

この計画書は日本語で書く。コマンド、ファイルパス、JSON/schemaキー、
外部ソース名、引用文は必要に応じて原文のままでよいが、判断・理由・
ユーザーに伝える結論は日本語で説明する。

## Request

- Source:
- Issue or task:
- Requester:

## Context Pack

- GitNexus used: yes/no
- Context scout used: yes/no
- Key files:
- Unknowns:

## Research Scout

Use this before choosing an approach when the task depends on current best
practice, UX patterns, libraries, APIs, regulations, security posture,
AI-tool behavior, vendor limits, or market/user expectations.

Research Scout is not broad information gathering. It must reframe the request
when needed, state hypotheses before searching, try to disprove them first, and
turn the result into a decision for this plan.

- browsing used: yes/no
- skipped reason if no:
- research brief: `.pipeline/plans/<issue-or-task>/research-brief.md`
- option matrix: `.pipeline/plans/<issue-or-task>/option-matrix.md`
- original request:
- reframed question:
- why the reframe matters:
- critical questions:
- hypotheses tested:
- disconfirming evidence checked:
- current sources checked:
- candidate approaches compared:
- chosen approach:
- why this is best for this user/task:
- confidence:
- what would overturn this decision:
- what would make this decision stale:

## KPI Backcast Roadmap

Use this when the task needs implementation KPIs, multiple categories of work,
future-state clarity, milestone scheduling, or checkpoint stability.

- required: yes/no
- skipped reason if no:
- roadmap: `.pipeline/plans/<issue-or-task>/kpi-backcast-roadmap.md`
- future KPI categories:
- current baselines:
- checkpoint conversions:
- deliverable destinations:
- dependency graph summary:
- schedule summary:
- quality conditions to create:
- verification contract entries to add:

## Agent Coding Best-Practice Check

Use this when Claude Code, Codex, subagents, skills, hooks, MCP,
browser/computer-use, or agent runtime behavior affects the plan.

- `docs/agent-coding-best-practices.md` read: yes/no
- official/current sources checked:
- stable planner rules applied:
- deterministic controls required instead of advisory text:
- subagent / skill / MCP / hook implications:
- what the implementation agent should receive:
- what QA must verify from evidence:

## Proposed Change

## Non Goals

## Verification Contract

Link: `.pipeline/plans/<issue-or-task>/verification-contract.md`

## S/M/L Decision

| Field | Value |
|---|---|
| size | S/M/L |
| reason | |
| remaining uncertainty | |
| human gate required | yes/no |
| Fable consultation required | yes/no |
| consultation reason or unavailable reason | |

## Implementation Notes

## Risks

## External Consultation

- adapter: claude-fable-cli/not needed
- brief: `.pipeline/plans/<issue-or-task>/consultation-brief.md`
- evidence: `.pipeline/evidence/<issue-or-task>/external-consultation/consultation-summary.json`
- trigger: phase_review / same_test_failed_twice / plan_deviation / final_audit / not needed
- run command:
- adopted points to verify locally:
- rejected points:
- claims needing source/test evidence:

## Codex Plan Critique

- critique file:
- adopted:
- rejected:
- needs human:
