# Harness Guide

## What This Is

This project uses a minimal AI delivery harness. The harness makes AI coding safer by controlling what can move from request to PR.

For a visual explanation, open `docs/backcast-harness-map.html`.

## Is This Task S?

Check all criteria. If **all** are true → S fast-path. If any is false or unclear → full relay.

```
≤ 2 files, ≤ ~30 lines? ──────────────────────────────────┐
No new external dependencies? ────────────────────────────┤
No schema/migration/auth/payment/PII? ────────────────────┤ ALL YES → S fast-path
Implementation path obvious after reading the code? ──────┤
Can be described in one sentence? ────────────────────────┘

ANY NO or UNSURE → full relay (decide size after plan)
```

**S fast-path:** write short `plan.md` + `sml-decision.json` → skip directly to Implementation.

## Flow

**S fast-path:**
```text
Request
  -> plan.md (intent + approach, ≤ 10 lines) + sml-decision.json
  -> Implementation
  -> Preflight / Gates (residency, hd, doc, adapter, feedback)
  -> PR Ready Gate
  -> PR
```

**M / L (full relay):**
```text
Request
  -> Residency Check
  -> Backcast Checkpoint (optional, required when configured)
  -> Context Pack
  -> Research Scout
  -> KPI Backcast Roadmap (when future KPIs or multi-step checkpoints are needed)
  -> Plan Relay
  -> Plan Gate and S/M/L
  -> Implementation
  -> Tribunal or Sidechain Review (required for L, optional for high-risk S/M)
  -> Fable Consultation (required for L by default; point checks for M/L)
  -> Preflight
  -> Evidence
  -> QA Judgment (M/L)
  -> Approval (L)
  -> Current Update
  -> Next Checkpoint Draft
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
| Backcast Contracts | `.claude/hooks/backcast-validate.sh` | checkpoint, evidence manifest, scope, state, or approval data is invalid |
| External Consultation | `.claude/hooks/external-consultation-validate.sh` | required Fable consultation evidence is missing, unsafe, or has open MUST-FIX findings |
| Feedback Prune | `.claude/hooks/feedback-prune.sh` | core harness changes add weak or contradictory rules |
| PR Ready | `.claude/hooks/pr-ready-gate.sh` | any gate, contract, evidence, QA, tribunal (L), or approval (L) check fails |

`gh pr create` is intercepted by the preflight hook and only allowed when the
PR Ready gate reports `ready`. Gate decisions come from script output, never
from agent self-report.

## Full-loop Skeleton Commands

Phase-1 execution is intentionally thin, but it must be real. A quality
checkpoint can now be taken from contract to evidence with these commands:

```bash
scripts/harness/backcast-checkpoint.sh <task-id> \
  --goal "<goal>" \
  --current "<current state>" \
  --target "<next quality checkpoint, not the finished issue goal>" \
  --condition "qc-001::<required condition>::<minimum OK line>::verify" \
  --command "verify::<test command>" \
  --allowed "src/**" \
  --approval-required

scripts/harness/sml-decision.sh <task-id> --size S --write-verification-contract

scripts/harness/worktree.sh create <task-id> --base HEAD

scripts/harness/build.sh <task-id> --worktree .pipeline/worktrees/<task-id>/checkout -- \
  sh -lc '<implementation command or agent command>'

scripts/harness/backcast-approval.sh <task-id> approved --approver "<name>"
scripts/harness/backcast-current.sh update <task-id> --actor "<name>"
scripts/harness/backcast-next-checkpoint.sh <task-id> \
  --next-task <next-task-id> \
  --target "<next quality checkpoint>" \
  --condition "qc-002::<required condition>::<minimum OK line>::verify-next" \
  --command "verify-next::<test command>" \
  --allowed "src/**"
bash .claude/hooks/backcast-validate.sh <task-id>
bash .claude/hooks/pr-ready-gate.sh <task-id>
```

`build.sh` runs the implementation command in the selected checkout, commits
implementation changes outside `.pipeline/`, runs verification commands from
the checkpoint contract, writes the Evidence Manifest, generates the Evidence
Pack, and advances the checkpoint state toward `awaiting_approval`.
`backcast-current.sh` promotes only approved or manual-override checkpoints into
`.pipeline/current/`. `backcast-next-checkpoint.sh` then drafts the next
checkpoint from that Current record, closing the fixture full-loop beyond
approval.

For Codex unlock, place `codex exec ...` behind the build command. The same
gates still decide readiness; Codex output is not proof until the manifest and
gate scripts pass.

When Codex CLI is available, use the dedicated runner:

```bash
scripts/harness/codex-build.sh <task-id> --worktree .pipeline/worktrees/<task-id>/checkout
```

It runs `codex exec --json` through the session ledger, requests a structured
build result schema, and then reuses `build.sh` so commit, verification,
manifest, pack, and state transitions stay deterministic.

For an end-to-end operational smoke in a disposable fixture repository:

```bash
scripts/harness/full-loop-smoke.sh
```

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

## Fable Consultation

L work also requires an external consultation record by default. The
`claude-fable-cli` adapter uses `claude -p --model fable` with bounded turns,
budget, timeout, a structured JSON response, and the same task's Fable session
resumed across repeated consultations.

M/L work may call Fable at review or stuck points: phase review, the same test
or approach failing twice, plan deviation, or final audit. S work normally does
not call Fable.

The consultation is a review fortress, not a source of truth. Record it under
`.pipeline/evidence/<task>/external-consultation/`, classify adopted/rejected
findings, then verify adopted claims with local tests, source checks, or project
evidence.

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

- Backcast checkpoints define the minimum OK lines for required conditions
  before build work continues; they are not the issue goal itself.
- Planner behavior improves when current Claude Code / Codex best practices are
  loaded, but advisory instructions are not enough for zero-exception behavior.
- Read `docs/agent-coding-best-practices.md` before planning agent-runtime,
  tool-use, browser/computer-use, or current AI-tool behavior changes.
- Research Scout checks whether the chosen approach is still a good fit when
  current UX, libraries, APIs, regulations, security, AI-tool behavior, or
  user expectations matter.
- Research Scout is a decision tool, not a reading marathon: reframe the
  request, state hypotheses, look for disproof first, then stop when the plan
  has enough evidence to choose a path.
- KPI Backcast turns future-state categories into realistic KPIs, quality
  conditions, deliverable destinations, dependency graph, and schedule. It
  protects checkpoints from drifting away from the user's intended outcome.
- The plan is the core artifact.
- S/M/L is decided after the plan exists.
- Gates are scripts and hooks where possible.
- Evidence is more important than agent self-report.
- Evidence Manifest is machine proof; Evidence Pack is human-readable summary.
- Approval must identify the exact hash it approves.
- Fable consultation is advisory evidence; adopted claims still need local verification.
- Recurring mistakes change the harness, not just the code.

## What Is Intentionally Missing

The old br/cm/dcg/ubs workflow is not part of this harness. GitHub Issue, `.pipeline`, and PR review should remain the source of delivery state.
