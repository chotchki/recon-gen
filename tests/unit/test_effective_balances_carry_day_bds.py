"""Regression: ``effective_balances`` carry-day ``business_day_start``.

CL.5's ``effective_balances`` matview fills sparse-day gaps with
carry-forward rows. The bug (caught 2026-06-08 by an external test,
fixed same day): on a carry day, ``business_day_start`` was stamped
from the **prior emit's** timestamp rather than the carry day's own
``calendar_day`` shifted by the account's offset. Every carry day
following an emit landed on that emit's date, collapsing N carry days
onto one ``(account_id, business_day_start)`` key and breaking
uniqueness on this matview + every downstream consumer (drift,
overdraft, daily_statement_summary).

This test plants the smallest fixture that exposes the shape — two
accounts, one dense (provides the spine), one sparse with a 3-day
emit gap — and asserts both the structural invariant (uniqueness)
and the specific bug shape (carry-day BDS lands on the carry day's
own calendar date, not the prior emit's). The bug-shape assertion is
the one that pins the root cause; the uniqueness assertion is the
guard the upstream report asked for and would catch any future
regression by a totally different code path.

In-memory DuckDB, no seed runner, no AWS — fast (~100ms).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from recon_gen.common.db import execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
)
_PREFIX = "ebt"
_DIALECT = Dialect.DUCKDB

# Account offset: 21:00 of each calendar day. Picked non-zero so the
# fix's per-account TOD-propagation logic gets exercised (a 00:00
# offset would mask any "we forgot to add the offset" regression).
_OFFSET_HOURS = 21
# Sparse emit pattern: day 1 + day 4 → spine fills carry days 2 + 3.
_DENSE_DAYS = (1, 2, 3, 4)
_SPARSE_DAYS = (1, 4)


def _ts(day: int) -> datetime:
    """A test-pinned BDS timestamp: 2030-01-<day> at the account offset."""
    return datetime(2030, 1, day, _OFFSET_HOURS, 0, 0)


def _bds_end(day: int) -> datetime:
    """The BDS-end timestamp 24h after BDS-start, matching the seed shape."""
    return datetime(2030, 1, day + 1, _OFFSET_HOURS, 0, 0)


def _emit_daily_balance(
    conn: duckdb.DuckDBPyConnection,
    *,
    account_id: str,
    account_name: str,
    day: int,
    money_cents: int,
) -> None:
    """Insert one synthetic ``<prefix>_daily_balances`` row.

    Mirrors the seed shape just enough to populate
    ``current_daily_balances`` (and through it the spine + matview).
    No transactions, no metadata — the matview only reads the daily-
    balance columns this test cares about.
    """
    cur = conn.cursor()
    cur.execute(
        f"""
        INSERT INTO {_PREFIX}_daily_balances (
            account_id, account_name, account_role, account_scope,
            account_parent_role, expected_eod_balance,
            business_day_start, business_day_end, money
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id, account_name,
            "CustomerSubledger", "internal", "CustomerLedger",
            money_cents,
            _ts(day), _bds_end(day),
            money_cents,
        ),
    )
    conn.commit()


@pytest.fixture
def conn_with_sparse_gap() -> Iterator[duckdb.DuckDBPyConnection]:
    """In-memory DuckDB, schema + two accounts: dense + sparse with gap.

    Dense account emits daily 01..04 so the spine (which discovers
    calendar days from ``current_daily_balances``) covers all four
    days. Sparse account emits 01 + 04 only — the matview must fill
    02 + 03 as carry days landing on THOSE dates.
    """
    conn = duckdb.connect(":memory:")
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()

    for day in _DENSE_DAYS:
        _emit_daily_balance(
            conn, account_id="acct-dense", account_name="Dense Spine",
            day=day, money_cents=1_000_000,
        )
    for day in _SPARSE_DAYS:
        _emit_daily_balance(
            conn, account_id="acct-sparse", account_name="Sparse Test Subject",
            day=day, money_cents=2_000_000,
        )

    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    yield conn
    conn.close()


