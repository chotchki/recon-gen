"""DN.3 — sparse-account carry-forward for the Daily Statement
opening_balance KPI.

Plants a DuckDB fixture with sparse ``current_daily_balances`` rows
(balance rows on days 1, 3, 7 — gaps on 2/4/5/6) and runs the
production ``build_daily_statement_summary_dataset`` SQL against it
to assert:

1. Picked day = 5 (no balance row of its own) → opening_balance
   returns day 3's balance, NOT NULL. The DN.3 correlated subquery
   over ``current_daily_balances`` with ``business_day_start < picked
   ORDER BY ... DESC LIMIT 1`` gives the most-recent prior emitted
   close, regardless of date gap.

2. Picked day = 1 (account's first emit, no prior balance) →
   opening_balance returns 0 (the COALESCE in the dataset SQL fires),
   NOT NULL. The KPI renders as ``$0.00`` rather than blank — the
   "no prior" edge case lands in the dollar zero state, ready for an
   optional ``(no prior)`` visual-layer badge per dn_0_running_balance.md.

3. Picked day = 3 (HAS a balance row of its own) → opening_balance
   returns day 1's balance (NOT day 3's). The strict ``<`` guard
   prevents the picked day's own end-of-day from leaking into the
   opening — matches the prior matview's LAG semantic exactly.

The matview ``<prefix>_daily_statement_summary`` is planted with
its own row for the picked day (the outer SELECT still narrows on
matview.business_day_start = picked). For days with no matview row,
the outer SELECT returns 0 rows — but that's a separate code path
(the full sparse-matview fix would also need to remove the outer
day-equality narrow, which is outside DN.3's scope per the design
lock).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from decimal import Decimal
from typing import TYPE_CHECKING

import duckdb
import pytest

from recon_gen.apps.l1_dashboard.datasets import (
    build_daily_statement_summary_dataset,
)
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config
from tests.e2e._drivers.base import query_db_via_cfg

if TYPE_CHECKING:
    from recon_gen.common.config import Config


_PREFIX = "pfx"


def _dataset_sql() -> str:
    """Lift the production daily-statement-summary SQL from the
    DataSet factory so the test reads exactly what the dashboard
    reads. ``_PREFIX`` matches the planted DuckDB tables.
    """
    cfg = make_test_config(
        db_dialect=Dialect.DUCKDB,
        db_table_prefix=_PREFIX,
    )
    ds = build_daily_statement_summary_dataset(cfg, default_l2_instance())
    physical = next(iter(ds.PhysicalTableMap.values()))
    assert physical.CustomSql is not None
    sql = physical.CustomSql.SqlQuery
    assert sql is not None
    return sql


@pytest.fixture
def sparse_account_duckdb() -> Iterator["Config"]:
    """DuckDB with planted ``current_daily_balances`` + matching
    ``daily_statement_summary`` rows.

    Plant strategy:

    - One account ``acc-1`` named ``Account One`` (matches the
      ``account_display`` shape ``"<name> (<id>)"`` the dataset
      WHERE clause compares against).
    - ``current_daily_balances`` emits on day-1 ($100), day-3 ($300),
      day-7 ($700). Days 2/4/5/6 have NO row (sparse-ETL shape).
    - ``daily_statement_summary`` has one row per balance-emit day
      with matching shape so the outer SELECT narrows successfully
      for picked days 1, 3, 7. The ``opening_balance`` column on
      the matview is INTENTIONALLY set to a sentinel ``-999_99`` cents
      so any test reading the matview's pre-computed opening (the
      pre-DN.3 path) would surface as ``-$999.99`` — the DN.3
      correlated-subquery path bypasses that column and reads from
      ``current_daily_balances`` directly.
    """
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    conn = duckdb.connect(path)
    conn.execute(
        f"CREATE TABLE {_PREFIX}_current_daily_balances ("
        "  entry INTEGER, account_id TEXT, account_name TEXT,"
        "  account_role TEXT, account_parent_role TEXT,"
        "  account_scope TEXT, expected_eod_balance INTEGER,"
        "  business_day_start TIMESTAMP, business_day_end TIMESTAMP,"
        "  money INTEGER, metadata TEXT, supersedes TEXT"
        ")"
    )
    # Day-1: $100 EOD. Day-3: $300 EOD. Day-7: $700 EOD. Gaps on
    # 2/4/5/6 — sparse ETL.
    conn.executemany(
        f"INSERT INTO {_PREFIX}_current_daily_balances VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "acc-1", "Account One", "dda", None, "internal", None,
             "2026-01-01 00:00:00", "2026-01-01 23:59:59",
             100_00, None, None),
            (1, "acc-1", "Account One", "dda", None, "internal", None,
             "2026-01-03 00:00:00", "2026-01-03 23:59:59",
             300_00, None, None),
            (1, "acc-1", "Account One", "dda", None, "internal", None,
             "2026-01-07 00:00:00", "2026-01-07 23:59:59",
             700_00, None, None),
        ],
    )
    # daily_statement_summary — minimal projection matching the
    # production matview's column list (only the columns the
    # production SQL projects need to exist). opening_balance is
    # pinned to -99_999 cents so a regression that reads the matview
    # column instead of the DN.3 subquery surfaces as -$999.99.
    conn.execute(
        f"CREATE TABLE {_PREFIX}_daily_statement_summary ("
        "  account_id TEXT, account_name TEXT, account_role TEXT,"
        "  account_parent_role TEXT, account_scope TEXT,"
        "  business_day_start TIMESTAMP, business_day_end TIMESTAMP,"
        "  opening_balance INTEGER, total_debits INTEGER,"
        "  total_credits INTEGER, net_flow INTEGER, leg_count INTEGER,"
        "  closing_balance_stored INTEGER,"
        "  closing_balance_recomputed INTEGER, drift INTEGER"
        ")"
    )
    conn.executemany(
        f"INSERT INTO {_PREFIX}_daily_statement_summary VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # Day-1 (account's first emit) — net_flow $100 so closing
            # = opening + net = 0 + 100 = $100. Matview opening_balance
            # pinned to sentinel ``-999_99`` cents — the DN.3 path
            # ignores it.
            ("acc-1", "Account One", "dda", None, "internal",
             "2026-01-01 00:00:00", "2026-01-01 23:59:59",
             -99_999, 0, 100_00, 100_00, 1, 100_00, 100_00, 0),
            # Day-3 — opening should be $100 (day-1's close); planted
            # net_flow $200 so closing = $300. Matview opening pinned
            # to sentinel.
            ("acc-1", "Account One", "dda", None, "internal",
             "2026-01-03 00:00:00", "2026-01-03 23:59:59",
             -99_999, 0, 200_00, 200_00, 1, 300_00, 300_00, 0),
            # Day-5 — gap day, but plant a synthetic matview row so
            # the outer SELECT can resolve the picked day (the
            # production matview would emit a carry-forward row via
            # effective_balances; this fixture short-circuits that
            # spine and plants the carry row directly). closing_stored
            # is the carried day-3 close = $300; net_flow 0. Opening
            # should come back as $300 (day-3's close) via DN.3.
            ("acc-1", "Account One", "dda", None, "internal",
             "2026-01-05 00:00:00", "2026-01-05 23:59:59",
             -99_999, 0, 0, 0, 0, 300_00, 300_00, 0),
            # Day-7 — opening = $300 (day-3's close); net_flow $400.
            ("acc-1", "Account One", "dda", None, "internal",
             "2026-01-07 00:00:00", "2026-01-07 23:59:59",
             -99_999, 0, 400_00, 400_00, 1, 700_00, 700_00, 0),
        ],
    )
    conn.commit()
    conn.close()
    cfg = make_test_config(
        db_dialect=Dialect.DUCKDB,
        db_url=path,
        db_table_prefix=_PREFIX,
    )
    try:
        yield cfg
    finally:
        os.unlink(path)


def _opening_for(cfg: "Config", account: str, day: str) -> Decimal:
    sql = _dataset_sql()
    rows = query_db_via_cfg(
        cfg, sql,
        binds={
            "param_pL1DsAccount": account,
            "param_pL1DsBalanceDate": day,
        },
    )
    assert len(rows) == 1, (
        f"expected exactly one summary row for {account!r} on {day!r}, "
        f"got {len(rows)}: {rows!r}"
    )
    return Decimal(str(rows[0]["opening_balance"]))


def test_dn3_carry_forward_when_picked_day_has_no_balance_row(
    sparse_account_duckdb: "Config",
) -> None:
    """Picked day = 5 (no row in current_daily_balances) →
    opening_balance returns day-3's $300, NOT NULL or sentinel.
    """
    opening = _opening_for(
        sparse_account_duckdb, "Account One (acc-1)", "2026-01-05",
    )
    assert opening == Decimal("300.00"), (
        f"DN.3 carry-forward failed: expected $300.00 (day-3 close), "
        f"got {opening!r}"
    )


def test_dn3_no_prior_balance_renders_zero_not_null(
    sparse_account_duckdb: "Config",
) -> None:
    """Picked day = 1 (account's first emit, no prior row) →
    opening_balance = 0 via the dataset's COALESCE, NOT NULL.
    Matches the dn_0_running_balance.md edge case: render as $0.00 so
    the running-balance arithmetic still composes.
    """
    opening = _opening_for(
        sparse_account_duckdb, "Account One (acc-1)", "2026-01-01",
    )
    assert opening == Decimal("0"), (
        f"DN.3 no-prior-balance edge case failed: expected $0.00, "
        f"got {opening!r}"
    )


def test_dn3_strict_less_than_excludes_picked_days_own_close(
    sparse_account_duckdb: "Config",
) -> None:
    """Picked day = 3 (has its own balance row) → opening_balance
    returns day-1's $100 (the prior-emit close), NOT day-3's $300.
    Verifies the strict ``<`` guard in the subquery — opening is the
    prior emit's EOD, never the picked day's own EOD.
    """
    opening = _opening_for(
        sparse_account_duckdb, "Account One (acc-1)", "2026-01-03",
    )
    assert opening == Decimal("100.00"), (
        f"DN.3 strict-< failed: expected $100.00 (day-1 close, not "
        f"day-3's own close), got {opening!r}"
    )
