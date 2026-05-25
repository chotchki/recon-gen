"""BC.3 — Dialect-aware SQL emission for interval value types.

Lives at the SQL seam rather than on the value types themselves
(``common/intervals.py``). The interval types are pure values; they
have no business knowing what database is downstream. This module is
the bridge: ``(interval, dialect, column)`` → ``SQLFragment``.

Two shapes:

- ``between_clause(interval: DateInterval, ...)`` — ``col BETWEEN start AND end``,
  both endpoints inclusive. The business convention (audit "week of
  May 17 - May 23" includes both endpoints).

- ``range_clause(interval, ...)`` — ``col >= start AND col < end_exclusive``,
  half-open. Overloaded:
  - ``DateInterval`` input → date+1 widens "end-of-day on end" into
    "start-of-day on end+1". Used by the audit queries that today
    hand-roll the ``+timedelta(days=1)``.
  - ``DateTimeInterval`` input → straight ``>= start AND <
    end_exclusive``. Used by ``stuck_*`` matview filters and any
    callsite that already has a half-open timestamp interval.

Dialect literal shapes are encapsulated in
``common/sql/dialect.py::date_literal`` (PG/Oracle: ``DATE 'YYYY-MM-DD'``;
SQLite: bare ``'YYYY-MM-DD'`` TEXT). This module composes the clauses
from those literals; it does not invent its own literal syntax.

Per ``feedback_invariants_in_types``: types bring meaning with them,
convention hides it. The audit code that today does ``date_literal((end
+ timedelta(days=1)).isoformat(), dialect)`` is encoding "end is
closed, so widen by one day for half-open shape" as math at every
callsite; this module owns that policy.
"""

from __future__ import annotations

from datetime import timedelta

from recon_gen.common.intervals import DateInterval, DateTimeInterval
from recon_gen.common.sql.dialect import Dialect, date_literal


def between_clause(
    interval: DateInterval, *, dialect: Dialect, column: str,
) -> str:
    """Emit ``<column> BETWEEN <start_literal> AND <end_literal>``.

    Both endpoints inclusive — matches the closed-closed convention
    that ``DateInterval`` carries. ``column`` is operator-controlled
    (no SQL injection at the seam; the caller produces it from a typed
    column name reference).

    Equivalent to ``col >= start AND col <= end``. For date columns,
    this is also equivalent to ``range_clause(interval, ...)`` because
    date arithmetic is integer-spaced; for date columns stored as
    TIMESTAMP (midnight), ``BETWEEN`` only includes the midnight
    instant of ``end`` — use ``range_clause`` instead if you need to
    cover the full day.
    """
    start_lit = date_literal(interval.start.isoformat(), dialect)
    end_lit = date_literal(interval.end.isoformat(), dialect)
    return f"{column} BETWEEN {start_lit} AND {end_lit}"


def range_clause(
    interval: DateInterval | DateTimeInterval,
    *, dialect: Dialect, column: str,
) -> str:
    """Emit half-open ``<column> >= <start> AND <column> < <end_exclusive>``.

    Overloaded:

    - ``DateInterval`` input (closed-closed dates): the ``+1 day``
      flip from closed-end to exclusive-end happens here, so callers
      don't write ``end + timedelta(days=1)`` by hand. The emitted
      SQL covers all of ``interval.end``'s day even when the column
      is a TIMESTAMP storing midnight.

    - ``DateTimeInterval`` input (half-open naive timestamps): emitted
      as-is. ``end_exclusive`` is already the half-open right edge.

    Why this is split from ``between_clause``: BETWEEN is closed-
    closed, which on a TIMESTAMP-shaped date column ONLY matches
    rows at midnight of ``end`` — the audit queries today work
    around that by hand-rolling ``+1 day`` math at each callsite.
    Encapsulating it here removes the per-callsite policy.
    """
    if isinstance(interval, DateInterval):
        start_lit = date_literal(interval.start.isoformat(), dialect)
        end_exclusive = interval.end + timedelta(days=1)
        end_lit = date_literal(end_exclusive.isoformat(), dialect)
        return f"{column} >= {start_lit} AND {column} < {end_lit}"
    # DateTimeInterval — already half-open; emit timestamps as
    # ISO literals. PG + Oracle accept ``TIMESTAMP 'YYYY-MM-DD HH:MM:SS'``;
    # SQLite stores TEXT and compares lexically (ISO format is correct).
    start_iso = interval.start.isoformat(sep=" ")
    end_iso = interval.end_exclusive.isoformat(sep=" ")
    if dialect is Dialect.SQLITE:
        return f"{column} >= '{start_iso}' AND {column} < '{end_iso}'"
    return (
        f"{column} >= TIMESTAMP '{start_iso}' AND "
        f"{column} < TIMESTAMP '{end_iso}'"
    )
