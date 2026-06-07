"""Dialect-aware SQL literal rendering for ETL hooks + the seed module.

Public re-home of the seed module's per-row VALUES emitter. Renders
Python primitives (``None`` / ``int`` / ``str``) as SQL literals
matching the target dialect's syntax — used by:

- ``common/l2/seed.py`` for the dialect-neutral seed-emit path
- ``common/etl.py::write_daily_balance`` for ETL-hook authoring
- any future per-dialect bulk-INSERT emitter

The renderer intentionally accepts a narrow type set (None / int / str).
Booleans don't appear (the schema has no BOOLEAN columns); floats don't
appear (money is pre-converted to integer cents at the boundary —
``common/money.py::Cents``). Mappings / sequences are serialized to
JSON strings by the caller before reaching here.
"""

from __future__ import annotations

from recon_gen.common.sql.dialect import Dialect


def strip_tz_offset(iso_8601_str: str) -> str:
    """Return the ISO-8601 string with any trailing ``+HH:MM`` / ``Z``
    offset removed. Idempotent on inputs that already lack an offset.

    The dashboards' TIMESTAMP columns are TZ-naive on every dialect
    (P.9a) — Oracle's plain ``TIMESTAMP`` literal rejects offsets;
    PG accepts then silently drops. The integrator's ETL is expected
    to normalize to local-or-UTC ahead of authoring; this helper is
    defense-in-depth at the literal-formatter boundary.
    """
    if iso_8601_str.endswith("Z"):
        return iso_8601_str[:-1]
    for sign_pos in range(len(iso_8601_str) - 1, -1, -1):
        ch = iso_8601_str[sign_pos]
        if ch in "+-" and sign_pos > 10:
            return iso_8601_str[:sign_pos]
        if ch == "T":
            break
    return iso_8601_str


def sql_timestamp_literal(iso_8601_str: str, dialect: Dialect) -> str:
    """Format an ISO-8601 timestamp string as a SQL literal per dialect.

    PG: bare string literal with the ``T`` separator preserved.
    Oracle / DuckDB: typed ``TIMESTAMP 'YYYY-MM-DD HH:MI:SS'`` literal
    with a space separator (Oracle's parser requires the space; DuckDB
    accepts both, so the Oracle form is portable).
    """
    naive = strip_tz_offset(iso_8601_str)
    if dialect is Dialect.POSTGRES:
        return "'" + naive.replace("'", "''") + "'"
    oracle_str = naive.replace("T", " ", 1).replace("'", "''")
    return f"TIMESTAMP '{oracle_str}'"


def render_sql_literal(
    value: object, dialect: Dialect, *, is_timestamp: bool = False,
) -> str:
    """Render one Python primitive as a SQL literal.

    Accepts the limited type set the seed + ETL paths emit:
    ``None`` → ``NULL``; ``int`` → ``str(value)``; ``str`` →
    single-quoted with ``'`` escaped. When ``is_timestamp=True``,
    the value MUST be a string (ISO 8601) and routes through
    ``sql_timestamp_literal`` for dialect-specific wrapping.

    Booleans aren't accepted (the schema has no BOOLEAN columns);
    floats aren't accepted (money is pre-converted to integer cents
    via ``common/money.py::Cents`` at the boundary).
    """
    if value is None:
        return "NULL"
    if is_timestamp:
        if not isinstance(value, str):
            raise TypeError(
                f"timestamp value must be ISO string, got "
                f"{type(value).__name__}",
            )
        return sql_timestamp_literal(value, dialect)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise TypeError(
        f"render_sql_literal: type {type(value).__name__!r} not "
        f"renderable: {value!r}",
    )
