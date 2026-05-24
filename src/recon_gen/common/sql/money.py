"""Dialect-aware cents → dollars projection for read-boundary SQL.

Per AO.1: storage moves to BIGINT integer cents on every dialect.
Wrap a money column (BIGINT cents) in this helper anywhere the renderer
needs dollars. The matview SQL math stays in cents (integer-safe,
no float dust); this helper is the only place the dollar projection
happens.

Paired with ``recon_gen.common.money.Cents.from_db`` on the Python-read
side — same boundary, two consumers (dataset SQL projections + spine
detect helpers).
"""

from __future__ import annotations

from recon_gen.common.sql.dialect import Dialect


def cents_to_dollars_sql(col: str, *, dialect: Dialect) -> str:
    """Return a SQL expression that projects ``col`` (BIGINT cents) to
    dollars.

    Postgres + Oracle: ``BIGINT / NUMERIC(20,2)`` literal promotes the
    division to NUMERIC (exact, no float fallback). SQLite: explicit
    ``CAST(... AS REAL) / 100.0`` to force float division — without
    the CAST, SQLite's INTEGER / INTEGER would integer-truncate (7500
    / 100 = 75 with no decimal, dropping the cents portion entirely).

    ``col`` is the bare column reference (caller has already qualified
    it as needed, e.g., ``"t.amount_money"``). Returns the parenthesized
    expression ready to drop into a SELECT list or WHERE clause.
    """
    if dialect == Dialect.SQLITE:
        return f"(CAST({col} AS REAL) / 100.0)"
    return f"({col} / 100.0)"
