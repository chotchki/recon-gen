"""Portable cursor-based fetch helper for spine `Invariant.detect()`
methods that run against either the in-memory sqlite generator pipeline
OR a live deployed DB (PG / Oracle / SQLite).

Background — pre-BM.5 every `.detect()` body called
``conn.execute(sql).fetchall()``. sqlite3.Connection ships ``.execute``
as a convenience shim that creates + returns a cursor in one shot;
oracledb.Connection + psycopg.Connection don't (DB-API 2.0 only
mandates the cursor-then-execute pattern). The
``test_spine_live_agreement`` tests passing an oracledb connection
tripped `AttributeError: 'Connection' object has no attribute 'execute'`
on the Oracle/AWS variant of every detect-touching test.

This helper unifies on the cursor path; ``rows = fetch_all(conn,
sql)`` works on all three. The cursor is closed in the ``finally``
so the cursor pool stays clean across the long-running spine pipeline.
"""

from __future__ import annotations

from typing import Any


def fetch_all(conn: Any, sql: str) -> list[Any]:  # typing-smell: ignore[explicit-any]: per-driver Connection/Cursor have no shared Protocol; row tuple shape is dialect+query-dependent
    """Cursor-based ``SELECT`` returning all rows; closes the cursor.

    Portable across sqlite3 / oracledb / psycopg DB-API 2.0
    connections. Use in spine ``.detect()`` methods that originally
    relied on sqlite3's convenience ``conn.execute()`` shim.
    """
    cur = conn.cursor()
    try:
        cur.execute(sql)
        return list(cur.fetchall())
    finally:
        cur.close()
