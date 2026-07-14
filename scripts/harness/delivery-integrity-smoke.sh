#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/../.." && pwd)"
skill_root="/Users/bonginkan-3-gouki/project/claude-dotfiles/skills/harness-init"
install_script="${HARNESS_INIT_INSTALLER:-$skill_root/scripts/install_harness.sh}"
if [[ ! -x "$install_script" ]]; then
  echo "harness-init installer が見つかりません: $install_script" >&2
  exit 1
fi

fixture="$(mktemp -d /tmp/harness-delivery-integrity-smoke.XXXXXX)"
cd "$fixture"
git init -q
git config user.email "delivery-integrity@example.com"
git config user.name "Delivery Integrity Smoke"
mkdir -p src
printf 'before\n' > src/value.txt
git add src/value.txt
git commit -qm "seed"
HARNESS_SKIP_GITNEXUS=1 bash "$install_script" "DeliveryIntegritySmoke" >/dev/null
chmod +x scripts/harness/*.sh .claude/hooks/*.sh 2>/dev/null || true
git add .
git commit -qm "install harness"

task="delivery-integrity-m"
verify_command="python3 -c 'from pathlib import Path; p=Path(\".pipeline/verification-count\"); n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1)); assert \"after\" in Path(\"src/value.txt\").read_text()'"
scripts/harness/backcast-checkpoint.sh "$task" \
  --goal "delivery artifacts stay bound to one implementation" \
  --current "source says before" \
  --target "source says after and deterministic evidence is connected" \
  --condition "qc-delivery::single verification::verification command runs once::verify-once" \
  --command "verify-once::$verify_command" \
  --allowed "src/**" \
  --approval-required >/dev/null
printf '# Plan\n\n- intent: delivery integrity smoke\n- approach: deterministic fixture\n' > ".pipeline/plans/$task/plan.md"
scripts/harness/sml-decision.sh "$task" --size M --write-verification-contract >/dev/null
mkdir -p .pipeline/tmp
printf '%s\n' '{"verdict":"SHIP","summary":"synthetic plan fixture","confidence":"high","findings":[]}' > .pipeline/tmp/fable-plan.json
scripts/harness/external-consultation.sh record "$task" --mode plan --response-file .pipeline/tmp/fable-plan.json >/dev/null
scripts/harness/worktree.sh create "$task" --base HEAD >/dev/null
scripts/harness/worktree.sh bootstrap "$task" >/dev/null

worktree="$(python3 - "$task" <<'PY'
import json, sys
print(json.load(open(f".pipeline/worktrees/{sys.argv[1]}/worktree.json"))["path"])
PY
)"
for name in agents hooks rules skills; do
  if [[ -L ".claude/$name" ]]; then
    test -L "$worktree/.claude/$name"
    test -e "$worktree/.claude/$name"
  fi
done
test -f "$worktree/.pipeline/plans/$task/plan.md"
test -f "$worktree/.pipeline/evidence/$task/external-consultation/consultation-plan-summary.json"

printf 'after\n' > "$worktree/src/value.txt"
scripts/harness/build.sh "$task" --worktree "$worktree" --implementation-complete >/dev/null
test "$(cat "$worktree/.pipeline/verification-count")" = "1"
python3 - "$worktree" "$task" <<'PY'
import json, pathlib, sys
root, task = pathlib.Path(sys.argv[1]), sys.argv[2]
summary = json.load(open(root / ".pipeline" / "evidence" / task / "build" / "build-summary.json"))
assert summary["mode"] == "implementation_complete"
assert summary["command"] == "<implementation-complete>"
PY

(
  cd "$worktree"
  mv ".pipeline/evidence/$task/evidence-pack.md" ".pipeline/evidence/$task/evidence-pack.saved"
  printf 'unrelated\n' > ".pipeline/evidence/$task/random-evidence.txt"
  .claude/hooks/pr-ready-gate.sh "$task" >/dev/null 2>&1 || true
  python3 - "$task" <<'PY'
import json, sys
task = sys.argv[1]
gate = json.load(open(f".pipeline/gates/{task}/pr-ready.json"))
checks = {item["check"]: item["ok"] for item in gate["checks"]}
assert checks.get("evidence-pack") is False
PY
  mv ".pipeline/evidence/$task/evidence-pack.saved" ".pipeline/evidence/$task/evidence-pack.md"
)

(
  cd "$worktree"
  if scripts/harness/codex-session-ledger.sh record "$task" --profile codex-app --status failed --summary "synthetic retry" >/dev/null 2>&1; then
    echo "failed session record が成功扱いになりました" >&2
    exit 1
  fi
  scripts/harness/codex-session-ledger.sh record "$task" --profile codex-app --status succeeded --summary "retry recovered" >/dev/null
  mkdir -p ".pipeline/evidence/$task" ".pipeline/outcomes/$task"
  printf '%s\n' "{\"schema_version\":\"1.0\",\"task_id\":\"$task\",\"verdict\":\"pass\",\"blocking_findings\":[]}" > ".pipeline/evidence/$task/qa-judgment.json"
  python3 - "$task" <<'PY'
import json, pathlib, sys
task = sys.argv[1]
path = pathlib.Path(".pipeline/outcomes") / task / "outcome-card.json"
payload = {
    "schema_version": "1.0",
    "task_id": task,
    "runtime_profile": "codex-app",
    "size": "S",
    "result": {
        "at_pass": True,
        "fp_pass": True,
        "nft_pass": True,
        "hd_resolved": True,
        "blocking_findings": 0,
    },
    "evidence": {
        "session_ledger": f".pipeline/sessions/{task}/events.jsonl",
        "verification": [f".pipeline/evidence/{task}/evidence-manifest.json"],
    },
}
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
  if scripts/harness/outcome-judge.sh "$task" >/dev/null 2>&1; then
    echo "S/M/L decision と不一致のOutcomeが通過しました" >&2
    exit 1
  fi
  python3 - "$task" <<'PY'
import json, pathlib, sys
path = pathlib.Path(".pipeline/outcomes") / sys.argv[1] / "outcome-card.json"
payload = json.loads(path.read_text(encoding="utf-8"))
payload["size"] = "M"
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
  scripts/harness/outcome-judge.sh "$task" >/dev/null
  .claude/hooks/pr-ready-gate.sh "$task" >/dev/null 2>&1 || true
  python3 - "$task" <<'PY'
import json, sys
task = sys.argv[1]
gate = json.load(open(f".pipeline/gates/{task}/pr-ready.json"))
checks = {item["check"]: item["ok"] for item in gate["checks"]}
for name in ("evidence-manifest", "evidence-pack", "qa-judgment", "outcome-judgment"):
    assert checks.get(name) is True, (name, checks.get(name))
PY
  scripts/harness/backcast-approval.sh "$task" approved --approver "delivery-smoke" --role "harness" >/dev/null
  .claude/hooks/approval-hash-check.sh "$task" >/dev/null
  printf 'stale\n' > src/value.txt
  git add src/value.txt
  git commit -qm "make approval stale"
  if .claude/hooks/approval-hash-check.sh "$task" >/dev/null 2>&1; then
    echo "stale approval が通過しました" >&2
    exit 1
  fi
)

make_l_outcome() {
  local l_task="$1" artifact_key="$2" artifact_path="$3"
  (
    cd "$worktree"
    mkdir -p ".pipeline/plans/$l_task" "$(dirname "$artifact_path")" ".pipeline/outcomes/$l_task"
    scripts/harness/sml-decision.sh "$l_task" --size L >/dev/null
    scripts/harness/codex-session-ledger.sh record "$l_task" --profile codex-app --status succeeded >/dev/null
    printf '%s\n' '{}' > "$artifact_path"
    python3 - "$l_task" "$artifact_key" "$artifact_path" <<'PY'
import json, pathlib, sys
task, artifact_key, artifact_path = sys.argv[1:4]
payload = {
    "schema_version": "1.0",
    "task_id": task,
    "runtime_profile": "codex-app",
    "size": "L",
    "result": {
        "at_pass": True, "fp_pass": True, "nft_pass": True,
        "hd_resolved": True, "blocking_findings": 0,
    },
    "evidence": {
        "session_ledger": f".pipeline/sessions/{task}/events.jsonl",
        "verification": ["synthetic deterministic verification"],
        artifact_key: artifact_path,
    },
}
path = pathlib.Path(".pipeline/outcomes") / task / "outcome-card.json"
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
    scripts/harness/outcome-judge.sh "$l_task" >/dev/null
  )
}

make_l_outcome "delivery-integrity-sidechain" "sidechain_synthesis" ".pipeline/evidence/delivery-integrity-sidechain/sidechain/synthesis.json"
make_l_outcome "delivery-integrity-tribunal" "tribunal_report" ".pipeline/evidence/delivery-integrity-tribunal/tribunal-report.json"

echo "delivery integrity smoke passed"
echo "fixture: $fixture"
echo "worktree: $worktree"
