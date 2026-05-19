"""Locked SQL snapshots for the per-invariant audit queries (U.8.c).

The audit PDF queries the L1 invariant matviews + base tables directly
via inline f-strings inside ``cli/audit/__init__.py``. There's no SQL
builder helper to import — the SQL only exists at execute time, woven
into the query function.

To snapshot it without refactoring the production code, this module
patches ``connect_demo_db`` with a recording stub that captures every
``cursor.execute(sql)`` call. Each test calls one ``_query_X`` function
against a tiny fake cfg/instance + a fixed period, then byte-asserts
the captured SQL against a locked literal. Any unintentional change
to the audit SQL — even whitespace — fails the test.

Pattern mirrors the dataset-contract tests in ``tests/json/`` (locked
expectation, single source of truth = the production code's actual
output). When the SQL legitimately changes, the assertion message
renders the diff and the operator pastes the new value.

Period under test is ``(2030-01-01, 2030-01-07)`` — chosen so the
inclusive-end → exclusive-end conversion (`< end + 1 day`) yields
``DATE '2030-01-08'`` and the digit shape stays distinct from any
realistic plant date. Prefix under test is ``ut`` (short,
unambiguous, not a real fixture prefix) so the SQL strings stay
legible.

The locked SQL captures the **Postgres dialect** form — the
``_FakeCfg`` defaults to ``Dialect.POSTGRES``, and ``date_literal``
emits ``DATE 'YYYY-MM-DD'`` for both Postgres and Oracle (the SQL-
standard form, byte-identical between those two dialects). The
SQLite arm of ``date_literal`` emits a plain ``'YYYY-MM-DD'`` string
literal — covered by the e2e SQLite cell at
``tests/e2e/test_audit_pdf_sqlite.py`` (X.3.g.2), not by these
locked strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest

from recon_gen.cli.audit import (
    _query_drift_violations,
    _query_executive_summary,
    _query_limit_breach_violations,
    _query_overdraft_violations,
    _query_stuck_pending_violations,
    _query_stuck_unbundled_violations,
    _query_supersession,
)
from recon_gen.common.sql.dialect import Dialect


# --- Fakes -------------------------------------------------------------------


@dataclass
class _FakeCfg:
    """Minimal cfg surface the query functions touch.

    They check ``demo_database_url`` (truthy → run; None → early
    return), ``dialect`` (the latter dispatches the per-dialect
    date literal in ``date_literal``), and ``db_table_prefix``
    (Z.C — used as the matview name prefix; was previously read
    off the L2 instance). The patched ``connect_demo_db``
    ignores ``demo_database_url`` so any truthy string suffices; the
    ``dialect`` value, in contrast, IS load-bearing — it picks which
    SQL form ``date_literal`` returns. Default is ``POSTGRES`` so the
    locked snapshots match the PG/Oracle form. ``db_table_prefix``
    defaults to ``ut`` to match the locked SQL string literals.
    """
    demo_database_url: str = "postgresql://stub/stub"
    dialect: Dialect = Dialect.POSTGRES
    db_table_prefix: str = "ut"


@dataclass
class _FakeInstance:
    """Minimal L2-instance surface: just the prefix attribute."""
    instance: str = "ut"


def _count_top_level_commas(s: str) -> int:
    """Count commas at parenthesis-depth 0. Used to compute the
    arity of a SELECT-list without counting commas inside function
    calls like ``COALESCE(SUM(x), 0)``."""
    depth = 0
    n = 0
    for c in s:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            n += 1
    return n


def _select_clause_top_level(sql: str) -> str:
    """Return the SELECT clause through the matching top-level FROM,
    so subquery FROMs (depth > 0) don't truncate prematurely."""
    depth = 0
    upper = sql.upper()
    i = 0
    while i < len(sql):
        c = sql[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and upper[i:i + 6] == " FROM ":
            return sql[:i]
        i += 1
    return sql


class _RecordingCursor:
    """Captures every ``execute(sql)`` call into a flat list.

    Returns empty results for ``fetchone()`` / ``fetchall()`` so the
    query function completes its body without DB-shaped data. The
    SQL string is what we're asserting on — the row payload is moot.
    """

    def __init__(self, captured: list[str]) -> None:
        self._captured = captured

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> None:
        self._captured.append(sql)

    def fetchone(self) -> tuple[int, ...]:
        # Inspect the last-recorded SQL and shape the row to match the
        # caller's tuple unpack arity. Counts top-level commas in the
        # SELECT-list (depth-0 only — commas inside ``COALESCE(...,0)``
        # don't count). Subqueries in FROM are excluded by truncating
        # at the first top-level ` FROM `.
        last_sql = self._captured[-1] if self._captured else ""
        select_clause = _select_clause_top_level(last_sql)
        n_cols = _count_top_level_commas(select_clause) + 1
        return tuple([0] * n_cols)

    def fetchall(self) -> list[Any]:
        return []


class _RecordingConn:
    """Connection stub: hands out the recording cursor, no-op close."""

    def __init__(self, cursor: _RecordingCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _RecordingCursor:
        return self._cursor

    def close(self) -> None:
        pass


@pytest.fixture
def captured_sql() -> list[str]:
    """Per-test mutable list the recording cursor appends into."""
    return []


@pytest.fixture
def patched_connect(captured_sql, monkeypatch):
    """Patch ``connect_demo_db`` with the recording stub.

    The query functions do a lazy ``from recon_gen.common.db
    import connect_demo_db`` inside the function body, so we patch
    the source module — that's where the lookup resolves.
    """
    cursor = _RecordingCursor(captured_sql)
    conn = _RecordingConn(cursor)
    with patch(
        "recon_gen.common.db.connect_demo_db",
        return_value=conn,
    ):
        yield


_PERIOD: tuple[date, date] = (date(2030, 1, 1), date(2030, 1, 7))
_INSTANCE = _FakeInstance(instance="ut")
_CFG = _FakeCfg()


# --- Per-invariant SQL snapshots --------------------------------------------


# Each constant below is the EXACT SQL the production code emits today.
# Update by re-running the corresponding test, copying the actual
# value from the failure diff, and pasting it here. The prefix `ut`
# and the period `(2030-01-01, 2030-01-07)` are the test fixtures;
# `2030-01-08` is the exclusive-end (period[1] + 1 day).


_DRIFT_SQL = (
    "SELECT account_id, account_name, account_role,"
    "       account_parent_role, business_day_end,"
    "       stored_balance, computed_balance, drift"
    "  FROM ut_drift"
    " WHERE business_day_start >= DATE '2030-01-01'"
    "   AND business_day_start < DATE '2030-01-08'"
    " ORDER BY business_day_end DESC, ABS(drift) DESC, account_id"
)

_OVERDRAFT_SQL = (
    "SELECT account_id, account_name, account_role,"
    "       account_parent_role, business_day_end,"
    "       stored_balance"
    "  FROM ut_overdraft"
    " WHERE business_day_start >= DATE '2030-01-01'"
    "   AND business_day_start < DATE '2030-01-08'"
    " ORDER BY business_day_end DESC,"
    "          ABS(stored_balance) DESC, account_id"
)

_LIMIT_BREACH_SQL = (
    "SELECT account_id, account_name, account_role,"
    "       account_parent_role, business_day,"
    "       rail_name, direction, outbound_total, cap"
    "  FROM ut_limit_breach"
    " WHERE business_day >= DATE '2030-01-01'"
    "   AND business_day < DATE '2030-01-08'"
    " ORDER BY business_day DESC,"
    "          (outbound_total - cap) DESC, account_id"
)

_STUCK_PENDING_SQL = (
    "SELECT account_id, account_name, account_role,"
    "       account_parent_role, transaction_id,"
    "       rail_name, posting, amount_money,"
    "       age_seconds, max_pending_age_seconds"
    "  FROM ut_stuck_pending"
    " ORDER BY age_seconds DESC, account_id"
)

_STUCK_UNBUNDLED_SQL = (
    "SELECT account_id, account_name, account_role,"
    "       account_parent_role, transaction_id,"
    "       rail_name, posting, amount_money,"
    "       age_seconds, max_unbundled_age_seconds"
    "  FROM ut_stuck_unbundled"
    " ORDER BY age_seconds DESC, account_id"
)

_SUPERSESSION_AGGREGATES_TXNS_SQL = (
    "SELECT supersedes, COUNT(*) AS total,"
    " SUM(CASE WHEN posting >= DATE '2030-01-01'"
    "          AND posting < DATE '2030-01-08'"
    "          THEN 1 ELSE 0 END) AS new_in_period"
    " FROM ut_transactions"
    " WHERE supersedes IS NOT NULL"
    " GROUP BY supersedes"
    " ORDER BY supersedes"
)

_SUPERSESSION_AGGREGATES_DAILY_SQL = (
    "SELECT supersedes, COUNT(*) AS total,"
    " SUM(CASE WHEN business_day_start >= DATE '2030-01-01'"
    "          AND business_day_start < DATE '2030-01-08'"
    "          THEN 1 ELSE 0 END) AS new_in_period"
    " FROM ut_daily_balances"
    " WHERE supersedes IS NOT NULL"
    " GROUP BY supersedes"
    " ORDER BY supersedes"
)

_SUPERSESSION_TXN_DETAILS_SQL = (
    "SELECT id, supersedes, account_id, account_name,"
    "       posting, amount_money"
    "  FROM ut_transactions"
    " WHERE supersedes IS NOT NULL"
    "   AND posting >= DATE '2030-01-01'"
    "   AND posting < DATE '2030-01-08'"
    " ORDER BY posting DESC, id"
)

_SUPERSESSION_DAILY_DETAILS_SQL = (
    "SELECT account_id, account_name, business_day_start,"
    "       supersedes, money"
    "  FROM ut_daily_balances"
    " WHERE supersedes IS NOT NULL"
    "   AND business_day_start >= DATE '2030-01-01'"
    "   AND business_day_start < DATE '2030-01-08'"
    " ORDER BY business_day_start DESC, account_id"
)


def _diff_msg(label: str, want: str, got: str) -> str:
    """Render a copy-pasteable failure message when SQL drifted."""
    return (
        f"\n{label} SQL drift detected. The production code now emits:\n"
        f"-----8<-----\n{got}\n-----8<-----\n"
        f"If intentional, paste this string into tests/audit/test_sql.py "
        f"as the new locked value. Expected was:\n"
        f"-----8<-----\n{want}\n-----8<-----"
    )


def test_drift_query_sql_locked(captured_sql, patched_connect):
    _query_drift_violations(_CFG, _INSTANCE, _PERIOD)
    assert len(captured_sql) == 1
    assert captured_sql[0] == _DRIFT_SQL, _diff_msg(
        "drift", _DRIFT_SQL, captured_sql[0],
    )


def test_overdraft_query_sql_locked(captured_sql, patched_connect):
    _query_overdraft_violations(_CFG, _INSTANCE, _PERIOD)
    assert len(captured_sql) == 1
    assert captured_sql[0] == _OVERDRAFT_SQL, _diff_msg(
        "overdraft", _OVERDRAFT_SQL, captured_sql[0],
    )


def test_limit_breach_query_sql_locked(captured_sql, patched_connect):
    _query_limit_breach_violations(_CFG, _INSTANCE, _PERIOD)
    assert len(captured_sql) == 1
    assert captured_sql[0] == _LIMIT_BREACH_SQL, _diff_msg(
        "limit_breach", _LIMIT_BREACH_SQL, captured_sql[0],
    )


def test_stuck_pending_query_sql_locked(captured_sql, patched_connect):
    _query_stuck_pending_violations(_CFG, _INSTANCE)
    assert len(captured_sql) == 1
    assert captured_sql[0] == _STUCK_PENDING_SQL, _diff_msg(
        "stuck_pending", _STUCK_PENDING_SQL, captured_sql[0],
    )


def test_stuck_unbundled_query_sql_locked(captured_sql, patched_connect):
    _query_stuck_unbundled_violations(_CFG, _INSTANCE)
    assert len(captured_sql) == 1
    assert captured_sql[0] == _STUCK_UNBUNDLED_SQL, _diff_msg(
        "stuck_unbundled", _STUCK_UNBUNDLED_SQL, captured_sql[0],
    )


def test_supersession_query_sql_locked(captured_sql, patched_connect):
    _query_supersession(_CFG, _INSTANCE, _PERIOD)
    # Four queries in fixed order: aggregates × 2 base tables, then
    # txn details, then daily-balance details.
    assert len(captured_sql) == 4, (
        f"expected 4 supersession queries, got {len(captured_sql)}: "
        f"{captured_sql}"
    )
    expected = [
        _SUPERSESSION_AGGREGATES_TXNS_SQL,
        _SUPERSESSION_AGGREGATES_DAILY_SQL,
        _SUPERSESSION_TXN_DETAILS_SQL,
        _SUPERSESSION_DAILY_DETAILS_SQL,
    ]
    labels = [
        "supersession aggregates (transactions)",
        "supersession aggregates (daily_balances)",
        "supersession transaction details",
        "supersession daily-balance details",
    ]
    for label, want, got in zip(labels, expected, captured_sql):
        assert got == want, _diff_msg(label, want, got)


def test_executive_summary_query_sql_locked(captured_sql, patched_connect):
    """Executive summary unrolls into multiple queries: 2 volume +
    6 invariant counts (drift / ledger_drift / overdraft / limit_breach
    / stuck_pending / stuck_unbundled) + 2 supersession totals (one per
    base table). Asserts each one's SQL matches its locked value."""
    _query_executive_summary(_CFG, _INSTANCE, _PERIOD)
    assert len(captured_sql) == 10, (
        f"expected 10 executive-summary queries (2 volume + 6 "
        f"invariant counts + 2 supersession totals), got "
        f"{len(captured_sql)}: {captured_sql}"
    )
    expected = [
        # Volume: leg + transfer counts.
        (
            "SELECT COUNT(*),"
            " COUNT(DISTINCT transfer_id)"
            " FROM ut_transactions"
            " WHERE status = 'Posted'"
            "   AND posting >= DATE '2030-01-01'"
            "   AND posting < DATE '2030-01-08'"
        ),
        # Volume: gross + net dollars per-transfer aggregate.
        (
            "SELECT COALESCE(SUM(transfer_gross), 0),"
            " COALESCE(SUM(transfer_net), 0)"
            " FROM ("
            "   SELECT MAX(ABS(amount_money)) AS transfer_gross,"
            "          SUM(amount_money) AS transfer_net"
            "   FROM ut_transactions"
            "   WHERE status = 'Posted'"
            "     AND posting >= DATE '2030-01-01'"
            "     AND posting < DATE '2030-01-08'"
            "   GROUP BY transfer_id"
            " ) per_transfer"
        ),
        # Date-scoped exception counts.
        (
            "SELECT COUNT(*) FROM ut_drift"
            " WHERE business_day_start >= DATE '2030-01-01'"
            "   AND business_day_start < DATE '2030-01-08'"
        ),
        (
            "SELECT COUNT(*) FROM ut_ledger_drift"
            " WHERE business_day_start >= DATE '2030-01-01'"
            "   AND business_day_start < DATE '2030-01-08'"
        ),
        (
            "SELECT COUNT(*) FROM ut_overdraft"
            " WHERE business_day_start >= DATE '2030-01-01'"
            "   AND business_day_start < DATE '2030-01-08'"
        ),
        (
            "SELECT COUNT(*) FROM ut_limit_breach"
            " WHERE business_day >= DATE '2030-01-01'"
            "   AND business_day < DATE '2030-01-08'"
        ),
        # Current-state matview counts (no date filter).
        "SELECT COUNT(*) FROM ut_stuck_pending",
        "SELECT COUNT(*) FROM ut_stuck_unbundled",
        # Supersession totals across both base tables.
        (
            "SELECT COUNT(*) FROM ut_transactions"
            " WHERE supersedes IS NOT NULL"
        ),
        (
            "SELECT COUNT(*) FROM ut_daily_balances"
            " WHERE supersedes IS NOT NULL"
        ),
    ]
    for i, (want, got) in enumerate(zip(expected, captured_sql)):
        assert got == want, _diff_msg(
            f"executive summary query #{i + 1}", want, got,
        )


# --- Sanity guards -----------------------------------------------------------


def test_skeleton_mode_short_circuits_drift(captured_sql, patched_connect):
    """``demo_database_url=None`` returns None without touching the DB.

    Mirrors the audit's emit-vs-execute contract: skeleton mode must
    never attempt a connection. If this regresses, every audit
    skeleton-mode preview suddenly requires a live DB.
    """
    cfg = _FakeCfg(demo_database_url=None)  # type: ignore[arg-type]: _FakeCfg is a stand-in for Config in skeleton-mode tests
    result = _query_drift_violations(cfg, _INSTANCE, _PERIOD)
    assert result is None
    assert captured_sql == []


def test_skeleton_mode_short_circuits_supersession(
    captured_sql, patched_connect,
):
    """Same skeleton-mode short-circuit as drift — repeated for the
    multi-query function to confirm no SQL leaks before the cfg check."""
    cfg = _FakeCfg(demo_database_url=None)  # type: ignore[arg-type]: _FakeCfg is a stand-in for Config in skeleton-mode tests
    result = _query_supersession(cfg, _INSTANCE, _PERIOD)
    assert result is None
    assert captured_sql == []
