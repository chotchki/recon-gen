"""DS.3.2 — drift compares CARRIED days (red-first).

The E1b bounded proof showed today's drift matview CANNOT emit a
carried-day row: computed_subledger_balance is keyed on emit days, so
the INNER JOIN restricts drift to days the institution chose to report.
Operator-decided law (DS.0 finding 1): a balance entry is the source of
an account's balance UNTIL a newer entry supersedes it — every day,
emitted or carried, must reconcile.

Carried-day CUTOFF rule (operator, 2026-07-02): the last loaded
balance day's END carries forward — an account whose business day ends
17:00 keeps a 17:00 cutover on every quiet day until the next loaded
balance row. A leg posting after the carried cutover belongs to the
NEXT day's cell.

These tests were born RED against the pre-fix emitter (the E1c witness
produced zero carried-day rows) and pin the fix.
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
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name",
    "template_name", "origin", "metadata", "supersedes",
)
DB_COLS = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance", "business_day_start",
    "business_day_end", "money", "metadata",
)

ACCT = "acct-ds32"
# The institution's business day: starts at midnight, ends 17:00 — the
# operator's worked example. The carried cutover must stay 17:00.
DAY0_START = dt.datetime(2030, 1, 1, 0, 0, 0)
DAY0_END = dt.datetime(2030, 1, 1, 17, 0, 0)


def _leg(id_: str, amount: int, posting: dt.datetime) -> tuple[object, ...]:
    direction = "Debit" if amount < 0 else "Credit"
    return (
        id_, ACCT, ACCT, "CustomerSubledger", "internal", "CustomerLedger",
        amount, direction, "Posted", posting, f"t-{id_}", None, "TrailRail",
        None, "InternalInitiated", None, None,
    )


def _balance(day: dt.date, money: int) -> tuple[object, ...]:
    start = dt.datetime(day.year, day.month, day.day, 0, 0, 0)
    end = dt.datetime(day.year, day.month, day.day, 17, 0, 0)
    return (
        ACCT, ACCT, "CustomerSubledger", "internal", "CustomerLedger",
        None, start, end, money, None,
    )


def _db_with(legs: list[tuple[object, ...]], balances: list[tuple[object, ...]],
             *, anchor: dt.date) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect()
    instance = load_instance(SPEC)
    execute_script(conn, emit_schema(instance, prefix=PREFIX, dialect=Dialect.DUCKDB),
                   dialect=Dialect.DUCKDB)
    execute_script(conn, emit_config_populate_sql(
        prefix=PREFIX, cfg_json="{}",
        l2_json='{"rails": [], "limit_schedules": []}',
        as_of=dt.datetime.combine(anchor, dt.time(23, 0)), dialect=Dialect.DUCKDB,
    ), dialect=Dialect.DUCKDB)
    if legs:
        ph = ", ".join("?" for _ in TX_COLS)
        conn.executemany(
            f"INSERT INTO {PREFIX}_transactions ({', '.join(TX_COLS)}) VALUES ({ph})",
            legs,
        )
    if balances:
        ph = ", ".join("?" for _ in DB_COLS)
        conn.executemany(
            f"INSERT INTO {PREFIX}_daily_balances ({', '.join(DB_COLS)}) VALUES ({ph})",
            balances,
        )
    execute_script(conn, refresh_matviews_sql(instance, prefix=PREFIX, dialect=Dialect.DUCKDB),
                   dialect=Dialect.DUCKDB)
    return conn


def _drift_rows(conn: duckdb.DuckDBPyConnection) -> dict[dt.date, int]:
    rows = conn.execute(
        f"SELECT business_day_start, drift FROM {PREFIX}_drift "
        f"WHERE account_id = '{ACCT}'",
    ).fetchall()
    return {bds.date(): int(d) for bds, d in rows}


def test_e1c_witness_fires_on_the_carried_day() -> None:
    """THE born-red witness (KAT M3 / the E1c state): emit −200 on d0
    with a matching −200 Posted leg; a −100 Posted leg lands on d1 with
    NO emit; a second balance row on d3 keeps d1+d2 inside the spine.
    The carried claim (−200) vs evidence (−300) is +100 drift on d1 AND
    d2 — per-day firing, overdraft-consistent."""
    legs = [
        _leg("l0", -200, dt.datetime(2030, 1, 1, 12, 0)),
        _leg("l1", -100, dt.datetime(2030, 1, 2, 12, 0)),
    ]
    balances = [
        _balance(dt.date(2030, 1, 1), -200),
        _balance(dt.date(2030, 1, 4), -300),  # keeps the spine open through d3
    ]
    conn = _db_with(legs, balances, anchor=dt.date(2030, 1, 4))
    drift = _drift_rows(conn)
    assert drift.get(dt.date(2030, 1, 2)) == 100, (
        f"carried-day drift missing on d1: {drift}"
    )
    assert drift.get(dt.date(2030, 1, 3)) == 100, (
        f"carried-day drift missing on d2 (per-day firing): {drift}"
    )
    # d0 reconciles (claim −200 vs evidence −200); d3's emit −300
    # matches the cumulative evidence −300 — no rows.
    assert dt.date(2030, 1, 1) not in drift
    assert dt.date(2030, 1, 4) not in drift


def test_carried_cutover_keeps_the_emit_end_time() -> None:
    """The operator's 5pm rule: a leg posting at 18:00 on a carried day
    is AFTER the carried 17:00 cutover — it belongs to the NEXT day's
    cell. d1's cell must NOT count it; d2's cell must."""
    legs = [
        _leg("l0", -200, dt.datetime(2030, 1, 1, 12, 0)),
        _leg("l1", -100, dt.datetime(2030, 1, 2, 18, 0)),  # after 17:00 cutover
    ]
    balances = [
        _balance(dt.date(2030, 1, 1), -200),
        _balance(dt.date(2030, 1, 4), -300),
    ]
    conn = _db_with(legs, balances, anchor=dt.date(2030, 1, 4))
    drift = _drift_rows(conn)
    # d1 (cutover 17:00): the 18:00 leg is out of the cell — claim −200
    # vs evidence −200 → clean.
    assert dt.date(2030, 1, 2) not in drift, (
        f"post-cutover leg leaked into the carried day's cell: {drift}"
    )
    # d2: the 18:00-on-d1 leg is inside (posted before d2's 17:00) —
    # claim −200 vs evidence −300 → +100.
    assert drift.get(dt.date(2030, 1, 3)) == 100, drift


def test_emitted_days_unchanged() -> None:
    """The fix widens the row universe; emit-day semantics must not
    move. Same-day claim-vs-evidence mismatch fires exactly as before."""
    legs = [_leg("l0", -100, dt.datetime(2030, 1, 1, 12, 0))]
    balances = [_balance(dt.date(2030, 1, 1), -200)]
    conn = _db_with(legs, balances, anchor=dt.date(2030, 1, 1))
    drift = _drift_rows(conn)
    assert drift == {dt.date(2030, 1, 1): -100}
