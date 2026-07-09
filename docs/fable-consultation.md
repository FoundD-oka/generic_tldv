# Fable Consultation

This harness uses Claude Fable through `claude -p` as the external advisor for
L work and as a point check for M/L work when a phase review, repeated failure,
plan deviation, or final audit needs an independent second opinion.

Fable is advisory. It does not implement, approve, or decide PR readiness by
itself. Local tests, source checks, evidence manifests, outcome cards, and
deterministic gates remain the source of truth.

## When To Run

| Size | Fable use |
|---|---|
| S | normally none |
| M | run only at review/stuck/deviation points |
| L | required by default before PR readiness |

Run Fable when:

- a phase or commit-sized diff is ready for review
- the same test or implementation path failed twice
- the plan needs a deviation
- completion is about to be claimed for L work

## Flow

```text
Plan / verification contract / current diff
  -> scripts/harness/external-consultation.sh run <task-id> --mode review
  -> Fable returns MUST_FIX / SHOULD_FIX / SHIP JSON
  -> classify adopted or rejected findings after reflection
  -> .claude/hooks/external-consultation-validate.sh <task-id>
  -> pr-ready-gate includes the consultation result when required or present
```

## Commands

Review the current diff:

```bash
scripts/harness/external-consultation.sh run <task-id> --mode review
```

Ask for stuck-case advice after repeated failure:

```bash
scripts/harness/external-consultation.sh run <task-id> \
  --mode stuck \
  --attempts-file .pipeline/evidence/<task-id>/failed-test.log \
  --hypothesis "<current hypothesis>" \
  --question "<one focused question>"
```

Check a plan deviation:

```bash
scripts/harness/external-consultation.sh run <task-id> \
  --mode deviation \
  --decision "<planned deviation and reason>"
```

Mark a finding after it has been reflected or rejected:

```bash
scripts/harness/external-consultation.sh classify <task-id> \
  --finding F1 \
  --status adopted \
  --note "<where the fix or decision was recorded>"
```

Allowed statuses are `adopted`, `rejected`, `deferred`, `already_handled`,
`invalid`, and `open`.

## Context Requirements

Every Fable brief must include:

1. original task and the plan step/checkpoint under review
2. approaches tried and failure reasons, including relevant log excerpts
3. current hypothesis
4. one to three concrete questions to decide

`external-consultation.sh run` generates this structure automatically from
available plan artifacts, the current diff, and the options passed to the
command.

## Cost Controls

The default limits are:

- `quality.fable_max_calls_per_task = 5`
- `--max-turns 3`
- `--max-budget-usd 1.00`
- `--timeout-seconds 300`

When the max call count is reached, the script records a Codex-only fallback
instead of starting another Fable call. Use this as a budget guard, not as a
quality claim.

## Required Artifacts

```text
.pipeline/plans/<task-id>/consultation-brief.md
.pipeline/evidence/<task-id>/external-consultation/fable.md
.pipeline/evidence/<task-id>/external-consultation/fable-*.raw.json
.pipeline/evidence/<task-id>/external-consultation/consultation-summary.json
.pipeline/evidence/<task-id>/external-consultation/consultation-events.jsonl
.pipeline/gates/<task-id>/external-consultation.json
```

## Required Summary Fields

`consultation-summary.json` must include:

- provider: `claude-fable-cli`
- status: `completed`, or `skipped` only for max-call fallback
- authentication_mode: `not_used`
- prompt_hash
- response_hash
- verdict: `MUST_FIX`, `SHOULD_FIX`, or `SHIP`
- findings
- adoption_status
- open_must_fix_count
- adopted_points
- rejected_points
- needs_verification
- not_source_of_truth: `true`

Open `MUST_FIX` findings block PR readiness until the work is fixed and Fable
is rerun, or the finding is classified with a justified non-open status.
