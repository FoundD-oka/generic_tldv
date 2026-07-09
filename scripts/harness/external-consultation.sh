#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/harness/external-consultation.sh prepare <task-id> [options]
  scripts/harness/external-consultation.sh run <task-id> [options]
  scripts/harness/external-consultation.sh record <task-id> --response-file <path> [options]
  scripts/harness/external-consultation.sh classify <task-id> --finding <id> --status <status> [--note <text>]

Options:
  --provider <id>          Default: claude-fable-cli
  --mode <review|stuck|deviation|final>
  --step <text>            Plan step or checkpoint under review
  --decision <text>        Decision, plan change, or implementation result
  --source <path>          Extra redacted context file to include
  --attempts-file <path>   Failed attempts or log excerpts for stuck reviews
  --hypothesis <text>      Current hypothesis for stuck/deviation reviews
  --question <text>        Repeat up to three focused questions
  --model <id>             Default: fable
  --max-calls <n>          Default: config quality.fable_max_calls_per_task or 5
  --max-turns <n>          Default: HARNESS_FABLE_MAX_TURNS or 3
  --timeout-seconds <n>    Default: HARNESS_FABLE_TIMEOUT_SECONDS or 300
  --max-budget-usd <n>     Default: HARNESS_FABLE_MAX_BUDGET_USD or 1.00
  --resume / --no-resume   Default: resume same task's Fable session when known
  --bare                   Pass --bare to claude. Off by default because it
                           requires explicit API-key auth in Claude Code.

prepare writes:
  .pipeline/plans/<task-id>/consultation-brief.md

run writes:
  .pipeline/plans/<task-id>/consultation-brief.md
  .pipeline/evidence/<task-id>/external-consultation/fable.md
  .pipeline/evidence/<task-id>/external-consultation/fable-*.raw.json
  .pipeline/evidence/<task-id>/external-consultation/consultation-summary.json
  .pipeline/evidence/<task-id>/external-consultation/consultation-events.jsonl

record is kept for legacy/manual providers and writes the same summary shape.
classify updates adoption_status so the adopted/rejected ratio can be counted.
USAGE
}

if [ "$#" -lt 2 ]; then
  usage >&2
  exit 2
fi

cmd="$1"
task_id="$2"
shift 2

case "$cmd" in
  prepare|run|record|classify) ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    printf 'unknown command: %s\n' "$cmd" >&2
    usage >&2
    exit 2
    ;;
esac

python3 - "$cmd" "$task_id" "$@" <<'PY'
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

cmd = sys.argv[1]
task_id = sys.argv[2]
argv = sys.argv[3:]
root = pathlib.Path.cwd()
plans = root / ".pipeline" / "plans" / task_id
consult_dir = root / ".pipeline" / "evidence" / task_id / "external-consultation"
brief_path = plans / "consultation-brief.md"
summary_path = consult_dir / "consultation-summary.json"
events_path = consult_dir / "consultation-events.jsonl"
session_path = consult_dir / "fable-session.json"

READ_ONLY_BASH = ",".join([
    "Read",
    "Bash(pwd)",
    "Bash(ls *)",
    "Bash(find *)",
    "Bash(rg *)",
    "Bash(grep *)",
    "Bash(sed *)",
    "Bash(cat *)",
    "Bash(nl *)",
    "Bash(wc *)",
    "Bash(test *)",
    "Bash(git status *)",
    "Bash(git diff *)",
    "Bash(git show *)",
    "Bash(git log *)",
])

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["verdict", "summary", "findings", "confidence"],
    "properties": {
        "verdict": {"type": "string", "enum": ["MUST_FIX", "SHOULD_FIX", "SHIP"]},
        "summary": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "required": ["id", "severity", "title", "evidence", "recommendation"],
                "properties": {
                    "id": {"type": "string"},
                    "severity": {"type": "string", "enum": ["MUST_FIX", "SHOULD_FIX", "NOTE"]},
                    "title": {"type": "string"},
                    "evidence": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
            },
        },
        "local_verification": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}


def load_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_config() -> dict:
    data = load_json(root / ".pipeline" / "config.json")
    return data if isinstance(data, dict) else {}


def default_provider() -> str:
    quality = load_config().get("quality")
    if isinstance(quality, dict):
        value = quality.get("external_consultation_provider")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "claude-fable-cli"


