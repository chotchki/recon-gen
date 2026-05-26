"""Unit tests for X.1.d.1 Layer-1 query helpers.

Uses sqlite3 in-memory as a stand-in for the live psycopg2 / oracledb
connection. Sqlite uses ``?`` placeholders, not ``%s`` or ``:1``, so
the dialect-branched placeholders won't validate against SQLite —
these tests cover the SQL-shape assembly + assertion behavior, not
the bind format itself. Bind format is verified by the live e2e
harness against real Aurora / Oracle.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import pytest


_LAYER1 = pytest.importorskip(
    "tests.e2e._layer1_query", reason="layer1 helpers under tests/e2e",
)


class _FakeCursor:
    """sqlite3-backed cursor that translates the dialect-branched
    placeholders (``%s``, ``:1``) into sqlite's ``?`` so the rest of
    the helper logic exercises end-to-end."""

    def __init__(self, real: sqlite3.Cursor) -> None:
        self._real = real

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        # Translate %s and :N placeholders to sqlite ?
        translated = re.sub(r":\d+", "?", sql).replace("%s", "?")
        self._real.execute(translated, params)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._real.fetchall()

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._real.fetchone()

    def close(self) -> None:
        self._real.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeConn:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._conn.cursor())


@pytest.fixture
def db() -> _FakeConn:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE drift (account_id TEXT, business_day DATE, magnitude REAL)"
    )
    conn.executemany(
        "INSERT INTO drift VALUES (?, ?, ?)",
        [
            ("cust-0001-snb", "2030-01-01", 250.00),
            ("cust-0002-snb", "2030-01-01", 12.34),
            ("cust-0001-snb", "2030-01-02", 99.99),
        ],
    )
    conn.commit()
    return _FakeConn(conn)


def test_query_matview_rows_unfiltered(db: _FakeConn) -> None:
    rows = _LAYER1.query_matview_rows(db, "drift")
    assert len(rows) == 3


def test_query_matview_rows_with_where(db: _FakeConn) -> None:
    rows = _LAYER1.query_matview_rows(
        db, "drift", {"account_id": "cust-0001-snb"},
    )
    assert len(rows) == 2


def test_query_matview_rows_with_columns_and_limit(db: _FakeConn) -> None:
    rows = _LAYER1.query_matview_rows(
        db, "drift", columns=["account_id", "magnitude"], limit=2,
    )
    assert len(rows) == 2
    assert rows[0] == ("cust-0001-snb", 250.00)


def test_oracle_limit_branch_renders_fetch_first() -> None:
    """SQLite doesn't accept Oracle's ``FETCH FIRST N ROWS ONLY`` so
    we can't execute the Oracle branch end-to-end here. Instead, verify
    the helper assembles the right syntax by snooping on the cursor's
    received SQL string.
    """
    from recon_gen.common.sql.dialect import Dialect

    received: dict[str, Any] = {}

    class _SnoopCursor:
        def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
            received["sql"] = sql
            received["params"] = params

        def fetchall(self) -> list[Any]:
            return []

        def close(self) -> None:
            pass

        def __enter__(self) -> "_SnoopCursor":
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

    class _SnoopConn:
        def cursor(self) -> "_SnoopCursor":
            return _SnoopCursor()

    _LAYER1.query_matview_rows(
        _SnoopConn(), "drift", columns=["account_id"], limit=5,
        dialect=Dialect.ORACLE,
    )
    assert "FETCH FIRST 5 ROWS ONLY" in received["sql"]
    assert "LIMIT" not in received["sql"]


def test_matview_row_count_unfiltered(db: _FakeConn) -> None:
    assert _LAYER1.matview_row_count(db, "drift") == 3


def test_matview_row_count_with_where(db: _FakeConn) -> None:
    assert _LAYER1.matview_row_count(
        db, "drift", {"account_id": "cust-0001-snb"},
    ) == 2


def test_assert_matview_has_row_passes(db: _FakeConn) -> None:
    # Shouldn't raise.
    _LAYER1.assert_matview_has_row(
        db, "drift", {"account_id": "cust-0001-snb"},
    )


def test_assert_matview_has_row_raises_with_total_count_in_message(db: _FakeConn) -> None:
    with pytest.raises(AssertionError, match=r"Total rows in matview: 3"):
        _LAYER1.assert_matview_has_row(
            db, "drift", {"account_id": "nope"},
        )


def test_assert_matview_has_row_distinguishes_empty_vs_drift(db: _FakeConn) -> None:
    """Error message branches on total — 0 → seed regression; >0 →
    column / filter drift. Helps the diagnostic ladder."""
    # Empty matview path (different table).
    db.cursor().execute(
        "CREATE TABLE empty_view (account_id TEXT)",
    )
    with pytest.raises(AssertionError, match=r"Seed/refresh regression"):
        _LAYER1.assert_matview_has_row(
            db, "empty_view", {"account_id": "anything"},
        )
    # Non-empty but missing the row.
    with pytest.raises(AssertionError, match=r"Column drift or filter mismatch"):
        _LAYER1.assert_matview_has_row(
            db, "drift", {"account_id": "ghost"},
        )


def test_assert_matview_has_row_carries_context(db: _FakeConn) -> None:
    with pytest.raises(AssertionError, match=r"^test_x_drill"):
        _LAYER1.assert_matview_has_row(
            db, "drift", {"account_id": "ghost"},
            context="test_x_drill",
        )


def test_assert_account_in_matview_passes(db: _FakeConn) -> None:
    _LAYER1.assert_account_in_matview(db, "drift", "cust-0001-snb")


def test_assert_account_in_matview_raises(db: _FakeConn) -> None:
    with pytest.raises(AssertionError, match=r"Layer 1 miss"):
        _LAYER1.assert_account_in_matview(db, "drift", "ghost-acct")


def test_multiple_where_clauses_and_joined(db: _FakeConn) -> None:
    """AND-joining multiple where keys is the most common shape —
    (account_id, business_day) pair queries on per-day matviews."""
    matched = _LAYER1.matview_row_count(
        db, "drift",
        {"account_id": "cust-0001-snb", "business_day": "2030-01-02"},
    )
    assert matched == 1


def test_oracle_placeholder_branch_translates(db: _FakeConn) -> None:
    """Smoke that the Oracle branch yields :1/:2/... that the fake
    cursor translates to ? and produces the same row set."""
    from recon_gen.common.sql.dialect import Dialect
    matched = _LAYER1.matview_row_count(
        db, "drift",
        {"account_id": "cust-0001-snb", "business_day": "2030-01-02"},
        dialect=Dialect.ORACLE,
    )
    assert matched == 1
