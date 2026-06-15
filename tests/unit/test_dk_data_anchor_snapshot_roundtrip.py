"""DK.7.snapshot_roundtrip — the matview refresh always re-derives
``<prefix>_data_anchor`` from the CURRENT row state of the base tables.

This property is what justifies DK.0's lock to persist the data anchor
as a matview (DK.1) rather than in ``config_kv``. The snapshotter
(common/snapshotter.py) restores base tables row-by-row and then
regenerates matviews via ``refresh_v_overlay_matviews_sql``. If the
anchor lived in config_kv, the snapshot would restore the OLD anchor
verbatim and stale-bind relative to the post-restore row set (the
trainer-dogfood scenario where each test plants a fresh row past the
snapshot point). With the matview shape, the post-restore refresh
re-derives the anchor from the actual restored + planted rows.

The test exercises the underlying property directly: insert a row at
date A, refresh, assert anchor=A; insert a row at date B>A, refresh,
assert anchor=B; delete the date B row (simulating snapshot restore
to the pre-B state), refresh, assert anchor=A again.

Runs in-process via DuckDB :memory: per the spine-test pattern. The
property holds the same on PG / Oracle by construction (the matview
SQL is identical across dialects); cross-dialect verification rides
on db-tier integration tests.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb

from recon_gen.common.db import execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect

_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "spec_example"
_DIALECT = Dialect.DUCKDB


def _fresh_db() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    return conn


def _refresh(conn: duckdb.DuckDBPyConnection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()


def _read_anchor(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    row = conn.execute(
        f"SELECT data_anchor FROM {_PREFIX}_data_anchor LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return row[0]


def _insert_tx(
    conn: duckdb.DuckDBPyConnection,
    *,
    tx_id: str,
    posting: datetime,
) -> None:
    # Minimal-shape INSERT — only the fields required by NOT NULL +
    # IS JSON check + amount-invariant CHECK
    # (Credit → money ≥ 0; Debit → money ≤ 0). account_id and rail
    # name are deliberately constants since this test isn't exercising
    # the L1 invariants, only the data_anchor MAX(posting) projection.
    conn.execute(
        f"""
        INSERT INTO {_PREFIX}_transactions (
            id, account_id, account_name, account_role,
            account_scope, account_parent_role,
            amount_money, amount_direction,
            status, posting, transfer_id,
            rail_name, origin, metadata
        ) VALUES (
            '{tx_id}', 'acc-1', 'Account One', 'GLCash',
            'internal', 'GLCash',
            100, 'Credit',
            'Posted', TIMESTAMP '{posting.isoformat(sep=" ")}',
            '{tx_id}-transfer',
            'TestRail', 'InternalInitiated', '{{}}'
        );
        """
    )
    conn.commit()


def test_data_anchor_reflects_current_state_after_refresh() -> None:
    """Refresh rebuilds the anchor from the row set every time."""
    conn = _fresh_db()
    try:
        date_a = datetime(2026, 6, 15, 10, 0, 0)
        date_b = datetime(2026, 7, 1, 14, 0, 0)

        # Insert at A, refresh, anchor pins on A.
        _insert_tx(conn, tx_id="tx-a", posting=date_a)
        _refresh(conn)
        assert _read_anchor(conn) == date_a

        # Insert at B (later), refresh, anchor advances to B.
        _insert_tx(conn, tx_id="tx-b", posting=date_b)
        _refresh(conn)
        assert _read_anchor(conn) == date_b

        # Delete tx-b — simulates the trainer-dogfood snapshot restore
        # path where the test's planted row reverts to the snapshot's
        # row set. Refresh the matview: anchor reverts to A because the
        # matview re-derives from the CURRENT row set, not from any
        # cached snapshot of its prior value. This is exactly the
        # property that justifies the matview-over-config_kv DK.0 lock
        # (config_kv would persist B as the anchor value even after
        # the row that produced it is gone).
        conn.execute(
            f"DELETE FROM {_PREFIX}_transactions WHERE id = 'tx-b'"
        )
        conn.commit()
        _refresh(conn)
        assert _read_anchor(conn) == date_a
    finally:
        conn.close()


def test_data_anchor_null_on_empty_tables() -> None:
    """Cold-DB case: with no transactions and no daily_balances rows,
    the matview projects a single row with data_anchor IS NULL.
    DK.4's maybe_export_data_anchor honors None and falls through to
    live(wall-clock) with a loud warning."""
    conn = _fresh_db()
    try:
        _refresh(conn)
        assert _read_anchor(conn) is None
    finally:
        conn.close()