def test_effective_balances_is_unique_on_account_and_business_day_start(
    conn_with_sparse_gap: duckdb.DuckDBPyConnection,
) -> None:
    """The structural invariant the matview's own design assumes.

    A duplicate ``(account_id, business_day_start)`` key means the
    matview emitted multiple rows for the same logical position —
    breaks every downstream JOIN's row-cardinality assumption (drift
    duplicates by 6.7× in the bundled-fixture repro; overdraft
    + daily_statement_summary similarly inflated).

    This assertion would have caught the original bug at any
    fixture size + any cadence, with no need for downstream impact
    instrumentation.
    """
    cur = conn_with_sparse_gap.cursor()
    cur.execute(
        f"""
        SELECT count(*) AS rows,
               count(*) - count(DISTINCT (account_id, business_day_start))
                   AS dup_rows,
               count(*) FILTER (WHERE business_day_start IS NULL)
                   AS null_bds_rows
        FROM {_PREFIX}_effective_balances
        """
    )
    row = cur.fetchone()
    assert row is not None
    total, dups, null_bds = row
    assert dups == 0, (
        f"effective_balances broke uniqueness on "
        f"(account_id, business_day_start): {dups} dup rows out of "
        f"{total} total."
    )
    assert null_bds == 0, (
        f"effective_balances emitted {null_bds} rows with NULL "
        f"business_day_start — leading-spine-day rows should be "
        f"filtered out at source."
    )


def test_effective_balances_carry_days_land_on_their_own_calendar_day(
    conn_with_sparse_gap: duckdb.DuckDBPyConnection,
) -> None:
    """The specific bug shape: carry days must NOT inherit the prior
    emit's date.

    Sparse account emits 2030-01-01 + 2030-01-04 at offset 21:00. The
    matview must produce 4 rows for this account with
    ``business_day_start`` timestamps on 2030-01-01..04 — one per
    calendar day. Pre-fix produced 4 rows ALL stamped 2030-01-01 21:00
    (carry days collapsed onto the prior emit's date).
    """
    cur = conn_with_sparse_gap.cursor()
    cur.execute(
        f"""
        SELECT business_day_start, source, emitted_money, effective_money
        FROM {_PREFIX}_effective_balances
        WHERE account_id = 'acct-sparse'
        ORDER BY business_day_start
        """
    )
    rows = cur.fetchall()
    expected = [
        (_ts(1), "emitted", 2_000_000, 2_000_000),
        (_ts(2), "carried", None,     2_000_000),
        (_ts(3), "carried", None,     2_000_000),
        (_ts(4), "emitted", 2_000_000, 2_000_000),
    ]
    assert rows == expected, (
        "carry-day business_day_start values must land on the carry "
        "day's own calendar date (at the account's offset), not the "
        "prior emit's date."
    )


def test_daily_statement_summary_no_emit_and_carry_share_same_key(
    conn_with_sparse_gap: duckdb.DuckDBPyConnection,
) -> None:
    """Downstream proof: the bug-shape collision in
    ``daily_statement_summary`` (an ``emitted`` row + a
    ``carried_with_activity_gap`` row sharing the same
    ``(account_id, business_day_start)`` key) is impossible when
    effective_balances itself is uniquely keyed. The check the
    upstream bug report's "84 of 84" query expressed.
    """
    cur = conn_with_sparse_gap.cursor()
    cur.execute(
        f"""
        SELECT count(*)
        FROM {_PREFIX}_daily_statement_summary g
        WHERE g.closing_balance_source = 'carried_with_activity_gap'
          AND EXISTS (
              SELECT 1 FROM {_PREFIX}_daily_statement_summary e
              WHERE e.account_id = g.account_id
                AND e.business_day_start = g.business_day_start
                AND e.closing_balance_source = 'emitted'
          )
        """
    )
    row = cur.fetchone()
    assert row is not None
    collisions = row[0]
    assert collisions == 0, (
        f"{collisions} daily_statement_summary 'carried_with_"
        f"activity_gap' rows share a (account, business_day_start) "
        f"key with an 'emitted' row — symptomatic of the CL.5 "
        f"carry-day BDS bug regressing."
    )
