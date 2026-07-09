#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/sml-decision.sh <task-id> --size S|M|L [--reason <text>] [--external-consultation optional|required|required_for_l] [--write-verification-contract]

Writes:
  .pipeline/plans/<task-id>/sml-decision.json
  .pipeline/plans/<task-id>/verification-contract.md when requested or missing
USAGE
}

task_id="${1:-}"
if [[ -z "$task_id" ]]; then
  usage
  exit 2
fi
shift

size=""
reason=""
external_policy="required_for_l"
write_contract=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --size)
      size="${2:-}"
      shift 2
      ;;
    --reason)
      reason="${2:-}"
      shift 2
      ;;
    --external-consultation)
      external_policy="${2:-}"
      shift 2
      ;;
    --write-verification-contract)
      write_contract=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  cd "$git_root"
fi

python3 - "$task_id" "$size" "$reason" "$external_policy" "$write_contract" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

task_id, size, reason, external_policy, write_contract_raw = sys.argv[1:6]
size = size.upper()
write_contract = write_contract_raw == "1"
if size not in {"S", "M", "L"}:
    print("--size must be S, M, or L", file=sys.stderr)
    raise SystemExit(2)
if external_policy not in {"optional", "required", "required_for_l"}:
    print("--external-consultation must be optional, required, or required_for_l", file=sys.stderr)
    raise SystemExit(2)

root = pathlib.Path.cwd()
config_path = root / ".pipeline" / "config.json"
provider = "claude-fable-cli"
try:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    quality = config.get("quality") if isinstance(config.get("quality"), dict) else {}
    provider = str(quality.get("external_consultation_provider", provider))
except Exception:
    pass
plans = root / ".pipeline" / "plans" / task_id
checkpoint_path = plans / "checkpoint-contract.json"
decision_path = plans / "sml-decision.json"
contract_path = plans / "verification-contract.md"
plans.mkdir(parents=True, exist_ok=True)

checkpoint = {}
if checkpoint_path.exists():
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))

required_gates = {
    "S": ["residency", "preflight", "hd-gate", "doc-staleness", "adapter-contract", "backcast-contracts"],
    "M": ["residency", "preflight", "hd-gate", "doc-staleness", "adapter-contract", "backcast-contracts", "evidence-pack", "qa-judgment"],
    "L": [
        "residency",
        "preflight",
        "hd-gate",
        "doc-staleness",
        "adapter-contract",
        "backcast-contracts",
        "evidence-pack",
        "qa-judgment",
        "tribunal-or-sidechain",
        "external-consultation",
        "approval-hash",
    ],
}[size]

payload = {
    "schema_version": "1.0",
    "task_id": task_id,
    "checkpoint_id": checkpoint.get("checkpoint_id", ""),
    "size": size,
    "decided_at": datetime.now(timezone.utc).isoformat(),
    "basis": {
        "residual_uncertainty": reason or "manual decision",
        "not_based_on": "issue title or diff size alone",
    },
    "quality_gates": {
        "required": required_gates,
        "external_consultation": external_policy,
    },
}
decision_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if write_contract or not contract_path.exists():
    commands = checkpoint.get("verification_commands") if isinstance(checkpoint.get("verification_commands"), list) else []
    lines = [
        f"# Verification Contract: {task_id}",
        "",
        f"- size: {size}",
        f"- external consultation required: {'yes' if external_policy == 'required' or (external_policy == 'required_for_l' and size == 'L') else 'no'}",
        f"- external consultation provider: {provider if external_policy != 'optional' else 'not needed'}",
        "",
        "## Required Commands",
    ]
    if commands:
        for command in commands:
            lines.append(f"- `{command.get('id', '')}`: `{command.get('command', '')}`")
    else:
        lines.append("- none recorded")
    lines.extend(["", "## Evidence Rule", "- Evidence Manifest must have no missing_evidence entries."])
    contract_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(f"wrote .pipeline/plans/{task_id}/sml-decision.json")
if write_contract or contract_path.exists():
    print(f"wrote .pipeline/plans/{task_id}/verification-contract.md")
PY
