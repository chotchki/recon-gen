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
        date_filter=app2_date_filter("t.posting"),
    )

The clause uses ``NULLIF(:name, '') IS NULL`` instead of
``:name = ''`` to handle Oracle's empty-string-as-NULL quirk:

- PG: ``NULLIF('', '')`` → NULL → IS NULL is TRUE → OR short-circuits
- Oracle: ``''`` is bound as NULL → ``NULLIF(NULL, '')`` is NULL → ditto
- SQLite: ``NULLIF('', '')`` → NULL → ditto

The CAST inside the OR's right side never evaluates when the left
side is TRUE, so an empty date_from doesn't trigger
``CAST('' AS DATE)`` on any dialect.
"""

from __future__ import annotations


def app2_date_filter(date_column: str) -> str:
    """Return an AND-clause snippet that narrows ``date_column`` by
    the URL-bound ``:date_from`` / ``:date_to`` params.

    Empty / missing values pass through. Caller interpolates the
    snippet into a template SQL via
    ``.format(date_filter=app2_date_filter("t.posting"))``.

    Strategy: always pass a valid date string into ``CAST(... AS
    DATE)``, using sentinel dates (``1900-01-01`` / ``9999-12-31``)
    that don't actually filter when the URL param is empty /
    missing. This avoids the PG gotcha where ``OR`` doesn't
    reliably short-circuit and ``CAST('' AS DATE)`` errors at
    plan time even when the guard is TRUE.

    Dialect handling:

    - PG: ``COALESCE(NULLIF('', ''), '1900-01-01')`` → ``'1900-01-01'``
      → ``CAST(... AS DATE)`` succeeds. Filter is a no-op when
      param is empty (``t.posting >= 1900-01-01`` matches all).
    - Oracle: ``''`` is NULL → NULLIF preserves NULL → COALESCE
      returns the sentinel → CAST succeeds. Same no-op semantics.
    - SQLite: ``''`` is text → NULLIF returns NULL → COALESCE
      returns sentinel. Comparison works against TEXT-stored ISO
      dates by lex order (1900 sorts before any real date).

    The leading ``AND`` is included so the snippet drops cleanly
    after a non-empty WHERE — for templates with no other WHERE
    conditions, the author writes ``WHERE 1=1 {date_filter}``.
    """
    return (
        f"AND {date_column} >= CAST("
        f"COALESCE(NULLIF(:date_from, ''), '1900-01-01') AS DATE) "
        f"AND {date_column} <= CAST("
        f"COALESCE(NULLIF(:date_to, ''), '9999-12-31') AS DATE)"
    )
