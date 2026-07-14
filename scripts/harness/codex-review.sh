#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/harness/codex-review.sh run <task-id> --mode <plan|post|stuck> [options]
  scripts/harness/codex-review.sh record <task-id> --mode <plan|post|stuck> --response-file <path>
  scripts/harness/codex-review.sh classify <task-id> --mode <plan|post|stuck> --finding <id> --status <status> [--note <text>]
  scripts/harness/codex-review.sh failure <task-id> --signature <stable-id> [--command-id <id>] [--note <text>]

Review options:
  --source <path>          Extra context to include after redaction
  --question <text>        Repeat up to three focused questions
  --timeout-seconds <n>    Default: HARNESS_CODEX_REVIEW_TIMEOUT_SECONDS or 900

The reviewer is pinned by .pipeline/config.json to GPT-5.6 Sol with ultra
reasoning, runs in a read-only sandbox, and writes stage-specific hash-bound
review evidence under .pipeline/evidence/<task-id>/codex-review/.
USAGE
}

if [[ "$#" -lt 2 ]]; then
  usage >&2
  exit 2
fi

cmd="$1"
task_id="$2"
shift 2
case "$cmd" in
  run|record|classify|failure) ;;
  -h|--help) usage; exit 0 ;;
  *) echo "unknown command: $cmd" >&2; usage >&2; exit 2 ;;
esac

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$git_root"
fi

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

cmd, task_id = sys.argv[1:3]
root = pathlib.Path.cwd()
plans = root / ".pipeline" / "plans" / task_id
review_dir = root / ".pipeline" / "evidence" / task_id / "codex-review"
latest_path = review_dir / "review-summary.json"
failures_path = review_dir / "failure-events.jsonl"
trigger_path = review_dir / "review-trigger.json"


def load_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


config = load_json(root / ".pipeline" / "config.json") or {}
policy = config.get("review_policy") if isinstance(config.get("review_policy"), dict) else {}
model = str(policy.get("codex_review_model", "gpt-5.6-sol"))
effort = str(policy.get("codex_review_reasoning_effort", "ultra"))
failure_threshold = int(policy.get("same_failure_threshold", 2))

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--mode", choices=["plan", "post", "stuck"], default="post")
parser.add_argument("--source", default="")
parser.add_argument("--question", action="append", default=[])
parser.add_argument("--response-file", default="")
parser.add_argument("--finding", default="")
parser.add_argument("--status", choices=["open", "adopted", "rejected", "deferred", "already_handled", "invalid"], default="")
parser.add_argument("--note", default="")
parser.add_argument("--signature", default="")
parser.add_argument("--command-id", default="")
parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("HARNESS_CODEX_REVIEW_TIMEOUT_SECONDS", "900")))
parser.add_argument("-h", "--help", action="store_true")
args, extra = parser.parse_known_args(sys.argv[3:])
if args.help or extra:
    if extra:
        print(f"unknown argument(s): {' '.join(extra)}", file=sys.stderr)
    raise SystemExit(2)

RESPONSE_SCHEMA = {
    "type": "object",
    # OpenAI structured outputs require every object in the response schema to
    # declare additionalProperties=false. Keep the normalized on-disk summary
    # schema separate; this schema only constrains the reviewer's raw answer.
    "additionalProperties": False,
    "required": ["verdict", "summary", "findings", "confidence"],
    "properties": {
        "verdict": {"type": "string", "enum": ["MUST_FIX", "SHOULD_FIX", "SHIP"]},
        "summary": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
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
    },
}


def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def rel(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def read_path(value: str, limit: int = 12000) -> str:
    if not value:
        return ""
    path = pathlib.Path(value)
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        raise SystemExit(f"source path not found: {value}")
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit] + ("\n[truncated]" if len(text) > limit else "")


