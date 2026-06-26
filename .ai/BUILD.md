---
owner: Platform
updated: 2026-06-24
watch_paths:
  - package.json
  - pnpm-lock.yaml
  - package-lock.json
  - yarn.lock
  - pyproject.toml
  - requirements.txt
  - Makefile
  - .github/workflows/
staleness_days: 30
breaks_build_if_stale: false
---

# Build

## Source Of Truth

Use the repository's existing CI and package manager. Do not invent new commands during harness setup.

## Commands

| Action | Command | Notes |
|---|---|---|
| install | `[fill in]` | Use the repo's package manager |
| test | `[fill in]` | Must be captured in evidence |
| lint | `[fill in]` | Optional for S, expected for M/L when available |
| build | `[fill in]` | Required before PR when available |

## Harness Verification

Run:

```bash
bash .ci/harness-doctor.sh
```
