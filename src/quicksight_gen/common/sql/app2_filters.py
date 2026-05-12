"""X.2.g.1.b — SQL filter snippets for App2 dataset templates.

When a dataset wants the X.2.d filter form to actually filter, the
SQL needs ``:date_from`` / ``:date_to`` bind placeholders. The QS
dialect doesn't accept those (it uses ``<<$paramName>>`` literal
substitution + analysis-level FilterGroups). So dataset SQL stays
templatized: one body, with ``{date_filter}`` (or whichever filter
slots are needed) interpolated to ``""`` for QS and to the App2
clause snippet here for App2.

Usage in a dataset builder:

    sql_template = '''
        SELECT ... FROM {p}_transactions t
        WHERE t.status = 'Posted' {date_filter}
        ...
    '''
    qs_sql = sql_template.format(p=prefix, date_filter="")
    app2_sql = sql_template.format(
        p=prefix,
        date_filter=app2_date_filter("t.posting", cfg.dialect),
    )

The clause uses ``NULLIF(:name, '') IS NULL`` to handle Oracle's
empty-string-as-NULL quirk:

- PG: ``NULLIF('', '')`` → NULL → COALESCE returns the sentinel
- Oracle: ``''`` is bound as NULL → ``NULLIF(NULL, '')`` is NULL → ditto
- SQLite: ``NULLIF('', '')`` → NULL → ditto
"""

from __future__ import annotations

from .dialect import Dialect


# Sentinel dates used when a URL param is empty / missing — they don't
# actually filter (every real ``posting`` falls inside). The UPPER
# sentinel is ``9999-12-30`` (not ``…-31``) on purpose: the upper-bound
# clause is exclusive-of-the-next-day (``column < date_to + 1 day`` —
# see ``app2_date_filter`` below), so the sentinel gets ``+ 1`` applied
# to it; ``9999-12-31 + 1`` overflows Oracle's max ``DATE`` (ORA-01841)
# and SQLite's ``date()`` range, so we start one day lower so ``+ 1``
# lands exactly on ``9999-12-31`` and stays in range on all three.
_DATE_FROM_SENTINEL = "1900-01-01"
_DATE_TO_SENTINEL = "9999-12-30"


def app2_date_filter(date_column: str, dialect: Dialect) -> str:
    """Return an AND-clause snippet that narrows ``date_column`` by
    the URL-bound ``:date_from`` / ``:date_to`` params.

    Empty / missing values pass through. Caller interpolates the
    snippet into a template SQL via
    ``.format(date_filter=app2_date_filter("t.posting", cfg.dialect))``.

    **Day-inclusive upper bound (X.2.j.dateparity).** ``date_column``
    is typically a ``TIMESTAMP`` (``t.posting`` carries a time-of-day;
    the L1-invariant matviews' ``business_day_start`` is midnight-aligned
    for non-fuzz instances but role-business-day offsets make it
    non-midnight). A naive ``column <= CAST(:date_to AS DATE)`` then
    EXCLUDES same-day non-midnight rows (``2026-05-10 14:32:01 <=
    2026-05-10 00:00:00`` is false), while QuickSight's analysis-level
    ``TimeRangeFilter`` with ``time_granularity="DAY"`` truncates the
    column at filter-eval time and INCLUDES it. To keep the two
    renderers in agreement (and to mean "through the end of ``:date_to``"
    the way an operator expects), the upper clause is
    ``column < CAST(:date_to AS DATE) + 1 day`` — exclusive of the day
    *after* ``date_to``. This keeps ``date_column`` untruncated so an
    index on it stays usable (vs. ``DATE_TRUNC(column)`` on the LHS,
    which would defeat the index).

    Strategy: always pass a valid date string into a per-dialect
    string-to-date conversion, using sentinel dates
    (``1900-01-01`` / ``9999-12-30``; see ``_DATE_TO_SENTINEL`` for why
    ``…-30``) that don't actually filter when the URL param is empty.

    Dialect handling (Y.3.f.alt.4a — Oracle TO_DATE):

    - PG: ``CAST('1900-01-01' AS DATE)`` — PG's CAST recognizes the
      ISO-8601 form natively; ``+ INTERVAL '1 day'`` for the upper bound.
    - Oracle: ``TO_DATE('1900-01-01', 'YYYY-MM-DD')`` — Oracle's
      session ``NLS_DATE_FORMAT`` defaults to ``DD-MON-RR`` (e.g.
      ``01-JAN-26``), and ``CAST(string AS DATE)`` honors that. ISO
      strings rejected with ``ORA-01847``. ``TO_DATE`` with an
      explicit format string bypasses the session setting; ``+ 1`` adds
      a day.
    - SQLite: ``'1900-01-01'`` as plain text — SQLite has no native
      DATE type; comparisons against TEXT-stored ISO dates work by
      lex order. The upper bound uses ``date(:date_to, '+1 day')``
      (a ``'YYYY-MM-DD'`` string; ``t.posting`` stored as
      ``'YYYY-MM-DD HH:MM:SS'`` sorts before the next bare date).

    The leading ``AND`` is included so the snippet drops cleanly
    after a non-empty WHERE — for templates with no other WHERE
    conditions, the author writes ``WHERE 1=1 {date_filter}``.
    """
    if dialect is Dialect.ORACLE:
        return (
            f"AND {date_column} >= TO_DATE("
            f"COALESCE(NULLIF(:date_from, ''), '{_DATE_FROM_SENTINEL}'), "
            f"'YYYY-MM-DD') "
            f"AND {date_column} < TO_DATE("
            f"COALESCE(NULLIF(:date_to, ''), '{_DATE_TO_SENTINEL}'), "
            f"'YYYY-MM-DD') + 1"
        )
    if dialect is Dialect.SQLITE:
        # SQLite stores ISO dates as TEXT; lex comparison works. The
        # upper bound is ``date(:date_to, '+1 day')`` — a bare
        # ``'YYYY-MM-DD'`` that sorts after every same-day timestamp
        # ``'YYYY-MM-DD HH:MM:SS'``.
        return (
            f"AND {date_column} >= "
            f"COALESCE(NULLIF(:date_from, ''), '{_DATE_FROM_SENTINEL}') "
            f"AND {date_column} < "
            f"date(COALESCE(NULLIF(:date_to, ''), '{_DATE_TO_SENTINEL}'), "
            f"'+1 day')"
        )
    # Postgres
    return (
        f"AND {date_column} >= CAST("
        f"COALESCE(NULLIF(:date_from, ''), '{_DATE_FROM_SENTINEL}') AS DATE) "
        f"AND {date_column} < CAST("
        f"COALESCE(NULLIF(:date_to, ''), '{_DATE_TO_SENTINEL}') AS DATE) "
        f"+ INTERVAL '1 day'"
    )
