# External Consultation Brief

## Purpose

Use this when an agent needs Fable as a second-model advisor, especially for L
work, phase review, repeated failures, plan deviations, or final audit.

This is advisory review evidence. It is not a deterministic gate result and is
not a substitute for tests, source checks, or project evidence.

## Safety

- provider: claude-fable-cli
- invocation: `claude -p --model fable`
- safe mode, built-in tools disabled, MCP disabled
- context-only review of the generated self-contained brief
- no repository mutation by the advisor
- no secrets, credentials, customer PII, private tokens, or unredacted proprietary data
- redaction confirmed: yes/no

## Task

- task id:
- goal:
- current plan:
- decision under review:
- plan step/checkpoint:
- approaches tried and failure reasons:
- current hypothesis:

## Questions For Fable

1. What MUST-FIX issue, if any, should block this phase or commit?
2. What SHOULD-FIX issue would materially improve quality without widening scope?
3. Is this shippable if local verification passes?

## Context To Send

Paste only redacted context here.

```text

```

## Required Output Shape

Ask Fable to answer with concise JSON in this shape:

```json
{
  "verdict": "MUST_FIX|SHOULD_FIX|SHIP",
  "summary": "...",
  "confidence": "low|medium|high",
  "findings": [
    {
      "id": "F1",
      "severity": "MUST_FIX|SHOULD_FIX|NOTE",
      "title": "...",
      "evidence": "...",
      "recommendation": "..."
    }
  ],
  "local_verification": ["..."]
}
```
