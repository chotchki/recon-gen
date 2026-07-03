"""DS.5.1 — the born-red cadence-universe witnesses, on the real engine.

Two states were INVISIBLE to the matview before DS.5.1, because its
account universe came from balance rows alone:

- CG2 — a DECLARED explicit_daily singleton (spec_example's
  ``clearing-suspense``) with ZERO rows anywhere. Its declaration binds
  every business day, but with no balance row it never entered the
  universe, so all-missing produced no alarm while one-missing did (the
  DS.3.3c blind-spot class exactly).
- CG3 — a transactions-only sparse account with activity and no balance
  row: money moved with no close to reconcile against, silent.

The fix unions balance-observed, non-failed-leg-observed and
L2-declared-cadence singletons into the universe and opens the in-scope
frame from either feed. These tests assert the rows now FIRE on the real
DuckDB engine — the flip from ``assert not rows`` (the historical blind
spot) to ``assert rows``, the DS.3.3c discipline. They guard against a
universe regression; the residual side is pinned by the cadence_gap KATs
and the enumeration gate proves engine == residual across the grid.
"""
from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from recon_gen.common.db import execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect
from tests.enumeration.domains._base import SPEC_EXAMPLE

_PREFIX = "spec_example"
_DAY0 = dt.date(2030, 1, 1)
_DECLARED_SINGLETON = "clearing-suspense"


def _fresh_conn() -> duckdb.DuckDBPyConnection:
    instance = load_instance(SPEC_EXAMPLE)
    conn = duckdb.connect()
    execute_script(
        conn, emit_schema(instance, prefix=_PREFIX, dialect=Dialect.DUCKDB),
        dialect=Dialect.DUCKDB,
    )
    return conn


def _refresh(conn: duckdb.DuckDBPyConnection) -> None:
    instance = load_instance(SPEC_EXAMPLE)
    execute_script(
        conn, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=Dialect.DUCKDB),
        dialect=Dialect.DUCKDB,
    )


def _insert_balance(
    conn: duckdb.DuckDBPyConnection, account: str, day: dt.date,
    *, role: str, parent_role: str | None,
) -> None:
    conn.execute(
        f"INSERT INTO {_PREFIX}_daily_balances "
        f"(account_id, account_name, account_role, account_scope, "
        f" account_parent_role, expected_eod_balance, business_day_start, "
        f" business_day_end, money) "
        f"VALUES (?, ?, ?, 'internal', ?, 0, ?, ?, 0)",
        [account, account, role, parent_role,
         f"{day} 00:00:00", f"{day} 23:59:59"],
    )


def _insert_leg(
    conn: duckdb.DuckDBPyConnection, account: str, day: dt.date, status: str,
) -> None:
    conn.execute(
        f"INSERT INTO {_PREFIX}_transactions "
        f"(id, account_id, account_name, account_role, account_scope, "
        f" account_parent_role, amount_money, amount_direction, status, "
        f" posting, transfer_id, rail_name, origin) "
        f"VALUES (?, ?, ?, 'CustomerSubledger', 'internal', 'CustomerLedger', "
        f" 500, 'Credit', ?, ?, ?, 'AnomRail', 'InternalInitiated')",
        [f"{account}-L", account, account, status,
         f"{day} 12:00:00", f"{account}-t"],
    )


def _gaps(conn: duckdb.DuckDBPyConnection) -> set[tuple[str, str]]:
    rows = conn.execute(
        f"SELECT account_id, gap_kind FROM {_PREFIX}_balance_cadence_gap",
    ).fetchall()
    return {(str(r[0]), str(r[1])) for r in rows}


def test_declared_singleton_zero_rows_alarms_whole_frame() -> None:
    """CG2 (was the blind spot): clearing-suspense is DECLARED
    explicit_daily and has NO balance row anywhere; a sparse bystander
    opens a two-day frame. The declaration alone must fire both days."""
    conn = _fresh_conn()
    try:
        _insert_balance(
            conn, "bystander", _DAY0, role="CustomerSubledger",
            parent_role="CustomerLedger",
        )
        _insert_balance(
            conn, "bystander", _DAY0 + dt.timedelta(days=1),
            role="CustomerSubledger", parent_role="CustomerLedger",
        )
        _refresh(conn)
        gaps = _gaps(conn)
    finally:
        conn.close()
    singleton_kinds = {g for g in gaps if g[0] == _DECLARED_SINGLETON}
    assert singleton_kinds == {(_DECLARED_SINGLETON, "declared_daily_missing")}, (
        "the declared explicit_daily singleton with zero rows must alarm "
        f"(the all-missing case DS.5.1 fixed); got {sorted(gaps)}"
    )
    # Both frame days must fire, not just one.
    conn = _fresh_conn()
    try:
        _insert_balance(
            conn, "bystander", _DAY0, role="CustomerSubledger",
            parent_role="CustomerLedger",
        )
        _insert_balance(
            conn, "bystander", _DAY0 + dt.timedelta(days=1),
            role="CustomerSubledger", parent_role="CustomerLedger",
        )
        _refresh(conn)
        day_rows = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_balance_cadence_gap "
            f"WHERE account_id = '{_DECLARED_SINGLETON}'",
        ).fetchone()
    finally:
        conn.close()
    assert day_rows is not None and int(day_rows[0]) == 2, (
        "declared singleton must gap BOTH frame days, not one"
    )


def test_transactions_only_sparse_activity_alarms() -> None:
    """CG3 (was the blind spot): a transactions-only sparse account with
    Posted activity and no balance row must fire sparse_with_activity."""
    conn = _fresh_conn()
    try:
        _insert_balance(
            conn, "bystander", _DAY0, role="CustomerSubledger",
            parent_role="CustomerLedger",
        )
        _insert_leg(conn, "tx-only", _DAY0, POSTED_STATUS)
        _refresh(conn)
        gaps = _gaps(conn)
    finally:
        conn.close()
    assert ("tx-only", "sparse_with_activity") in gaps, (
        "a transactions-only sparse account with activity and no balance "
        f"row must alarm (DS.5.1); got {sorted(gaps)}"
    )


def test_failed_only_activity_does_not_alarm() -> None:
    """The existence predicate boundary (status != Failed): a
    Failed-only account is neither in the universe nor counts as
    activity — no alarm."""
    conn = _fresh_conn()
    try:
        _insert_balance(
            conn, "bystander", _DAY0, role="CustomerSubledger",
            parent_role="CustomerLedger",
        )
        _insert_leg(conn, "failed-only", _DAY0, "Failed")
        _refresh(conn)
        gaps = _gaps(conn)
    finally:
        conn.close()
    assert not any(g[0] == "failed-only" for g in gaps), (
        f"a Failed-only account must not alarm; got {sorted(gaps)}"
    )


@pytest.mark.parametrize("status", [POSTED_STATUS, "Pending"])
def test_pending_counts_as_activity(status: str) -> None:
    """Posted AND Pending are activity (in-flight is still a movement);
    the existence predicate is status != Failed, not status == Posted."""
    conn = _fresh_conn()
    try:
        _insert_balance(
            conn, "bystander", _DAY0, role="CustomerSubledger",
            parent_role="CustomerLedger",
        )
        _insert_leg(conn, "act", _DAY0, status)
        _refresh(conn)
        gaps = _gaps(conn)
    finally:
        conn.close()
    assert ("act", "sparse_with_activity") in gaps, (
        f"{status} activity must alarm the sparse gap; got {sorted(gaps)}"
    )
