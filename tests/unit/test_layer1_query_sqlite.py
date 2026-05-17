"""X.3.g.1 — Layer 1 query helpers wired against SQLite.

Layer 1 (matview presence check, renderer-agnostic) was built for
Postgres + Oracle in X.1.d.1. SQLite arrives via X.3 — the same
helpers should now drive a SQLite connection: ``?`` placeholder,
explicit cursor close (sqlite3 cursors don't support context-
manager protocol), no other dialect-specific gotchas in the
SELECT path.

Lives under ``tests/unit/`` — sqlite3 ships with Python, so no
Docker / no AWS / no service container needed. (Originally landed
under ``tests/e2e/`` but the e2e conftest's ``RECON_GEN_E2E=1`` gate
silently skipped it in CI — moved here so the existing CI ``test``
job actually picks it up via pytest discovery.) Imports the helpers
from ``tests/e2e/_layer1_query.py`` because the helpers themselves
are still used by live e2e harnesses that need them.

X.3.g matrix cells exercised: Layer 1 × SQLite (the cell that
PLAN.md X.2's table marks TODO; this file ticks it).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest

from recon_gen.common.sql.dialect import Dialect
from tests.e2e._layer1_query import (
    assert_account_in_matview,
    assert_matview_has_row,
    matview_row_count,
    query_matview_rows,
)


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    """In-memory SQLite seeded with a tiny drift-shaped matview.

    The matview-as-table convention X.3.b/c established for SQLite
    (no native materialized views; CREATE TABLE substitutes) means
    Layer 1 query helpers see the same shape they see against
    Postgres/Oracle — a regular relation that the SELECT path
    consumes. Schema mirrors the L1 ``drift`` matview's relevant
    columns so the assertions look like the real e2e harness.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE drift ("
        "  account_id TEXT NOT NULL, "
        "  business_day TEXT NOT NULL, "
        "  delta_money REAL NOT NULL"
        ")"
    )
    conn.executemany(
        "INSERT INTO drift VALUES (?, ?, ?)",
        [
            ("cust-0001-snb", "2030-01-01", 250.00),
            ("cust-0002-snb", "2030-01-01", 12.34),
            ("cust-0001-snb", "2030-01-02", 99.99),
            ("cust-0003-snb", "2030-01-03", 1.00),
        ],
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def test_query_matview_rows_unfiltered_against_sqlite(
    db: sqlite3.Connection,
) -> None:
    """Unfiltered SELECT returns every row. Pure round-trip against
    sqlite3 — proves the helper's cursor lifecycle works with the
    cursor that has no context-manager protocol."""
    rows = query_matview_rows(db, "drift", dialect=Dialect.SQLITE)
    assert len(rows) == 4


def test_query_matview_rows_filtered_uses_question_mark_placeholder(
    db: sqlite3.Connection,
) -> None:
    """``?`` placeholder works against SQLite — proves the X.3.g
    branch in ``_placeholder``. Filters down to one account's two
    rows."""
    rows = query_matview_rows(
        db, "drift",
        where={"account_id": "cust-0001-snb"},
        dialect=Dialect.SQLITE,
    )
    assert len(rows) == 2


def test_query_matview_rows_with_columns_and_limit(
    db: sqlite3.Connection,
) -> None:
    """Column projection + LIMIT clause work in tandem."""
    rows = query_matview_rows(
        db, "drift",
        columns=["account_id", "delta_money"],
        limit=2,
        dialect=Dialect.SQLITE,
    )
    assert len(rows) == 2
    assert all(len(r) == 2 for r in rows)


def test_matview_row_count_against_sqlite(
    db: sqlite3.Connection,
) -> None:
    """COUNT(*) path uses the same _placeholder + cursor lifecycle.
    Proves both query_matview_rows and matview_row_count work for
    the SQLite dialect."""
    count = matview_row_count(
        db, "drift", dialect=Dialect.SQLITE,
    )
    assert count == 4

    filtered = matview_row_count(
        db, "drift",
        where={"account_id": "cust-0001-snb"},
        dialect=Dialect.SQLITE,
    )
    assert filtered == 2


def test_assert_matview_has_row_against_sqlite(
    db: sqlite3.Connection,
) -> None:
    """Assertion helper finds a planted row + raises on a
    not-found one."""
    # Found — no exception.
    assert_matview_has_row(
        db, "drift",
        {"account_id": "cust-0002-snb"},
        dialect=Dialect.SQLITE,
    )
    # Missing — assertion fires.
    with pytest.raises(AssertionError, match="drift"):
        assert_matview_has_row(
            db, "drift",
            {"account_id": "not-a-real-account"},
            dialect=Dialect.SQLITE,
        )


def test_assert_account_in_matview_against_sqlite(
    db: sqlite3.Connection,
) -> None:
    """The convenience wrapper that filters by ``account_id``."""
    assert_account_in_matview(
        db, "drift", "cust-0003-snb",
        dialect=Dialect.SQLITE,
    )
    with pytest.raises(AssertionError):
        assert_account_in_matview(
            db, "drift", "no-such-account",
            dialect=Dialect.SQLITE,
        )


def test_placeholder_returns_question_mark_for_sqlite() -> None:
    """Direct unit on the dispatcher — proves SQLite gets ``?``,
    not ``%s`` or ``:N``."""
    from tests.e2e._layer1_query import _placeholder

    assert _placeholder(Dialect.SQLITE, 1) == "?"
    assert _placeholder(Dialect.SQLITE, 2) == "?"
    # Other dialects unchanged.
    assert _placeholder(Dialect.ORACLE, 1) == ":1"
    assert _placeholder(Dialect.POSTGRES, 1) == "%s"


def test_sqlite_limit_uses_limit_clause_not_fetch_first(
    db: sqlite3.Connection,
) -> None:
    """SQLite uses ``LIMIT N`` (same as Postgres). Oracle uses
    ``FETCH FIRST N ROWS ONLY``. Quick check that the dialect
    branch picks LIMIT for SQLite."""
    rows = query_matview_rows(
        db, "drift", limit=3, dialect=Dialect.SQLITE,
    )
    # Round-trip works. The SQL shape is captured in
    # test_layer1_query.py via parametrized snoop tests; this just
    # confirms execution.
    assert len(rows) == 3
