"""Date-range pushdown helper for the universal L1 + Exec date pickers.

Phase BM replaced the pre-BM dual-SQL ``{date_filter}`` template +
``app2_date_filter()`` snippet with the unified
``<<$pXxxDateStart>>`` / ``<<$pXxxDateEnd>>`` dataset-parameter
pushdown shape used elsewhere in Phase Y. One SQL form across QS +
App2; the renderer-specific bind machinery dissolved. This module
now exposes a single helper that emits the day-inclusive predicate
fragment per dialect.

Pre-BM history (preserved for context): the file previously held the
``app2_date_filter`` helper that emitted dialect-specific
``:date_from`` / ``:date_to`` bind clauses for App2 while QS got an
empty substitution + an analysis-level ``TimeRangeFilter`` FG. The
dual-form caused the day-edge quirk (QS's TimeRangeFilter included
the upper-bound day's late-day rows differently from App2's
``< date_to + 1 day`` shape) plus the ``visual_identifier=`` test
dance documented in BL.2.B. Both artifacts dissolved with BM.
"""

from __future__ import annotations

from .dialect import Dialect


def universal_date_range_clause(
    date_column: str,
    *,
    start_param: str,
    end_param: str,
    dialect: Dialect,
) -> str:
    """Phase BM — day-inclusive range pushdown via QS dataset parameters.

    Returns an unprefixed predicate fragment narrowing ``date_column``
    by two ``DateTimeDatasetParameter``s named ``start_param`` /
    ``end_param``. Used by L1 + Exec date-scoped datasets in place of
    the pre-BM dual-SQL ``{date_filter}`` slot.

    The substituted parameter value arrives as an ISO datetime string
    (``'2026-05-20T00:00:00'``) on both renderers — QS substitutes
    ``<<$pX>>`` literally, App2 binds via ``:param_pX``. The clause
    uses dialect-portable casts so that string parses to a DATE /
    TIMESTAMP on every backend.

    **Day-inclusive on both ends**: the upper bound expands to "just
    before midnight on the day AFTER end_param" so TIMESTAMP-shaped
    columns (``posting``) include same-day non-midnight rows. The
    lower bound is the natural ``>= start_param`` (midnight, inclusive).

    No ``AND`` prefix — caller composes via ``WHERE ... AND <clause>``
    explicitly.
    """
    p_start = f"<<${start_param}>>"
    p_end = f"<<${end_param}>>"
    if dialect is Dialect.ORACLE:
        # Oracle's default NLS_DATE_FORMAT (``DD-MON-RR``) doesn't
        # parse ISO-T strings via bare CAST, so route through TO_DATE
        # with an explicit format string that matches both QS's
        # ``'YYYY-MM-DDTHH24:MI:SS'`` substitution AND App2's URL-bound
        # binding. ``+ 1`` adds one day (Oracle DATE arithmetic).
        return (
            f"{date_column} >= TO_DATE({p_start}, "
            f"'YYYY-MM-DD\"T\"HH24:MI:SS') "
            f"AND {date_column} < TO_DATE({p_end}, "
            f"'YYYY-MM-DD\"T\"HH24:MI:SS') + 1"
        )
    if dialect is Dialect.SQLITE:
        # SQLite has no DATE type — stored timestamps are ISO TEXT.
        # ``datetime(...)`` normalizes the input + supports modifiers;
        # ``'+1 day'`` lands the upper bound on the next midnight, so
        # any same-day ``YYYY-MM-DD HH:MM:SS`` stored on end_param's day
        # sorts BEFORE it lex-wise.
        return (
            f"{date_column} >= datetime({p_start}) "
            f"AND {date_column} < datetime({p_end}, '+1 day')"
        )
    # Postgres — CAST(<ISO-T string> AS TIMESTAMP) parses natively.
    return (
        f"{date_column} >= CAST({p_start} AS TIMESTAMP) "
        f"AND {date_column} < CAST({p_end} AS TIMESTAMP) "
        f"+ INTERVAL '1 day'"
    )
