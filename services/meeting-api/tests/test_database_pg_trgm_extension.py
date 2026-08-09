"""init_db must create pg_trgm before schema-sync, and must not fail-fast on it.

Fresh installs (compose/lite) never run scripts/migrations/, so create_all of
ix_transcription_text_trgm needs the extension to already exist. A DB that
refuses the CREATE EXTENSION must still boot (#57: don't add startup fail-fast).
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_repo = Path(__file__).resolve().parents[3]
_schema_sync_path = str(_repo / "libs" / "schema-sync")
if _schema_sync_path not in sys.path:
    sys.path.insert(0, _schema_sync_path)

import schema_sync  # noqa: E402

from meeting_api import database  # noqa: E402


def _fake_engine(calls: list, fail: bool = False):
    class FakeConn:
        async def execute(self, statement):
            calls.append(str(statement))
            if fail:
                raise RuntimeError("permission denied to create extension \"pg_trgm\"")

    class FakeEngine:
        @asynccontextmanager
        async def begin(self):
            yield FakeConn()

    return FakeEngine()


@pytest.mark.asyncio
async def test_init_db_creates_pg_trgm_before_ensure_schema():
    calls: list = []

    async def fake_ensure_schema(engine, base, prerequisites=None):
        calls.append("ensure_schema")

    with patch.object(database, "engine", _fake_engine(calls)), \
         patch.object(schema_sync, "ensure_schema", fake_ensure_schema):
        await database.init_db()

    assert calls[0] == "CREATE EXTENSION IF NOT EXISTS pg_trgm"
    assert calls[1] == "ensure_schema"


@pytest.mark.asyncio
async def test_init_db_continues_when_extension_creation_fails(caplog):
    calls: list = []

    async def fake_ensure_schema(engine, base, prerequisites=None):
        calls.append("ensure_schema")

    with patch.object(database, "engine", _fake_engine(calls, fail=True)), \
         patch.object(schema_sync, "ensure_schema", fake_ensure_schema), \
         caplog.at_level("WARNING", logger="meeting_api.database"):
        await database.init_db()

    assert "ensure_schema" in calls
    assert any("pg_trgm" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_init_db_still_raises_when_schema_sync_fails():
    """The best-effort extension step must not swallow real schema errors."""
    calls: list = []

    async def failing_ensure_schema(engine, base, prerequisites=None):
        raise RuntimeError("schema sync broke")

    with patch.object(database, "engine", _fake_engine(calls)), \
         patch.object(schema_sync, "ensure_schema", failing_ensure_schema), \
         pytest.raises(RuntimeError):
        await database.init_db()
