"""DN.5 — running-balance correctness + 4-way agreement gates.

DN.1 added the ``running_balance`` window-function column to the Daily
Statement per-leg dataset; DN.4 mirrored it onto the audit's
``DailyStatementTransaction`` + ``_query_daily_statement_walks``.
DN-followup (2026-06-16): the running balance is ANCHORED to the opening
balance — the actual account balance after each posting — not the day's
postings summed from zero. This module proves the BEHAVIOR (the SQL
produces the right sequence), not just the emitted-SQL shape (the
json-tier ``test_daily_statement_transactions_projects_running_balance``
already pins the dialect-invariant fragments across PG / Oracle / DuckDB).

Gates here (all DuckDB-executed — the one dialect we can run without a
container; the cross-dialect SQL-emit shape is pinned at the json tier):

  (i)   **Python-accumulate equivalence** — the window-function
        ``running_balance`` sequence equals ``opening + a Python
        running-sum`` (``itertools.accumulate``) over the same legs in
        ``(posting, id)`` order. This is the direct-DB ≡ dataset-SQL
        leg of the `[[project_audit_dashboard_agreement]]` contract.
  (ii)  **Multi-account isolation** — the defensive
        ``PARTITION BY account_id`` keeps two accounts' running sums
        independent even though the WHERE narrows to one at render time
        (so a multi-account fixture still gives the right per-account
        sequence, per `[[feedback_production_honest_invariants]]`).
  (iii) **Closing arithmetic agreement** —
        ``running_balance(last_leg) == closing_balance_recomputed``
        (opening-anchored: the last leg lands ON the day's
        posting-implied closing). A disagreement here is a real
        arithmetic bug — the window function and the matview's
        ``opening + credits - debits`` would diverge.
  (iv)  **Audit-PDF parity** — ``_query_daily_statement_walks`` produces
        the SAME ``running_balance`` sequence as the dashboard dataset
        SQL for the same account-day (PDF ≡ dashboards leg of the 4-way
        gate).

Sparse-account carry-forward (DN.3) is proven by its own module
``test_dn3_sparse_account_opening_balance.py``; (iii) here ties the
running-balance closing to the same opening_balance the carry-forward
fix feeds, closing the loop end-to-end.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from itertools import accumulate
from typing import TYPE_CHECKING

import duckdb
import pytest

from recon_gen.apps.l1_dashboard.datasets import (
    build_daily_statement_transactions_dataset,
)
from recon_gen.cli.audit import _query_daily_statement_walks
from recon_gen.common.as_of_frame import AsOfFrame
from recon_gen.common.intervals import DateInterval
from recon_gen.common.l2 import default_l2_instance
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config
from tests.e2e._drivers.base import query_db_via_cfg

if TYPE_CHECKING:
    from recon_gen.common.config import Config


_PREFIX = "pfx"
_DAY = date(2026, 1, 5)
_DAY_ISO = _DAY.isoformat()
_PRIOR_ISO = (_DAY - timedelta(days=1)).isoformat()

# Account A — five legs on the picked day, in posting order.
# (id, posting_HH:MM:SS, signed cents, direction)
# DS.3.3 — the fixture plants NON-Posted legs (the 4-way's historical
# blind spot: all-Posted fixtures meant the four copies' status filters
# could disagree forever without a test firing). The money law: only
# Posted legs move the running balance; Pending / Failed / the
# deterministically-random unknown status DISPLAY but don't move it.
from recon_gen.common.env_keys import RECON_GEN_FUZZ_SEED as _FUZZ_SEED_KEY
from recon_gen.common.l2.primitives import POSTED_STATUS

_DS33_UNKNOWN_STATUS = "Zz" + __import__("hashlib").sha256(
    f"ds33-{_FUZZ_SEED_KEY.get_or_none() or '0'}".encode()
).hexdigest()[:6]
_ACCT_A_LEGS: list[tuple[int, str, int, str, str]] = [
    (101, "08:00:00", 250_00, "Credit", "Posted"),
    (102, "09:30:00", -75_00, "Debit", "Posted"),
    (110, "10:10:00", -999_00, "Debit", "Pending"),
    (103, "11:15:00", 500_00, "Credit", "Posted"),
    (111, "12:20:00", 777_00, "Credit", "Failed"),
    (104, "14:45:00", -120_00, "Debit", "Posted"),
    (112, "15:10:00", -555_00, "Debit", _DS33_UNKNOWN_STATUS),
    (105, "16:00:00", -30_00, "Debit", "Posted"),
]
# Account B — three legs same day; proves PARTITION isolation.
_ACCT_B_LEGS: list[tuple[int, str, int, str, str]] = [
    (201, "08:15:00", 90_00, "Credit", "Posted"),
    (202, "12:00:00", -40_00, "Debit", "Posted"),
    (203, "15:30:00", 1000_00, "Credit", "Posted"),
]

# Opening balances (cents) carried into the picked day for each account.
# DN-followup: these are the prior-emit EOD ``money`` the running balance
# anchors to (seeded into current_daily_balances on the prior day) AND the
# daily_statement_summary opening_balance — both must agree so gate (iii)
# (running lands on closing_recomputed) holds.
_ACCT_A_OPENING = 1_000_00
_ACCT_B_OPENING = 50_00


def _signed_dollars(cents: int) -> Decimal:
    return Decimal(cents) / Decimal(100)


def _expected_running(
    legs: list[tuple[int, str, int, str, str]], opening_cents: int,
) -> list[Decimal]:
    """Python running-sum over the legs in their fixture order (which is
    already (posting, id) order), ANCHORED to the opening balance and
    accumulating POSTED legs only (DS.3.3 money law) — a Pending /
    Failed / unknown-status row displays the unchanged balance. One
    value per displayed row, so the sequences zip 1:1 with the dataset
    + audit projections."""
    return [
        _signed_dollars(opening_cents + c)
        for c in accumulate(
            leg[2] if leg[4] == POSTED_STATUS else 0 for leg in legs
        )
    ]


@pytest.fixture
def two_account_duckdb() -> Iterator["Config"]:
    """DuckDB with ``current_transactions`` + ``current_daily_balances``
    + ``daily_statement_summary`` + ``drift`` planted for two accounts on
    the picked day.

    The transactions feed the dataset SQL + the audit walk's per-leg
    query; current_daily_balances feeds the opening-balance correlated
    subquery the running balance anchors to (DN-followup); the summary
    feeds the closing-arithmetic gate + the audit walk's KPI read; the
    drift table feeds the audit walk's account-enumeration (the walk emits
    for every drifted (account, day) OR every singleton parent). Both
    accounts are planted with a non-zero drift row so they enumerate
    regardless of singleton-set.
    """
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    conn = duckdb.connect(path)

    # current_transactions — the matview the dataset SQL + the audit
    # walk's per-leg query both read. Only the columns both queries
    # project need to exist.
    conn.execute(
        f"CREATE TABLE {_PREFIX}_current_transactions ("
        "  id TEXT, transfer_id TEXT, account_id TEXT, account_name TEXT,"
        "  account_scope TEXT, rail_name TEXT, amount_money BIGINT,"
        "  amount_direction TEXT, status TEXT, origin TEXT,"
        "  posting TIMESTAMP, metadata TEXT"
        ")"
    )
    rows: list[tuple[object, ...]] = []
    for acct_id, acct_name, legs in (
        ("acc-A", "Account A", _ACCT_A_LEGS),
        ("acc-B", "Account B", _ACCT_B_LEGS),
    ):
        for leg_id, hms, signed_cents, direction, status in legs:
            rows.append((
                str(leg_id), f"xfer-{leg_id}", acct_id, acct_name,
                "internal", "SomeRail", signed_cents, direction,
                status, "organic", f"{_DAY_ISO} {hms}", None,
            ))
    conn.executemany(
        f"INSERT INTO {_PREFIX}_current_transactions VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )

    # current_daily_balances — the opening-balance source. DN-followup
    # (2026-06-16): the running balance anchors to the prior emit's EOD
    # ``money`` via the dataset/audit correlated subquery, so seed a
    # prior-day balance row per account = that account's opening.
    conn.execute(
        f"CREATE TABLE {_PREFIX}_current_daily_balances ("
        "  account_id TEXT, business_day_start TIMESTAMP, money BIGINT"
        ")"
    )
    conn.executemany(
        f"INSERT INTO {_PREFIX}_current_daily_balances VALUES (?,?,?)",
        [
            ("acc-A", f"{_PRIOR_ISO} 00:00:00", _ACCT_A_OPENING),
            ("acc-B", f"{_PRIOR_ISO} 00:00:00", _ACCT_B_OPENING),
        ],
    )

    # daily_statement_summary — KPIs the closing gate + the audit walk
    # read. opening_balance / closing_balance_recomputed wired so the
    # arithmetic gate (iii) holds: closing_recomputed = opening +
    # credits - debits = opening + sum(signed legs).
    conn.execute(
        f"CREATE TABLE {_PREFIX}_daily_statement_summary ("
        "  account_id TEXT, account_name TEXT, account_role TEXT,"
        "  account_parent_role TEXT, account_scope TEXT,"
        "  business_day_start TIMESTAMP, business_day_end TIMESTAMP,"
        "  opening_balance BIGINT, total_debits BIGINT,"
        "  total_credits BIGINT, net_flow BIGINT, leg_count BIGINT,"
        "  closing_balance_stored BIGINT,"
        "  closing_balance_recomputed BIGINT, drift BIGINT"
        ")"
    )
    summary_rows: list[tuple[object, ...]] = []
    for acct_id, acct_name, legs, opening in (
        ("acc-A", "Account A", _ACCT_A_LEGS, _ACCT_A_OPENING),
        ("acc-B", "Account B", _ACCT_B_LEGS, _ACCT_B_OPENING),
    ):
        posted = [leg for leg in legs if leg[4] == POSTED_STATUS]
        credits = sum(c for _, _, c, _, _ in posted if c > 0)
        debits = -sum(c for _, _, c, _, _ in posted if c < 0)  # stored positive
        net = credits - debits
        closing_recomputed = opening + net
        summary_rows.append((
            acct_id, acct_name, "dda", None, "internal",
            f"{_DAY_ISO} 00:00:00", f"{_DAY_ISO} 23:59:59",
            opening, debits, credits, net, len(posted),
            closing_recomputed, closing_recomputed, 0,
        ))
    conn.executemany(
        f"INSERT INTO {_PREFIX}_daily_statement_summary VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        summary_rows,
    )

    # drift — the audit walk enumerates every (account, day) here. Plant
    # both accounts with a non-zero drift so they always enumerate.
    conn.execute(
        f"CREATE TABLE {_PREFIX}_drift ("
        "  account_id TEXT, business_day_start TIMESTAMP,"
        "  business_day_end TIMESTAMP, drift BIGINT"
        ")"
    )
    conn.executemany(
        f"INSERT INTO {_PREFIX}_drift VALUES (?,?,?,?)",
        [
            ("acc-A", f"{_DAY_ISO} 00:00:00", f"{_DAY_ISO} 23:59:59", 1),
            ("acc-B", f"{_DAY_ISO} 00:00:00", f"{_DAY_ISO} 23:59:59", 1),
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


def _dataset_running_balance(
    cfg: "Config", account_display: str,
) -> list[Decimal]:
    """Run the production Daily Statement transactions dataset SQL and
    return the ``running_balance`` column in display (posting, id) order
    for ``account_display`` on the picked day."""
    ds = build_daily_statement_transactions_dataset(cfg, default_l2_instance())
    sql = ds.sql
    rows = query_db_via_cfg(
        cfg, sql,
        binds={
            "param_pL1DsAccount": account_display,
            "param_pL1DsBalanceDate": _DAY_ISO,
        },
    )
    # The dataset SQL does not ORDER BY (QS/App2 sort at the visual
    # layer), so sort by (posting, id) here to match the window's order.
    rows.sort(key=lambda r: (str(r["posting"]), int(str(r["transaction_id"]))))
    return [Decimal(str(r["running_balance"])) for r in rows]


# (i) + (ii) — Python-accumulate equivalence, multi-account isolation ----


def test_dn5_window_running_balance_equals_python_accumulate(
    two_account_duckdb: "Config",
) -> None:
    """(i) The dataset SQL's window-function ``running_balance`` sequence
    equals ``opening + a Python accumulate`` over the same legs for
    account A."""
    got = _dataset_running_balance(two_account_duckdb, "Account A (acc-A)")
    expected = _expected_running(_ACCT_A_LEGS, _ACCT_A_OPENING)
    assert got == expected, (
        f"running_balance window-function sequence diverged from the "
        f"opening-anchored Python running-sum.\n"
        f"  SQL:    {got}\n  Python: {expected}"
    )


def test_dn5_partition_isolates_accounts(
    two_account_duckdb: "Config",
) -> None:
    """(ii) The defensive ``PARTITION BY account_id`` keeps account B's
    running sum independent of account A's even though both share the
    picked day. Account B's first leg must start its own partition at B's
    own opening + first leg ($50.00 + $90.00 = $140.00), not continue A's
    accumulated total."""
    got = _dataset_running_balance(two_account_duckdb, "Account B (acc-B)")
    expected = _expected_running(_ACCT_B_LEGS, _ACCT_B_OPENING)
    assert got == expected, (
        f"PARTITION BY account_id failed to isolate account B: "
        f"\n  SQL:    {got}\n  Python: {expected}"
    )
    # First leg starts a fresh partition (not A's running total) — B's
    # own opening ($50.00) + its first leg ($90.00) = $140.00.
    assert got[0] == Decimal("140.00"), (
        f"account B's first running_balance must be its own opening + "
        f"first leg ($140.00), got {got[0]!r} — partition leaked from "
        f"account A"
    )


# (iii) — closing arithmetic agreement -----------------------------------


def _matview_closing_and_opening(
    cfg: "Config", account_id: str,
) -> tuple[Decimal, Decimal]:
    """Read ``closing_balance_recomputed`` + ``opening_balance`` straight
    from the planted ``daily_statement_summary`` matview (cents → dollars)
    for the picked day. These are the SAME columns the Daily Statement
    KPI cards + the audit walk read — gate (iii) compares the window
    function's running sum against THIS, not a Python recompute, so a
    window-vs-matview drift surfaces."""
    rows = query_db_via_cfg(
        cfg,
        f"SELECT (closing_balance_recomputed / 100.0) AS closing,"
        f"       (opening_balance / 100.0) AS opening"
        f"  FROM {_PREFIX}_daily_statement_summary"
        f" WHERE account_id = '{account_id}'",
    )
    assert len(rows) == 1, f"expected one summary row for {account_id}"
    return Decimal(str(rows[0]["closing"])), Decimal(str(rows[0]["opening"]))


@pytest.mark.parametrize(
    ("account_id", "display"),
    [
        ("acc-A", "Account A (acc-A)"),
        ("acc-B", "Account B (acc-B)"),
    ],
)
def test_dn5_closing_arithmetic_agreement(
    two_account_duckdb: "Config",
    account_id: str,
    display: str,
) -> None:
    """(iii) ``running_balance(last_leg) == closing_balance_recomputed``
    (DN-followup 2026-06-16: the running balance is opening-anchored, so
    the last leg lands ON the day's posting-implied closing, not the
    delta). closing + opening come from the ``daily_statement_summary``
    matview (the KPI source); the running balance from the window
    function. A disagreement means the window-function running sum and the
    matview's ``opening + credits - debits`` diverge — a real arithmetic
    bug, not a test artifact."""
    running = _dataset_running_balance(two_account_duckdb, display)
    last_running = running[-1]
    closing, opening = _matview_closing_and_opening(
        two_account_duckdb, account_id,
    )

    assert last_running == closing, (
        f"closing-arithmetic disagreement for {display}: "
        f"running_balance(last_leg)={last_running} but "
        f"closing_recomputed = {closing} "
        f"(matview closing={closing}, opening={opening})"
    )


# (iv) — audit-PDF parity ------------------------------------------------


def test_dn5_audit_walk_running_balance_matches_dataset(
    two_account_duckdb: "Config",
) -> None:
    """(iv) ``_query_daily_statement_walks`` (the audit/PDF path) produces
    the SAME ``running_balance`` sequence as the dashboard dataset SQL for
    each account-day. This is the PDF ≡ dashboards leg of the 4-way
    `[[project_audit_dashboard_agreement]]` gate, exercised at unit time."""
    frame = AsOfFrame(
        as_of=_DAY,
        window=DateInterval.single_day(_DAY),
    )
    walks = _query_daily_statement_walks(
        two_account_duckdb, default_l2_instance(),
        frame, singleton_ids=set(),
    )
    assert walks is not None, (
        "_query_daily_statement_walks returned None — cfg.db.url unset?"
    )
    by_account = {w.account_id: w for w in walks}
    assert {"acc-A", "acc-B"} <= set(by_account), (
        f"both planted accounts should enumerate a walk; got "
        f"{sorted(by_account)}"
    )

    for acct_id, display, legs, opening in (
        ("acc-A", "Account A (acc-A)", _ACCT_A_LEGS, _ACCT_A_OPENING),
        ("acc-B", "Account B (acc-B)", _ACCT_B_LEGS, _ACCT_B_OPENING),
    ):
        walk = by_account[acct_id]
        # Walk transactions are ordered (posting, id) by the audit query.
        audit_running = [t.running_balance for t in walk.transactions]
        dataset_running = _dataset_running_balance(two_account_duckdb, display)
        assert audit_running == dataset_running, (
            f"audit-walk running_balance diverged from the dataset SQL "
            f"for {acct_id}:\n  audit:   {audit_running}"
            f"\n  dataset: {dataset_running}"
        )
        # And both equal the opening-anchored Python ground truth (closes
        # the 4-way loop: audit ≡ dataset ≡ direct-DB-accumulate).
        assert audit_running == _expected_running(legs, opening), (
            f"audit-walk running_balance diverged from the Python "
            f"running-sum for {acct_id}: {audit_running}"
        )
