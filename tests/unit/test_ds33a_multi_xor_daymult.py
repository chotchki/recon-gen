"""DS.3.3a — multi_xor day-multiplication fix (red-first).

The DS.0 spike found 4,914/27,000 exhaustive cells diverging from the
docstring spec, 2,268 of them production-shaped FALSE POSITIVES: a
parent that fired exactly ONE child correctly, but whose own legs post
across midnight, read as ('overlap', child_count=2, 'X,X') — because
``fired_children_distinct`` carried ``pf.business_day`` in its
DISTINCT, multiplying the count by |distinct parent posting days|.
Invisible to every generator (all plant at a single anchor_day); KAT
MX4 pins the law side (names count ONCE); these witnesses pin the SQL.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb
import yaml

from recon_gen.common.db import execute_script
from recon_gen.common.l2.config_table import emit_config_populate_sql
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.serializer import serialize_l2
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect

SPEC = Path(__file__).parent.parent / "l2" / "spec_example.yaml"
PREFIX = "spec_example"

TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name",
    "template_name", "origin", "metadata", "supersedes",
)

# spec_example's rail-parent multi-XOR chain (DS.0 spike §1):
PARENT_RAIL = "BulkAccrualSettlement"
CHILD_A = "BulkAccrualSettleACH"
CHILD_B = "BulkAccrualSettleWire"

BEFORE_MIDNIGHT = dt.datetime(2030, 1, 1, 23, 50, 0)
AFTER_MIDNIGHT = dt.datetime(2030, 1, 2, 0, 10, 0)


def _leg(id_: str, tid: str, rail: str, posting: dt.datetime,
         *, ptid: str | None = None, amount: int = -100) -> tuple[object, ...]:
    direction = "Debit" if amount < 0 else "Credit"
    return (
        id_, "acct-mx", "acct-mx", "CustomerSubledger", "internal",
        "CustomerLedger", amount, direction, "Posted", posting, tid, ptid,
        rail, None, "InternalInitiated", None, None,
    )


def _mx_rows(legs: list[tuple[object, ...]]) -> list[tuple[object, ...]]:
    conn = duckdb.connect()
    instance = load_instance(SPEC)
    execute_script(conn, emit_schema(instance, prefix=PREFIX, dialect=Dialect.DUCKDB),
                   dialect=Dialect.DUCKDB)
    # The REAL serialized L2 — multi_xor reads its chain declarations
    # through v_config_chain_children (BS.5); an empty l2_json makes
    # the detector silently see no chains and every assertion vacuous.
    l2_json = json.dumps(yaml.safe_load(serialize_l2(instance)), separators=(",", ":"))
    execute_script(conn, emit_config_populate_sql(
        prefix=PREFIX, cfg_json="{}",
        l2_json=l2_json,
        as_of=dt.datetime(2030, 1, 3), dialect=Dialect.DUCKDB,
    ), dialect=Dialect.DUCKDB)
    ph = ", ".join("?" for _ in TX_COLS)
    conn.executemany(
        f"INSERT INTO {PREFIX}_transactions ({', '.join(TX_COLS)}) VALUES ({ph})",
        legs,
    )
    execute_script(conn, refresh_matviews_sql(instance, prefix=PREFIX, dialect=Dialect.DUCKDB),
                   dialect=Dialect.DUCKDB)
    return conn.execute(
        f"SELECT child_count, fired_children, disagreement_kind, business_day "
        f"FROM {PREFIX}_multi_xor_violation "
        f"WHERE parent_transfer_id = 't-p'",
    ).fetchall()


def test_midnight_straddling_parent_with_one_child_is_clean() -> None:
    """THE born-red witness (KAT MX4's SQL twin): parent legs at 23:50
    and 00:10, exactly one child fires — names count ONCE, no row.
    Pre-fix this read ('overlap', child_count=2, 'ACH,ACH')."""
    rows = _mx_rows([
        _leg("lp1", "t-p", PARENT_RAIL, BEFORE_MIDNIGHT),
        _leg("lp2", "t-p", PARENT_RAIL, AFTER_MIDNIGHT),
        _leg("lc", "t-c1", CHILD_A, AFTER_MIDNIGHT, ptid="t-p", amount=100),
    ])
    assert rows == [], rows


def test_midnight_straddling_parent_with_no_children_still_alarms() -> None:
    """'missed' survives the fix: multi-day parent, zero children ->
    one row, count 0, business_day = the FIRST posting day."""
    rows = _mx_rows([
        _leg("lp1", "t-p", PARENT_RAIL, BEFORE_MIDNIGHT),
        _leg("lp2", "t-p", PARENT_RAIL, AFTER_MIDNIGHT),
    ])
    assert len(rows) == 1, rows
    count, fired, kind, day = rows[0]
    assert (count, fired, kind) == (0, "", "missed")
    assert day == dt.datetime(2030, 1, 1)


def test_midnight_straddling_parent_with_two_children_counts_two() -> None:
    """'overlap' counts NAMES: two distinct children under a two-day
    parent is count 2 — not 4."""
    rows = _mx_rows([
        _leg("lp1", "t-p", PARENT_RAIL, BEFORE_MIDNIGHT),
        _leg("lp2", "t-p", PARENT_RAIL, AFTER_MIDNIGHT),
        _leg("lc1", "t-c1", CHILD_A, AFTER_MIDNIGHT, ptid="t-p", amount=100),
        _leg("lc2", "t-c2", CHILD_B, AFTER_MIDNIGHT, ptid="t-p", amount=100),
    ])
    assert len(rows) == 1, rows
    count, fired, kind, _ = rows[0]
    assert count == 2 and kind == "overlap"
    assert sorted(str(fired).split(",")) == [CHILD_A, CHILD_B]


def test_single_day_parent_unchanged() -> None:
    """The ordinary case is untouched: one day, one child, clean."""
    rows = _mx_rows([
        _leg("lp1", "t-p", PARENT_RAIL, BEFORE_MIDNIGHT),
        _leg("lc", "t-c1", CHILD_A, BEFORE_MIDNIGHT, ptid="t-p", amount=100),
    ])
    assert rows == [], rows
