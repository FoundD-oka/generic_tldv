# Verification Contract

## Acceptance Tests

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| AT-001 | | unit/integration/smoke/manual | |

## Failure Patterns

| ID | Must Not Regress | Method | Evidence |
|---|---|---|---|
| FP-001 | | | |

## Non-Functional Checks

| ID | Requirement | Method | Evidence |
|---|---|---|---|
| NFT-001 | | | |

## KPI Checks

Use these when `kpi-backcast-roadmap.md` exists. Each KPI check should map to a
quality condition, a minimum OK line, and evidence that can be reviewed without
trusting agent self-report.

| KPI ID | Category | Minimum OK Line | Method | Evidence |
|---|---|---|---|---|
| KPI-001 | | | command/manual/source check | |

## Gate Requirements

- preflight result required: yes
- evidence pack required: yes
- hash-bound approval required: yes/no
- research brief required: yes/no
- option matrix required: yes/no
- kpi backcast roadmap required: yes/no
- external consultation required: yes/no
- external consultation provider: claude-fable-cli/not needed

## Research Freshness Checks

Use this when the chosen plan depends on current UX patterns, external APIs,
libraries, vendor behavior, security guidance, regulations, or market/user
expectations.

| ID | Decision That Can Go Stale | Freshness Method | Evidence |
|---|---|---|---|
| RF-001 | | browser/source check/manual note | `.pipeline/plans/<issue-or-task>/research-brief.md` |
