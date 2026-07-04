"""DQ.4.1 — ``DbObject.columns`` must match the ACTUALLY-emitted schema.

The declarations in ``db_objects.py::_COLUMNS`` are the emitted-column map
from the DQ.4 recon (docs/audits/dq_4_column_map.md). This test is their
safety net: it applies ``emit_schema`` to an in-memory DuckDB and
introspects each object's real emitted columns (name + order + type via
``DESCRIBE``), then asserts the declaration matches. A transcription slip
— wrong name, wrong order, an INTEGER that's really a DECIMAL — fails here
against the real schema, not against a re-read of the SQL text (the DQ.1
review's error class).

Shape / currency / storage are ColumnSpec ANNOTATIONS, not DB-introspectable
— they're validated against the DatasetContracts at DQ.4.2, not here.
"""
from __future__ import annotations

import duckdb

from recon_gen.common.db import execute_script
from recon_gen.common.db_objects import SCHEMA_GRAPH
from recon_gen.common.l2 import L2Instance
from recon_gen.common.l2.schema import emit_schema
from recon_gen.common.sql import Dialect

_PREFIX = "dq4t"


def _empty_instance() -> L2Instance:
    return L2Instance(
        accounts=(), account_templates=(), rails=(),
        transfer_templates=(), chains=(), limit_schedules=(),
    )


def _coarse(duckdb_type: str) -> str:
    """Map a DuckDB column type to the coarse ColumnSpec type."""
    t = duckdb_type.upper()
    if t.startswith(("DECIMAL", "NUMERIC")) or t in {"DOUBLE", "FLOAT", "REAL"}:
        return "DECIMAL"
    if t in {"BIGINT", "HUGEINT", "INTEGER", "SMALLINT", "TINYINT", "UBIGINT", "UINTEGER"}:
        return "INTEGER"
    if t.startswith("TIMESTAMP") or t in {"DATE", "TIME", "DATETIME"}:
        return "DATETIME"
    if t == "BOOLEAN":
        return "BIT"
    # VARCHAR / TEXT / JSON / everything else → STRING
    return "STRING"


def _introspect(cur: duckdb.DuckDBPyConnection, name: str) -> list[tuple[str, str]]:
    """(column_name, column_type) in projection order for a table/view."""
    rows = cur.execute(f"DESCRIBE {name}").fetchall()
    # DESCRIBE → (column_name, column_type, null, key, default, extra)
    return [(str(r[0]), str(r[1])) for r in rows]


def test_dq4_columns_match_emitted_schema() -> None:
    conn = duckdb.connect(":memory:")
    cur = conn.cursor()
    execute_script(
        cur,
        emit_schema(_empty_instance(), prefix=_PREFIX, dialect=Dialect.DUCKDB),
        dialect=Dialect.DUCKDB,
    )

    problems: list[str] = []
    for obj in SCHEMA_GRAPH.objects:
        emitted = _introspect(cur, obj.physical_name(_PREFIX))
        emitted_names = [n for n, _ in emitted]
        declared_names = [c.name for c in obj.columns]
        if emitted_names != declared_names:
            problems.append(
                f"{obj.obj_id}: name/order mismatch\n"
                f"    emitted : {emitted_names}\n"
                f"    declared: {declared_names}"
            )
            continue  # can't type-check a mismatched shape
        for (name, dtype), spec in zip(emitted, obj.columns):
            coarse = _coarse(dtype)
            if coarse != spec.type:
                problems.append(
                    f"{obj.obj_id}.{name}: emitted DuckDB {dtype!r}→{coarse} "
                    f"!= declared {spec.type}"
                )

    assert not problems, (
        "DbObject.columns diverged from the actually-emitted DuckDB "
        "schema (DQ.4.1):\n" + "\n".join(problems)
    )
