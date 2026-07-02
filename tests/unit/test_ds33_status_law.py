"""DS.3.3 — the status law, aligned across production SQL (red-first).

Operator-decided law: money moves on Posted ONLY; a rail firing counts
on Posted or Pending (an in-flight leg still represents a rail
choice); anything else — Failed or a value nobody has seen before —
lands on the failed side in BOTH families. The EXISTENCE predicate is
pinned with it: an all-void transfer gets no expected cell (invisible,
not alarmed — the accepted consequence from the DS.0 spike review).

Born-red witnesses against the pre-fix SQL: the unknown-status leg
used to COUNT as a firing (``<> 'Failed'``), a Zq9x-only transfer used
to raise a missed-firing alarm, and net_flow moved on Pending legs.

The unknown status value is deterministically random — derived from
RECON_GEN_FUZZ_SEED — so no detector can quietly hardcode it back into
a pass (operator: "a test case that sets a deterministically random
value so the tests can't drift").
"""
from __future__ import annotations

import datetime as dt
import hashlib
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
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name",
    "template_name", "origin", "metadata", "supersedes",
)

# spec_example's declared XOR group (loader-verified in the DS.0 spike):
XOR_TEMPLATE = "SettlementTimingCycle"
XOR_MEMBERS = ("SettlementAuto", "SettlementStandard")
XOR_NON_MEMBER = "SettlementSlow"

POSTING = dt.datetime(2030, 1, 1, 12, 0, 0)


def fuzz_status() -> str:
    """A deterministically-random unknown status: seeded from
    RECON_GEN_FUZZ_SEED so it's stable per configuration but no
    detector can hardcode it."""
    from recon_gen.common.env_keys import RECON_GEN_FUZZ_SEED
    seed = RECON_GEN_FUZZ_SEED.get_or_none() or "0"
    return "Zz" + hashlib.sha256(f"ds33-{seed}".encode()).hexdigest()[:6]


def _leg(id_: str, status: str, tid: str, rail: str, template: str | None,
         *, amount: int = -100, ptid: str | None = None) -> tuple[object, ...]:
    direction = "Debit" if amount < 0 else "Credit"
    return (
        id_, "acct-s", "acct-s", "CustomerSubledger", "internal", "CustomerLedger",
        amount, direction, status, POSTING, tid, ptid, rail,
        template, "InternalInitiated", None, None,
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


def _xor_rows(conn: duckdb.DuckDBPyConnection, tid: str) -> list[tuple[object, ...]]:
    return conn.execute(
        f"SELECT firing_count, fired_rails FROM {PREFIX}_xor_group_violation "
        f"WHERE transfer_id = '{tid}'",
    ).fetchall()


def test_unknown_status_lands_on_the_failed_side() -> None:
    """KAT C2/C3's SQL twin: Posted member + unknown-status member =
    count 1, no violation (the old `<> 'Failed'` counted the unknown
    leg and alarmed an overlap)."""
    conn = _db_with([
        _leg("l1", "Posted", "t-a", XOR_MEMBERS[0], XOR_TEMPLATE),
        _leg("l2", fuzz_status(), "t-a", XOR_MEMBERS[1], XOR_TEMPLATE),
    ])
    assert _xor_rows(conn, "t-a") == []


def test_pending_keeps_its_in_flight_standing() -> None:
    """Posted + Pending members = a real overlap (count 2)."""
    conn = _db_with([
        _leg("l1", "Posted", "t-b", XOR_MEMBERS[0], XOR_TEMPLATE),
        _leg("l2", "Pending", "t-b", XOR_MEMBERS[1], XOR_TEMPLATE),
    ])
    rows = _xor_rows(conn, "t-b")
    assert len(rows) == 1 and rows[0][0] == 2, rows


def test_all_void_transfer_is_invisible_not_alarmed() -> None:
    """The existence pin (KAT C4's SQL twin): a transfer whose only
    legs are Failed + unknown gets NO expected cell — the old SQL
    raised a firing_count=0 'missed' alarm off the unknown leg's
    existence."""
    conn = _db_with([
        _leg("l1", "Failed", "t-c", XOR_MEMBERS[0], XOR_TEMPLATE),
        _leg("l2", fuzz_status(), "t-c", XOR_NON_MEMBER, XOR_TEMPLATE),
    ])
    assert _xor_rows(conn, "t-c") == []


def test_missed_firing_still_alarms_with_a_live_anchor() -> None:
    """The conservative alarm survives where it should: a POSTED
    non-member leg anchors the transfer's existence, member count 0
    -> missed."""
    conn = _db_with([
        _leg("l1", "Posted", "t-d", XOR_NON_MEMBER, XOR_TEMPLATE),
    ])
    rows = _xor_rows(conn, "t-d")
    assert len(rows) == 1 and rows[0][0] == 0, rows


def test_chain_parent_ignores_unknown_status_claims() -> None:
    """A parent claim on an unknown-status leg is void metadata — one
    real (Posted) claim remains, no disagreement. The old SQL counted
    the unknown leg's claim and alarmed."""
    conn = _db_with([
        _leg("l1", "Posted", "t-e", "r1", "InternalTransferCycle", ptid="p-1"),
        _leg("l2", fuzz_status(), "t-e", "r2", "InternalTransferCycle", ptid="p-2",
             amount=100),
    ])
    rows = conn.execute(
        f"SELECT distinct_parent_count FROM {PREFIX}_chain_parent_disagreement "
        f"WHERE transfer_id = 't-e'",
    ).fetchall()
    assert rows == [], rows


def test_net_flow_moves_on_posted_only() -> None:
    """The money law on daily_statement_summary: a Pending leg shows
    in the statement but does not move net_flow."""
    conn = _db_with([
        _leg("l1", "Posted", "t-f", "r1", None, amount=-300),
        _leg("l2", "Pending", "t-f2", "r1", None, amount=-500),
        _leg("l3", fuzz_status(), "t-f3", "r1", None, amount=-700),
    ])
    # A balance emit puts the account on the day spine so
    # daily_statement_summary materializes the row.
    conn.execute(
        f"INSERT INTO {PREFIX}_daily_balances (account_id, account_name, "
        f"account_role, account_scope, account_parent_role, "
        f"business_day_start, business_day_end, money) VALUES "
        f"('acct-s', 'acct-s', 'CustomerSubledger', 'internal', "
        f"'CustomerLedger', TIMESTAMP '2030-01-01 00:00:00', "
        f"TIMESTAMP '2030-01-01 17:00:00', -300)",
    )
    instance = load_instance(SPEC)
    execute_script(conn, refresh_matviews_sql(instance, prefix=PREFIX, dialect=Dialect.DUCKDB),
                   dialect=Dialect.DUCKDB)
    rows = conn.execute(
        f"SELECT net_flow FROM {PREFIX}_daily_statement_summary "
        f"WHERE account_id = 'acct-s'",
    ).fetchall()
    assert rows and all(int(nf) == -300 for (nf,) in rows), rows
