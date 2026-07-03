#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  scripts/harness/worktree.sh create <task-id> [--base <ref>] [--path <path>] [--branch <name>] [--force]
  scripts/harness/worktree.sh status <task-id>
  scripts/harness/worktree.sh remove <task-id> [--force]

Writes:
  .pipeline/worktrees/<task-id>/worktree.json
USAGE
}

cmd="${1:-}"
task_id="${2:-}"
if [[ -z "$cmd" || -z "$task_id" ]]; then
  usage
  exit 2
fi
shift 2

base_ref="HEAD"
branch_name=""
path_arg=""
force=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --base)
      base_ref="${2:-}"
      shift 2
      ;;
    --path)
      path_arg="${2:-}"
      shift 2
      ;;
    --branch)
      branch_name="${2:-}"
      shift 2
      ;;
    --force)
      force=1
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
else
  echo "not inside a git repository" >&2
  exit 1
fi

python3 - "$cmd" "$task_id" "$base_ref" "$path_arg" "$branch_name" "$force" <<'PY'
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

cmd, task_id, base_ref, path_arg, branch_name, force_raw = sys.argv[1:7]
force = force_raw == "1"
root = pathlib.Path.cwd()
meta_dir = root / ".pipeline" / "worktrees" / task_id
meta_path = meta_dir / "worktree.json"
default_path = root / ".pipeline" / "worktrees" / task_id / "checkout"
worktree_path = pathlib.Path(path_arg).expanduser() if path_arg else default_path
if not worktree_path.is_absolute():
    worktree_path = (root / worktree_path).resolve()
branch_name = branch_name or f"harness/{task_id}"


def run(args, cwd=root, check=True):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip() or f"git {' '.join(args)} failed"
        raise SystemExit(message)
    return proc


def rel(path):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def write_meta(status, extra=None):
    meta_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "task_id": task_id,
        "status": status,
        "branch": branch_name,
        "base_ref": base_ref,
        "path": str(worktree_path),
        "relative_path": rel(worktree_path),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(extra)
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if cmd == "create":
    if worktree_path.exists() and force:
        run(["worktree", "remove", "--force", str(worktree_path)], check=False)
    if worktree_path.exists():
        raise SystemExit(f"worktree path already exists: {worktree_path}")
    run(["rev-parse", "--verify", base_ref])
    existing = run(["rev-parse", "--verify", branch_name], check=False)
    args = ["worktree", "add"]
    if force:
        args.append("--force")
    if existing.returncode == 0:
        args.extend([str(worktree_path), branch_name])
    else:
        args.extend(["-b", branch_name, str(worktree_path), base_ref])
    run(args)
    plan_src = root / ".pipeline" / "plans" / task_id
    plan_dst = worktree_path / ".pipeline" / "plans" / task_id
    if plan_src.exists() and not plan_dst.exists():
        import shutil

        plan_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(plan_src, plan_dst)
    head = run(["rev-parse", "HEAD"], cwd=worktree_path).stdout.strip()
    payload = write_meta("created", {"head_sha": head})
    print(f"created {payload['relative_path']} on {branch_name}")
elif cmd == "status":
    if not meta_path.exists():
        raise SystemExit(f"missing worktree metadata: {rel(meta_path)}")
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    path = pathlib.Path(payload.get("path", ""))
    exists = path.exists()
    head = ""
    dirty = None
    if exists:
        head = run(["rev-parse", "HEAD"], cwd=path).stdout.strip()
        dirty = bool(run(["status", "--short"], cwd=path).stdout.strip())
    payload.update({"exists": exists, "head_sha": head, "dirty": dirty})
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
elif cmd == "remove":
    if not meta_path.exists():
        raise SystemExit(f"missing worktree metadata: {rel(meta_path)}")
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    path = pathlib.Path(payload.get("path", ""))
    if path.exists():
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(path))
        run(args)
    write_meta("removed")
    print(f"removed {rel(path)}")
else:
    raise SystemExit(f"unknown command: {cmd}")
PY
