"""Issue #1 unit tests for retention metadata, per-file backend dispatch,
and the lifecycle policy file. Pure units — no DB / async harness needed."""

import json
import os
from datetime import datetime

import pytest

from meeting_api import recordings as rec_mod
from meeting_api.recordings import (
    RECORDING_DELETE_AFTER_DAYS,
    RECORDING_STORAGE_CLASS_POLICY,
    _compute_delete_after,
    get_storage_client_for,
)


# --------------------------------------------------------------------------
# delete_after computation (advisory metadata)
# --------------------------------------------------------------------------

def test_compute_delete_after_adds_retention_window():
    anchor = "2026-01-01T00:00:00"
    out = _compute_delete_after(anchor)
    # 60 days after the anchor.
    assert out is not None
    delta_days = (datetime.fromisoformat(out) - datetime.fromisoformat(anchor)).days
    assert delta_days == RECORDING_DELETE_AFTER_DAYS == 60


def test_compute_delete_after_handles_bad_input():
    assert _compute_delete_after(None) is None
    assert _compute_delete_after("not-a-date") is None


def test_storage_class_policy_constant():
    assert RECORDING_STORAGE_CLASS_POLICY == "standard_14d_nearline_until_60d"


# --------------------------------------------------------------------------
# Per-file backend dispatch (migration safety)
# --------------------------------------------------------------------------

def test_get_storage_client_for_dispatches_non_default_backend(monkeypatch):
    # Default backend is gcs; a legacy file written under minio must resolve to
    # a dedicated minio client (migration safety), not the default singleton.
    rec_mod._storage_clients_by_backend.clear()
    monkeypatch.setenv("STORAGE_BACKEND", "gcs")
    calls = []

    def _fake_create(backend=None):
        calls.append(backend)
        return f"client::{backend}"

    monkeypatch.setattr(rec_mod, "create_storage_client", _fake_create)
    assert get_storage_client_for("minio") == "client::minio"
    assert get_storage_client_for("s3") == "client::s3"
    # Cached — second call must not re-create.
    assert get_storage_client_for("minio") == "client::minio"
    assert calls.count("minio") == 1


def test_get_storage_client_for_default_backend_uses_singleton(monkeypatch):
    # When the file's backend == the current default, reuse the shared singleton.
    rec_mod._storage_clients_by_backend.clear()
    monkeypatch.setenv("STORAGE_BACKEND", "gcs")
    monkeypatch.setattr(rec_mod, "get_storage_client", lambda: "DEFAULT")
    assert get_storage_client_for("gcs") == "DEFAULT"


def test_get_storage_client_for_missing_backend_uses_default(monkeypatch):
    rec_mod._storage_clients_by_backend.clear()
    monkeypatch.setattr(rec_mod, "get_storage_client", lambda: "DEFAULT")
    assert get_storage_client_for(None) == "DEFAULT"
    assert get_storage_client_for("") == "DEFAULT"


def test_get_storage_client_for_init_failure_falls_back(monkeypatch):
    rec_mod._storage_clients_by_backend.clear()
    monkeypatch.setenv("STORAGE_BACKEND", "gcs")

    def _boom(backend=None):
        raise RuntimeError("cannot init")

    monkeypatch.setattr(rec_mod, "create_storage_client", _boom)
    monkeypatch.setattr(rec_mod, "get_storage_client", lambda: "DEFAULT")
    assert get_storage_client_for("weird-backend") == "DEFAULT"


# --------------------------------------------------------------------------
# Lifecycle policy file (acceptance criteria AC3, AC4)
# --------------------------------------------------------------------------

def _load_lifecycle():
    here = os.path.dirname(__file__)
    path = os.path.normpath(
        os.path.join(here, "..", "..", "..", "deploy", "gcs", "lifecycle.json")
    )
    with open(path) as fh:
        return json.load(fh)


def test_lifecycle_has_nearline_at_14d():
    rules = _load_lifecycle()["rule"]
    nearline = [
        r for r in rules
        if r["action"].get("type") == "SetStorageClass"
        and r["action"].get("storageClass") == "NEARLINE"
    ]
    assert len(nearline) == 1
    assert nearline[0]["condition"]["age"] == 14


def test_lifecycle_has_delete_at_60d():
    rules = _load_lifecycle()["rule"]
    delete = [r for r in rules if r["action"].get("type") == "Delete"]
    assert len(delete) == 1
    assert delete[0]["condition"]["age"] == 60
