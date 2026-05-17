"""Direct-SQL anchor for the 4-way agreement test (X.2.j.B.2).

The 5th anchor in the chain ``scenario_plants ⊆ direct_matview_query ==
PDF == QS == App2``. The L1-invariant matview is the *ground truth* all
three renderers should be showing; this module queries it straight off
the just-seeded DB so the test compares each renderer against the matview
(and the matview against the scenario plants), not just the renderers
against each other.

Two reads per invariant:

- ``l1_invariant_matview_row_keys`` — the set of natural-key tuples for
  the flat-shape invariants (drift / overdraft → ``(account_id, day)``;
  limit_breach → ``(account_id, day, rail_name)`` post-Z.B). The ``day``
  is the matview's day column (``business_day_start`` for drift/overdraft,
  ``business_day`` for limit_breach), date-truncated — matching the
  scenario's ``_eff(p)`` semantics and the audit's
  ``>= start AND < (end + 1 day)`` window.
- ``count_l1_invariant_matview_rows`` — a plain row count. Used for the
  divergent-shape invariants (stuck_* / supersession), whose matviews
  carry no date column (the row count is whatever the matview currently
  holds) and whose natural key (``transaction_id``) the scenario plants
  don't expose, so a count comparison among the renderers + the matview
  is the meaningful check there.

``conn`` is a live DB connection (psycopg / oracledb / sqlite3) — the
test opens its own (the ``seeded_audit`` fixture's conn is closed by the
time the asserts run). Date literals are inlined (test-controlled date
values, no injection surface) with a per-dialect form (Oracle needs
``TO_DATE(..., 'YYYY-MM-DD')`` — its session ``NLS_DATE_FORMAT`` rejects
ISO strings; PG / SQLite take the plain string).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from quicksight_gen.common.sql import Dialect


L1Invariant = Literal[
    "drift",
    "overdraft",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "supersession",
]


# invariant → (matview suffix, day column or None, natural-key columns).
# day column None ⇒ no period filter (current-state matview).
#
# ``supersession`` is intentionally absent: there's no ``<prefix>_supersession``
# matview — the L1 dashboard's "Transactions Audit" table and the audit
# PDF's supersession section each query their own shape over the base
# ``<prefix>_transactions`` / ``<prefix>_daily_balances`` (the entry-trail of
# any logical key with a superseded entry; rows with ``supersedes IS NOT
# NULL`` per category; …). So supersession has no clean direct-SQL
# anchor — the 4-way test keeps it count-level (the two renderers must
# agree with each other) without it. ``MATVIEW_ANCHORED`` is the set of
# invariants this module can serve.
_MATVIEW: dict[L1Invariant, tuple[str, str | None, tuple[str, ...]]] = {
    "drift": ("drift", "business_day_start", ("account_id", "business_day_start")),
    "overdraft": (
        "overdraft", "business_day_start", ("account_id", "business_day_start"),
    ),
    "limit_breach": (
        # Z.B (2026-05-15) — ``transfer_type`` subsumed into ``rail_name`` on
        # the limit_breach matview (the cap view's CASE-WHEN now keys on
        # ``tx.rail_name`` per common/l2/schema.py::_render_limit_breach_cases).
        # The scenario-plant identity tuple is ``(account_id, day, rail_name)``
        # — see tests/audit/test_scenario_expectations.py::
        # test_limit_breach_carries_transfer_type_in_identity (kept the test
        # name from before the Z.B rename so the historical context survives).
        "limit_breach", "business_day",
        ("account_id", "business_day", "rail_name"),
    ),
    "stuck_pending": ("stuck_pending", None, ("transaction_id",)),
    "stuck_unbundled": ("stuck_unbundled", None, ("transaction_id",)),
}

MATVIEW_ANCHORED: frozenset[str] = frozenset(_MATVIEW)


def _date_literal(d: date, dialect: Dialect) -> str:
    """A SQL literal for ``d`` that compares correctly against the
    matview's day column on each dialect."""
    iso = d.isoformat()
    if dialect is Dialect.ORACLE:
        return f"TO_DATE('{iso}', 'YYYY-MM-DD')"
    # PG: implicit cast of the ISO string to timestamp; SQLite: lexical
    # comparison against the TEXT-stored ISO datetime.
    return f"'{iso}'"


