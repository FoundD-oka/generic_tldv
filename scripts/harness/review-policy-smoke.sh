#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/review-policy-smoke.sh

Creates a disposable repository and verifies:
- S requires Fable plan review but not routine Codex Ultra post review
- the same failure twice promotes S to Codex Ultra review
- M requires Codex Ultra post review
- L requires Fable/Codex plan and post reviews plus dual consensus
No model calls are made; structured synthetic reviewer responses exercise the gates.
USAGE
}
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then usage; exit 0; fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/../.." && pwd)"
skill_root="/Users/bonginkan-3-gouki/project/claude-dotfiles/skills/harness-init"
if [[ "$skill_root" == "/Users/bonginkan-3-gouki/project/claude-dotfiles/skills/harness-init" ]]; then
  skill_root="$(cd "$script_dir/../../.." && pwd)"
fi
install_script="$skill_root/scripts/install_harness.sh"
fixture="$(mktemp -d /tmp/harness-review-policy-smoke.XXXXXX)"
cd "$fixture"
git init -q
git config user.email "review-policy@example.com"
git config user.name "Review Policy Smoke"
mkdir -p src
printf 'before\n' > src/value.txt
git add src/value.txt
git commit -qm "seed"

if [[ -x "$install_script" ]]; then
  HARNESS_SKIP_GITNEXUS=1 bash "$install_script" "ReviewPolicySmoke" >/dev/null
else
  cp -R "$project_root/.ai" "$project_root/.pipeline" "$project_root/.ci" "$project_root/.codex" "$project_root/docs" "$project_root/schemas" "$project_root/scripts" .
  cp "$project_root/CLAUDE.md" "$project_root/AGENTS.md" .
  cp -R "$project_root/.claude" .
fi
chmod +x scripts/harness/*.sh .claude/hooks/*.sh 2>/dev/null || true
git add .
git commit -qm "install harness"

mkdir -p .pipeline/tmp
printf '%s\n' '{"verdict":"SHIP","summary":"reviewed","confidence":"high","findings":[]}' > .pipeline/tmp/ship.json
printf '%s\n' '{"verdict":"AGREE","summary":"agreed after peer review","confidence":"high","blockers":[],"accepted_peer_points":[],"rejected_peer_points":[]}' > .pipeline/tmp/agree.json

make_plan() {
  local task="$1" size="$2"
  mkdir -p ".pipeline/plans/$task"
  printf '# Plan\n\n- intent: preserve requested behavior\n- approach: targeted change\n' > ".pipeline/plans/$task/plan.md"
  scripts/harness/sml-decision.sh "$task" --size "$size" --write-verification-contract >/dev/null
}
fable() { scripts/harness/external-consultation.sh record "$1" --mode "$2" --response-file .pipeline/tmp/ship.json >/dev/null; }
codex_review() { scripts/harness/codex-review.sh record "$1" --mode "$2" --response-file .pipeline/tmp/ship.json >/dev/null; }

# S: plan review is required, routine post review is not.
make_plan review-s S
if .claude/hooks/external-consultation-validate.sh review-s >/dev/null 2>&1; then
  echo "expected S to block before Fable plan review" >&2; exit 1
fi
if .claude/hooks/pre-implementation-review-gate.sh review-s >/dev/null 2>&1; then
  echo "expected implementation start to block before Fable plan review" >&2; exit 1
fi
fable review-s plan
.claude/hooks/pre-implementation-review-gate.sh review-s >/dev/null
.claude/hooks/external-consultation-validate.sh review-s >/dev/null
.claude/hooks/codex-review-validate.sh review-s >/dev/null
.claude/hooks/dual-review-validate.sh review-s >/dev/null

# A hash-bound max-call fallback is accepted; arbitrary skipped evidence is not.
make_plan review-fallback S
scripts/harness/external-consultation.sh run review-fallback --mode plan --max-calls 0 >/dev/null
.claude/hooks/pre-implementation-review-gate.sh review-fallback >/dev/null
.claude/hooks/external-consultation-validate.sh review-fallback >/dev/null

# One repeated signature is not enough; the second occurrence requires Ultra.
scripts/harness/codex-review.sh failure review-s --signature test-alpha >/dev/null
.claude/hooks/codex-review-validate.sh review-s >/dev/null
scripts/harness/codex-review.sh failure review-s --signature test-alpha >/dev/null
if .claude/hooks/codex-review-validate.sh review-s >/dev/null 2>&1; then
  echo "expected S to block after the same failure twice" >&2; exit 1
fi
codex_review review-s stuck
.claude/hooks/codex-review-validate.sh review-s >/dev/null

# M: post review is mandatory.
make_plan review-m M
fable review-m plan
if .claude/hooks/codex-review-validate.sh review-m >/dev/null 2>&1; then
  echo "expected M to block before Codex Ultra post review" >&2; exit 1
fi
base_sha="$(git rev-parse HEAD)"
mkdir -p .pipeline/evidence/review-m/build
printf '{"head_sha":"%s"}\n' "$base_sha" > .pipeline/evidence/review-m/build/build-summary.json
printf 'after\n' > src/value.txt
git add src/value.txt
git commit -qm "implement M fixture"
codex_review review-m post
python3 - <<'PY'
import hashlib, json
data = json.load(open('.pipeline/evidence/review-m/codex-review/review-post-summary.json'))
empty_hash = 'sha256:' + hashlib.sha256(b'').hexdigest()
assert data['target_hash'] != empty_hash, 'post review must include committed implementation diff'
PY
.claude/hooks/external-consultation-validate.sh review-m >/dev/null
.claude/hooks/codex-review-validate.sh review-m >/dev/null

# L: independent reviews and explicit two-model agreement are required at both stages.
make_plan review-l L
fable review-l plan
codex_review review-l plan
fable review-l post
codex_review review-l post
if .claude/hooks/dual-review-validate.sh review-l >/dev/null 2>&1; then
  echo "expected L to block before dual consensus" >&2; exit 1
fi
scripts/harness/dual-review.sh record review-l --stage plan \
  --fable-response-file .pipeline/tmp/agree.json --codex-response-file .pipeline/tmp/agree.json >/dev/null
.claude/hooks/pre-implementation-review-gate.sh review-l >/dev/null
scripts/harness/dual-review.sh record review-l --stage post \
  --fable-response-file .pipeline/tmp/agree.json --codex-response-file .pipeline/tmp/agree.json >/dev/null
.claude/hooks/external-consultation-validate.sh review-l >/dev/null
.claude/hooks/codex-review-validate.sh review-l >/dev/null
.claude/hooks/dual-review-validate.sh review-l >/dev/null

echo "review policy smoke passed"
echo "fixture: $fixture"
