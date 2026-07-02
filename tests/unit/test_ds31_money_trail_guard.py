"""DS.3.1 — money_trail refresh guard: depth cap + cycle tripwire.

`transfer_parent_id` is customer ETL data with no FK and no DDL-level
cycle guard. Before this guard, a cycle made root-reachable by a
multi-parent row (exactly a chain_parent_disagreement corruption) sent
the recursive walk divergent — the refresh HUNG on PG/DuckDB and
errored ORA-32044 on Oracle: the detector of chain corruption hanging
ON chain corruption. (The divergence case can't be demonstrated as a
running red test — it hangs — so the red-first evidence is the
tripwire test failing against the un-guarded emitter; test + fix land
in the same chain per POLICY 2.)

The guard: `MONEY_TRAIL_DEPTH_CAP` bounds the recursive arm (refresh is
TOTAL by construction) and every refresh script carries a tripwire
statement that errors — with the diagnosis in the error text — when
edge rows sit at the cap. Known + accepted under-detection: a pure
cycle unreachable from any root terminates but stays SILENT (its
members simply never enter the walk), and cycle members with no
source×target leg pair emit no edge rows for the tripwire to see —
that residual class is a candidate data-quality invariant, recorded in
the DS backlog.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from recon_gen.common.db import execute_script
from recon_gen.common.l2.config_table import emit_config_populate_sql
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import (
    MONEY_TRAIL_DEPTH_CAP,
    emit_schema,
    money_trail_tripwire_sql,
    refresh_matviews_sql,
)
from recon_gen.common.sql import Dialect

SPEC = Path(__file__).parent.parent / "l2" / "spec_example.yaml"
PREFIX = "spec_example"

TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name",
    "template_name", "origin", "metadata", "supersedes",
)

POSTING = dt.datetime(2030, 1, 1, 12, 0, 0)


def _leg(id_: str, acct: str, amount: int, tid: str, ptid: str | None) -> tuple[object, ...]:
    direction = "Debit" if amount < 0 else "Credit"
    return (
        id_, acct, acct, "CustomerSubledger", "internal", "CustomerLedger",
        amount, direction, "Posted", POSTING, tid, ptid, "TrailRail",
        None, "InternalInitiated", None, None,
    )


def _transfer(tid: str, src: str, tgt: str, ptid: str | None) -> list[tuple[object, ...]]:
    """One multi-leg transfer: src -100 / tgt +100 — the shape that
    materializes exactly one trail edge per walk visit."""
    return [
        _leg(f"{tid}-s", src, -100, tid, ptid),
        _leg(f"{tid}-t", tgt, 100, tid, ptid),
    ]


def _fresh_db() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    instance = load_instance(SPEC)
    execute_script(
        conn,
        emit_schema(instance, prefix=PREFIX, dialect=Dialect.DUCKDB),
        dialect=Dialect.DUCKDB,
    )
    execute_script(
        conn,
        emit_config_populate_sql(
            prefix=PREFIX, cfg_json="{}",
            l2_json='{"rails": [], "limit_schedules": []}',
            as_of=dt.datetime(2030, 1, 3), dialect=Dialect.DUCKDB,
        ),
        dialect=Dialect.DUCKDB,
    )
    return conn


def _insert(conn: duckdb.DuckDBPyConnection, rows: list[tuple[object, ...]]) -> None:
    placeholders = ", ".join("?" for _ in TX_COLS)
    conn.executemany(
        f"INSERT INTO {PREFIX}_transactions ({', '.join(TX_COLS)}) "
        f"VALUES ({placeholders})",
        rows,
    )


def _refresh(conn: duckdb.DuckDBPyConnection) -> None:
    instance = load_instance(SPEC)
    execute_script(
        conn,
        refresh_matviews_sql(instance, prefix=PREFIX, dialect=Dialect.DUCKDB),
        dialect=Dialect.DUCKDB,
    )


# -- Emitted-text properties (all three dialects) ----------------------------


def test_recursive_arm_carries_the_depth_cap() -> None:
    instance = load_instance(SPEC)
    for dialect in (Dialect.POSTGRES, Dialect.ORACLE, Dialect.DUCKDB):
        schema = emit_schema(instance, prefix=PREFIX, dialect=dialect)
        assert f"WHERE c.depth < {MONEY_TRAIL_DEPTH_CAP}" in schema, dialect


def test_every_refresh_script_carries_the_tripwire() -> None:
    instance = load_instance(SPEC)
    for dialect in (Dialect.POSTGRES, Dialect.ORACLE, Dialect.DUCKDB):
        script = refresh_matviews_sql(instance, prefix=PREFIX, dialect=dialect)
        assert money_trail_tripwire_sql(PREFIX, dialect) in script, dialect
        assert "money_trail_cycle_tripwire" in script, dialect


def test_tripwire_statement_shape_per_dialect() -> None:
    pg = money_trail_tripwire_sql(PREFIX, Dialect.POSTGRES)
    orc = money_trail_tripwire_sql(PREFIX, Dialect.ORACLE)
    duck = money_trail_tripwire_sql(PREFIX, Dialect.DUCKDB)
    assert orc.rstrip(";").endswith("FROM DUAL")
    assert "FROM DUAL" not in pg and "FROM DUAL" not in duck
    for stmt in (pg, orc, duck):
        # The cast input must be DATA-DEPENDENT (EXISTS-driven CASE):
        # PG constant-folds a constant failing cast at plan time and
        # would fail every HEALTHY refresh (verified empirically).
        assert "CASE WHEN EXISTS" in stmt
        assert f"depth >= {MONEY_TRAIL_DEPTH_CAP}" in stmt


# -- Behavioral (real emitter + real refresh, DuckDB) -------------------------


def test_root_reachable_cycle_trips_loudly_instead_of_hanging() -> None:
    """The divergence class: C1 carries TWO parent rows — one to the
    root R, one into the C1<->C2 cycle. Pre-guard this walk never
    terminated; now it walks to the cap and the tripwire errors with
    the diagnosis in the message."""
    conn = _fresh_db()
    rows = _transfer("R", "A", "B", None)
    rows += _transfer("C1", "B", "C", "R")
    rows.append(_leg("C1-x", "B", -50, "C1", "C2"))  # the second parent claim
    rows += _transfer("C2", "C", "D", "C1")
    _insert(conn, rows)
    with pytest.raises(Exception, match="money_trail_depth_cap_exceeded"):
        _refresh(conn)


def test_pure_cycle_terminates_and_stays_silent() -> None:
    """The silent-omission class, documented + accepted at DS.3.1: a
    cycle with no root anchor never enters the walk. Refresh succeeds;
    the cycle members contribute no edges; healthy roots are intact."""
    conn = _fresh_db()
    rows = _transfer("R", "A", "B", None)
    rows += _transfer("P", "X", "Y", "Q")
    rows += _transfer("Q", "Y", "X", "P")
    _insert(conn, rows)
    _refresh(conn)  # must not raise
    trail = conn.execute(
        f"SELECT DISTINCT transfer_id FROM {PREFIX}_inv_money_trail_edges",
    ).fetchall()
    assert {t for (t,) in trail} == {"R"}


def test_healthy_chain_under_cap_is_untouched() -> None:
    conn = _fresh_db()
    rows = _transfer("R", "A", "B", None)
    rows += _transfer("S", "B", "C", "R")
    rows += _transfer("T", "C", "D", "S")
    _insert(conn, rows)
    _refresh(conn)  # must not raise
    depths = conn.execute(
        f"SELECT transfer_id, depth FROM {PREFIX}_inv_money_trail_edges "
        f"ORDER BY depth",
    ).fetchall()
    assert [(t, d) for t, d in depths] == [("R", 0), ("S", 1), ("T", 2)]