def default_max_calls() -> int:
    quality = load_config().get("quality")
    value = None
    if isinstance(quality, dict):
        value = quality.get("fable_max_calls_per_task")
    if value is None:
        value = os.environ.get("HARNESS_FABLE_MAX_CALLS", "5")
    try:
        return max(0, int(value))
    except Exception:
        return 5


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--provider", default=default_provider())
    p.add_argument("--mode", choices=["review", "stuck", "deviation", "final"], default="review")
    p.add_argument("--step", default="")
    p.add_argument("--decision", default="")
    p.add_argument("--source", default="")
    p.add_argument("--attempts-file", default="")
    p.add_argument("--hypothesis", default="")
    p.add_argument("--question", action="append", default=[])
    p.add_argument("--response-file", default="")
    p.add_argument("--model", default=os.environ.get("HARNESS_FABLE_MODEL", "fable"))
    p.add_argument("--max-calls", type=int, default=default_max_calls())
    p.add_argument("--max-turns", type=int, default=int(os.environ.get("HARNESS_FABLE_MAX_TURNS", "3")))
    p.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("HARNESS_FABLE_TIMEOUT_SECONDS", "300")))
    p.add_argument("--max-budget-usd", default=os.environ.get("HARNESS_FABLE_MAX_BUDGET_USD", "1.00"))
    p.add_argument("--resume", dest="resume", action="store_true", default=True)
    p.add_argument("--no-resume", dest="resume", action="store_false")
    p.add_argument("--bare", action="store_true", default=os.environ.get("HARNESS_FABLE_BARE") == "1")
    p.add_argument("--finding", default="")
    p.add_argument("--status", choices=["open", "adopted", "rejected", "deferred", "already_handled", "invalid"], default="")
    p.add_argument("--note", default="")
    p.add_argument("-h", "--help", action="store_true")
    return p


args, extra = parser().parse_known_args(argv)
if args.help or extra:
    if extra:
        print(f"unknown argument(s): {' '.join(extra)}", file=sys.stderr)
    raise SystemExit(2)
args.max_calls = max(0, args.max_calls)


