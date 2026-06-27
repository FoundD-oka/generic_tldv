---
owner: Documentation Owner
updated: 2026-06-27
watch_paths:
  - docs/
  - .ai/
  - AGENTS.md
  - CLAUDE.md
staleness_days: 45
breaks_build_if_stale: false
---

# Documentation Policy

## Scope

This project uses doc-update only as a support layer. It keeps harness-facing documentation aligned with implementation changes.

## Update Rules

- Update `.ai/HARNESS.md` when gates, evidence, approval, or routing changes.
- Update `.ai/BUILD.md` when build/test/lint commands change.
- Update `AGENTS.md` when Codex-facing execution rules change.
- Update `CLAUDE.md` when Claude routing rules change.
- GitNexus index-count refreshes in `AGENTS.md` / `CLAUDE.md` do not change
  execution policy; record the refresh date here when doc-staleness watches
  those files.

## Non Goals

- Do not maintain old br/cm/dcg/ubs workflow documentation.
- Do not create duplicate task state outside GitHub Issue or `.pipeline`.
