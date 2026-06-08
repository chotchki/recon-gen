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

from typing import Any

from recon_gen.common.config import Config
from recon_gen.common.db import connect_demo_db
from recon_gen.common.sql.dialect import Dialect, date_trunc_day


def find_two_days_for_same_account(
    cfg: Config,
) -> tuple[str, str, str, str]:
    """BG.2 — return ``(account_display, account_role, day1_iso, day2_iso)``
    for an account with **at least two distinct business days** of data
    in the deployed transactions table.

    BG.2's delta assertion needs to switch the Daily Statement Business
    Day picker between two days on the SAME account and prove the KPIs
    change. ``find_account_day_with_data`` returns only one day — this
    sibling returns two. ``day1`` is the most-recent populated day for
    the account; ``day2`` is the next-most-recent (older).

    Same role-narrowing as ``find_account_day_with_data`` (alphabetically-
    first role so the picked account survives the Daily Statement Role
    cascade's initial-load defaults). Raises ``RuntimeError`` if no
    account in the matching role has ≥2 distinct days of data — typically
    a thin local seed.
    """
    if cfg.dialect not in (Dialect.POSTGRES, Dialect.ORACLE, Dialect.DUCKDB):
        raise RuntimeError(
            f"find_two_days_for_same_account: unsupported dialect "
            f"{cfg.dialect!r}"
        )
    if not cfg.demo_database_url:
        raise RuntimeError(
            "find_two_days_for_same_account: cfg.demo_database_url is unset"
        )
    bday_expr = date_trunc_day("posting", cfg.dialect)
    prefix = cfg.db_table_prefix
    # Find the lowest-id account (by the dropdown-window rationale in
    # find_account_day_with_data) WITH ≥2 distinct days, within the
    # alphabetically-first role. Returns one row carrying both days.
    sql = (
        f"WITH per_account AS ("
        f"  SELECT account_name, account_id, account_role, {bday_expr} AS bday "
        f"  FROM {prefix}_transactions "
        f"  WHERE account_role = ("
        f"    SELECT MIN(account_role) FROM {prefix}_current_daily_balances"
        f"  ) "
        f"  GROUP BY account_name, account_id, account_role, {bday_expr}"
        f"), "
        f"distinct_day_count AS ("
        f"  SELECT account_id, COUNT(*) AS day_count "
        f"  FROM per_account "
        f"  GROUP BY account_id "
        f"  HAVING COUNT(*) >= 2"
        f") "
        f"SELECT pa.account_name, pa.account_id, pa.account_role, pa.bday "
        f"FROM per_account pa "
        f"JOIN distinct_day_count dc ON dc.account_id = pa.account_id "
        f"ORDER BY pa.account_id ASC, pa.bday DESC "
    )
    # Need the top 2 rows for the lowest-id account. We sort by
    # (account_id ASC, bday DESC), so the first 2 rows belong to the
    # same account (lowest_id) — most-recent + next-most-recent days.
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            rows = cur.fetchmany(2)
        finally:
            cur.close()
    finally:
        conn.close()
    if len(rows) < 2:
        raise RuntimeError(
            f"find_two_days_for_same_account: no account in role-narrowed "
            f"{prefix}_transactions has ≥2 distinct business days — thin "
            f"seed? Re-run `recon-gen data apply --execute` with the "
            f"default density."
        )
    name1, id1, role1, bday1 = rows[0]
    _name2, id2, _role2, bday2 = rows[1]
    if id1 != id2:
        raise RuntimeError(
            f"find_two_days_for_same_account: lowest-id account "
            f"{id1!r} has only one populated day; next-lowest "
            f"{id2!r} would split the (day1, day2) pair across "
            f"accounts. Helper invariant violated."
        )
    return (
        f"{name1} ({id1})",
        str(role1),
        _iso_day(bday1),
        _iso_day(bday2),
    )


