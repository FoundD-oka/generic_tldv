#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/outcome-judge.sh <task-id> [outcome-card-path]

Default outcome card path:
  .pipeline/outcomes/<task-id>/outcome-card.json

Writes:
  .pipeline/gates/<task-id>/outcome.json
USAGE
}

task_id="${1:-}"
if [[ -z "$task_id" ]]; then usage; exit 2; fi
card_path="${2:-.pipeline/outcomes/$task_id/outcome-card.json}"
if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then cd "$git_root"; fi

python3 - "$task_id" "$card_path" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

expected_task_id = sys.argv[1]
root = pathlib.Path.cwd().resolve()
card_path = pathlib.Path(sys.argv[2])
if not card_path.is_absolute():
    card_path = root / card_path
decision_path = root / ".pipeline" / "plans" / expected_task_id / "sml-decision.json"
gate_path = root / ".pipeline" / "gates" / expected_task_id / "outcome.json"
failures = []

def load_json(path, label):
    if not path.is_file():
        failures.append(f"missing {label}: {path.relative_to(root) if path.is_relative_to(root) else path}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        failures.append(f"{label} is not valid JSON: {exc}")
        return {}
    if not isinstance(value, dict):
        failures.append(f"{label} must be a JSON object")
        return {}
    return value

card = load_json(card_path, "outcome card")
decision = load_json(decision_path, "S/M/L decision")

if card.get("task_id") != expected_task_id:
    failures.append(f"task_id mismatch: expected {expected_task_id}, got {card.get('task_id')}")

size = str(card.get("size", "")).upper()
expected_size = str(decision.get("size", "")).upper()
if size not in {"S", "M", "L"}:
    failures.append("size must be S, M, or L")
if expected_size not in {"S", "M", "L"}:
    failures.append("S/M/L decision size must be S, M, or L")
elif size != expected_size:
    failures.append(f"size mismatch: decision={expected_size}, outcome={size or 'missing'}")

result = card.get("result") if isinstance(card.get("result"), dict) else {}
for key in ("at_pass", "fp_pass", "nft_pass", "hd_resolved"):
    if result.get(key) is not True:
        failures.append(f"result.{key} is not true")
blocking = result.get("blocking_findings")
if not isinstance(blocking, int) or isinstance(blocking, bool) or blocking != 0:
    failures.append("result.blocking_findings must be 0")

evidence = card.get("evidence") if isinstance(card.get("evidence"), dict) else {}
verification = evidence.get("verification")
if not isinstance(verification, list) or not verification or not all(
    isinstance(item, str) and item.strip() for item in verification
):
    failures.append("evidence.verification must contain at least one non-empty command or artifact")

if size in {"M", "L"}:
    canonical_ledger = (root / ".pipeline" / "sessions" / expected_task_id / "events.jsonl").resolve()
    ledger_value = evidence.get("session_ledger")
    if not isinstance(ledger_value, str) or not ledger_value:
        failures.append("M/L tasks require evidence.session_ledger")
    else:
        ledger_path = pathlib.Path(ledger_value)
        if not ledger_path.is_absolute():
            ledger_path = root / ledger_path
        ledger_path = ledger_path.resolve()
        if ledger_path != canonical_ledger:
            failures.append(
                "session ledger must be task-specific: "
                f"expected {canonical_ledger.relative_to(root)}, got {ledger_value}"
            )
        elif not ledger_path.is_file():
            failures.append(f"session ledger does not exist: {ledger_value}")
        else:
            events = []
            try:
                for line_number, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), 1):
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("event must be an object")
                    if event.get("task_id") not in {None, expected_task_id}:
                        failures.append(
                            f"session event task_id mismatch at line {line_number}: {event.get('task_id')}"
                        )
                    if str(event.get("schema_version", "")).startswith("2"):
                        for key in ("type", "task_id", "runtime_profile", "timestamp", "sequence", "run_id", "status"):
                            if event.get(key) in {None, ""}:
                                failures.append(f"v2 session event missing {key} at line {line_number}")
                    events.append(event)
            except Exception as exc:
                failures.append(f"session ledger is not valid JSONL: {ledger_value}: {exc}")
                events = []

            event_types = [event.get("type") for event in events]
            if not events:
                failures.append(f"session ledger has no events: {ledger_value}")
            if "thread.started" not in event_types:
                failures.append("session ledger missing thread.started event")
            terminal = [event for event in events if event.get("type") in {"turn.completed", "turn.failed", "error"}]
            if not terminal:
                failures.append("session ledger has no terminal turn event")
            else:
                latest = terminal[-1]
                success_statuses = {None, "", "succeeded", "verified", "pass", "completed"}
                if latest.get("type") != "turn.completed" or latest.get("status") not in success_statuses:
                    failures.append(
                        "latest session turn is not successful: "
                        f"type={latest.get('type')} status={latest.get('status')}"
                    )

if size == "L":
    candidates = [
        ("sidechain_synthesis", evidence.get("sidechain_synthesis")),
        ("tribunal_report", evidence.get("tribunal_report")),
    ]
    present = [(label, value) for label, value in candidates if isinstance(value, str) and value]
    if not present:
        failures.append("L tasks require evidence.sidechain_synthesis or evidence.tribunal_report")
    else:
        evidence_root = (root / ".pipeline" / "evidence" / expected_task_id).resolve()
        valid_artifact = False
        artifact_errors = []
        for label, value in present:
            artifact = pathlib.Path(value)
            if not artifact.is_absolute():
                artifact = root / artifact
            artifact = artifact.resolve()
            try:
                artifact.relative_to(evidence_root)
            except ValueError:
                artifact_errors.append(f"{label} must be under .pipeline/evidence/{expected_task_id}: {value}")
                continue
            if not artifact.is_file():
                artifact_errors.append(f"{label} does not exist: {value}")
                continue
            valid_artifact = True
        if not valid_artifact:
            failures.extend(artifact_errors)

status = "pass" if not failures else "block"
payload = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "task_id": expected_task_id,
    "size": size or None,
    "status": status,
    "card_path": str(card_path.relative_to(root)) if card_path.is_relative_to(root) else str(card_path),
    "failures": failures,
}
gate_path.parent.mkdir(parents=True, exist_ok=True)
gate_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
if failures:
    for failure in failures:
        print(f"[FAIL] {failure}", file=sys.stderr)
else:
    print(f"outcome accepted for {expected_task_id}")
raise SystemExit(0 if not failures else 1)
PY
