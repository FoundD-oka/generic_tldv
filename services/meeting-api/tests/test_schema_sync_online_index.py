"""Codex sidechain finding: startup schema-sync must NOT build the new
transcriptions cluster index synchronously (write-blocking on ~507K rows).
Indexes marked info={'online_only': True} are skipped by _sync_indexes and
created out-of-band via scripts/migrations/ with CREATE INDEX CONCURRENTLY.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import Index

_repo = Path(__file__).resolve().parents[3]
_schema_sync_path = str(_repo / "libs" / "schema-sync")
if _schema_sync_path not in sys.path:
    sys.path.insert(0, _schema_sync_path)

from schema_sync.sync import _is_online_only_index, _sync_indexes  # noqa: E402

from meeting_api.models import Base, Transcription  # noqa: E402


def _find_index(name: str) -> Index:
    for index in Transcription.__table__.indexes:
        if index.name == name:
            return index
    raise AssertionError(f"index {name} not found on transcriptions")


def test_cluster_index_is_marked_online_only():
    assert _is_online_only_index(_find_index("ix_transcription_meeting_cluster"))
    assert not _is_online_only_index(_find_index("ix_transcription_meeting_start"))


def test_sync_indexes_skips_online_only_but_creates_normal_ones():
    created: list[str] = []

    inspector = MagicMock()
    inspector.get_table_names.return_value = [t.name for t in Base.metadata.sorted_tables]
    inspector.get_indexes.return_value = []  # everything "missing"

    with patch("schema_sync.sync.inspect", return_value=inspector), \
         patch.object(Index, "create", new=lambda self, conn: created.append(self.name)):
        _sync_indexes(MagicMock(), Base)

    assert "ix_transcription_meeting_cluster" not in created
    assert "ix_transcription_meeting_start" in created
