#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/harness/external-consultation.sh prepare <task-id> [--decision <text>] [--source <path>]
  scripts/harness/external-consultation.sh record <task-id> --response-file <path> [--provider chatgpt-pro-web]

prepare writes:
  .pipeline/plans/<task-id>/consultation-brief.md

record writes:
  .pipeline/evidence/<task-id>/external-consultation/chatgpt-pro.md
  .pipeline/evidence/<task-id>/external-consultation/consultation-summary.json

This script never stores browser credentials, cookies, tokens, or API keys.
USAGE
}

if [ "$#" -lt 2 ]; then
  usage >&2
  exit 2
fi

cmd="$1"
task_id="$2"
shift 2

provider="chatgpt-pro-web"
decision=""
source_path=""
response_file=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --decision)
      decision="${2:-}"
      shift 2
      ;;
    --source)
      source_path="${2:-}"
      shift 2
      ;;
    --response-file)
      response_file="${2:-}"
      shift 2
      ;;
    --provider)
      provider="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

python3 - "$cmd" "$task_id" "$provider" "$decision" "$source_path" "$response_file" <<'PY'
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

cmd, task_id, provider, decision, source_path_raw, response_file_raw = sys.argv[1:7]
root = pathlib.Path.cwd()
plans = root / ".pipeline" / "plans" / task_id
consult_dir = root / ".pipeline" / "evidence" / task_id / "external-consultation"
brief_path = plans / "consultation-brief.md"
response_path = consult_dir / "chatgpt-pro.md"
summary_path = consult_dir / "consultation-summary.json"


def rel(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_optional(path: str) -> str:
    if not path:
        return ""
    p = pathlib.Path(path)
    if not p.is_absolute():
        p = root / p
    if not p.exists():
        raise SystemExit(f"source path not found: {path}")
    return p.read_text(encoding="utf-8")


if cmd == "prepare":
    plans.mkdir(parents=True, exist_ok=True)
    source_text = read_optional(source_path_raw)
    excerpt = source_text.strip()
    if len(excerpt) > 6000:
        excerpt = excerpt[:6000] + "\n\n[truncated before consultation; verify against source artifact]"
    now = datetime.now(timezone.utc).isoformat()
    text = f"""# External Consultation Brief: {task_id}

Generated: {now}
Provider target: {provider}
Authentication mode: existing_browser_session_only

## Safety

- Redact secrets, credentials, customer PII, private tokens, and proprietary identifiers before sending.
- Do not ask the harness to log in.
- Do not paste passwords, one-time codes, cookies, API keys, or full private files.
- Treat the response as advisory review evidence, not source of truth.

## Decision Under Review

{decision or "[fill in the decision, plan, or option that needs a second-model review]"}

## Questions For GPT-Pro

1. What is the strongest objection to this plan?
2. What current best practice or trend could make this plan stale?
3. Which option should win, and what evidence would change that decision?
4. What should be verified locally before trusting this recommendation?

## Context To Send

```text
{excerpt or "[paste redacted context here]"}
```

## Required Output Shape

```text
Verdict:
Top risks:
Better option, if any:
Claims that need verification:
Sources or search terms to verify:
Do not trust this response for:
```
"""
    brief_path.write_text(text, encoding="utf-8")
    print(f"wrote {rel(brief_path)}")
    print("Open ChatGPT Pro Web with Computer Use only if an existing browser session is already authenticated.")
    sys.exit(0)

if cmd == "record":
    if provider != "chatgpt-pro-web":
        raise SystemExit("only provider=chatgpt-pro-web is supported by this script")
    if not response_file_raw:
        raise SystemExit("--response-file is required")
    response_file = pathlib.Path(response_file_raw)
    if not response_file.is_absolute():
        response_file = root / response_file
    if not response_file.exists():
        raise SystemExit(f"response file not found: {response_file_raw}")
    if not brief_path.exists():
        raise SystemExit(f"missing consultation brief: {rel(brief_path)}")

    consult_dir.mkdir(parents=True, exist_ok=True)
    response_text = response_file.read_text(encoding="utf-8")
    response_path.write_text(response_text.rstrip() + "\n", encoding="utf-8")
    brief_text = brief_path.read_text(encoding="utf-8")
    summary = {
        "schema_version": "1.0",
        "task_id": task_id,
        "provider": "chatgpt-pro-web",
        "status": "completed",
        "authentication_mode": "existing_browser_session_only",
        "redaction_confirmed": True,
        "prompt_hash": sha256_text(brief_text),
        "response_hash": sha256_text(response_text),
        "brief_path": rel(brief_path),
        "response_path": rel(response_path),
        "not_source_of_truth": True,
        "adopted_points": [],
        "rejected_points": [],
        "needs_verification": [
            "Populate this list with every GPT-Pro claim that affects the plan before PR readiness."
        ],
        "notes": "Generated from a ChatGPT Pro Web response captured through an existing browser session. Edit adopted/rejected/needs_verification after review."
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {rel(response_path)}")
    print(f"wrote {rel(summary_path)}")
    sys.exit(0)

raise SystemExit(f"unknown command: {cmd}")
PY
