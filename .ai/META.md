---
owner: Harness Owner
updated: 2026-06-24
watch_paths:
  - .ai/
  - .pipeline/config.json
staleness_days: 90
breaks_build_if_stale: false
---

# Meta

## Review Cadence

Review the harness monthly or after a gate misses a real risk.

## Escalation

If a gate is skipped, evidence is incomplete, or approval cannot be reproduced, record it in:

```text
.pipeline/reports/pain-log.md
```

## Simplification Rule

Keep the harness small. Add a new skill only if it protects Plan quality, gates, evidence, approval, GitNexus context, or doc-update.