def target() -> tuple[str, str]:
    if args.mode == "plan":
        chunks = []
        for name in ["request.md", "issue.md", "task-brief.md", "plan.md", "verification-contract.md"]:
            path = plans / name
            if path.exists():
                chunks.append(f"## {name}\n{path.read_text(encoding='utf-8', errors='replace')}")
        return "plan", "\n\n".join(chunks)
    build = load_json(root / ".pipeline" / "evidence" / task_id / "build" / "build-summary.json") or {}
    base = str(build.get("head_sha") or "HEAD")
    try:
        diff = subprocess.check_output(
            ["git", "diff", base, "--", ".", ":(exclude).pipeline"],
            cwd=root, text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        diff = ""
    return "diff", diff


def questions() -> list[str]:
    if args.question:
        return args.question[:3]
    if args.mode == "plan":
        return [
            "Is the plan technically sound and implementable without hidden scope expansion?",
            "Which failure mode or verification gap could invalidate the plan?",
            "Should implementation start, or is there a MUST-FIX blocker?",
        ]
    if args.mode == "stuck":
        return [
            "What shared wrong assumption best explains the repeated failure?",
            "What is the smallest discriminating experiment to run next?",
            "Which code path deserves the deepest inspection?",
        ]
    return [
        "Does the diff correctly implement the approved plan without regression?",
        "Are tests and evidence sufficient for the claimed behavior?",
        "Is any MUST-FIX issue still present before PR readiness?",
    ]


def make_prompt() -> str:
    kind, material = target()
    plan_text = []
    for name in ["request.md", "issue.md", "task-brief.md", "plan.md", "verification-contract.md"]:
        path = plans / name
        if path.exists():
            plan_text.append(f"## {name}\n{path.read_text(encoding='utf-8', errors='replace')[:10000]}")
    qs = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions()))
    return f"""You are the independent implementation guardian for task {task_id}.

Model policy: GPT-5.6 Sol with ultra reasoning.
Review mode: {args.mode}
Target kind: {kind}
Target hash: {sha(material)}

Operate read-only. Do not edit, commit, push, install dependencies, or change repository state.
Judge correctness, regressions, interface compatibility, verification quality, and scope drift.
Do not approve from prose alone; cite concrete files, diff evidence, tests, or missing evidence.

Plan and verification context:
{chr(10).join(plan_text) or '[missing plan artifacts]'}

Target material:
```text
{material[:30000]}
```

Extra redacted context:
```text
{read_path(args.source) or '[none]'}
```

Questions:
{qs}

Return only JSON matching the supplied schema.
"""


def parse_payload(text: str) -> dict:
    text = text.strip()
    try:
        value = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise SystemExit("review response did not contain JSON")
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise SystemExit("review response must be a JSON object")
    return value


def normalize(payload: dict, prompt: str, response: str, response_path: pathlib.Path, raw_path=None) -> dict:
    findings = []
    for index, item in enumerate(payload.get("findings") or [], 1):
        if not isinstance(item, dict):
            item = {"title": str(item)}
        severity = str(item.get("severity", "NOTE")).upper()
        if severity not in {"MUST_FIX", "SHOULD_FIX", "NOTE"}:
            severity = "NOTE"
        finding_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(item.get("id") or f"C{index}"))
        findings.append({
            "id": finding_id or f"C{index}",
            "severity": severity,
            "title": str(item.get("title") or "(untitled)"),
            "evidence": str(item.get("evidence") or ""),
            "recommendation": str(item.get("recommendation") or ""),
        })
    adoption = [{"id": f["id"], "severity": f["severity"], "status": "open", "note": ""} for f in findings]
    verdict = str(payload.get("verdict") or "SHIP").upper()
    if verdict not in {"MUST_FIX", "SHOULD_FIX", "SHIP"}:
        verdict = "MUST_FIX" if any(f["severity"] == "MUST_FIX" for f in findings) else "SHOULD_FIX"
    kind, material = target()
    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "status": "completed",
        "mode": args.mode,
        "model": model,
        "reasoning_effort": effort,
        "sandbox": "read-only",
        "target_kind": kind,
        "target_hash": sha(material),
        "prompt_hash": sha(prompt),
        "response_hash": sha(response),
        "response_path": rel(response_path),
        "raw_response_path": rel(raw_path) if raw_path else None,
        "verdict": verdict,
        "confidence": str(payload.get("confidence") or ""),
        "summary": str(payload.get("summary") or ""),
        "findings": findings,
        "adoption_status": adoption,
        "open_must_fix_count": sum(1 for item in adoption if item["severity"] == "MUST_FIX"),
        "not_source_of_truth": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def write_summary(summary: dict) -> None:
    review_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    latest_path.write_text(text, encoding="utf-8")
    (review_dir / f"review-{args.mode}-summary.json").write_text(text, encoding="utf-8")
    print(f"wrote {rel(review_dir / f'review-{args.mode}-summary.json')}")


