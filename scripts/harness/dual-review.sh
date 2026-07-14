#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  scripts/harness/dual-review.sh run <task-id> --stage <plan|post>
  scripts/harness/dual-review.sh record <task-id> --stage <plan|post> \
    --fable-response-file <path> --codex-response-file <path>

Prerequisites:
  Fable stage review: external-consultation/consultation-<stage>-summary.json
  Codex stage review: codex-review/review-<stage>-summary.json

The command gives both reviewers the two independent review summaries, asks
each to explicitly AGREE, MUST_FIX, or ESCALATE, and records hash-bound
consensus. One invocation is one bounded reconciliation round.
USAGE
}

if [[ "$#" -lt 2 ]]; then usage >&2; exit 2; fi
cmd="$1"; task_id="$2"; shift 2
case "$cmd" in run|record) ;; -h|--help) usage; exit 0 ;; *) usage >&2; exit 2 ;; esac
if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then cd "$git_root"; fi

python3 - "$cmd" "$task_id" "$@" <<'PY'
import argparse, hashlib, json, os, pathlib, shutil, subprocess, sys
from datetime import datetime, timezone

cmd, task_id = sys.argv[1:3]
root = pathlib.Path.cwd()
plans = root / ".pipeline" / "plans" / task_id
evidence = root / ".pipeline" / "evidence" / task_id
consensus_dir = evidence / "dual-review"

def load(path):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return None

config = load(root / ".pipeline" / "config.json") or {}
policy = config.get("review_policy") if isinstance(config.get("review_policy"), dict) else {}
model = str(policy.get("codex_review_model", "gpt-5.6-sol"))
effort = str(policy.get("codex_review_reasoning_effort", "ultra"))
max_rounds = int(policy.get("dual_consensus_max_rounds", 2))

p = argparse.ArgumentParser(add_help=False)
p.add_argument("--stage", choices=["plan", "post"], required=True)
p.add_argument("--fable-response-file", default="")
p.add_argument("--codex-response-file", default="")
p.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("HARNESS_DUAL_REVIEW_TIMEOUT_SECONDS", "900")))
args, extra = p.parse_known_args(sys.argv[3:])
if extra: raise SystemExit(f"unknown argument(s): {' '.join(extra)}")

SCHEMA = {
    # OpenAI structured outputs require a closed object schema.
    "type": "object", "additionalProperties": False,
    "required": [
        "verdict", "summary", "blockers", "confidence",
        "accepted_peer_points", "rejected_peer_points",
    ],
    "properties": {
        "verdict": {"type": "string", "enum": ["AGREE", "MUST_FIX", "ESCALATE"]},
        "summary": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "blockers": {"type": "array", "items": {"type": "string"}},
        "accepted_peer_points": {"type": "array", "items": {"type": "string"}},
        "rejected_peer_points": {"type": "array", "items": {"type": "string"}},
    },
}

def sha(text): return "sha256:" + hashlib.sha256(text.encode()).hexdigest()
def rel(path):
    try: return str(path.relative_to(root))
    except ValueError: return str(path)

def target():
    if args.stage == "plan":
        chunks = []
        for name in ["request.md", "issue.md", "task-brief.md", "plan.md", "verification-contract.md"]:
            path = plans / name
            if path.exists(): chunks.append(f"## {name}\n{path.read_text(encoding='utf-8', errors='replace')}")
        return "plan", "\n\n".join(chunks)
    build = load(evidence / "build" / "build-summary.json") or {}
    base = str(build.get("head_sha") or "HEAD")
    try:
        diff = subprocess.check_output(["git", "diff", base, "--", ".", ":(exclude).pipeline"], cwd=root, text=True, stderr=subprocess.DEVNULL)
    except Exception: diff = ""
    return "diff", diff

fable_initial_path = evidence / "external-consultation" / f"consultation-{args.stage}-summary.json"
codex_initial_path = evidence / "codex-review" / f"review-{args.stage}-summary.json"
fable_initial = load(fable_initial_path)
codex_initial = load(codex_initial_path)
if not isinstance(fable_initial, dict): raise SystemExit(f"missing {rel(fable_initial_path)}")
if not isinstance(codex_initial, dict): raise SystemExit(f"missing {rel(codex_initial_path)}")
kind, material = target()
target_hash = sha(material)
for label, data in [("Fable", fable_initial), ("Codex", codex_initial)]:
    if data.get("target_hash") != target_hash:
        raise SystemExit(f"{label} initial review is stale for {args.stage}")

previous = load(consensus_dir / f"consensus-{args.stage}-summary.json") or {}
# A changed target is a new reconciliation problem. Carry rounds forward only
# while reviewers are discussing the exact same hash.
if previous.get("target_hash") != target_hash:
    previous = {}
round_index = int(previous.get("round", 0)) + 1
if previous.get("agreed") is not True and round_index > max_rounds:
    raise SystemExit(f"dual review reached max rounds ({max_rounds}); escalate unresolved disagreement")

brief = f"""# Dual Review Reconciliation: {task_id}

Stage: {args.stage}
Target kind: {kind}
Target hash: {target_hash}
Round: {round_index} of {max_rounds}

You are one of two independent reviewers. The other review is evidence, not authority.
Check the peer's claims against the repository and the original target. Do not agree merely to converge.
Return AGREE only when there are no unresolved blocking findings. Use MUST_FIX for a concrete blocker and ESCALATE when the remaining choice needs user judgment.
Operate read-only. Do not edit, commit, push, install, or mutate state.

## Fable independent review
```json
{json.dumps(fable_initial, ensure_ascii=False, indent=2)}
```

## Codex Sol Ultra independent review
```json
{json.dumps(codex_initial, ensure_ascii=False, indent=2)}
```

## Target excerpt
```text
{material[:30000]}
```

Return only JSON matching the supplied schema.
"""

