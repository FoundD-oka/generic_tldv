---
owner: Product Owner
updated: 2026-06-24
watch_paths:
  - .ai/HARNESS.md
  - .pipeline/config.json
staleness_days: 90
breaks_build_if_stale: false
---

# Intent

## What We Are Building
Vexa uses an AI Delivery Harness: a guardrail layer that routes work through planning, S/M/L sizing, deterministic gates, evidence, and hash-bound approvals before implementation results are trusted.

## What We Are NOT Building
- A generic task manager
- A replacement for Claude Code, Codex, or another coding agent
- A legacy all-in-one AI development workflow with br/cm/dcg/ubs dependencies

## Current Phase
Phase 1: Install the minimal harness foundation and use it on real changes.

## Success Criteria
- Plans are reviewed before implementation starts.
- S/M/L is decided after the plan exists.
- Preflight gates can block risky diffs.
- Evidence is written under `.pipeline/evidence/`.
- Approvals are tied to target hashes.