def run_review() -> None:
    if not shutil.which("codex"):
        raise SystemExit("codex CLI not found")
    prompt = make_prompt()
    review_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = root / ".pipeline" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    schema_path = tmp_dir / f"{task_id}-codex-review-schema.json"
    output_path = tmp_dir / f"{task_id}-codex-review-output.json"
    schema_path.write_text(json.dumps(RESPONSE_SCHEMA), encoding="utf-8")
    command = [
        "codex", "exec", "--json", "--ephemeral", "--model", model,
        "--config", f'model_reasoning_effort="{effort}"',
        "--sandbox", "read-only", "--output-schema", str(schema_path),
        "--output-last-message", str(output_path), prompt,
    ]
    try:
        proc = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=args.timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"Codex review timed out after {args.timeout_seconds}s") from exc
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = review_dir / f"codex-{args.mode}-{stamp}.jsonl"
    raw_path.write_text(proc.stdout, encoding="utf-8")
    if proc.returncode != 0:
        raise SystemExit(f"Codex review failed with exit code {proc.returncode}: {proc.stderr[-1000:]}")
    response = output_path.read_text(encoding="utf-8")
    response_path = review_dir / f"codex-{args.mode}.md"
    response_path.write_text(response.rstrip() + "\n", encoding="utf-8")
    write_summary(normalize(parse_payload(response), prompt, response, response_path, raw_path))


def record_review() -> None:
    if not args.response_file:
        raise SystemExit("record requires --response-file")
    source = pathlib.Path(args.response_file)
    if not source.is_absolute():
        source = root / source
    response = source.read_text(encoding="utf-8")
    prompt = make_prompt()
    review_dir.mkdir(parents=True, exist_ok=True)
    response_path = review_dir / f"codex-{args.mode}.md"
    response_path.write_text(response.rstrip() + "\n", encoding="utf-8")
    write_summary(normalize(parse_payload(response), prompt, response, response_path))


def classify() -> None:
    if not args.finding or not args.status:
        raise SystemExit("classify requires --finding and --status")
    path = review_dir / f"review-{args.mode}-summary.json"
    summary = load_json(path)
    if not isinstance(summary, dict):
        raise SystemExit(f"missing {rel(path)}")
    found = False
    for item in summary.get("adoption_status") or []:
        if isinstance(item, dict) and item.get("id") == args.finding:
            item.update({"status": args.status, "note": args.note, "updated_at": datetime.now(timezone.utc).isoformat()})
            found = True
    if not found:
        raise SystemExit(f"finding not found: {args.finding}")
    summary["open_must_fix_count"] = sum(
        1 for item in summary.get("adoption_status") or []
        if item.get("severity") == "MUST_FIX" and item.get("status") in {"open", "deferred"}
    )
    write_summary(summary)


def record_failure() -> None:
    if not args.signature:
        raise SystemExit("failure requires --signature <stable-id>")
    review_dir.mkdir(parents=True, exist_ok=True)
    signature_hash = sha(args.signature)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "signature": args.signature,
        "signature_hash": signature_hash,
        "command_id": args.command_id,
        "note": args.note,
    }
    with failures_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    count = 0
    for line in failures_path.read_text(encoding="utf-8").splitlines():
        try:
            if json.loads(line).get("signature_hash") == signature_hash:
                count += 1
        except Exception:
            pass
    _, diff_material = target()
    trigger = {
        "schema_version": "1.0",
        "task_id": task_id,
        "trigger": "same_failure_threshold",
        "signature_hash": signature_hash,
        "failure_count": count,
        "threshold": failure_threshold,
        "codex_ultra_review_required": count >= failure_threshold,
        "target_hash": sha(diff_material),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    trigger_path.write_text(json.dumps(trigger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(trigger, ensure_ascii=False, indent=2))


if cmd == "run":
    run_review()
elif cmd == "record":
    record_review()
elif cmd == "classify":
    classify()
elif cmd == "failure":
    record_failure()
PY
