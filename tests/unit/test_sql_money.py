"""AO.1 — ``common.sql.money.cents_to_dollars_sql`` tests.

The helper is the only place dataset-SQL projects BIGINT cents back
to dollar-domain for the renderer. PG / Oracle get plain ``/ 100.0``
(NUMERIC promotion is exact). SQLite needs an explicit ``CAST(... AS
REAL)`` first — without the CAST, INTEGER / INTEGER would truncate to
zero decimal places.
"""

from __future__ import annotations

import sqlite3

from recon_gen.common.sql import Dialect
from recon_gen.common.sql.money import cents_to_dollars_sql


def test_postgres_projection_shape() -> None:
    assert cents_to_dollars_sql("col", dialect=Dialect.POSTGRES) == "(col / 100.0)"


def test_oracle_projection_shape() -> None:
    assert cents_to_dollars_sql("col", dialect=Dialect.ORACLE) == "(col / 100.0)"


def test_sqlite_projection_shape() -> None:
    """SQLite needs the explicit ``CAST(... AS REAL)`` — bare ``col /
    100.0`` would integer-truncate when col is BIGINT (REAL operand
    triggers float division but only when at least one side already
    parses as REAL)."""
    assert (
        cents_to_dollars_sql("col", dialect=Dialect.SQLITE)
        == "(CAST(col AS REAL) / 100.0)"
    )


def test_sqlite_qualified_column() -> None:
    assert (
        cents_to_dollars_sql("t.amount_money", dialect=Dialect.SQLITE)
        == "(CAST(t.amount_money AS REAL) / 100.0)"
    )


def test_sqlite_round_trip_integer_storage() -> None:
    """End-to-end check on an in-memory SQLite: insert BIGINT 7500
    (cents), project via the helper, fetched value must equal 75.0
    (REAL dollars). This guards the SQLite-truncation bug the helper
    exists to prevent."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE t (amount_money BIGINT NOT NULL)")
        conn.execute("INSERT INTO t (amount_money) VALUES (7500)")
        projection = cents_to_dollars_sql("amount_money", dialect=Dialect.SQLITE)
        cur = conn.execute(f"SELECT {projection} FROM t")
        row = cur.fetchone()
        assert row[0] == 75.0
        assert isinstance(row[0], float)
    finally:
        conn.close()
