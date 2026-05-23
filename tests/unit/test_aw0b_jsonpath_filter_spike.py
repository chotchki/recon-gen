"""AW.0.b spike — dialect-portable JSONPath filter syntax for multi-
valued cfg reads.

The AW migration needs matviews to read per-L2-entity values via JSON
paths from `<prefix>_config.l2_yaml`. For SCALAR fields (`$.as_of`)
this is trivial — `JSON_VALUE(col, '$.as_of')` works in PG / Oracle /
SQLite uniformly.

The harder case is **filter-by-key over arrays** — e.g., "find the rail
named 'ExternalRailInbound' and return its `max_pending_age_seconds'."
The SQL/JSON standard syntax for this is:

    $.rails[?(@.name == "ExternalRailInbound")].max_pending_age_seconds

PG (12+) and Oracle (12c+) both support the SQL/JSON filter-path syntax.
**SQLite does NOT** — its `json_extract` accepts only basic paths
(`$.foo.bar`, `$.arr[0]`) per the json1 extension docs. The portable
workaround in SQLite is `json_each` + WHERE on the iteration value.

This spike validates:

1. SQLite's `json_extract` rejects the filter-path syntax (confirming
   the portability concern).
2. The SQLite workaround using `json_each(...)` + WHERE returns the
   right value.
3. The `dialect.py` helper would need a per-dialect render: one shape
   for PG/Oracle (filter-path in path string), another for SQLite
   (json_each + WHERE clause).

If both halves verify, AW.1+ can land the config-table migration with
a known-shape `json_select_by_key` helper in `common/sql/dialect.py`
that switches per-dialect. If SQLite's `json_each` workaround fails
or hits a different shape, AW's design needs another iteration.
"""

from __future__ import annotations

import json
import sqlite3


_L2_YAML_AS_JSON = json.dumps({
    "rails": [
        {"name": "ExternalRailInbound", "max_pending_age_seconds": 86400},
        {"name": "ExternalRailOutbound", "max_pending_age_seconds": None},
        {"name": "SubledgerCharge", "max_unbundled_age_seconds": 14400},
    ],
    "limit_schedules": [
        {
            "parent_role": "CustomerLedger",
            "rail": "ExternalRailOutbound",
            "direction": "Outbound",
            "cap": 5000,
        },
        {
            "parent_role": "CustomerLedger",
            "rail": "ExternalRailInbound",
            "direction": "Inbound",
            "cap": 3000,
        },
    ],
})


