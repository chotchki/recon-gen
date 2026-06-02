"""AO.1 — ``common.sql.money.cents_to_dollars_sql`` tests.

The helper is the only place dataset-SQL projects BIGINT cents back
to dollar-domain for the renderer. All current dialects (PG / Oracle /
DuckDB) produce plain ``/ 100.0`` — INTEGER / FLOAT promotes to FLOAT
in each. The original SQLite branch needed an explicit ``CAST(... AS
REAL)`` to avoid INTEGER truncation; CB.8 dropped SQLite, so the
single shared shape applies everywhere.
"""

from __future__ import annotations

import duckdb

from recon_gen.common.sql import Dialect
from recon_gen.common.sql.money import cents_to_dollars_sql


def test_postgres_projection_shape() -> None:
    assert cents_to_dollars_sql("col", dialect=Dialect.POSTGRES) == "(col / 100.0)"


def test_oracle_projection_shape() -> None:
    assert cents_to_dollars_sql("col", dialect=Dialect.ORACLE) == "(col / 100.0)"


def test_duckdb_projection_shape() -> None:
    """DuckDB promotes INTEGER / FLOAT to FLOAT — no explicit CAST
    needed (unlike the legacy SQLite path)."""
    assert (
        cents_to_dollars_sql("col", dialect=Dialect.DUCKDB)
        == "(col / 100.0)"
    )


def test_duckdb_qualified_column() -> None:
    assert (
        cents_to_dollars_sql("t.amount_money", dialect=Dialect.DUCKDB)
        == "(t.amount_money / 100.0)"
    )


def test_duckdb_round_trip_integer_storage() -> None:
    """End-to-end check on an in-memory DuckDB: insert BIGINT 7500
    (cents), project via the helper, fetched value must equal 75.0
    (REAL dollars)."""
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE t (amount_money BIGINT NOT NULL)")
        conn.execute("INSERT INTO t (amount_money) VALUES (7500)")
        projection = cents_to_dollars_sql("amount_money", dialect=Dialect.DUCKDB)
        cur = conn.execute(f"SELECT {projection} FROM t")
        row = cur.fetchone()
        assert row[0] == 75.0
        assert isinstance(row[0], float)
    finally:
        conn.close()