def find_account_day_with_data(cfg: Config) -> tuple[str, str, str]:
    """Return ``(account_display, account_role, business_day_iso)`` for
    a known-good Daily Statement filter combination in the deployed
    ``<cfg.db_table_prefix>_transactions`` table.

    ``account_display`` matches the ``"Name (id)"`` shape AA.E.2 wired
    into the Account dropdown's ``LinkedValues.from_column(...
    account_display)``. ``account_role`` is returned for callers that
    still want it (no longer used to narrow). ``business_day_iso`` is
    ``YYYY-MM-DD`` (the protocol's date format).

    CQ.4.a — was pre-CQ.4 narrowed to the alphabetically-first role
    in ``<prefix>_current_daily_balances`` (matching the SINGLE_SELECT
    Role dropdown's initial-load auto-pick) because the Account picker
    was cascade-narrowed by the Role dropdown and that narrowing did
    NOT refresh after a runtime pick. Post-CQ.4 the Role dropdown +
    cascade are gone — every ``scope = 'internal'`` account is in the
    picker. The narrowing is dropped; the helper just biases toward
    low ``account_id`` so the pick lands inside QS's virtualized
    dropdown window (~14 visible options at a time).

    Raises ``RuntimeError`` if the deployed DB has no rows at all
    (deploy step skipped? wrong cfg? wrong prefix?) — refusing to
    silently return a useless tuple.
    """
    if cfg.dialect not in (Dialect.POSTGRES, Dialect.ORACLE, Dialect.DUCKDB):
        raise RuntimeError(
            f"find_account_day_with_data: unsupported dialect "
            f"{cfg.dialect!r}"
        )
    if not cfg.demo_database_url:
        raise RuntimeError(
            "find_account_day_with_data: cfg.demo_database_url is unset"
        )
    bday_expr = date_trunc_day("posting", cfg.dialect)
    prefix = cfg.db_table_prefix
    # Group by (account, day); bias the pick toward low ``account_id`` so
    # the resulting account lands in QS's MUI Autocomplete first-visible
    # window (the Account dropdown virtualizes options at ~14 items —
    # picks past that window aren't reachable via Playwright clicks even
    # though the dropdown's SQL returns them). bday DESC + n DESC are
    # tiebreaks so within the lowest-id account we still favor the most-
    # recent / most-active day.
    #
    # CQ.4.a — restrict to internal-scope accounts (matches the picker's
    # `WHERE account_scope = 'internal'` source narrowing); previously
    # this restricted further to the alphabetically-first role, dropped
    # because the Role cascade is gone.
    #
    # CR.x — mirror the picker's seed-page SELECT exactly:
    # `SELECT DISTINCT (COALESCE(name, id) || ' (' || id || ')')
    #  FROM _current_daily_balances WHERE account_scope = 'internal'
    #  ORDER BY 1`
    # This guarantees the helper's first (name, id) pair = the picker's
    # alphabetically-first option. Pre-fix, the helper joined to
    # _transactions and picked by `account_id ASC` — for an account_id
    # with multiple `account_name` values in the matview (e.g.
    # cust-0001-snb has both 'Drift Child' and 'Stuck Pending'
    # planted), the helper picked one display string while the picker
    # showed the other, tripping AA.E.2.
    sql = (
        f"WITH picker AS ("
        f"  SELECT DISTINCT account_name, account_id, account_role"
        f"  FROM {prefix}_current_daily_balances"
        f"  WHERE account_scope = 'internal'"
        f") "
        f"SELECT p.account_name, p.account_id, p.account_role, "
        f"       {bday_expr} AS bday, COUNT(*) AS n "
        f"FROM {prefix}_transactions t "
        f"INNER JOIN picker p"
        f"  ON p.account_name = t.account_name"
        f" AND p.account_id   = t.account_id "
        f"WHERE t.account_scope = 'internal' "
        f"GROUP BY p.account_name, p.account_id, p.account_role, "
        f"         {bday_expr} "
        f"HAVING COUNT(*) > 0 "
        f"ORDER BY (COALESCE(p.account_name, p.account_id) || ' ('"
        f"          || p.account_id || ')') ASC, bday DESC, n DESC "
    )
    if cfg.dialect is Dialect.ORACLE:
        sql += "FETCH FIRST 1 ROWS ONLY"
    else:
        # Postgres + SQLite both speak LIMIT.
        sql += "LIMIT 1"

    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            row = cur.fetchone()
        finally:
            cur.close()
    finally:
        conn.close()
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
        _iso_day(bday),
    )


def _iso_day(value: Any) -> str:
    """Return ``YYYY-MM-DD`` from a `bday` column value. Postgres /
    Oracle drivers return ``datetime``; SQLite returns ``str``."""
    if hasattr(value, "date"):
        return str(value.date().isoformat())
    text = str(value)
    return text[:10]


