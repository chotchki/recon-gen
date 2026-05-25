"""X.3.g.2 — Audit PDF query layer wired against SQLite.

The Audit PDF reconciliation report queries the L1 invariant matviews
(plus base tables for supersession) via inline SQL strings inside
``cli/audit/__init__.py``. Until X.3.g.2 those f-strings hardcoded the
SQL-standard ``DATE 'YYYY-MM-DD'`` literal form, which Postgres + Oracle
accept but SQLite rejects with ``OperationalError: no such column:
DATE`` (SQLite parses ``DATE`` as a column reference, not a type
keyword). X.3.g.2 routed the 8 literal sites (15 lines) through
``date_literal(value, dialect)`` from ``common/sql/dialect.py`` so
SQLite gets a plain ``'YYYY-MM-DD'`` text literal that compares
correctly against the TEXT-stored ISO dates SQLite uses. PG + Oracle
keep emitting ``DATE 'YYYY-MM-DD'`` byte-identically. The cell now
ticks in the PLAN.md X.2 multi-renderer test matrix:

  L2 Audit PDF dialect × SQLite — ✓ X.3.g.2

Why not ``CAST('YYYY-MM-DD' AS DATE)`` (which seems portable)? SQLite's
NUMERIC affinity coerces ``CAST('2030-01-01' AS DATE)`` to INTEGER 2030
(extracts the leading digits), then comparison against TEXT columns
follows SQLite's odd type-affinity rules and silently filters wrong.
The ``date_literal`` helper carries this rationale + the dialect
dispatch in one place.

This file mirrors the pattern from ``test_layer1_query_sqlite.py``: an
in-memory ``sqlite3`` connection seeded with the matview shapes the
audit query helpers expect, then each ``_query_*`` function is called
through a monkeypatched ``connect_demo_db`` that hands back that
connection. Assertions cover the SQL-execution claim (the rows come
back correctly shaped) — the PDF rendering layer is dialect-agnostic
and already covered by the PG / Oracle audit tests in ``tests/audit/``.

Lives under ``tests/audit/`` alongside the other audit tests so the
existing CI ``test`` job picks it up via pytest discovery. (Originally
landed under ``tests/e2e/`` but that directory's conftest gates on
``RECON_GEN_E2E=1``, which silently skipped the SQLite cells in CI —
moved here, and the X.3.g.1 Layer 1 SQLite file moved to
``tests/unit/`` for the same reason.)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

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
from recon_gen.common.intervals import DateInterval
from recon_gen.common.sql.dialect import Dialect


# --- Fakes -------------------------------------------------------------------


@dataclass
class _FakeCfg:
    """Minimal cfg surface the audit query helpers touch.

    They check ``demo_database_url`` (truthy → run the SQL; None →
    early-return None for skeleton mode), read ``dialect`` to pick
    the right ``date_literal`` SQL form, and read ``db_table_prefix``
    (Z.C — formerly read off the L2 instance). The patched
    ``connect_demo_db`` ignores ``demo_database_url`` so any truthy
    string suffices; the ``dialect`` is load-bearing — ``SQLITE``
    causes ``date_literal`` to emit ``'YYYY-MM-DD'`` (plain text),
    which is the only form SQLite accepts for date comparisons
    against TEXT-stored ISO dates. ``db_table_prefix`` defaults to
    ``ut`` to match the planted-schema table names.
    """
    demo_database_url: str = "sqlite:///:memory:"
    dialect: Dialect = Dialect.SQLITE
    db_table_prefix: str = "ut"


@dataclass
class _FakeInstance:
    """Minimal L2-instance surface — just the ``instance`` attribute
    that the query helpers use as the matview name prefix."""
    instance: str = "ut"


_PERIOD: DateInterval = DateInterval.closed(date(2030, 1, 1), date(2030, 1, 7))
_INSTANCE = _FakeInstance(instance="ut")
_CFG = _FakeCfg()


# --- Schema + seed -----------------------------------------------------------


def _create_audit_schema(conn: sqlite3.Connection) -> None:
    """Create the seven tables the audit query helpers SELECT from.

    Mirrors the relevant column shapes from the L1 invariant matviews
    (under the SQLite dialect's matview-as-table convention from
    X.3.b/c). Only the columns each ``_query_*`` function actually
    projects are declared — the production matviews carry more, but the
    audit queries select a fixed subset.

    Money columns are ``INTEGER`` (BIGINT cents per AO.1 foundation —
    the production schema migrated from DECIMAL dollars to BIGINT cents
    to kill SQLite REAL-backed float-dust drift). The audit query
    helpers project these to ``Decimal`` dollars at the cursor
    boundary via ``_cents_to_dollars`` — seed values below are in
    cents, assertions are in dollars.

    Naming uses the ``ut_`` prefix matched by ``_INSTANCE.instance``.
    """
    conn.executescript(
        """
        CREATE TABLE ut_drift (
            account_id TEXT NOT NULL,
            account_name TEXT,
            account_role TEXT,
            account_parent_role TEXT,
            business_day_start TEXT NOT NULL,
            business_day_end TEXT NOT NULL,
            stored_balance INTEGER NOT NULL,
            computed_balance INTEGER NOT NULL,
            drift INTEGER NOT NULL
        );
        CREATE TABLE ut_ledger_drift (
            account_id TEXT NOT NULL,
            business_day_start TEXT NOT NULL
        );
        CREATE TABLE ut_overdraft (
            account_id TEXT NOT NULL,
            account_name TEXT,
            account_role TEXT,
            account_parent_role TEXT,
            business_day_start TEXT NOT NULL,
            business_day_end TEXT NOT NULL,
            stored_balance INTEGER NOT NULL
        );
        CREATE TABLE ut_limit_breach (
            account_id TEXT NOT NULL,
            account_name TEXT,
            account_role TEXT,
            account_parent_role TEXT,
            business_day TEXT NOT NULL,
            rail_name TEXT,
            direction TEXT NOT NULL,
            outbound_total INTEGER NOT NULL,
            cap INTEGER NOT NULL
        );
        CREATE TABLE ut_stuck_pending (
            account_id TEXT NOT NULL,
            account_name TEXT,
            account_role TEXT,
            account_parent_role TEXT,
            transaction_id TEXT NOT NULL,
            rail_name TEXT,
            posting TEXT NOT NULL,
            amount_money INTEGER NOT NULL,
            age_seconds REAL NOT NULL,
            max_pending_age_seconds INTEGER NOT NULL
        );
        CREATE TABLE ut_stuck_unbundled (
            account_id TEXT NOT NULL,
            account_name TEXT,
            account_role TEXT,
            account_parent_role TEXT,
            transaction_id TEXT NOT NULL,
            rail_name TEXT,
            posting TEXT NOT NULL,
            amount_money INTEGER NOT NULL,
            age_seconds REAL NOT NULL,
            max_unbundled_age_seconds INTEGER NOT NULL
        );
        CREATE TABLE ut_transactions (
            id TEXT NOT NULL,
            transfer_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            account_name TEXT,
            posting TEXT NOT NULL,
            amount_money INTEGER NOT NULL,
            status TEXT NOT NULL,
            supersedes TEXT
        );
        CREATE TABLE ut_daily_balances (
            account_id TEXT NOT NULL,
            account_name TEXT,
            business_day_start TEXT NOT NULL,
            money INTEGER NOT NULL,
            supersedes TEXT
        );
        """
    )


def _seed_audit_data(conn: sqlite3.Connection) -> None:
    """Plant a minimal but representative violation per matview.

    Dates are inside the ``_PERIOD`` window where the helper is
    period-scoped (drift / overdraft / limit_breach / supersession
    details). ``stuck_*`` are current-state — no period filter — so any
    row at all suffices to prove the SELECT works.
    """
    cur = conn.cursor()
    # AO.1: money columns below are INTEGER cents to match the
    # production BIGINT cents schema (drift 25.00 → 2500 cents,
    # stored_balance -42.50 → -4250 cents, etc.). The audit query
    # helpers project cents → dollars at the cursor boundary, so
    # assertions stay in dollars (``Decimal("25")``).
    cur.executemany(
        "INSERT INTO ut_drift VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("acct-001", "Acct One", "dda", "DDAControl",
             "2030-01-03", "2030-01-03", 10000, 7500, 2500),
            ("acct-002", "Acct Two", "dda", "DDAControl",
             "2030-01-05", "2030-01-05", 5000, 6000, -1000),
        ],
    )
    cur.executemany(
        "INSERT INTO ut_ledger_drift (account_id, business_day_start) VALUES (?, ?)",
        [("acct-009", "2030-01-04")],
    )
    cur.executemany(
        "INSERT INTO ut_overdraft VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("acct-003", "Acct Three", "dda", "DDAControl",
             "2030-01-04", "2030-01-04", -4250),
        ],
    )
    cur.executemany(
        "INSERT INTO ut_limit_breach VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            # AB.1: 1 Outbound + 1 Inbound row prove the audit query
            # surfaces both directions.
            ("acct-004", "Acct Four", "dda", "DDAControl",
             "2030-01-02", "ach", "Outbound", 1_500_000, 1_000_000),
            ("acct-004", "Acct Four", "dda", "DDAControl",
             "2030-01-02", "ach", "Inbound", 2_500_000, 2_000_000),
        ],
    )
    cur.executemany(
        "INSERT INTO ut_stuck_pending VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("acct-005", "Acct Five", "dda", "DDAControl",
             "txn-001", "wire", "2029-12-15T10:00:00",
             12345, 432000.0, 86400),
        ],
    )
    cur.executemany(
        "INSERT INTO ut_stuck_unbundled VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("acct-006", "Acct Six", "dda", "DDAControl",
             "txn-002", "ach", "2029-12-20T11:30:00",
             6789, 259200.0, 86400),
        ],
    )
    # transactions: one in-period correcting entry + one out-of-period
    # original to exercise the date filter on the supersession details.
    cur.executemany(
        "INSERT INTO ut_transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("txn-100", "xfer-A", "acct-007", "Acct Seven",
             "2030-01-04T12:00:00", 25000, "Posted", "void_redo"),
            ("txn-099", "xfer-A", "acct-007", "Acct Seven",
             "2029-12-31T12:00:00", 10000, "Posted", None),
            ("txn-101", "xfer-B", "acct-008", "Acct Eight",
             "2030-01-06T09:00:00", 7500, "Posted", None),
        ],
    )
    cur.executemany(
        "INSERT INTO ut_daily_balances VALUES (?, ?, ?, ?, ?)",
        [
            ("acct-007", "Acct Seven", "2030-01-04", 25000, "void_redo"),
            ("acct-008", "Acct Eight", "2030-01-05", 7500, None),
        ],
    )
    conn.commit()


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    """In-memory SQLite seeded with the audit query helpers' input shapes."""
    conn = sqlite3.connect(":memory:")
    try:
        _create_audit_schema(conn)
        _seed_audit_data(conn)
        yield conn
    finally:
        conn.close()