_CONFIG_DDL = """
CREATE TABLE spike_config (
    l2_yaml TEXT NOT NULL
);
"""


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(_CONFIG_DDL)
    conn.execute(
        "INSERT INTO spike_config (l2_yaml) VALUES (?)",
        (_L2_YAML_AS_JSON,),
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Verify scalar paths work uniformly (the easy half).
# ---------------------------------------------------------------------------


def test_scalar_path_works_in_sqlite() -> None:
    """Sanity check: simple scalar extract works. (`$.as_of` if we had
    it; using an arbitrary nested scalar here.)"""
    conn = _fresh_db()
    try:
        row = conn.execute(
            "SELECT json_extract(l2_yaml, '$.rails[0].name') "
            "FROM spike_config",
        ).fetchone()
    finally:
        conn.close()
    assert row == ("ExternalRailInbound",)


# ---------------------------------------------------------------------------
# The hard half: SQL/JSON filter-path syntax against SQLite.
# ---------------------------------------------------------------------------


def test_sqlite_rejects_filter_path_syntax() -> None:
    """The portability finding — SQLite's `json_extract` does NOT
    support `$.arr[?(@.name == 'X')]` filter expressions. PG / Oracle
    DO support this (per their JSONPath standard implementations).
    AW's dialect-portable helper has to switch on dialect."""
    conn = _fresh_db()
    try:
        # Two SQLite failure modes the test tolerates: it might error
        # outright (OperationalError) or return NULL silently. Either
        # way it does NOT return the rail's max_pending_age value.
        try:
            row = conn.execute(
                "SELECT json_extract(l2_yaml, "
                "'$.rails[?(@.name == \"ExternalRailInbound\")].max_pending_age_seconds') "
                "FROM spike_config",
            ).fetchone()
            # If we got here, SQLite didn't error — but it must have
            # returned NULL (not the actual 86400) because the path
            # syntax isn't recognized.
            assert row == (None,), (
                f"unexpected — SQLite returned {row!r} for a SQL/JSON "
                f"filter path; if this assertion fires, SQLite's JSON "
                f"support has expanded beyond json1 + the AW design "
                f"can be simplified."
            )
        except sqlite3.OperationalError as exc:
            # The other failure mode — explicit syntax rejection.
            assert "json" in str(exc).lower() or "path" in str(exc).lower(), (
                f"unexpected SQLite error shape: {exc!r}"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The SQLite workaround: json_each + WHERE.
# ---------------------------------------------------------------------------


def test_sqlite_json_each_workaround_returns_filtered_value() -> None:
    """The portable SQLite shape: iterate the array via `json_each`,
    filter via WHERE on the iteration value, project the desired field
    via a second `json_extract`. Verbose but functional.

    The dialect helper in AW would render this for SQLite and the
    filter-path syntax for PG/Oracle — same logical query, different
    SQL shape per backend."""
    conn = _fresh_db()
    try:
        row = conn.execute(
            "SELECT json_extract(value, '$.max_pending_age_seconds') "
            "FROM json_each((SELECT l2_yaml FROM spike_config), '$.rails') "
            "WHERE json_extract(value, '$.name') = ?",
            ("ExternalRailInbound",),
        ).fetchone()
    finally:
        conn.close()
    assert row == (86400,)


def test_sqlite_json_each_handles_multi_key_filter() -> None:
    """The limit_breach case: filter on THREE keys (parent_role + rail
    + direction). The json_each + WHERE shape composes."""
    conn = _fresh_db()
    try:
        row = conn.execute(
            "SELECT json_extract(value, '$.cap') "
            "FROM json_each((SELECT l2_yaml FROM spike_config), '$.limit_schedules') "
            "WHERE json_extract(value, '$.parent_role') = ? "
            "  AND json_extract(value, '$.rail')        = ? "
            "  AND json_extract(value, '$.direction')   = ?",
            ("CustomerLedger", "ExternalRailOutbound", "Outbound"),
        ).fetchone()
    finally:
        conn.close()
    assert row == (5000,)


def test_sqlite_json_each_returns_null_for_no_match() -> None:
    """The no-match case — the matview's WHERE filter would exclude
    the row. SQLite returns no rows from the json_each + WHERE; the
    SELECT returns nothing (fetchone → None)."""
    conn = _fresh_db()
    try:
        row = conn.execute(
            "SELECT json_extract(value, '$.max_pending_age_seconds') "
            "FROM json_each((SELECT l2_yaml FROM spike_config), '$.rails') "
            "WHERE json_extract(value, '$.name') = ?",
            ("NoSuchRail",),
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_sqlite_json_each_handles_null_field() -> None:
    """The rail-without-cap case: rail exists but max_pending_age is
    NULL. json_extract returns the JSON `null` literal which converts
    to Python None.

    AW's matview filters would then exclude this row in the outer
    WHERE — matching the current stuck_pending behavior where rails
    without `max_pending_age` are filtered out."""
    conn = _fresh_db()
    try:
        row = conn.execute(
            "SELECT json_extract(value, '$.max_pending_age_seconds') "
            "FROM json_each((SELECT l2_yaml FROM spike_config), '$.rails') "
            "WHERE json_extract(value, '$.name') = ?",
            ("ExternalRailOutbound",),  # has explicit None
        ).fetchone()
    finally:
        conn.close()
    assert row == (None,)


# ---------------------------------------------------------------------------
# The dialect-helper shape AW would land.
# ---------------------------------------------------------------------------


def test_join_shape_for_matview_use() -> None:
    """The end-to-end shape a matview would use — JOIN the config
    table's json_each iteration into the matview's main FROM clause,
    so each transaction row gets paired with the right rail's cap.
    This is what stuck_pending's `{pending_age_cases}` substitution
    would become post-AW."""
    conn = _fresh_db()
    try:
        # Set up a tiny transactions table
        conn.execute(
            "CREATE TABLE spike_transactions ("
            "  id TEXT, rail_name TEXT, posting TEXT"
            ")"
        )
        conn.execute(
            "INSERT INTO spike_transactions VALUES "
            "  ('tx-1', 'ExternalRailInbound', '2030-01-01'), "
            "  ('tx-2', 'SubledgerCharge',     '2030-01-01'), "
            "  ('tx-3', 'ExternalRailInbound', '2030-01-01')"
        )
        conn.commit()

        rows = conn.execute("""
            SELECT
                tx.id,
                tx.rail_name,
                json_extract(rail.value, '$.max_pending_age_seconds')
                    AS max_pending_age_seconds
            FROM spike_transactions tx
            LEFT JOIN json_each(
                (SELECT l2_yaml FROM spike_config), '$.rails'
            ) rail
              ON json_extract(rail.value, '$.name') = tx.rail_name
            ORDER BY tx.id
        """).fetchall()
    finally:
        conn.close()

    # tx-1 and tx-3 match ExternalRailInbound → 86400
    # tx-2 matches SubledgerCharge → it has max_unbundled but no
    #   max_pending → json_extract on missing key returns NULL
    assert rows == [
        ("tx-1", "ExternalRailInbound", 86400),
        ("tx-2", "SubledgerCharge",     None),  # no max_pending_age_seconds key
        ("tx-3", "ExternalRailInbound", 86400),
    ]
