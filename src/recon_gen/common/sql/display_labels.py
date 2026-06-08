"""CQ.1 — NULL-safe display-label SQL expression for account pickers.

Every account picker on every renderer (QS LinkedValues + App2 Tom Select)
binds an ``account_display`` column built as
``"<account_name> (<account_id>)"``. Pre-CQ.1 the concat was inline at 7
sites + 1 helper; SQL ``||`` propagates NULL so any row with
``account_name IS NULL`` collapsed the whole label to NULL and the App2
fetcher's ``WHERE account_display IS NOT NULL`` (plus the QS LinkedValues
equivalent) dropped the row from the picker entirely. The same NULL hits
the *value* the dropdown sends back, so even cross-sheet drills carrying
the un-rendered string compared unequal to the row's recomputed concat.

The COALESCE shape ``COALESCE(name, id) || ' (' || id || ')'`` is
dialect-portable across PG / Oracle / DuckDB (ANSI COALESCE +
NULL-propagating ``||``) and yields ``"acct-id (acct-id)"`` when the name
is NULL — readable, picker-selectable, drill-target-equal-on-both-sides.

Caller contract: BOTH the WHERE-side (the helper that compares the
dropdown-picked value to the row's recomputed expression) AND the
SELECT-side (the projection that builds ``account_display``) must call
this helper. A partial fix is worse than no fix — the WHERE side would
compute ``"id (id)"`` while the SELECT side still rendered NULL, and
the WHERE would still filter the row out.
"""

from __future__ import annotations


def account_display_expr(name_col: str, id_col: str) -> str:
    """Render the NULL-safe ``account_display`` SQL fragment.

    Args:
        name_col: SQL reference to the name column. May be bare
            (``"account_name"``) or table-aliased (``"tx.account_name"``).
        id_col: SQL reference to the id column. Same shape conventions.

    Returns:
        An expression-level SQL fragment that resolves to
        ``"<name> (<id>)"`` when the name is non-null, falling back to
        ``"<id> (<id>)"`` when the name is null. Always non-NULL.

    Portable across PG / Oracle / DuckDB; ANSI COALESCE + ``||`` semantics
    are identical across all three live dialects.
    """
    return f"(COALESCE({name_col}, {id_col}) || ' (' || {id_col} || ')')"