def rel(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_optional(path: str, limit: int = 8000) -> str:
    if not path:
        return ""
    p = pathlib.Path(path)
    if not p.is_absolute():
        p = root / p
    if not p.exists():
        raise SystemExit(f"source path not found: {path}")
    text = p.read_text(encoding="utf-8", errors="replace")
    if len(text) > limit:
        return text[:limit] + "\n\n[truncated; verify against the source artifact]"
    return text


def run_readonly(command: list[str], limit: int = 8000) -> str:
    try:
        out = subprocess.check_output(
            command,
            cwd=root,
            text=True,
            stderr=subprocess.STDOUT,
            timeout=20,
        )
    except Exception as exc:
        return f"[unavailable: {' '.join(command)}: {exc}]"
    return out[:limit] + ("\n[truncated]" if len(out) > limit else "")


def existing_plan_text() -> str:
    chunks = []
    for name in ["plan.md", "verification-contract.md", "option-matrix.md", "research-brief.md"]:
        path = plans / name
        if path.exists():
            chunks.append(f"## {name}\n\n{read_optional(str(path), 5000)}")
    return "\n\n".join(chunks) or "[no plan artifacts found yet]"


def default_questions(mode: str) -> list[str]:
    if mode == "stuck":
        return [
            "What is the most likely wrong assumption behind the repeated failure?",
            "What should be tried next with the smallest blast radius?",
            "Which evidence would falsify your diagnosis?",
        ]
    if mode == "deviation":
        return [
            "Is the proposed deviation justified by evidence?",
            "What must be changed in plan or verification before continuing?",
            "What risk appears if we keep the original plan?",
        ]
    if mode == "final":
        return [
            "Are there any remaining MUST-FIX issues before completion?",
            "Is the evidence strong enough for the claimed outcome?",
            "What should be explicitly reported as residual risk?",
        ]
    return [
        "What MUST-FIX issue, if any, should block this phase or commit?",
        "What SHOULD-FIX issue would materially improve quality without widening scope?",
        "Is this shippable if local verification passes?",
    ]


def make_brief() -> str:
    questions = (args.question or default_questions(args.mode))[:3]
    now = datetime.now(timezone.utc).isoformat()
    source_text = read_optional(args.source)
    attempts_text = read_optional(args.attempts_file)
    diff_stat = run_readonly(["git", "diff", "--stat", "HEAD", "--", ".", ":(exclude).pipeline"], 4000)
    diff_text = run_readonly(["git", "diff", "HEAD", "--", ".", ":(exclude).pipeline"], 10000)
    status_text = run_readonly(["git", "status", "--short"], 4000)
    plan_text = existing_plan_text()
    question_text = "\n".join(f"{idx + 1}. {q}" for idx, q in enumerate(questions))
    return f"""# Fable Consultation Brief: {task_id}

Generated: {now}
Provider target: {args.provider}
Mode: {args.mode}
Model: {args.model}

## Safety And Boundaries

- You are an advisory reviewer, not the implementer.
- Use local file reads and read-only shell inspection only.
- Do not edit files, run write commands, commit, push, install dependencies, or change state.
- Keep the answer concise. Each finding should be at most two sentences plus evidence.
- Treat your answer as advisory review evidence. Local tests, source checks, and project evidence remain the source of truth.

## Required Context Summary

### 1. Original Task And Plan Step

Task id: {task_id}
Plan step or checkpoint: {args.step or "[not specified]"}

Relevant plan artifacts:

```text
{plan_text}
```

### 2. Approaches Tried And Failure Reasons

```text
{attempts_text or "[none recorded for this consultation]"}
```

### 3. Current Hypothesis

{args.hypothesis or "[not specified]"}

### 4. Questions To Decide

{question_text}

## Decision Or Result Under Review

{args.decision or "[review the current diff and plan evidence]"}

## Extra Context

```text
{source_text or "[no extra source file provided]"}
```

## Current Git Status

```text
{status_text}
```

## Current Diff Stat

```text
{diff_stat}
```

## Current Diff Excerpt

```diff
{diff_text}
```

## Required JSON Output

Return only JSON matching this shape:

```json
{json.dumps(RESPONSE_SCHEMA, ensure_ascii=False, indent=2)}
```
"""


def write_brief() -> None:
    plans.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(make_brief(), encoding="utf-8")
    print(f"wrote {rel(brief_path)}")


def current_call_count(provider: str) -> int:
    if not events_path.exists():
        return 0
    count = 0
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("provider") == provider and event.get("event") == "run":
            count += 1
    return count


def append_event(event: dict) -> None:
    consult_dir.mkdir(parents=True, exist_ok=True)
    event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    event.setdefault("task_id", task_id)
    events_path.open("a", encoding="utf-8").write(json.dumps(event, ensure_ascii=False) + "\n")


def load_session_id() -> str:
    data = load_json(session_path)
    if isinstance(data, dict):
        value = data.get("session_id")
        if isinstance(value, str):
            return value
    return ""


def save_session_id(session_id: str) -> None:
    if not session_id:
        return
    consult_dir.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps({
            "provider": "claude-fable-cli",
            "model": args.model,
            "session_id": session_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def extract_json_object(text: str):
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("could not parse JSON object from model result")


def normalize_findings(payload: dict) -> list[dict]:
    findings = payload.get("findings")
    if not isinstance(findings, list):
        findings = []
    normalized = []
    for idx, item in enumerate(findings, start=1):
        if not isinstance(item, dict):
            item = {"title": str(item)}
        severity = str(item.get("severity", "NOTE")).upper()
        if severity not in {"MUST_FIX", "SHOULD_FIX", "NOTE"}:
            severity = "NOTE"
        finding_id = str(item.get("id") or f"F{idx}")
        finding_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", finding_id).strip("-") or f"F{idx}"
        normalized.append({
            "id": finding_id,
            "severity": severity,
            "title": str(item.get("title", "")).strip() or "(untitled)",
            "evidence": str(item.get("evidence", "")).strip(),
            "recommendation": str(item.get("recommendation", "")).strip(),
        })
    return normalized


def build_summary(provider: str, status: str, prompt_text: str, response_text: str, response_path: pathlib.Path,
                  raw_path, payload, call_index: int,
                  max_calls: int, session_id: str = "", resumed_session: str = "",
                  skip_reason: str = "") -> dict:
    payload = payload if isinstance(payload, dict) else {}
    findings = normalize_findings(payload)
    adoption_status = [
        {
            "id": f["id"],
            "severity": f["severity"],
            "status": "open",
            "note": "",
        }
        for f in findings
    ]
    needs_verification = []
    for f in findings:
        if f["severity"] in {"MUST_FIX", "SHOULD_FIX"}:
            needs_verification.append(f"{f['id']}: {f['recommendation'] or f['title']}")
    verdict = str(payload.get("verdict") or ("MUST_FIX" if any(f["severity"] == "MUST_FIX" for f in findings) else "SHIP"))
    if verdict not in {"MUST_FIX", "SHOULD_FIX", "SHIP"}:
        verdict = "SHOULD_FIX" if findings else "SHIP"
    open_must = sum(1 for item in adoption_status if item["severity"] == "MUST_FIX" and item["status"] in {"open", "deferred"})
    return {
        "schema_version": "1.1",
        "task_id": task_id,
        "provider": provider,
        "status": status,
        "mode": args.mode,
        "model": args.model if provider == "claude-fable-cli" else None,
        "authentication_mode": "not_used" if provider == "claude-fable-cli" else "existing_browser_session_only",
        "redaction_confirmed": True,
        "prompt_hash": sha256_text(prompt_text),
        "response_hash": sha256_text(response_text),
        "brief_path": rel(brief_path),
        "response_path": rel(response_path),
        "raw_response_path": rel(raw_path) if raw_path else None,
        "not_source_of_truth": True,
        "session_id": session_id,
        "resumed_session": resumed_session,
        "call_index": call_index,
        "max_calls": max_calls,
        "fallback_to_codex_only": status == "skipped" and skip_reason == "max_calls_reached",
        "skip_reason": skip_reason,
        "verdict": verdict,
        "confidence": str(payload.get("confidence", "")),
        "summary": str(payload.get("summary", "")),
        "findings": findings,
        "adoption_status": adoption_status,
        "open_must_fix_count": open_must,
        "adopted_points": [],
        "rejected_points": [],
        "needs_verification": needs_verification,
        "notes": "Fable CLI advisory review evidence. Classify each finding with the classify command after reflecting or rejecting it.",
    }


def write_summary(summary: dict) -> None:
    consult_dir.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {rel(summary_path)}")


def run_fable() -> None:
    if args.provider != "claude-fable-cli":
        raise SystemExit("run currently supports provider=claude-fable-cli")

    call_count = current_call_count(args.provider)
    if call_count >= args.max_calls:
        consult_dir.mkdir(parents=True, exist_ok=True)
        prompt_text = brief_path.read_text(encoding="utf-8") if brief_path.exists() else make_brief()
        response_path = consult_dir / "fable.md"
        response_text = "Fable consultation skipped because max_calls was reached.\n"
        response_path.write_text(response_text, encoding="utf-8")
        summary = build_summary(
            args.provider, "skipped", prompt_text, response_text, response_path,
            None, {"verdict": "SHIP", "summary": response_text, "findings": [], "confidence": "low"},
            call_count + 1, args.max_calls, skip_reason="max_calls_reached",
        )
        write_summary(summary)
        append_event({
            "event": "run",
            "provider": args.provider,
            "mode": args.mode,
            "status": "skipped",
            "skip_reason": "max_calls_reached",
            "call_index": call_count + 1,
            "max_calls": args.max_calls,
        })
        print("Fable max-calls reached; recorded Codex-only fallback.")
        return

    if not shutil.which("claude"):
        raise SystemExit("claude CLI not found; cannot run provider=claude-fable-cli")

    write_brief()
    prompt_text = brief_path.read_text(encoding="utf-8")
    previous_session = load_session_id() if args.resume else ""
    claude_args = [
        "claude",
        "-p",
        "--model", args.model,
        "--output-format", "json",
        "--json-schema", json.dumps(RESPONSE_SCHEMA, ensure_ascii=False),
        "--max-turns", str(args.max_turns),
        "--max-budget-usd", str(args.max_budget_usd),
        "--tools", "Read,Bash",
        "--allowedTools", READ_ONLY_BASH,
        "--disallowedTools", "Edit,Write,MultiEdit,NotebookEdit",
        "--permission-mode", "dontAsk",
    ]
    if args.bare:
        claude_args.append("--bare")
    if previous_session:
        claude_args.extend(["--resume", previous_session])
    claude_args.append(prompt_text)

    started = datetime.now(timezone.utc).isoformat()
    try:
        proc = subprocess.run(
            claude_args,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=args.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"claude fable consultation timed out after {args.timeout_seconds}s") from exc

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = consult_dir / f"fable-{args.mode}-{stamp}.raw.json"
    response_path = consult_dir / "fable.md"
    consult_dir.mkdir(parents=True, exist_ok=True)
    raw_text = proc.stdout.strip() or json.dumps({
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
    }, ensure_ascii=False)
    raw_path.write_text(raw_text + "\n", encoding="utf-8")

    if proc.returncode != 0:
        append_event({
            "event": "run",
            "provider": args.provider,
            "mode": args.mode,
            "status": "failed",
            "returncode": proc.returncode,
            "stderr": proc.stderr[-4000:],
            "started_at": started,
        })
        raise SystemExit(f"claude fable consultation failed with exit code {proc.returncode}: {proc.stderr[-1000:]}")

    outer = json.loads(raw_text)
    session_id = outer.get("session_id") or outer.get("sessionId") or ""
    result_obj = outer.get("result", outer.get("message", outer.get("content", outer)))
    if isinstance(result_obj, dict):
        payload = result_obj
        response_text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        response_text = str(result_obj)
        payload = extract_json_object(response_text)

    response_path.write_text(response_text.rstrip() + "\n", encoding="utf-8")
    save_session_id(session_id)
    summary = build_summary(
        args.provider,
        "completed",
        prompt_text,
        response_text,
        response_path,
        raw_path,
        payload,
        call_count + 1,
        args.max_calls,
        session_id=session_id,
        resumed_session=previous_session,
    )
    write_summary(summary)
    append_event({
        "event": "run",
        "provider": args.provider,
        "mode": args.mode,
        "status": "completed",
        "call_index": call_count + 1,
        "max_calls": args.max_calls,
        "verdict": summary.get("verdict"),
        "open_must_fix_count": summary.get("open_must_fix_count"),
        "session_id": session_id,
        "resumed_session": previous_session,
        "brief_path": rel(brief_path),
        "response_path": rel(response_path),
        "raw_response_path": rel(raw_path),
        "started_at": started,
    })
    print(f"wrote {rel(response_path)}")
    print(f"wrote {rel(raw_path)}")


def record_manual() -> None:
    if not args.response_file:
        raise SystemExit("--response-file is required")
    response_file = pathlib.Path(args.response_file)
    if not response_file.is_absolute():
        response_file = root / response_file
    if not response_file.exists():
        raise SystemExit(f"response file not found: {args.response_file}")
    if not brief_path.exists():
        write_brief()
    consult_dir.mkdir(parents=True, exist_ok=True)
    response_text = response_file.read_text(encoding="utf-8")
    if args.provider == "chatgpt-pro-web":
        response_path = consult_dir / "chatgpt-pro.md"
        payload = {
            "verdict": "SHIP",
            "summary": "Manual ChatGPT Pro response recorded; classify adopted/rejected points manually.",
            "findings": [],
            "confidence": "low",
        }
    else:
        response_path = consult_dir / "fable.md"
        try:
            payload = extract_json_object(response_text)
        except Exception:
            payload = {
                "verdict": "SHOULD_FIX",
                "summary": "Manual Fable response recorded without structured JSON.",
                "findings": [],
                "confidence": "low",
            }
    response_path.write_text(response_text.rstrip() + "\n", encoding="utf-8")
    prompt_text = brief_path.read_text(encoding="utf-8")
    summary = build_summary(
        args.provider,
        "completed",
        prompt_text,
        response_text,
        response_path,
        None,
        payload,
        current_call_count(args.provider) + 1,
        args.max_calls,
    )
    write_summary(summary)
    append_event({
        "event": "record",
        "provider": args.provider,
        "mode": args.mode,
        "status": "completed",
        "response_path": rel(response_path),
    })
    print(f"wrote {rel(response_path)}")


def classify() -> None:
    if not args.finding or not args.status:
        raise SystemExit("classify requires --finding <id> and --status <status>")
    summary = load_json(summary_path)
    if not isinstance(summary, dict):
        raise SystemExit(f"missing or invalid {rel(summary_path)}")
    statuses = summary.get("adoption_status")
    if not isinstance(statuses, list):
        statuses = []
    found = False
    for item in statuses:
        if isinstance(item, dict) and item.get("id") == args.finding:
            item["status"] = args.status
            item["note"] = args.note
            item["updated_at"] = datetime.now(timezone.utc).isoformat()
            found = True
            break
    if not found:
        statuses.append({
            "id": args.finding,
            "severity": "NOTE",
            "status": args.status,
            "note": args.note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    summary["adoption_status"] = statuses

    findings_by_id = {
        f.get("id"): f for f in summary.get("findings", [])
        if isinstance(f, dict)
    }
    adopted = []
    rejected = []
    for item in statuses:
        if not isinstance(item, dict):
            continue
        finding = findings_by_id.get(item.get("id"), {})
        title = finding.get("title") or item.get("id")
        status = item.get("status")
        if status in {"adopted", "already_handled"}:
            adopted.append(str(title))
        elif status in {"rejected", "invalid"}:
            rejected.append(str(title))
    summary["adopted_points"] = adopted
    summary["rejected_points"] = rejected
    summary["open_must_fix_count"] = sum(
        1 for item in statuses
        if isinstance(item, dict)
        and item.get("severity") == "MUST_FIX"
        and item.get("status") in {"open", "deferred"}
    )
    write_summary(summary)
    append_event({
        "event": "classify",
        "provider": summary.get("provider"),
        "finding": args.finding,
        "status": args.status,
        "note": args.note,
    })


if cmd == "prepare":
    write_brief()
elif cmd == "run":
    run_fable()
elif cmd == "record":
    record_manual()
elif cmd == "classify":
    classify()
else:
    raise SystemExit(f"unknown command: {cmd}")
PY