def find_one_account_day_per_role(
    cfg: Config,
) -> list[tuple[str, str, str]]:
    """BO.1 — one ``(account_display, account_role, business_day_iso)``
    triple PER role that has ≥1 row in
    ``<prefix>_current_daily_balances``. Each triple is a known-good
    Daily Statement filter combination for its role:

      * Picked account has a daily_balances row (the BO.1 contract —
        picker source narrowed to balance-only).
      * Picked account has ≥1 transaction on the picked day (so the
        per-(account, day) detail table renders ≥1 row).

    Iterates every role in ``<prefix>_current_daily_balances`` so the
    e2e test exercises the contract for every role the operator can
    pick on first load, not just the alphabetically-first one
    ``find_account_day_with_data`` returns. Operator-driven: real
    deployments have multiple roles (cardholder DDA, GL control,
    suspense, sweep, etc.); the BO.1 regression mode is "role X's
    accounts in the dropdown still don't have balance rows", which
    a one-role test would miss.

    Same low-account-id / most-recent-day biases as
    ``find_account_day_with_data`` so each role's picked account lands
    inside QS's virtualized dropdown window.

    Raises ``RuntimeError`` if no role has rows — the seed state is
    broken upstream and the test would be useless either way.
    """
    if cfg.dialect not in (Dialect.POSTGRES, Dialect.ORACLE, Dialect.DUCKDB):
        raise RuntimeError(
            f"find_one_account_day_per_role: unsupported dialect "
            f"{cfg.dialect!r}"
        )
    if not cfg.demo_database_url:
        raise RuntimeError(
            "find_one_account_day_per_role: cfg.demo_database_url unset"
        )

    bday_expr = date_trunc_day("posting", cfg.dialect)
    prefix = cfg.db_table_prefix

    # 1) Enumerate roles that have ≥1 row in current_daily_balances —
    #    that's the universe BO.1 narrows the picker source to.
    roles_sql = (
        f"SELECT DISTINCT account_role FROM {prefix}_current_daily_balances"
    )

    results: list[tuple[str, str, str]] = []
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(roles_sql)
            roles = [str(r[0]) for r in cur.fetchall()]

            # 2) For each role, find the (account, day) pair with the
            #    most transactions among accounts that DO have a
            #    daily_balances row of that role. Same low-id /
            #    most-recent biases as ``find_account_day_with_data``
            #    (operator clicks first-visible in the virtualized
            #    dropdown). Role names come from our own DB query
            #    (above); splice-as-literal is safe + dialect-portable.
            for role in roles:
                role_literal = role.replace("'", "''")
                # f-string is safe — role came from our own SELECT.
                # BR.x — pull ``account_name`` from
                # ``current_daily_balances`` (the picker's display source),
                # NOT ``transactions``. Sasquatch's StuckPendingGenerator
                # overrides ``transactions.account_name`` for accounts
                # with planted Pending legs (e.g. "Stuck Pending
                # (ConcentrationToFRBSweep)"); the picker shows the
                # balance-side name ("SNB Customer 1"). Joining to
                # balances and selecting its name gives the display
                # string the dropdown will actually advertise.
                # CB.17.c1 — also require a row in daily_statement_summary
                # for (account_id, business_day) so the Opening Balance /
                # Closing Stored KPIs (which query that matview, not
                # current_daily_balances or transactions) have data to
                # render. Otherwise the picker offers a triple where
                # daily_balances has rows + transactions has rows, but
                # the KPI matview is empty → blank KPI cards (cold-read
                # F1 failure shape). Surfaced by thin qs_browser against
                # sasquatch_pr's "Drift Child" rollup-only account.
                per_role_sql = (
                    f"SELECT b.account_name, b.account_id, "
                    f"       t.bday, t.n "
                    f"FROM {prefix}_current_daily_balances b "
                    f"JOIN ("
                    f"  SELECT account_id, {bday_expr} AS bday, "
                    f"         COUNT(*) AS n "
                    f"  FROM {prefix}_transactions "
                    f"  WHERE account_role = '{role_literal}' "
                    f"  GROUP BY account_id, {bday_expr} "
                    f"  HAVING COUNT(*) > 0"
                    f") t ON t.account_id = b.account_id "
                    f"JOIN {prefix}_daily_statement_summary s "
                    f"  ON s.account_id = b.account_id "
                    f"  AND s.business_day_start = t.bday "
                    f"WHERE b.account_role = '{role_literal}' "
                    f"ORDER BY b.account_id ASC, t.bday DESC, t.n DESC "
                )
                per_role_sql += (
                    "FETCH FIRST 1 ROWS ONLY"
                    if cfg.dialect is Dialect.ORACLE else "LIMIT 1"
                )
                cur.execute(per_role_sql)
                row = cur.fetchone()
                if row is None:
                    # Role has a daily_balances row but no transactions
                    # — picker would still offer the account, but the
                    # Posted Money Records table would correctly be
                    # empty. Skip from the e2e set; the dataset SQL
                    # test already pinned the picker source contract.
                    continue
                name, acct_id, bday, _ = row
                results.append((
                    f"{name} ({acct_id})", role, _iso_day(bday),
                ))
        finally:
            cur.close()
    finally:
        conn.close()
    if not results:
        raise RuntimeError(
            f"find_one_account_day_per_role: no role in "
            f"{prefix}_current_daily_balances has matching transactions "
            f"— deploy skipped? wrong cfg? wrong prefix?"
        )
    return results
