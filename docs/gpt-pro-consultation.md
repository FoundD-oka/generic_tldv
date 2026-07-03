# GPT-Pro Web Consultation

This harness can use ChatGPT Pro Web as an external review fortress through
Codex App Computer Use.

It does not manage authentication. It only uses an already-authenticated,
operator-owned browser session. If the browser is logged out, blocked, or asks
for credentials, stop and record the consultation as unavailable.

## Flow

```text
Plan / option matrix
  -> scripts/harness/external-consultation.sh prepare <task-id>
  -> Codex App Computer Use opens https://chatgpt.com
  -> paste the redacted consultation brief
  -> copy the response into a temporary response file
  -> scripts/harness/external-consultation.sh record <task-id> --response-file <file>
  -> .claude/hooks/external-consultation-validate.sh <task-id>
  -> pr-ready-gate includes the consultation result when required
```

## Computer Use Rules

- Open `https://chatgpt.com` in the existing browser.
- Do not type passwords, one-time codes, or recovery information.
- Do not inspect, export, or save cookies.
- Do not send secrets, customer PII, private keys, full proprietary files, or
  unredacted identifiers.
- Prefer short, redacted briefs with concrete questions.
- Treat the answer as advisory. Verify anything adopted.

## Required Artifacts

```text
.pipeline/plans/<task-id>/consultation-brief.md
.pipeline/evidence/<task-id>/external-consultation/chatgpt-pro.md
.pipeline/evidence/<task-id>/external-consultation/consultation-summary.json
.pipeline/gates/<task-id>/external-consultation.json
```

## Required Summary Fields

`consultation-summary.json` must include:

- provider: `chatgpt-pro-web`
- authentication_mode: `existing_browser_session_only`
- redaction_confirmed: `true`
- prompt_hash
- response_hash
- not_source_of_truth: `true`
- adopted_points
- rejected_points
- needs_verification