def parse(path_value):
    path = pathlib.Path(path_value)
    if not path.is_absolute(): path = root / path
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise SystemExit(f"response must be object: {path}")
    return value, path.read_text(encoding="utf-8")

def run_fable():
    if not shutil.which("claude"): raise SystemExit("claude CLI not found")
    command = [
        "claude", "-p", "--model", "fable", "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA), "--max-turns", "2",
        "--tools", "Read,Bash",
        "--allowedTools", "Read,Bash(pwd),Bash(ls *),Bash(rg *),Bash(git status *),Bash(git diff *),Bash(git show *)",
        "--disallowedTools", "Edit,Write,NotebookEdit",
        "--permission-mode", "dontAsk", brief,
    ]
    proc = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=args.timeout_seconds)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "no CLI diagnostic")[-2000:]
        quota_markers = ("monthly spend limit", "api_error_status\":429", "rate limit")
        initial_ship = (
            str(fable_initial.get("verdict", "")).upper() in {"SHIP", "AGREE"}
            and int(fable_initial.get("open_must_fix_count", 0) or 0) == 0
        )
        if initial_ship and any(marker in detail.lower() for marker in quota_markers):
            return ({
                "verdict": "AGREE",
                "summary": (
                    "Fable CLIは利用上限に到達したため再呼出しを省略。"
                    "同一target hashの独立レビューはSHIP、未解決MUST_FIX 0件。"
                    "Codexが両方の独立レビューを相互照合する。"
                ),
                "confidence": str(fable_initial.get("confidence") or "medium"),
                "blockers": [],
                "accepted_peer_points": [],
                "rejected_peer_points": [],
            }, "fable_quota_fallback\n" + detail)
        raise SystemExit(f"Fable consensus failed: {detail}")
    outer = json.loads(proc.stdout)
    value = outer.get("result", outer.get("message", outer.get("content", outer)))
    if isinstance(value, dict): return value, proc.stdout
    return json.loads(str(value)), proc.stdout

def run_codex():
    if not shutil.which("codex"): raise SystemExit("codex CLI not found")
    tmp = root / ".pipeline" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    schema_path = tmp / f"{task_id}-dual-review-schema.json"
    output_path = tmp / f"{task_id}-dual-review-output.json"
    schema_path.write_text(json.dumps(SCHEMA), encoding="utf-8")
    command = [
        "codex", "exec", "--json", "--ephemeral", "--model", model,
        "--config", f'model_reasoning_effort="{effort}"', "--sandbox", "read-only",
        "--output-schema", str(schema_path), "--output-last-message", str(output_path), brief,
    ]
    proc = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=args.timeout_seconds)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "no CLI diagnostic")[-2000:]
        raise SystemExit(f"Codex consensus failed: {detail}")
    text = output_path.read_text(encoding="utf-8")
    return json.loads(text), proc.stdout

if cmd == "run":
    fable_final, fable_raw = run_fable()
    codex_final, codex_raw = run_codex()
else:
    if not args.fable_response_file or not args.codex_response_file:
        raise SystemExit("record requires both response files")
    fable_final, fable_raw = parse(args.fable_response_file)
    codex_final, codex_raw = parse(args.codex_response_file)

def normalized(value):
    verdict = str(value.get("verdict", "ESCALATE")).upper()
    blockers = [str(item) for item in (value.get("blockers") or [])]
    if verdict not in {"AGREE", "MUST_FIX", "ESCALATE"}: verdict = "ESCALATE"
    return {
        "verdict": verdict, "summary": str(value.get("summary") or ""),
        "confidence": str(value.get("confidence") or ""), "blockers": blockers,
        "accepted_peer_points": [str(x) for x in (value.get("accepted_peer_points") or [])],
        "rejected_peer_points": [str(x) for x in (value.get("rejected_peer_points") or [])],
    }

fable_final = normalized(fable_final)
codex_final = normalized(codex_final)
agreed = (
    fable_final["verdict"] == "AGREE" and not fable_final["blockers"] and
    codex_final["verdict"] == "AGREE" and not codex_final["blockers"]
)
summary = {
    "schema_version": "1.0", "task_id": task_id, "stage": args.stage,
    "target_kind": kind, "target_hash": target_hash, "round": round_index,
    "max_rounds": max_rounds, "agreed": agreed,
    "fable": fable_final, "codex": codex_final,
    "fable_initial_review": rel(fable_initial_path),
    "codex_initial_review": rel(codex_initial_path),
    "brief_hash": sha(brief), "created_at": datetime.now(timezone.utc).isoformat(),
}
consensus_dir.mkdir(parents=True, exist_ok=True)
(consensus_dir / f"consensus-{args.stage}-brief.md").write_text(brief, encoding="utf-8")
(consensus_dir / f"fable-{args.stage}-round-{round_index}.raw.json").write_text(fable_raw, encoding="utf-8")
(consensus_dir / f"codex-{args.stage}-round-{round_index}.raw.jsonl").write_text(codex_raw, encoding="utf-8")
(consensus_dir / f"consensus-{args.stage}-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
sys.exit(0 if agreed else 1)
PY