def _period_where(
    day_col: str | None, period: tuple[date, date] | None, dialect: Dialect,
) -> str:
    """The ``WHERE`` clause narrowing ``day_col`` to ``period`` — the
    audit's ``>= start AND < (end + 1 day)`` window, so a same-day
    non-midnight timestamp is included (matches QS DAY-granularity and
    the X.2.j.dateparity-fixed ``app2_date_filter``).

    Returns ``""`` when there's no day column or no period."""
    if day_col is None or period is None:
        return ""
    start, end = period
    end_plus_one = end + timedelta(days=1)
    return (
        f" WHERE {day_col} >= {_date_literal(start, dialect)} "
        f"AND {day_col} < {_date_literal(end_plus_one, dialect)}"
    )


def count_l1_invariant_matview_rows(
    conn: Any,  # typing-smell: ignore[explicit-any]: per-driver connection union (psycopg/oracledb/sqlite3) has no shared Protocol
    prefix: str,
    invariant: L1Invariant,
    period: tuple[date, date] | None,
    dialect: Dialect,
) -> int:
    """``SELECT count(*)`` from the named invariant's matview, narrowed
    to ``period`` when the matview has a day column (drift / overdraft /
    limit_breach) and unfiltered otherwise (stuck_* / supersession)."""
    suffix, day_col, _keys = _MATVIEW[invariant]
    sql = (
        f"SELECT count(*) FROM {prefix}_{suffix}"
        + _period_where(day_col, period, dialect)
    )
    with conn.cursor() as cur:
        cur.execute(sql)
        return int(cur.fetchone()[0])


def l1_invariant_matview_row_keys(
    conn: Any,  # typing-smell: ignore[explicit-any]: per-driver connection union has no shared Protocol
    prefix: str,
    invariant: L1Invariant,
    period: tuple[date, date] | None,
    dialect: Dialect,
) -> set[tuple[str | date, ...]]:
    """The set of natural-key tuples in the named invariant's matview,
    narrowed to ``period`` (when applicable).

    Keys mirror the scenario's row-identity tuples: ``(account_id, day)``
    for drift / overdraft, ``(account_id, day, rail_name)`` for
    limit_breach (Z.B subsumed ``transfer_type`` into the rail),
    ``(transaction_id,)`` for the divergent-shape ones.
    Day values are normalised to a ``date`` (the cursor returns a
    ``datetime`` for PG/Oracle TIMESTAMP cols, a ``str`` for SQLite TEXT)
    so they compare against the scenario's ``date``-typed ``_eff(p)``.
    """
    suffix, day_col, key_cols = _MATVIEW[invariant]
    select_cols = ", ".join(key_cols)
    sql = (
        f"SELECT {select_cols} FROM {prefix}_{suffix}"
        + _period_where(day_col, period, dialect)
    )
    out: set[tuple[str | date, ...]] = set()
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur.fetchall():
            out.add(tuple(_normalise_key_cell(c, col) for c, col in zip(row, key_cols, strict=True)))
    return out


def _normalise_key_cell(value: object, col_name: str) -> str | date:
    """Normalise a cursor cell to ``date`` (for a day column) or ``str``.

    Day columns come back as ``datetime`` (PG/Oracle TIMESTAMP), ``date``,
    or an ISO ``str`` (SQLite TEXT). Everything else (account_id,
    rail_name, transaction_id) → ``str``."""
    if col_name in ("business_day_start", "business_day_end", "business_day"):
        if isinstance(value, datetime):  # check before date — datetime IS a date
            return value.date()
        if isinstance(value, date):
            return value
        # SQLite: an ISO string like '2026-05-07 00:00:00' or '2026-05-07'.
        return date.fromisoformat(str(value)[:10])
    return str(value)
