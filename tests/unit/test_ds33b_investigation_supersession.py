"""DS.3.3b — Investigation reads go through supersession (red-first).

Operator ruling at DS.0 sign-off: anomaly + money_trail reading raw
``{p}_transactions`` is a BUG — a superseded (corrected) leg still
contributed pair-flows and trail edges, so the Investigation surfaces
showed money movements the institution had explicitly re-stated away.
Both switch to the Current* projection; the audit PDF alone keeps
raw-row access (reproducibility of the correction history is its job).

Born-red witnesses: a leg superseded to a DIFFERENT counterparty must
vanish from the trail and from the pair universe — pre-fix both the
stale and the corrected rows contributed.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb

from recon_gen.common.db import execute_script
from recon_gen.common.l2.config_table import emit_config_populate_sql
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect

SPEC = Path(__file__).parent.parent / "l2" / "spec_example.yaml"
PREFIX = "spec_example"

TX_COLS = (
    "id", "entry", "account_id", "account_name", "account_role",
    "account_scope", "account_parent_role", "amount_money",
    "amount_direction", "status", "posting", "transfer_id",
    "transfer_parent_id", "rail_name", "template_name", "origin",
    "metadata", "supersedes",
)

POSTING = dt.datetime(2030, 1, 1, 12, 0, 0)


def _leg(id_: str, entry: int, acct: str, amount: int, tid: str,
         *, supersedes: str | None = None) -> tuple[object, ...]:
    direction = "Debit" if amount < 0 else "Credit"
    return (
        id_, entry, acct, acct, "CustomerSubledger", "internal",
        "CustomerLedger", amount, direction, "Posted", POSTING, tid,
        None, "TrailRail", None, "InternalInitiated", None, supersedes,
    )


def _db_with(legs: list[tuple[object, ...]]) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    instance = load_instance(SPEC)
    execute_script(conn, emit_schema(instance, prefix=PREFIX, dialect=Dialect.DUCKDB),
                   dialect=Dialect.DUCKDB)
    execute_script(conn, emit_config_populate_sql(
        prefix=PREFIX, cfg_json="{}",
        l2_json='{"rails": [], "limit_schedules": []}',
        as_of=dt.datetime(2030, 1, 2), dialect=Dialect.DUCKDB,
    ), dialect=Dialect.DUCKDB)
    ph = ", ".join("?" for _ in TX_COLS)
    conn.executemany(
        f"INSERT INTO {PREFIX}_transactions ({', '.join(TX_COLS)}) VALUES ({ph})",
        legs,
    )
    execute_script(conn, refresh_matviews_sql(instance, prefix=PREFIX, dialect=Dialect.DUCKDB),
                   dialect=Dialect.DUCKDB)
    return conn


def _superseded_target_state() -> list[tuple[object, ...]]:
    """Transfer R: src leg A -100; tgt leg originally +100 to B-STALE
    (entry 1), corrected to B-REAL (entry 2, TechnicalCorrection)."""
    return [
        _leg("r-src", 1, "A", -100, "R"),
        _leg("r-tgt", 1, "B-STALE", 100, "R"),
        _leg("r-tgt", 2, "B-REAL", 100, "R", supersedes="TechnicalCorrection"),
    ]


def test_money_trail_edges_follow_supersession() -> None:
    """The corrected leg's edge is the ONLY edge — pre-fix the raw read
    emitted A->B-STALE alongside A->B-REAL."""
    conn = _db_with(_superseded_target_state())
    edges = conn.execute(
        f"SELECT source_account_id, target_account_id "
        f"FROM {PREFIX}_inv_money_trail_edges WHERE root_transfer_id = 'R'",
    ).fetchall()
    assert edges == [("A", "B-REAL")], edges


def test_anomaly_pair_universe_follows_supersession() -> None:
    """The stale recipient pair vanishes from the anomaly matview's
    pair universe — pre-fix the raw read kept (A, B-STALE) flowing."""
    conn = _db_with(_superseded_target_state())
    pairs = conn.execute(
        f"SELECT DISTINCT sender_account_id, recipient_account_id "
        f"FROM {PREFIX}_inv_pair_rolling_anomalies",
    ).fetchall()
    assert ("A", "B-STALE") not in pairs, pairs
    assert ("A", "B-REAL") in pairs, pairs
