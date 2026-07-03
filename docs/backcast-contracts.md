# Backcast Contracts

Backcast mode adds a goal/checkpoint control layer before implementation. A
checkpoint is not the issue goal. It is the minimum quality line for one or more
conditions that must be true before the agent continues. Simple one-condition
work may not need a checkpoint; multi-condition work should use one to prevent
looped implementation from expanding past the requested quality line.

For a visual beginner-friendly map, open `docs/backcast-harness-map.html`.

## Paths

Use the current harness layout:

```text
.pipeline/plans/<task>/goal-contract.json
.pipeline/plans/<task>/current-state.md
.pipeline/plans/<task>/kpi-backcast-roadmap.md
.pipeline/plans/<task>/checkpoint-contract.json
.pipeline/evidence/<task>/evidence-manifest.json
.pipeline/approvals/<task>/approval-decision.json
.pipeline/current/<goal-id>.json
.pipeline/rules/backcast-state-machine.json
.pipeline/gates/<task>/backcast.json
```

## Commands

When the future state is broad or multi-category, draft
`.pipeline/plans/<task>/kpi-backcast-roadmap.md` from
`docs/kpi-backcast-roadmap-template.md` first. Convert each realistic KPI into a
checkpoint `quality_condition` with a minimum OK line, verification method, and
evidence path. Do not treat the KPI roadmap as proof; it is planning input for
the checkpoint contract and verification contract.

Create a Goal/Current/Checkpoint artifact set:

```bash
scripts/harness/backcast-checkpoint.sh <task-id> \
  --goal "User-facing goal" \
  --current "Current limitation" \
  --target "Next quality checkpoint, not the finished issue goal" \
  --condition "qc-001::API contract::API behavior is observable and stable::test" \
  --command "test::npm test" \
  --allowed "src/**" \
  --allowed "tests/**"
```

KPI conversion example:

```text
Future KPI:
  Category: API behavior
  KPI: The endpoint returns stable, documented error shapes for invalid input.

Quality condition:
  qc-api-errors::API error contract::Invalid input returns documented stable
  error shapes covered by integration tests::api-errors
```

Create a manifest from a checkpoint and run its verification commands:

```bash
scripts/harness/backcast-manifest.sh <task-id>
```

Collect git/scope/artifact state without running commands:

```bash
scripts/harness/backcast-manifest.sh <task-id> --no-run
```

Generate a readable pack strictly from the manifest:

```bash
scripts/harness/backcast-evidence-pack.sh <task-id>
```

Record an approval decision:

```bash
scripts/harness/backcast-approval.sh <task-id> approved --approver "Client Owner"
```

Promote an approved checkpoint into Current:

```bash
scripts/harness/backcast-current.sh update <task-id> --actor "Client Owner"
```

Draft the next checkpoint from the updated Current:

```bash
scripts/harness/backcast-next-checkpoint.sh <task-id> \
  --next-task <next-task-id> \
  --target "Next quality checkpoint" \
  --condition "qc-002::Condition::OK line::test" \
  --command "test::npm test" \
  --allowed "src/**"
```

## Minimum Checkpoint Contract

```json
{
  "schema_version": "1.0",
  "task_id": "issue-123",
  "goal_id": "client-goal-001",
  "checkpoint_id": "issue-123-cp-001",
  "checkpoint_type": "implementation",
  "state": "checkpoint_approved",
  "target_state": {
    "description": "The next quality checkpoint before continuing.",
    "target_state_type": "quality_checkpoint",
    "relationship_to_goal": "checkpoint_is_a_quality_line_before_issue_goal"
  },
  "quality_checkpoint": {
    "purpose": "Provide minimum state guarantees for required implementation conditions so agents can advance without overbuilding.",
    "issue_goal": "Implement the requested issue completely.",
    "checkpoint_target": "API behavior is observable and stable.",
    "not_the_issue_goal": true,
    "conditions_must_be_minimal": true
  },
  "kpi_backcast": {
    "roadmap": ".pipeline/plans/issue-123/kpi-backcast-roadmap.md",
    "future_kpis_converted_to_quality_conditions": true,
    "schedule_is_dependency_order_not_proof": true
  },
  "quality_conditions": [
    {
      "id": "qc-001",
      "condition": "API contract",
      "ok_line": "API behavior is observable and stable.",
      "target_state": "API behavior is observable and stable.",
      "verification": {
        "type": "command",
        "command_id": "test"
      },
      "progression": {
        "on_pass": "continue_to_next_condition_or_build",
        "on_fail": "fix_condition_or_split_scope"
      }
    }
  ],
  "acceptance_criteria": [
    {
      "id": "qc-001",
      "text": "API behavior is observable and stable.",
      "condition": "API contract",
      "quality_line": "API behavior is observable and stable.",
      "verification": {
        "type": "command",
        "command_id": "test"
      }
    }
  ],
  "progression_policy": {
    "pass_rule": "Every quality condition must pass before advancing.",
    "loop_guard": "Do not expand design beyond the ok_line while satisfying a condition; split scope if the condition needs a larger design.",
    "single_simple_feature_may_skip_checkpoint": true
  },
  "non_goals": [
    {
      "id": "ng-001",
      "text": "Do not change unrelated behavior."
    }
  ],
  "blast_radius": {
    "allowed_paths": [
      "src/**",
      "tests/**"
    ],
    "forbidden_paths": [
      "database/migrations/**",
      "src/auth/**"
    ]
  },
  "execution_bounds": {
    "max_changed_files": 12,
    "require_human_approval_before_pr": false,
    "require_scope_check_before_pr": true,
    "require_evidence_manifest_before_pr": true
  },
  "verification_commands": [
    {
      "id": "test",
      "command": "npm test",
      "required": true
    }
  ],
  "evidence_manifest_required": [
    "checkpoint_id",
    "base_sha",
    "head_sha",
    "command_results",
    "changed_files",
    "forbidden_path_check",
    "missing_evidence"
  ]
}
```

## Gate Rules

`backcast-validate.sh` fails closed when required data is present but invalid:

- the state machine references undefined states
- the checkpoint state is not declared in the state machine
- the checkpoint has no `quality_conditions`
- any quality condition is missing, failed, or lacks evidence
- changed files touch `blast_radius.forbidden_paths`
- changed file count exceeds `execution_bounds.max_changed_files`
- the checkpoint requires an evidence manifest and it is missing
- a required command in the manifest has a non-zero exit code
- a required artifact is marked missing or does not exist on disk
- `missing_evidence` is not empty
- `repo.head_sha` does not match current `HEAD`
- approval is required but missing or not approved

Evidence Pack prose must summarize the Evidence Manifest; it must not invent
tests, artifacts, or approval state not present in the machine-readable
manifest.
