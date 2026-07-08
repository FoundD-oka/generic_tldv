#!/usr/bin/env python3
"""Online migration: add speaker_cluster / speaker_auto to transcriptions.

Designed for the production-size table (~507K rows) — run BEFORE deploying
the code that adds these columns to the model, so the startup schema-sync
(plain ALTER + locking index.create) finds everything already in place and
no-ops.

Steps (up):
  1. ALTER TABLE transcriptions ADD COLUMN speaker_cluster VARCHAR(64)
     (nullable, no default → metadata-only, instant)
  2. ALTER TABLE transcriptions ADD COLUMN speaker_auto VARCHAR(255)
     (nullable, no default → metadata-only, instant)
  3. Batched backfill: speaker_auto = speaker for existing rows
     (id-range batches, no long transaction; existing rows keep `speaker`)
  4. CREATE INDEX CONCURRENTLY ix_transcription_meeting_cluster
     ON transcriptions (meeting_id, speaker_cluster)  — outside a transaction

Rollback (down):
  DROP INDEX CONCURRENTLY ix_transcription_meeting_cluster;
  ALTER TABLE transcriptions DROP COLUMN speaker_cluster, DROP COLUMN speaker_auto;

Usage:
  DATABASE_URL=postgresql://user:pass@host:5432/db \
      python scripts/migrations/20260708_add_speaker_cluster.py up
  ... down   (rollback)
  ... status (inspect only)
"""
from __future__ import annotations

import os
import sys
import time

try:
    import psycopg2
except ImportError:  # pragma: no cover - depends on runtime env
    psycopg2 = None

BATCH_SIZE = int(os.getenv("MIGRATION_BATCH_SIZE", "20000"))
BATCH_SLEEP_S = float(os.getenv("MIGRATION_BATCH_SLEEP_S", "0.1"))
INDEX_NAME = "ix_transcription_meeting_cluster"

ADD_COLUMNS = [
    'ALTER TABLE "transcriptions" ADD COLUMN IF NOT EXISTS "speaker_cluster" VARCHAR(64)',
    'ALTER TABLE "transcriptions" ADD COLUMN IF NOT EXISTS "speaker_auto" VARCHAR(255)',
]
BACKFILL = (
    'UPDATE "transcriptions" SET "speaker_auto" = "speaker" '
    'WHERE id >= %s AND id < %s AND "speaker_auto" IS NULL AND "speaker" IS NOT NULL'
)
CREATE_INDEX = (
    f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{INDEX_NAME}" '
    'ON "transcriptions" ("meeting_id", "speaker_cluster")'
)
DROP_INDEX = f'DROP INDEX CONCURRENTLY IF EXISTS "{INDEX_NAME}"'
DROP_COLUMNS = [
    'ALTER TABLE "transcriptions" DROP COLUMN IF EXISTS "speaker_cluster"',
    'ALTER TABLE "transcriptions" DROP COLUMN IF EXISTS "speaker_auto"',
]


def batch_ranges(min_id: int, max_id: int, batch_size: int):
    """Yield [start, end) id ranges covering [min_id, max_id]."""
    if max_id < min_id:
        return
    start = min_id
    while start <= max_id:
        yield start, start + batch_size
        start += batch_size


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
        # 1-2. instant nullable column adds (short transaction)
        with conn:
            with conn.cursor() as cur:
                for stmt in ADD_COLUMNS:
                    print(f"+ {stmt}")
                    cur.execute(stmt)

        # 3. batched backfill, one short transaction per batch
        with conn:
            with conn.cursor() as cur:
                cur.execute('SELECT MIN(id), MAX(id) FROM "transcriptions"')
                min_id, max_id = cur.fetchone()
        if min_id is not None:
            total = 0
            for start, end in batch_ranges(min_id, max_id, BATCH_SIZE):
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(BACKFILL, (start, end))
                        total += cur.rowcount
                print(f"  backfill ids [{start},{end}): total updated={total}")
                time.sleep(BATCH_SLEEP_S)

        # 4. CONCURRENTLY index build (must run outside a transaction)
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
            for stmt in DROP_COLUMNS:
                print(f"+ {stmt}")
                cur.execute(stmt)
        print("down: done")
    finally:
        conn.close()


def status() -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'transcriptions' "
                "AND column_name IN ('speaker_cluster', 'speaker_auto')"
            )
            cols = sorted(row[0] for row in cur.fetchall())
            cur.execute("SELECT indexname FROM pg_indexes WHERE indexname = %s", (INDEX_NAME,))
            has_index = cur.fetchone() is not None
            print(f"columns: {cols or 'MISSING'}  index {INDEX_NAME}: {'present' if has_index else 'MISSING'}")
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
