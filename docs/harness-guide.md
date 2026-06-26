# Harness Guide

## What This Is

This project uses a minimal AI delivery harness. The harness makes AI coding safer by controlling what can move from request to PR.

## Flow

```text
Request
  -> Residency Check
  -> Context Pack
  -> Plan Relay
  -> Plan Gate and S/M/L
  -> Implementation
  -> Tribunal or Sidechain Review (required for L, optional for high-risk S/M)
  -> Preflight
  -> Evidence
  -> QA Judgment (M/L)
  -> Approval (L)
  -> PR Ready Gate
  -> PR
```

## Gates

| Gate | Script | Blocks when |
|---|---|---|
| Residency | `.claude/hooks/harness-residency.sh` | core hooks, skills, links, `.pipeline`, or guard docs are missing |
| Preflight | `.claude/hooks/preflight.sh --full` | tests weakened, dangerous diff |
| HD Gate | `.claude/hooks/hd-gate.sh` | a finding category recurred without a recorded harness change |
| Doc Staleness | `.claude/hooks/doc-staleness.sh` | a `breaks_build_if_stale` doc is past its window |
| Adapter Contract | `.claude/hooks/adapter-validate.sh` | an existing adapter manifest is invalid |
| Feedback Prune | `.claude/hooks/feedback-prune.sh` | core harness changes add weak or contradictory rules |
| PR Ready | `.claude/hooks/pr-ready-gate.sh` | any gate, contract, evidence, QA, tribunal (L), or approval (L) check fails |

`gh pr create` is intercepted by the preflight hook and only allowed when the
PR Ready gate reports `ready`. Gate decisions come from script output, never
from agent self-report.

## Recurrence (HD)

Every review finding is recorded with `hd-record.sh`. When the same category
recurs, the HD gate blocks PR readiness until the harness itself changes
(rule, test, lint, skill, or doc) and the resolution is recorded. See
`.claude/skills/hd-log/SKILL.md`.

## Tribunal

L work must run the adversarial Bug Tribunal
(`.claude/agents/bug-tribunal.md`) and save `tribunal-report.json` into the
evidence pack before the PR Ready gate can pass.

`sidechain-review` is an accepted alternative for L work when the risk is not
best reviewed as code bugs. Save the synthesis at
`.pipeline/evidence/<task>/sidechain/synthesis.json`.

## Adapter Contracts

External tools used by the harness should have manifests under
`.pipeline/adapters/*.adapter.json`. The adapter gate validates manifests that
exist; young projects do not need adapters.

## Feedback Governance

When harness rules, skills, agents, hooks, `AGENTS.md`, `CLAUDE.md`, `.ci`, or
`schemas/` change, record measured feedback in `.pipeline/feedback/ledger.jsonl`
and run feedback pruning. This prevents the harness from accumulating
contradictory rules.

## What Matters

- The plan is the core artifact.
- S/M/L is decided after the plan exists.
- Gates are scripts and hooks where possible.
- Evidence is more important than agent self-report.
- Approval must identify the exact hash it approves.
- Recurring mistakes change the harness, not just the code.

## What Is Intentionally Missing

The old br/cm/dcg/ubs workflow is not part of this harness. GitHub Issue, `.pipeline`, and PR review should remain the source of delivery state.
