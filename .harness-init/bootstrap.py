#!/usr/bin/env python3
"""Version-neutral emergency switcher copied outside the selected runtime.

This file deliberately imports no harness package. It remains executable when
the runtime symlink points at a version whose Python entrypoint cannot start.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
from typing import Any


LOCK_FILE = "harness-version.lock.json"
PREVIOUS_LOCK_FILE = "harness-version.previous.lock.json"
RUNTIME_LINK = "runtime"
LOCK_KEYS = {
    "schema_version",
    "runtime_version",
    "runtime_manifest_sha256",
    "policy_version",
    "schema_versions",
    "channel",
}


class BootstrapError(RuntimeError):
    pass


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BootstrapError(f"expected JSON object: {path}")
    return payload


def write_json_atomic(path: pathlib.Path, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = pathlib.Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def validate_lock(lock: dict[str, Any], path: pathlib.Path) -> None:
    if set(lock) != LOCK_KEYS or lock.get("schema_version") != "1.0":
        raise BootstrapError(f"lock schema mismatch: {path}")
    if not isinstance(lock.get("runtime_version"), str) or not lock["runtime_version"]:
        raise BootstrapError(f"lock runtime_version missing: {path}")
    digest = lock.get("runtime_manifest_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise BootstrapError(f"lock runtime manifest digest invalid: {path}")
    if not isinstance(lock.get("policy_version"), str) or not lock["policy_version"]:
        raise BootstrapError(f"lock policy version invalid: {path}")
    schemas = lock.get("schema_versions")
    if not isinstance(schemas, dict) or not all(
        isinstance(key, str)
        and key
        and isinstance(value, str)
        and value
        for key, value in schemas.items()
    ):
        raise BootstrapError(f"lock schema versions invalid: {path}")
    if lock.get("channel") not in {"shadow", "alpha", "stable"}:
        raise BootstrapError(f"lock release channel invalid: {path}")


def validate_runtime(runtime: pathlib.Path) -> dict[str, Any]:
    runtime = runtime.resolve()
    manifest_path = runtime / "runtime-manifest.json"
    manifest = read_json(manifest_path)
    required = {
        "schema_version",
        "runtime_version",
        "channel",
        "policy_version",
        "schema_versions",
        "files",
    }
    if set(manifest) != required or manifest.get("schema_version") != "1.0":
        raise BootstrapError("runtime manifest schema mismatch")
    if manifest.get("runtime_version") != runtime.name:
        raise BootstrapError("runtime version/directory mismatch")
    if manifest.get("channel") not in {"shadow", "alpha", "stable"}:
        raise BootstrapError("runtime release channel invalid")
    if not isinstance(manifest.get("policy_version"), str) or not manifest[
        "policy_version"
    ]:
        raise BootstrapError("runtime policy version invalid")
    schemas = manifest.get("schema_versions")
    if not isinstance(schemas, dict) or not all(
        isinstance(key, str)
        and key
        and isinstance(value, str)
        and value
        for key, value in schemas.items()
    ):
        raise BootstrapError("runtime schema versions invalid")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise BootstrapError("runtime manifest has no files")
    actual_files: set[str] = set()
    for candidate in runtime.rglob("*"):
        rel = candidate.relative_to(runtime)
        if candidate.is_symlink():
            raise BootstrapError(f"runtime contains symlink: {rel.as_posix()}")
        if not candidate.is_file() or rel.as_posix() == "runtime-manifest.json":
            continue
        if "__pycache__" in rel.parts or candidate.suffix == ".pyc" or candidate.name == ".DS_Store":
            continue
        actual_files.add(rel.as_posix())
    if actual_files != set(files):
        raise BootstrapError("runtime manifest inventory mismatch")
    for rel, expected in sorted(files.items()):
        if not isinstance(expected, dict):
            raise BootstrapError(f"invalid runtime manifest entry: {rel}")
        candidate = (runtime / rel).resolve()
        try:
            candidate.relative_to(runtime)
        except ValueError as exc:
            raise BootstrapError(f"runtime path escapes root: {rel}") from exc
        if not candidate.is_file():
            raise BootstrapError(f"runtime file missing: {rel}")
        if sha256_file(candidate) != expected.get("sha256"):
            raise BootstrapError(f"runtime file hash mismatch: {rel}")
        mode = format(stat.S_IMODE(candidate.stat().st_mode), "04o")
        if mode != expected.get("mode"):
            raise BootstrapError(f"runtime file mode mismatch: {rel}")
    expected_schemas = {
        path.name: "1.0" for path in sorted((runtime / "schemas").glob("*.json"))
    }
    if schemas != expected_schemas:
        raise BootstrapError("runtime schema inventory mismatch")
    entrypoint = runtime / "harnessctl"
    try:
        proc = subprocess.run(
            [sys.executable, str(entrypoint), "version"],
            cwd=runtime,
            text=True,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(f"runtime entrypoint preflight failed: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "entrypoint failed"
        raise BootstrapError(f"runtime entrypoint preflight failed: {detail}")
    return manifest


def make_lock(runtime: pathlib.Path, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "runtime_version": manifest["runtime_version"],
        "runtime_manifest_sha256": sha256_file(runtime / "runtime-manifest.json"),
        "policy_version": manifest["policy_version"],
        "schema_versions": manifest["schema_versions"],
        "channel": manifest["channel"],
    }


def collection_for(control: pathlib.Path, explicit: str | None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    link = control / RUNTIME_LINK
    if not link.is_symlink():
        raise BootstrapError("runtime symlink is missing; pass --runtime-collection")
    raw = pathlib.Path(os.readlink(link))
    target = raw if raw.is_absolute() else (link.parent / raw).resolve()
    return target.parent


def runtime_for(collection: pathlib.Path, version: str) -> pathlib.Path:
    target = (collection / version).resolve()
    try:
        target.relative_to(collection.resolve())
    except ValueError as exc:
        raise BootstrapError("runtime version escapes collection") from exc
    if not target.is_dir():
        raise BootstrapError(f"runtime is not installed: {version}")
    return target


def atomic_symlink(link: pathlib.Path, target: pathlib.Path) -> None:
    if link.exists() and not link.is_symlink():
        raise BootstrapError(f"refusing to replace non-symlink: {link}")
    tmp = link.parent / f".{link.name}.{os.getpid()}.tmp"
    tmp.unlink(missing_ok=True)
    os.symlink(str(target.resolve()), tmp)
    try:
        os.replace(tmp, link)
    finally:
        tmp.unlink(missing_ok=True)


def raw_link_target(link: pathlib.Path) -> pathlib.Path | None:
    if not link.is_symlink():
        return None
    raw = pathlib.Path(os.readlink(link))
    return raw if raw.is_absolute() else (link.parent / raw).resolve()


def switch(
    *,
    control: pathlib.Path,
    collection: pathlib.Path,
    target_lock: dict[str, Any],
    swap_previous: bool,
    allow_invalid_current: bool = False,
) -> dict[str, Any]:
    lock_path = control / LOCK_FILE
    previous_path = control / PREVIOUS_LOCK_FILE
    lock_existed = lock_path.exists()
    old_lock = lock_path.read_bytes() if lock_existed else None
    try:
        current_lock = read_json(lock_path)
        validate_lock(current_lock, lock_path)
    except BootstrapError:
        if not allow_invalid_current:
            raise
        current_lock = {}
    target_version = str(target_lock["runtime_version"])
    target_runtime = runtime_for(collection, target_version)
    expected = make_lock(target_runtime, validate_runtime(target_runtime))
    if target_lock != expected:
        raise BootstrapError("target lock does not match target runtime")

    old_previous = previous_path.read_bytes() if previous_path.exists() else None
    link = control / RUNTIME_LINK
    old_link = raw_link_target(link)
    try:
        if swap_previous and current_lock:
            write_json_atomic(previous_path, current_lock)
        write_json_atomic(lock_path, target_lock)
        atomic_symlink(link, target_runtime)
        if make_lock(target_runtime, validate_runtime(target_runtime)) != read_json(lock_path):
            raise BootstrapError("post-switch parity failed")
    except Exception:
        if old_lock is None:
            lock_path.unlink(missing_ok=True)
        else:
            lock_path.write_bytes(old_lock)
        if old_previous is None:
            previous_path.unlink(missing_ok=True)
        else:
            previous_path.write_bytes(old_previous)
        if old_link is None:
            link.unlink(missing_ok=True)
        else:
            atomic_symlink(link, old_link)
        raise
    return {
        "schema_version": "1.0",
        "status": "pass",
        "runtime_version": target_version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="harness-bootstrap")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-collection")
    commands = parser.add_subparsers(dest="command", required=True)
    repair = commands.add_parser("repair")
    repair.add_argument("--to", required=True, dest="target_version")
    commands.add_parser("rollback")
    args = parser.parse_args()

    root = pathlib.Path(args.project_root).expanduser().resolve()
    control = root / ".harness-init"
    if control.is_symlink() or not control.is_dir() or control.resolve().parent != root:
        raise BootstrapError("control directory escapes project root")
    for name in (LOCK_FILE, PREVIOUS_LOCK_FILE):
        path = control / name
        if path.is_symlink():
            raise BootstrapError(f"control lock must not be a symlink: {path}")
    collection = collection_for(control, args.runtime_collection)
    if args.command == "rollback":
        previous_path = control / PREVIOUS_LOCK_FILE
        target_lock = read_json(previous_path)
        validate_lock(target_lock, previous_path)
        report = switch(
            control=control,
            collection=collection,
            target_lock=target_lock,
            swap_previous=True,
        )
    else:
        target_runtime = runtime_for(collection, args.target_version)
        target_lock = make_lock(target_runtime, validate_runtime(target_runtime))
        try:
            current = read_json(control / LOCK_FILE)
            validate_lock(current, control / LOCK_FILE)
        except BootstrapError:
            current = {}
        report = switch(
            control=control,
            collection=collection,
            target_lock=target_lock,
            swap_previous=bool(current)
            and current.get("runtime_version") != args.target_version,
            allow_invalid_current=True,
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BootstrapError as exc:
        print(f"harness-bootstrap: {exc}", file=sys.stderr)
        raise SystemExit(3)
