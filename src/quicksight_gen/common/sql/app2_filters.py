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


def app2_date_filter(date_column: str, dialect: Dialect) -> str:
    """Return an AND-clause snippet that narrows ``date_column`` by
    the URL-bound ``:date_from`` / ``:date_to`` params.

    Empty / missing values pass through. Caller interpolates the
    snippet into a template SQL via
    ``.format(date_filter=app2_date_filter("t.posting", cfg.dialect))``.

    Strategy: always pass a valid date string into a per-dialect
    string-to-date conversion, using sentinel dates (``1900-01-01`` /
    ``9999-12-31``) that don't actually filter when the URL param is
    empty / missing.

    Dialect handling (Y.3.f.alt.4a — Oracle TO_DATE):

    - PG: ``CAST('1900-01-01' AS DATE)`` — PG's CAST recognizes the
      ISO-8601 form natively. No format hint needed.
    - Oracle: ``TO_DATE('1900-01-01', 'YYYY-MM-DD')`` — Oracle's
      session ``NLS_DATE_FORMAT`` defaults to ``DD-MON-RR`` (e.g.
      ``01-JAN-26``), and ``CAST(string AS DATE)`` honors that. ISO
      strings rejected with ``ORA-01847``. ``TO_DATE`` with an
      explicit format string bypasses the session setting.
    - SQLite: ``'1900-01-01'`` as plain text — SQLite has no native
      DATE type; comparisons against TEXT-stored ISO dates work by
      lex order (1900 sorts before any real date).

    The leading ``AND`` is included so the snippet drops cleanly
    after a non-empty WHERE — for templates with no other WHERE
    conditions, the author writes ``WHERE 1=1 {date_filter}``.
    """
    if dialect is Dialect.ORACLE:
        return (
            f"AND {date_column} >= TO_DATE("
            f"COALESCE(NULLIF(:date_from, ''), '1900-01-01'), 'YYYY-MM-DD') "
            f"AND {date_column} <= TO_DATE("
            f"COALESCE(NULLIF(:date_to, ''), '9999-12-31'), 'YYYY-MM-DD')"
        )
    if dialect is Dialect.SQLITE:
        # SQLite stores ISO dates as TEXT; lex comparison works.
        # No CAST needed — wrapping the COALESCE in a TEXT comparison.
        return (
            f"AND {date_column} >= "
            f"COALESCE(NULLIF(:date_from, ''), '1900-01-01') "
            f"AND {date_column} <= "
            f"COALESCE(NULLIF(:date_to, ''), '9999-12-31')"
        )
    # Postgres
    return (
        f"AND {date_column} >= CAST("
        f"COALESCE(NULLIF(:date_from, ''), '1900-01-01') AS DATE) "
        f"AND {date_column} <= CAST("
        f"COALESCE(NULLIF(:date_to, ''), '9999-12-31') AS DATE)"
    )
