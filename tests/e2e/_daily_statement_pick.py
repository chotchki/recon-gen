"""AA.B.5.followon — DB-driven Daily Statement filter picker.

Returns a ``(account_display, account_role, business_day_iso)`` triple
guaranteed to have ≥1 transaction in the deployed L1 schema, so the
Daily Statement browser tests can drive their three filter pickers
(Role / Account / Business Day) to a known-good combination regardless
of calendar position, seed re-shuffle, or matview state.

Why this exists: ``test_daily_statement_picked_account_narrows_table``
+ ``test_daily_statement_role_then_account_populates_table`` originally
picked ``options[0]`` from the Account dropdown and left the Business
Day picker at its default (``RollingDate=yesterday``). The combination
is calendar-fragile: a chain that runs the day after a thin-data day
(or crosses UTC midnight between the prev run and this one) lands the
Business Day default on a date the picked account has no transactions
for. QS faithfully renders "No data found" — the test then fails on a
clock skew, not a code regression.

The helper queries the deployed ``<prefix>_transactions`` base table
(matview-equivalent for current rows) for the **most-recent** (account,
day) pair with the **highest** row count on that day. Preferring most-
recent biases toward the near-today end of the seed so the test
exercises the same data shape an analyst would land on in normal use;
the row-count tiebreak guarantees the asserted ``len(rows) >= 1`` is a
meaningful signal (a single-row plant on day N won't be picked over a
10-row organic firing on day N).

Dialect-aware via ``date_trunc_day`` (Y.3.f) so the same helper works
against the deployed PG, Oracle, or — when the test variant matrix
expands to it — SQLite.
"""

from __future__ import annotations

import psycopg

from quicksight_gen.common.config import Config
from quicksight_gen.common.sql.dialect import Dialect, date_trunc_day


def find_account_day_with_data(cfg: Config) -> tuple[str, str, str]:
    """Return ``(account_display, account_role, business_day_iso)`` for
    a known-good Daily Statement filter combination in the deployed
    ``<cfg.db_table_prefix>_transactions`` table.

    ``account_display`` matches the ``"Name (id)"`` shape AA.E.2 wired
    into the Account dropdown's ``LinkedValues.from_column(...
    account_display)``. ``account_role`` matches the Role dropdown's
    ``LinkedValues.from_column(... account_role)``. ``business_day_iso``
    is ``YYYY-MM-DD`` (the protocol's date format).

    Raises ``RuntimeError`` if the deployed DB has no rows at all
    (deploy step skipped? wrong cfg? wrong prefix?) — refusing to
    silently return a useless tuple.

    Only Postgres + Oracle are wired; SQLite is reachable via the
    same ``date_trunc_day`` SQL but not connected here (the runner
    doesn't dispatch browser e2e against SQLite — QS can't reach a
    sqlite tempfile).
    """
    if cfg.dialect not in (Dialect.POSTGRES, Dialect.ORACLE):
        raise RuntimeError(
            f"find_account_day_with_data: unsupported dialect "
            f"{cfg.dialect!r} — only Postgres + Oracle wired"
        )
    if not cfg.demo_database_url:
        raise RuntimeError(
            "find_account_day_with_data: cfg.demo_database_url is unset"
        )
    bday_expr = date_trunc_day("posting", cfg.dialect)
    prefix = cfg.db_table_prefix
    # Group by (account, day); pick most-recent-then-most-active. The
    # tiebreak (n DESC) matters: in a thin-data day a single 1-row
    # planted scenario would be picked over a 10-row organic firing
    # on the same day if we ordered by bday alone with deterministic-
    # but-unhelpful tiebreaks.
    sql = (
        f"SELECT account_name, account_id, account_role, "
        f"       {bday_expr} AS bday, COUNT(*) AS n "
        f"FROM {prefix}_transactions "
        f"GROUP BY account_name, account_id, account_role, {bday_expr} "
        f"HAVING COUNT(*) > 0 "
        f"ORDER BY bday DESC, n DESC "
    )
    if cfg.dialect is Dialect.POSTGRES:
        sql += "LIMIT 1"
    else:
        sql += "FETCH FIRST 1 ROWS ONLY"

    # Match warm_aurora's connect timeout (60s) — Aurora cold-start
    # tolerance, harmless on a warm cluster.
    with psycopg.connect(cfg.demo_database_url, connect_timeout=60) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
    if row is None:
        raise RuntimeError(
            f"find_account_day_with_data: no (account, day) pair with "
            f">=1 row in {prefix}_transactions — deploy skipped? "
            f"wrong cfg? wrong prefix? Check the chain's seed/db layers."
        )
    name, acct_id, role, bday, _ = row
    return (
        f"{name} ({acct_id})",
        str(role),
        bday.date().isoformat(),
    )
