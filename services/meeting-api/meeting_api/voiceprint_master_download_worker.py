"""Isolated finalized-master downloader for voiceprint processing.

The storage SDKs used by the meeting API expose synchronous methods.  A
blocked socket/read in one of those methods cannot be interrupted safely when
it runs in an ``asyncio.to_thread`` worker.  This module is therefore executed
in a dedicated OS process.  The parent can terminate (and, if needed, kill)
that process at the voiceprint download deadline without leaving a Python
thread or storage request alive in the API process.

The worker deliberately reports only fixed status codes.  Storage exception
messages can contain bucket/object names or SDK request details and must never
cross back into an HTTP-facing process.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from .storage import create_storage_client


STATUS_OK = "ok"
STATUS_INVALID_REQUEST = "invalid_request"
STATUS_INVALID_SIZE = "invalid_size"
STATUS_EMPTY = "empty"
STATUS_TOO_LARGE = "too_large"
STATUS_INVALID_CHUNK = "invalid_chunk"
STATUS_RANGE_LENGTH = "range_length"
STATUS_DOWNLOAD_LENGTH = "download_length"
STATUS_STORAGE_ERROR = "storage_error"

_MAX_CONTROL_BYTES = 64 * 1024


class MasterDownloadRejected(Exception):
    """Expected, sanitized validation/integrity failure in the worker."""

    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


def download_master_to_fd(
    storage: Any,
    storage_path: str,
    output_fd: int,
    *,
    max_bytes: int,
    chunk_bytes: int,
) -> None:
    """Stat, validate, and range-copy one object to an inherited descriptor.

    The metadata stat always completes before the first body/range request.
    Every range is inclusive and bounded by ``chunk_bytes``.  No full object
    is assembled in memory.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise MasterDownloadRejected(STATUS_INVALID_REQUEST)
    if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or chunk_bytes <= 0:
        raise MasterDownloadRejected(STATUS_INVALID_CHUNK)

    size = storage.get_file_size(storage_path)
    if isinstance(size, bool) or not isinstance(size, int):
        raise MasterDownloadRejected(STATUS_INVALID_SIZE)
    if size <= 0:
        raise MasterDownloadRejected(STATUS_EMPTY)
    if size > max_bytes:
        raise MasterDownloadRejected(STATUS_TOO_LARGE)

    written = 0
    # The descriptor was created by mkstemp in the parent and passed through
    # exec with pass_fds.  Opening a duplicate keeps ownership explicit for
    # direct unit tests and guarantees the inherited descriptor is not reused.
    destination_fd = os.dup(output_fd)
    with os.fdopen(destination_fd, "wb") as destination:
        for start in range(0, size, chunk_bytes):
            end = min(size - 1, start + chunk_bytes - 1)
            chunk = storage.download_file_range(storage_path, start, end)
            expected = end - start + 1
            if not isinstance(chunk, (bytes, bytearray)) or len(chunk) != expected:
                raise MasterDownloadRejected(STATUS_RANGE_LENGTH)
            destination.write(chunk)
            written += len(chunk)

    if written != size:
        raise MasterDownloadRejected(STATUS_DOWNLOAD_LENGTH)


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(_MAX_CONTROL_BYTES + 1)
    if not raw or len(raw) > _MAX_CONTROL_BYTES:
        raise MasterDownloadRejected(STATUS_INVALID_REQUEST)
    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise MasterDownloadRejected(STATUS_INVALID_REQUEST) from exc
    if not isinstance(request, dict):
        raise MasterDownloadRejected(STATUS_INVALID_REQUEST)

    storage_backend = request.get("storage_backend")
    storage_path = request.get("storage_path")
    output_fd = request.get("output_fd")
    max_bytes = request.get("max_bytes")
    chunk_bytes = request.get("chunk_bytes")
    if storage_backend is not None and not isinstance(storage_backend, str):
        raise MasterDownloadRejected(STATUS_INVALID_REQUEST)
    if not isinstance(storage_path, str) or not storage_path:
        raise MasterDownloadRejected(STATUS_INVALID_REQUEST)
    if isinstance(output_fd, bool) or not isinstance(output_fd, int) or output_fd < 3:
        raise MasterDownloadRejected(STATUS_INVALID_REQUEST)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise MasterDownloadRejected(STATUS_INVALID_REQUEST)
    if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int):
        raise MasterDownloadRejected(STATUS_INVALID_REQUEST)
    return {
        "storage_backend": storage_backend,
        "storage_path": storage_path,
        "output_fd": output_fd,
        "max_bytes": max_bytes,
        "chunk_bytes": chunk_bytes,
    }


def main() -> int:
    status = STATUS_STORAGE_ERROR
    try:
        request = _read_request()
        storage = create_storage_client(request["storage_backend"])
        download_master_to_fd(
            storage,
            request["storage_path"],
            request["output_fd"],
            max_bytes=request["max_bytes"],
            chunk_bytes=request["chunk_bytes"],
        )
        status = STATUS_OK
    except MasterDownloadRejected as exc:
        status = exc.status
    except Exception:
        # Never serialize the exception.  SDK errors may include object paths,
        # endpoint details, or request identifiers.
        status = STATUS_STORAGE_ERROR

    sys.stdout.write(status)
    sys.stdout.flush()
    return 0 if status == STATUS_OK else 2


if __name__ == "__main__":  # pragma: no cover - exercised via parent process
    raise SystemExit(main())