@pytest.fixture
def patched_connect(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``connect_demo_db`` so the audit helpers receive the
    in-memory SQLite connection.

    The helpers do a lazy ``from recon_gen.common.db import
    connect_demo_db`` inside the function body; patch the source
    module so that lazy lookup resolves to the fake.

    Wraps the connection so ``conn.close()`` (called in each helper's
    ``finally:``) becomes a no-op — the fixture owns the lifecycle.
    """

    class _NoCloseConn:
        def __init__(self, real: sqlite3.Connection) -> None:
            self._real = real

        def cursor(self) -> Any:
            return self._real.cursor()

        def close(self) -> None:
            pass

    fake_conn = _NoCloseConn(db)
    monkeypatch.setattr(
        "recon_gen.common.db.connect_demo_db",
        lambda _cfg: fake_conn,
    )


# --- Tests -------------------------------------------------------------------


def test_drift_query_runs_against_sqlite(patched_connect: None) -> None:
    """``CAST('YYYY-MM-DD' AS DATE)`` → SQLite accepts the form, the
    drift query returns the planted in-period rows."""
    rows = _query_drift_violations(_CFG, _INSTANCE, _PERIOD)
    assert rows is not None, "skeleton-mode short-circuit fired unexpectedly"
    assert len(rows) == 2
    # Sort: business_day DESC, |drift| DESC. Acct-002 day=2030-01-05 first.
    assert rows[0].account_id == "acct-002"
    assert rows[0].drift == Decimal("-10")
    assert rows[1].account_id == "acct-001"
    assert rows[1].drift == Decimal("25")


def test_overdraft_query_runs_against_sqlite(patched_connect: None) -> None:
    """Period-scoped overdraft helper. Single planted in-period row
    comes back with the correct stored_balance."""
    rows = _query_overdraft_violations(_CFG, _INSTANCE, _PERIOD)
    assert rows is not None
    assert len(rows) == 1
    assert rows[0].account_id == "acct-003"
    # Stored as -42.5; Decimal preserves through float→Decimal in helper.
    assert rows[0].stored_balance == Decimal("-42.5")


def test_limit_breach_query_runs_against_sqlite(patched_connect: None) -> None:
    """Limit-breach query uses ``business_day`` (not ``business_day_start``)
    for the period filter — distinct column name from the others.
    Confirms the CAST sweep covers every WHERE-clause site, not just
    business_day_start ones. AB.1 (2026-05-19): also verifies both
    Outbound + Inbound rows surface via the new ``direction`` column."""
    rows = _query_limit_breach_violations(_CFG, _INSTANCE, _PERIOD)
    assert rows is not None
    assert len(rows) == 2
    by_direction = {r.direction: r for r in rows}
    assert set(by_direction) == {"Outbound", "Inbound"}
    assert by_direction["Outbound"].account_id == "acct-004"
    assert by_direction["Outbound"].overshoot == Decimal("5000")
    assert by_direction["Inbound"].overshoot == Decimal("5000")


def test_stuck_pending_query_runs_against_sqlite(patched_connect: None) -> None:
    """Stuck-pending is current-state — no date filter, no CAST sites
    in this query, but it runs against the same connection so we verify
    the helper completes end-to-end against SQLite."""
    rows = _query_stuck_pending_violations(_CFG, _INSTANCE)
    assert rows is not None
    assert len(rows) == 1
    assert rows[0].account_id == "acct-005"
    assert rows[0].transaction_id == "txn-001"


def test_stuck_unbundled_query_runs_against_sqlite(patched_connect: None) -> None:
    """Same shape as stuck_pending but a different matview + age cap
    column. Round-trip through SQLite proves both stuck_* helpers."""
    rows = _query_stuck_unbundled_violations(_CFG, _INSTANCE)
    assert rows is not None
    assert len(rows) == 1
    assert rows[0].account_id == "acct-006"
    assert rows[0].transaction_id == "txn-002"


def test_supersession_query_runs_against_sqlite(patched_connect: None) -> None:
    """Supersession runs four queries: aggregates × 2 base tables, then
    txn details + daily-balance details. Both detail queries period-filter
    via CAST. Aggregates use a CASE WHEN with two CAST sites apiece.
    Eight total CAST sites in this one helper — broadest coverage."""
    data = _query_supersession(_CFG, _INSTANCE, _PERIOD)
    assert data is not None

    # Aggregates: void_redo from each base table.
    by_table = {(a.base_table, a.supersedes_category): a for a in data.aggregates}
    txn_agg = by_table[("transactions", "void_redo")]
    assert txn_agg.total_count == 1
    assert txn_agg.new_in_period_count == 1
    db_agg = by_table[("daily_balances", "void_redo")]
    assert db_agg.total_count == 1
    assert db_agg.new_in_period_count == 1

    # In-window transaction details: only the supersedes-IS-NOT-NULL
    # row inside the period (txn-100). txn-099 (out-of-period) and
    # txn-101 (no supersedes) both excluded.
    assert len(data.transaction_details) == 1
    assert data.transaction_details[0].transaction_id == "txn-100"
    assert data.transaction_details[0].supersedes_category == "void_redo"

    # In-window daily-balance details: acct-007 (supersedes=void_redo,
    # in period). acct-008 has supersedes=NULL → excluded by the WHERE.
    assert len(data.daily_balance_details) == 1
    assert data.daily_balance_details[0].account_id == "acct-007"


def test_executive_summary_query_runs_against_sqlite(
    patched_connect: None,
) -> None:
    """Exec summary unrolls into 10 SELECTs: 2 volume + 6 invariant
    counts + 2 supersession totals. Most carry CAST sites in the period
    filter. Round-trip through SQLite proves the whole helper."""
    summary = _query_executive_summary(_CFG, _INSTANCE, _PERIOD)
    assert summary is not None

    # Volume: one in-period Posted txn (txn-100; txn-099 is out, txn-101
    # is in but for transfer xfer-B which has no other legs).
    # Posted + in-period: txn-100 + txn-101 = 2 legs across 2 transfers.
    assert summary.transactions_count == 2
    assert summary.transfers_count == 2

    # Per-invariant exception counts. The drift / overdraft / limit_breach
    # rows planted above all fall in-period; ledger_drift has one in-period
    # row. stuck_* are current-state (no date filter).
    counts = dict(summary.exception_counts)
    assert counts["Drift"] == 2
    assert counts["Ledger drift"] == 1
    assert counts["Overdraft"] == 1
    # AB.1: 2 limit_breach rows planted (1 Outbound + 1 Inbound) — both
    # surface in the exec summary count.
    assert counts["Limit breach"] == 2
    assert counts["Stuck pending*"] == 1
    assert counts["Stuck unbundled*"] == 1
    # Supersession total = 1 (transactions) + 1 (daily_balances) = 2.
    assert counts["Supersession*"] == 2


def test_skeleton_mode_short_circuits_against_sqlite() -> None:
    """``demo_database_url=None`` returns None without trying to connect.

    Same skeleton-mode contract the PG / Oracle audit tests verify;
    repeat here so the SQLite cell isn't blind to a regression that
    breaks skeleton mode for the local-iteration loop."""
    cfg = _FakeCfg(demo_database_url=None)  # type: ignore[arg-type]: _FakeCfg is a stand-in for Config in skeleton-mode tests
    assert _query_drift_violations(cfg, _INSTANCE, _PERIOD) is None
    assert _query_overdraft_violations(cfg, _INSTANCE, _PERIOD) is None
    assert _query_limit_breach_violations(cfg, _INSTANCE, _PERIOD) is None
    assert _query_stuck_pending_violations(cfg, _INSTANCE) is None
    assert _query_stuck_unbundled_violations(cfg, _INSTANCE) is None
    assert _query_supersession(cfg, _INSTANCE, _PERIOD) is None
    assert _query_executive_summary(cfg, _INSTANCE, _PERIOD) is None
