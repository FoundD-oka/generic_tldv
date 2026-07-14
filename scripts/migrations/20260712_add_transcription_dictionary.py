#!/usr/bin/env python3
"""Create the per-user transcription dictionary table.

Usage:
  DATABASE_URL=postgresql://... python scripts/migrations/20260712_add_transcription_dictionary.py up
  DATABASE_URL=postgresql://... python scripts/migrations/20260712_add_transcription_dictionary.py down
  python scripts/migrations/20260712_add_transcription_dictionary.py --check
"""
from __future__ import annotations

import os
import sys

try:
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None

TABLE = "transcription_dictionary_terms"
UNIQUE = "uq_transcription_dictionary_user_normalized_term"
INDEX = "ix_transcription_dictionary_user_enabled"

UP = [
    f'''CREATE TABLE IF NOT EXISTS "{TABLE}" (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL,
        term VARCHAR(100) NOT NULL,
        normalized_term VARCHAR(100) NOT NULL,
        reading VARCHAR(100),
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
        CONSTRAINT "{UNIQUE}" UNIQUE (user_id, normalized_term)
    )''',
    f'CREATE INDEX IF NOT EXISTS "{INDEX}" ON "{TABLE}" (user_id, enabled)',
]
DOWN = [f'DROP TABLE IF EXISTS "{TABLE}"']


def _connect():
    dsn = os.getenv("DATABASE_URL")
    if not dsn or psycopg2 is None:
        raise SystemExit("DATABASE_URL and psycopg2 are required")
    return psycopg2.connect(dsn)


def apply(statements: list[str]) -> None:
    conn = _connect()
    try:
        with conn:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)
    finally:
        conn.close()


def status() -> int:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (f"public.{TABLE}",))
            exists = cur.fetchone()[0] is not None
            print(f"{TABLE}: {'present' if exists else 'missing'}")
            return 0 if exists else 1
    finally:
        conn.close()


def check() -> None:
    assert "IF NOT EXISTS" in UP[0]
    assert "UNIQUE (user_id, normalized_term)" in UP[0]
    assert "DEFAULT TRUE" in UP[0]
    assert "IF EXISTS" in DOWN[0]
    print("migration contract: ok")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "up":
        apply(UP)
    elif action == "down":
        apply(DOWN)
    elif action == "status":
        raise SystemExit(status())
    elif action == "--check":
        check()
    else:
        raise SystemExit(__doc__)
