#!/usr/bin/env python3
"""Online migration: pg_trgm GIN index on transcriptions.text (cross-meeting search).

Designed for the production-size table (~507K rows) — run BEFORE deploying the
code that adds this index to the model, so the startup schema-sync finds it
already in place and no-ops. The model marks the index
``info={'online_only': True}`` so schema-sync never builds it synchronously.

Steps (up):
  1. CREATE EXTENSION IF NOT EXISTS pg_trgm
     (trusted extension since PG13 → DB owner is enough)
  2. CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transcription_text_trgm
     ON transcriptions USING gin (text gin_trgm_ops)  — outside a transaction,
     so writers are never blocked.

Rollback (down):
  DROP INDEX CONCURRENTLY IF EXISTS ix_transcription_text_trgm;
  (the extension is left in place — it may be used by other objects)

Both actions are idempotent: running `up` twice in a row succeeds.

Usage:
  DATABASE_URL=postgresql://user:pass@host:5432/db \
      python scripts/migrations/20260809_add_transcription_text_trgm.py up
  ... down   (rollback)
  ... status (inspect only)
"""
from __future__ import annotations

import os
import sys

try:
    import psycopg2
except ImportError:  # pragma: no cover - depends on runtime env
    psycopg2 = None

INDEX_NAME = "ix_transcription_text_trgm"

CREATE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS pg_trgm"
CREATE_INDEX = (
    f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{INDEX_NAME}" '
    'ON "transcriptions" USING gin ("text" gin_trgm_ops)'
)
DROP_INDEX = f'DROP INDEX CONCURRENTLY IF EXISTS "{INDEX_NAME}"'


def _connect():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is required", file=sys.stderr)
        sys.exit(2)
    if psycopg2 is None:
        print("psycopg2 is required (pip install psycopg2-binary)", file=sys.stderr)
        sys.exit(2)
    return psycopg2.connect(dsn)


def up() -> None:
    conn = _connect()
    try:
        # 1. extension (short transaction)
        with conn:
            with conn.cursor() as cur:
                print(f"+ {CREATE_EXTENSION}")
                cur.execute(CREATE_EXTENSION)

        # 2. CONCURRENTLY index build (must run outside a transaction)
        conn.autocommit = True
        with conn.cursor() as cur:
            print(f"+ {CREATE_INDEX}")
            cur.execute(CREATE_INDEX)
        print("up: done")
    finally:
        conn.close()


def down() -> None:
    conn = _connect()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            print(f"+ {DROP_INDEX}")
            cur.execute(DROP_INDEX)
        print("down: done (extension pg_trgm left in place)")
    finally:
        conn.close()


def status() -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
            has_extension = cur.fetchone() is not None
            cur.execute("SELECT indexname FROM pg_indexes WHERE indexname = %s", (INDEX_NAME,))
            has_index = cur.fetchone() is not None
            print(
                f"extension pg_trgm: {'present' if has_extension else 'MISSING'}  "
                f"index {INDEX_NAME}: {'present' if has_index else 'MISSING'}"
            )
    finally:
        conn.close()


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "up":
        up()
    elif action == "down":
        down()
    elif action == "status":
        status()
    else:
        print(__doc__)
        sys.exit(2)
