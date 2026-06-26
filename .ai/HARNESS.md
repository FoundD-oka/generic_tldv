---
owner: Harness Owner
updated: 2026-06-24
watch_paths:
  - .pipeline/config.json
  - .pipeline/adapters/
  - .pipeline/feedback/
  - .claude/agents/
  - .claude/skills/
  - .claude/hooks/
  - schemas/
staleness_days: 30
breaks_build_if_stale: false
---

# Harness Design

## Core

The harness owns the quality control surface around AI implementation:

1. Residency check
2. Plan Relay
3. S/M/L decision after planning
4. Gates
5. Evidence
6. Approval

## Plan Relay

Minimum relay:

```text
Context Scout
  -> Opus Planner
  -> Codex Plan Critic
  -> Opus Plan Refiner
  -> Plan Gate
```

If GitNexus exists, use it before broad file reading. If GitNexus is not available, run a read-only Context Scout first and pass a compact context pack into the planner.

## S/M/L Routing

S/M/L is not decided from the issue body alone. Decide it after the plan and verification contract exist.

| Size | Route |
|---|---|
| S | Claude Code plans, then Codex can perform a pinpoint fix. Residency, preflight, HD, doc, adapter, and feedback gates. |
| M | Test design, implementation, automated validation, evidence, draft PR when green. |
| L | Claude Code recursive loop, Codex as headless critic/checkpoint, tribunal or sidechain review, explicit human evaluation before PR. |

## Gates

Gates must be deterministic where possible. Do not rely on an implementing agent's self-report.

P0 gates:

- residency: core hooks, symlinks, schemas, and guard docs are still installed
- preflight: risky diff and test weakening detection
- adapter-contract: external tool manifests are valid
- feedback-prune: core harness growth is measured and non-contradictory
- qa-judge: independent QA judgment from plan/evidence only
- delivery-report: evidence and hash-bound approval bundle

## Evidence

Store evidence under:

```text
.pipeline/evidence/<issue-or-task>/
```

Required evidence for PR readiness:

- plan
- verification contract
- test command results
- preflight result
- residency / adapter / feedback gate results
- review or QA judgment
- tribunal report or sidechain synthesis for L work
- diff hash
- approval record when human approval is required

## Approval

Approval is not a chat message. Approval is an append-only record tied to a target hash:

```text
.pipeline/evidence/<issue-or-task>/approvals.jsonl
```
