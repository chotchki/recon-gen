"""DM/DN — App2-only Daily Statement cascade + day-availability coverage.

POLICY-2 browser coverage (CLAUDE.md build-hygiene contract) for three
Daily Statement features that QuickSight cannot render, so these are
App2-TARGETED tests (not ``[qs, app2]`` parametrized — the QS gap is a
permanent renderer-capability limit documented via the structured triple:
``NotImplementedError`` on ``QsEmbedDriver.filter_value`` /
``QsEmbedDriver.day_availability`` + ``docs/reference/quicksight-quirks.md``
+ ``[[project_qs_no_searchfilter_cascading]]``).

All three live on the L1 Dashboard ``Daily Statement`` sheet
(``l1-sheet-daily-statement``):

1. **Role→Account cascade narrowing (DM.2).** The App2-only Role dropdown
   (``pL1DsRole``) narrows the Account dropdown (``pL1DsAccount``) to the
   accounts in the picked role. Server-side in
   ``_tree_fetcher.make_options_search_fetcher`` via the
   ``CascadeRule`` built from the tree
   (``("l1-ds-accounts-ds", "account_display") → ("account_role",
   "pL1DsRole")``). Asserted against the DB: the narrowed option count
   equals the role's distinct-account count (strictly fewer than the full
   universe) and every option belongs to that role.

2. **Cascade clear-on-source-change (DM/BR.1).** ``bootstrap.js``'s
   ``wireTomSelect`` listens for the cascade source's ``change`` and calls
   ``tomInstance.clear() + clearOptions()`` so the Account picker drops a
   stale value that belonged to the OLD role. Asserted: pick Role A → pick
   an Account → Account has a value → change Role to B → Account value is
   cleared.

3. **Day-availability decoration (DM.3).** After an Account is picked, the
   Business Day flatpickr decorates each calendar day per the account's
   per-day activity: ``.has-transactions`` (a posting that day) and/or
   ``.has-balance`` (an end-of-day balance that day). Server fetcher:
   ``make_day_availability_fetcher`` (UNION-ALL of ``CAST(posting AS DATE)``
   from ``current_transactions`` + ``CAST(business_day_start AS DATE)``
   from ``current_daily_balances`` matched on ``account_display``).
   Asserted against the DB: the decorated days match the account's actual
   transaction/balance day sets.

Expectations are derived from the deployed DB + the tree (no hardcoded
account/role names — the suite runs against both ``spec_example`` and
``sasquatch_pr`` per the runner matrix, which carry different topologies).
Design lock: ``docs/audits/dm_0_daily_statement_app2_cascade.md``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

import pytest

from recon_gen.apps.l1_dashboard.app import _DAILY_STATEMENT_NAME
from recon_gen.common.db import connect_demo_db
from recon_gen.common.env_keys import RECON_GEN_TEST_L2_INSTANCE
from tests.e2e._drivers.app2 import App2Driver

if TYPE_CHECKING:
    from recon_gen.common.config import Config
    from recon_gen.common.l2 import L2Instance
    from recon_gen.common.tree import App


pytestmark = [
    pytest.mark.e2e,
]


# account_display is computed at the dataset layer (CQ.1 NULL-safe
# COALESCE), NOT stored as a column. The helper SQL computes the SAME
# expression inline so the value the test derives matches the value the
# LinkedValues picker advertises + the day-availability fetcher matches on.
_ACCOUNT_DISPLAY_EXPR = (
    "(COALESCE(account_name, account_id) || ' (' || account_id || ')')"
)


# ---------------------------------------------------------------------------
# Fixtures — read the runner-seeded plain-prefix DB + serve the live L1
# Daily Statement sheet (App2-only).
# ---------------------------------------------------------------------------
#
# DuckDB-cell mechanics (POLICY 1): the runner pre-seeds the PLAIN
# ``cfg.db.table_prefix`` (``recon-gen schema/data/data refresh apply``)
# BEFORE the app2 layer and opens the app2 pytest workers READ-ONLY
# (``RECON_GEN_DB_READ_ONLY=1`` for DuckDB single-writer safety). So this
# file reads the plain ``cfg`` fixture's already-populated tables — it does
# NOT seed its own DB (an ``isolated_cfg`` self-seed would fail on the
# read-only DuckDB handle). The L1 app is built against the SAME L2 the
# runner seeded with (``RECON_GEN_TEST_L2_INSTANCE``), so matview prefixes +
# dataset SQL line up. Mirrors ``test_html2_executives_live.py``.


def _load_l2_instance() -> "L2Instance":
    """The L2 the runner pinned for this cell
    (``RECON_GEN_TEST_L2_INSTANCE``) or bundled ``spec_example`` when
    unset. The app build uses the SAME instance the runner seeded with so
    the matview prefixes + L2-derived dataset SQL match the populated
    tables."""
    from recon_gen.common.l2 import default_l2_instance, load_instance

    override = RECON_GEN_TEST_L2_INSTANCE.get_or_none()
    if override is not None:
        return load_instance(override)
    return default_l2_instance()


def _try_db_connection(cfg: "Config") -> tuple[bool, str]:
    if not cfg.db.url:
        return False, "no cfg.db.url in cfg"
    try:
        conn = connect_demo_db(cfg)
        conn.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"DB connection failed: {exc}"


@pytest.fixture(scope="module")
def dm_l1_app(cfg: "Config") -> "Iterator[App]":
    """L1 Dashboard tree built against the plain ``cfg`` + the runner-seeded
    L2. Registry-isolated so this build's prefixed SQL doesn't clobber a
    session-scoped L1 app's dataset registry entries.

    Hard-gates on ``RECON_GEN_TEST_L2_INSTANCE`` (mirrors the live
    Executives fixture) — without it the build falls back to
    ``spec_example``, which likely won't match the prefix the operator's DB
    was seeded with, producing a misleading "relation does not exist"."""
    from recon_gen.apps.l1_dashboard.app import build_l1_dashboard_app
    from recon_gen.common.dataset_contract import isolated_dataset_registries

    if RECON_GEN_TEST_L2_INSTANCE.get_or_none() is None:
        pytest.skip(
            "live-DB e2e skipped: set RECON_GEN_TEST_L2_INSTANCE to the L2 "
            "YAML matching your seeded DB (the runner injects it per cell)"
        )
    ok, reason = _try_db_connection(cfg)
    if not ok:
        pytest.skip(f"live-DB e2e skipped: {reason}")

    instance = _load_l2_instance()
    with isolated_dataset_registries():
        app = build_l1_dashboard_app(cfg, l2_instance=instance)
        app.validate()
        yield app


@pytest.fixture()
def dm_driver(
    cfg: "Config", dm_l1_app: "App",
) -> "Iterator[App2Driver]":
    """A live-DB App2 driver serving the L1 Daily Statement sheet with the
    cascade-narrowing options fetcher AND the day-availability fetcher
    wired — the same fetcher set ``cli/_html_serve.build_real_dashboards``
    wires in production (POLICY 1). Reads the plain ``cfg`` (read-only,
    runner-seeded plain prefix)."""
    from tests.e2e._harness_html2 import (
        make_live_db_day_availability_fetcher,
        make_live_db_fetchers_for_app,
    )

    assert dm_l1_app.analysis is not None
    data_fetcher, options_search_fetcher = make_live_db_fetchers_for_app(
        tree_app=dm_l1_app, cfg=cfg,
    )
    day_availability_fetcher = make_live_db_day_availability_fetcher(cfg=cfg)
    with App2Driver.serving(
        cfg=cfg,
        tree_app=dm_l1_app,
        sheet=dm_l1_app.analysis.sheets[0],
        data_fetcher=data_fetcher,
        options_search_fetcher=options_search_fetcher,
        day_availability_fetcher=day_availability_fetcher,
        dashboard_id="l1", dashboard_title="L1 (live)",
    ) as driver:
        yield driver


# ---------------------------------------------------------------------------
# DB-derived expectation helpers.
# ---------------------------------------------------------------------------


def _query(cfg: "Config", sql: str) -> list[tuple[Any, ...]]:
    conn = connect_demo_db(cfg)
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
            return list(cur.fetchall())
        finally:
            cur.close()
    finally:
        conn.close()


def _roles_with_counts(cfg: "Config") -> list[tuple[str, int]]:
    """``[(account_role, distinct_account_display_count)]`` for internal-
    scope roles, descending by count. The cascade map narrows the Account
    picker to ``account_role IN (<picked role>)`` over the SAME
    ``current_daily_balances`` source the picker dataset wraps, so this is
    the ground truth for "how many options should the narrowed picker
    show"."""
    prefix = cfg.db.table_prefix
    rows = _query(cfg, (
        f"SELECT account_role, "
        f"       COUNT(DISTINCT {_ACCOUNT_DISPLAY_EXPR}) AS n "
        f"FROM {prefix}_current_daily_balances "
        f"WHERE account_scope = 'internal' AND account_role IS NOT NULL "
        f"GROUP BY account_role "
        f"ORDER BY n DESC, account_role ASC"
    ))
    return [(str(r[0]), int(r[1])) for r in rows]


def _accounts_for_role(cfg: "Config", role: str) -> set[str]:
    """The ``account_display`` set the Account picker should advertise when
    Role=``role`` is picked — the cascade's narrowed universe."""
    prefix = cfg.db.table_prefix
    role_lit = role.replace("'", "''")
    rows = _query(cfg, (
        f"SELECT DISTINCT {_ACCOUNT_DISPLAY_EXPR} "
        f"FROM {prefix}_current_daily_balances "
        f"WHERE account_scope = 'internal' "
        f"  AND account_role = '{role_lit}'"
    ))
    return {str(r[0]) for r in rows if r[0] is not None}


def _internal_account_universe_count(cfg: "Config") -> int:
    prefix = cfg.db.table_prefix
    rows = _query(cfg, (
        f"SELECT COUNT(DISTINCT {_ACCOUNT_DISPLAY_EXPR}) "
        f"FROM {prefix}_current_daily_balances "
        f"WHERE account_scope = 'internal'"
    ))
    return int(rows[0][0])


def _two_roles_for_cascade(cfg: "Config") -> tuple[str, str]:
    """Pick two DISTINCT internal roles for the cascade-clear test. Role A
    is a multi-account role (so picking it then an account is meaningful);
    Role B is any other role. Raises if the L2 has fewer than two roles —
    that's a seed-shape problem, not a test bug."""
    roles = _roles_with_counts(cfg)
    multi = [r for r, n in roles if n >= 1]
    if len(multi) < 2:
        raise RuntimeError(
            f"need ≥2 internal account_roles for the cascade-clear test; "
            f"DB has {len(multi)}: {multi[:5]}"
        )
    # Role A = highest-count (most accounts → most stable pick); Role B =
    # the next distinct role.
    return multi[0], multi[1]


def _day_sets_for_account(
    cfg: "Config", account_display: str, window_start: str, window_end: str,
) -> tuple[set[str], set[str]]:
    """``(transaction_days, balance_days)`` — the DB ground truth the
    day-availability decoration must reproduce. Mirrors the fetcher's
    UNION-ALL SQL exactly (CAST-to-DATE on ``posting`` for tx, on
    ``business_day_start`` for balance; matched on the derived
    ``account_display``; over the same window)."""
    prefix = cfg.db.table_prefix
    acct_lit = account_display.replace("'", "''")
    tx = _query(cfg, (
        f"SELECT DISTINCT CAST(posting AS DATE) "
        f"FROM {prefix}_current_transactions "
        f"WHERE {_ACCOUNT_DISPLAY_EXPR} = '{acct_lit}' "
        f"  AND CAST(posting AS DATE) >= DATE '{window_start}' "
        f"  AND CAST(posting AS DATE) <= DATE '{window_end}'"
    ))
    bal = _query(cfg, (
        f"SELECT DISTINCT CAST(business_day_start AS DATE) "
        f"FROM {prefix}_current_daily_balances "
        f"WHERE {_ACCOUNT_DISPLAY_EXPR} = '{acct_lit}' "
        f"  AND CAST(business_day_start AS DATE) >= DATE '{window_start}' "
        f"  AND CAST(business_day_start AS DATE) <= DATE '{window_end}'"
    ))
    return _iso_set(tx), _iso_set(bal)


def _account_with_both_and_carry(
    cfg: "Config", window_start: str, window_end: str,
) -> str:
    """Find an internal account that is RICH in ``has-balance``-only carry
    days (weekend balances with no posting) AND also has ``has-both`` days
    (a posting day that also carries a balance) in the window — the richest
    decoration shape that makes the carry channel robust to prove.

    Ordering prioritizes ``carry_days DESC`` (then ``both_days DESC``), the
    inverse of the pre-DM-fix ``both_days DESC`` order: that old order
    landed on a carry-POOR account (sasquatch_pr gl-1810: 65 both-days but
    its carry days clump at month edges), so a single rendered month grid
    could miss the only carry day in it. A carry-RICH account has a carry
    day in (nearly) every month, so the month the test opens is guaranteed
    to render one. We also require a small carry threshold so the picked
    account has carry days spread across months (not one lone weekend).
    Raises if none exists (thin window / seed)."""
    prefix = cfg.db.table_prefix
    rows = _query(cfg, (
        f"WITH txd AS ("
        f"  SELECT {_ACCOUNT_DISPLAY_EXPR} AS ad, "
        f"         CAST(posting AS DATE) AS d "
        f"  FROM {prefix}_current_transactions "
        f"  WHERE CAST(posting AS DATE) >= DATE '{window_start}' "
        f"    AND CAST(posting AS DATE) <= DATE '{window_end}'"
        f"), bald AS ("
        f"  SELECT {_ACCOUNT_DISPLAY_EXPR} AS ad, "
        f"         CAST(business_day_start AS DATE) AS d "
        f"  FROM {prefix}_current_daily_balances "
        f"  WHERE account_scope = 'internal' "
        f"    AND CAST(business_day_start AS DATE) >= DATE '{window_start}' "
        f"    AND CAST(business_day_start AS DATE) <= DATE '{window_end}'"
        f") "
        f"SELECT bald.ad, "
        f"  COUNT(DISTINCT CASE WHEN txd.d IS NOT NULL THEN bald.d END) "
        f"    AS both_days, "
        f"  COUNT(DISTINCT CASE WHEN txd.d IS NULL THEN bald.d END) "
        f"    AS carry_days "
        f"FROM bald "
        f"LEFT JOIN txd ON txd.ad = bald.ad AND txd.d = bald.d "
        f"GROUP BY bald.ad "
        f"HAVING COUNT(DISTINCT CASE WHEN txd.d IS NOT NULL THEN bald.d END) > 0 "
        f"   AND COUNT(DISTINCT CASE WHEN txd.d IS NULL THEN bald.d END) > 0 "
        f"ORDER BY carry_days DESC, both_days DESC, bald.ad ASC"
    ))
    if not rows:
        raise RuntimeError(
            "no internal account has BOTH posting-and-balance days AND "
            "balance-only carry days in the window — seed/window too thin"
        )
    return str(rows[0][0])


def _grid_window_for_month(month_start: str) -> tuple[str, str]:
    """The ISO ``(grid_start, grid_end)`` a flatpickr month grid actually
    renders for ``month_start`` (``YYYY-MM-01``).

    flatpickr ALWAYS lays out a fixed 6-week grid (42 cells) starting on
    the leading Sunday on/before the 1st — NOT the minimal number of weeks.
    So the window is exactly ``[leading_sunday, leading_sunday + 41 days]``.
    Empirically confirmed against the live picker (March 2026 starts on a
    Sunday → grid 2026-03-01 .. 2026-04-11, 42 cells).

    The day-availability test asserts the rendered markers against the DB
    sets restricted to THIS window (the grid renders prev/next-month spill
    cells too, so the visible set is the month plus a few adjacent days —
    not just the calendar month). Computing it here keeps the DB ground
    truth and the rendered grid in lock-step regardless of dialect.
    """
    from datetime import date, timedelta

    y, m, _ = (int(p) for p in month_start.split("-"))
    first = date(y, m, 1)
    # flatpickr weeks start Sunday (weekday(): Mon=0..Sun=6 → Sun=6).
    grid_start = first - timedelta(days=(first.weekday() + 1) % 7)
    grid_end = grid_start + timedelta(days=41)  # fixed 6×7 = 42 cells
    return grid_start.isoformat(), grid_end.isoformat()


def _best_month_for_account(
    cfg: "Config", account_display: str, window_start: str, window_end: str,
) -> tuple[str, str, str]:
    """Pick the month grid to open + the specific (both_day, carry_day)
    pair to assert on.

    Returns ``(month_start, both_day, carry_day)`` where ``month_start`` is
    ``YYYY-MM-01`` and both ``both_day`` / ``carry_day`` are ISO dates the
    DB confirms fall inside the SAME rendered 6-week grid (so opening that
    month is guaranteed to render both channels — no edge-of-window miss).
    ``both_day`` is a posting+balance day; ``carry_day`` is a balance-only
    (weekend carry) day.

    The picker renders one month's 6-week grid at a time (the month plus
    prev/next-month spill cells), so the test lands on a month whose
    rendered grid the DB confirms carries both day kinds. Iterates months
    in order, computing each month's true rendered grid window via
    :func:`_grid_window_for_month` and checking the DB sets restricted to
    that grid window carry at least one of each — so the asserted days are
    provably visible, not merely in the calendar month.
    """
    tx_days, bal_days = _day_sets_for_account(
        cfg, account_display, window_start, window_end,
    )
    carry_days = bal_days - tx_days
    both_days = bal_days & tx_days
    months = sorted({d[:7] for d in bal_days})
    for month in months:
        month_start = f"{month}-01"
        grid_start, grid_end = _grid_window_for_month(month_start)
        grid_both = sorted(
            d for d in both_days if grid_start <= d <= grid_end
        )
        grid_carry = sorted(
            d for d in carry_days if grid_start <= d <= grid_end
        )
        if grid_both and grid_carry:
            return month_start, grid_both[0], grid_carry[0]
    raise RuntimeError(
        f"no single rendered month grid in [{window_start}, {window_end}] "
        f"carries both a has-both day AND a carry day for "
        f"{account_display!r} — the marker channels can't both be proven "
        f"in one rendered grid"
    )


def _data_window(cfg: "Config") -> tuple[str, str]:
    """A date window covering the seeded data for the active prefix —
    ``[min(posting), max(business_day_start)]`` widened a few days each
    side. The day-availability fetcher overscans ±30 days around the
    visible month; the test passes an explicit window to the DB ground
    truth + asserts the decorated days are a subset of that window's DB
    sets (the picker can only decorate days it actually rendered)."""
    prefix = cfg.db.table_prefix
    rows = _query(cfg, (
        f"SELECT MIN(CAST(business_day_start AS DATE)), "
        f"       MAX(CAST(business_day_start AS DATE)) "
        f"FROM {prefix}_current_daily_balances "
        f"WHERE account_scope = 'internal'"
    ))
    lo, hi = rows[0]
    return _to_iso(lo), _to_iso(hi)


def _iso_set(rows: list[tuple[Any, ...]]) -> set[str]:
    return {_to_iso(r[0]) for r in rows if r[0] is not None}


def _to_iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())[:10]
    return str(value)[:10]


# ---------------------------------------------------------------------------
# 1 — Role→Account cascade NARROWING (DM.2).
# ---------------------------------------------------------------------------


def test_dm2_role_cascade_narrows_account_options(
    dm_driver: App2Driver, cfg: "Config",
) -> None:
    """DM.2 — picking a Role narrows the Account dropdown to that role's
    accounts (strictly fewer than the full internal universe, and every
    option belongs to the role), and picking a DIFFERENT role swaps the
    option set to the second role's accounts.

    Ground truth (per [[tree-is-source-of-truth]]): the narrowed option
    count == the role's distinct ``account_display`` count from
    ``current_daily_balances`` (the SAME source the picker dataset wraps),
    NOT the full universe. A cascade misfire (the predicate not applied,
    or applied with the wrong match column) surfaces as the option count
    matching the full universe or the wrong role's set.
    """
    role_a, role_b = _two_roles_for_cascade(cfg)
    accounts_a = _accounts_for_role(cfg, role_a)
    accounts_b = _accounts_for_role(cfg, role_b)
    universe = _internal_account_universe_count(cfg)

    # Pre-conditions on the seed shape — fail loud here rather than as a
    # confusing option-count mismatch below.
    assert accounts_a, f"role {role_a!r} has no internal accounts in the DB"
    assert accounts_b, f"role {role_b!r} has no internal accounts in the DB"
    assert len(accounts_a) < universe, (
        f"role {role_a!r} covers the WHOLE internal universe "
        f"({len(accounts_a)} == {universe}) — can't prove narrowing; "
        f"pick a role with a strict subset"
    )

    dm_driver.open("l1", sheet=_DAILY_STATEMENT_NAME)
    dm_driver.wait_loaded("Opening Balance")

    # No role picked yet → the Account picker's seed page is the full
    # internal universe (or the PICKER_PAGE_SIZE=100 cap). Both L2s' internal
    # universe is < 100 so we can compare counts directly; guard anyway.
    full_seed = dm_driver.filter_options("Account")
    assert universe <= 100, (
        f"internal account universe ({universe}) exceeds the picker seed "
        f"page cap (100) — this DB-count comparison would need the "
        f"per-keystroke search path instead"
    )
    assert len(full_seed) == universe, (
        f"pre-cascade Account seed page shows {len(full_seed)} options, "
        f"expected the full internal universe {universe}. Cascade leaked "
        f"a narrowing onto the no-role-picked state?"
    )

    # Pick Role A → Account options must narrow to A's accounts.
    dm_driver.pick_filter("Role", [role_a])
    opts_a = set(dm_driver.filter_options("Account"))
    assert opts_a == accounts_a, (
        f"after Role={role_a!r}, Account options ({len(opts_a)}) != the "
        f"role's DB account set ({len(accounts_a)}). "
        f"Only-in-options: {sorted(opts_a - accounts_a)[:5]}; "
        f"only-in-DB: {sorted(accounts_a - opts_a)[:5]}. "
        f"Cascade narrowing (AND account_role IN (:pL1DsRole_cascade_0)) "
        f"either didn't apply or matched the wrong column."
    )
    assert len(opts_a) < universe, (
        f"Role={role_a!r} did NOT narrow: {len(opts_a)} options == full "
        f"universe {universe}. The cascade predicate is a no-op."
    )

    # Pick Role B → options must SWAP to B's accounts (proves the narrowing
    # is re-evaluated per source change, not stuck on A).
    dm_driver.pick_filter("Role", [role_b])
    opts_b = set(dm_driver.filter_options("Account"))
    assert opts_b == accounts_b, (
        f"after switching to Role={role_b!r}, Account options "
        f"({len(opts_b)}) != the role's DB account set "
        f"({len(accounts_b)}). only-in-options: "
        f"{sorted(opts_b - accounts_b)[:5]}; only-in-DB: "
        f"{sorted(accounts_b - opts_b)[:5]}."
    )
    dm_driver.screenshot()


# ---------------------------------------------------------------------------
# 2 — Cascade CLEAR-on-source-change (DM/BR.1).
# ---------------------------------------------------------------------------


def test_dm_cascade_clears_account_on_role_change(
    dm_driver: App2Driver, cfg: "Config",
) -> None:
    """DM/BR.1 — when the Role source changes, the Account target CLEARS its
    selected value (the prior pick belongs to the OLD role's scope).

    ``bootstrap.js::wireTomSelect`` reads ``data-cascade-source-param`` off
    the Account ``<select>``, listens for the source's ``change``, and
    calls ``tomInstance.clear() + clearOptions()``. Without the clear, the
    Account picker would keep advertising (and the form would submit) an
    account that no longer belongs to the picked role — a stale-pick bug.

    Shape: pick Role A → pick an Account from A → assert Account has a
    value → change Role to B → assert Account value is now empty/cleared.
    """
    role_a, role_b = _two_roles_for_cascade(cfg)
    accounts_a = sorted(_accounts_for_role(cfg, role_a))
    assert accounts_a, f"role {role_a!r} has no internal accounts in the DB"
    picked_account = accounts_a[0]

    dm_driver.open("l1", sheet=_DAILY_STATEMENT_NAME)
    dm_driver.wait_loaded("Opening Balance")

    # Pick Role A then an account that belongs to A.
    dm_driver.pick_filter("Role", [role_a])
    opts_a = set(dm_driver.filter_options("Account"))
    assert picked_account in opts_a, (
        f"DB-derived account {picked_account!r} not advertised after "
        f"Role={role_a!r} (options: {sorted(opts_a)[:5]}) — cascade "
        f"narrowing out of sync with the DB"
    )
    dm_driver.pick_filter("Account", [picked_account])

    # The Account picker now holds the picked value.
    value_before = dm_driver.filter_value("Account")
    assert value_before == picked_account, (
        f"Account picker should hold {picked_account!r} after the pick; "
        f"got {value_before!r}. (If None, the pick didn't register — a "
        f"different bug than the clear-on-change this test targets.)"
    )

    # Change the Role source → the cascade-clear listener must drop the
    # stale Account value.
    dm_driver.pick_filter("Role", [role_b])
    value_after = dm_driver.filter_value("Account")
    assert value_after is None, (
        f"Account value should be CLEARED after Role changed "
        f"{role_a!r}→{role_b!r} (the prior pick {picked_account!r} belongs "
        f"to the old role); got {value_after!r}. The "
        f"data-cascade-source-param change listener in wireTomSelect "
        f"(tomInstance.clear()) didn't fire."
    )
    dm_driver.screenshot()


# ---------------------------------------------------------------------------
# 3 — Day-availability decoration (DM.3).
# ---------------------------------------------------------------------------


def test_dm3_day_availability_decorates_picked_account(
    dm_driver: App2Driver, cfg: "Config",
) -> None:
    """DM.3 — after an Account is picked, the Business Day flatpickr
    decorates each visible calendar day per the account's per-day activity:
    ``.has-transactions`` (a posting that day) and/or ``.has-balance`` (an
    end-of-day balance that day).

    Ground truth (per [[tree-is-source-of-truth]]): the account's
    ``transaction_days`` / ``balance_days`` sets straight from the DB
    (same UNION-ALL shape the fetcher issues). The picked account is the
    DB-richest decoration shape — it has BOTH ``has-both`` days (a posting
    day that also carries a balance, e.g. a business day) AND
    ``has-balance``-only carry days (a weekend end-of-day balance with no
    posting). The test:

    - opens the calendar on the month the account has data, reads the
      rendered day markers via the driver, and
    - asserts every decorated day matches the DB (a ``transactions`` marker
      ⟺ the day is in the DB tx set; a ``balance`` marker ⟺ in the DB
      balance set), AND that at least one ``has-both`` day and one
      ``balance``-only carry day actually rendered (proving the two
      independent channels both light up).

    DECORATION not restriction: every day stays clickable; the test only
    asserts on the markers, never on pickability.
    """
    window_start, window_end = _data_window(cfg)
    account = _account_with_both_and_carry(cfg, window_start, window_end)
    tx_days, bal_days = _day_sets_for_account(
        cfg, account, window_start, window_end,
    )
    assert tx_days and bal_days, (
        f"account {account!r} has tx_days={len(tx_days)} "
        f"bal_days={len(bal_days)} — picker chose an account without both"
    )
    # The month to open the calendar on + the EXACT (both_day, carry_day)
    # pair the DB confirms fall inside that month's rendered 6-week grid —
    # so opening the month is guaranteed to render both marker channels
    # (no edge-of-window miss). The picker's default month is the live-
    # clock as_of anchor, which can be far from the LOCKED_ANCHOR-seeded
    # data, so we navigate explicitly.
    open_on, expected_both_day, expected_carry_day = _best_month_for_account(
        cfg, account, window_start, window_end,
    )
    # The DB ground truth restricted to the EXACT grid the open month
    # renders (the month plus prev/next-month spill cells). The rendered
    # markers are a subset of this; comparing against the grid window
    # rather than the full data window keeps the DB and the rendered grid
    # in lock-step.
    grid_start, grid_end = _grid_window_for_month(open_on)
    grid_tx_days = {d for d in tx_days if grid_start <= d <= grid_end}
    grid_bal_days = {d for d in bal_days if grid_start <= d <= grid_end}

    dm_driver.open("l1", sheet=_DAILY_STATEMENT_NAME)
    dm_driver.wait_loaded("Opening Balance")

    # Pick the account directly (no Role needed — the picker advertises all
    # internal accounts; the cascade source is optional). The Business Day
    # picker's onDayCreate keys its decoration off this picked value.
    dm_driver.pick_filter("Account", [account])

    # Open the calendar, navigate to the data-window month, read the
    # decorated days. The driver settles on a STABLE marker snapshot (the
    # async onDayCreate flush has quiesced) — not the first non-empty read,
    # which used to catch a mid-flush partial (the carry-day CI flake).
    markers = dm_driver.day_availability("Business Day", open_on=open_on)
    assert markers, (
        f"Business Day picker rendered NO decorated days for "
        f"{account!r}. Expected markers on the days in the DB tx set "
        f"({len(grid_tx_days)}) ∪ balance set ({len(grid_bal_days)}) "
        f"within the rendered grid [{grid_start}, {grid_end}]. The "
        f"onDayCreate fetch (day-availability endpoint) returned empty or "
        f"the markers never applied — check the day_availability_fetcher "
        f"wiring + the param_pL1DsAccount value the JS reads."
    )

    # Every decorated day must agree with the DB: a 'transactions' marker
    # ⟺ the day is in the DB tx set; a 'balance' marker ⟺ in the DB
    # balance set. Compared against the DB sets restricted to the rendered
    # grid window so the rendered (subset) markers line up exactly.
    mismatches: list[str] = []
    for iso, states in markers.items():
        has_tx_marker = "transactions" in states
        has_bal_marker = "balance" in states
        if has_tx_marker != (iso in grid_tx_days):
            mismatches.append(
                f"{iso}: transactions marker={has_tx_marker} but "
                f"DB-tx={iso in grid_tx_days}"
            )
        if has_bal_marker != (iso in grid_bal_days):
            mismatches.append(
                f"{iso}: balance marker={has_bal_marker} but "
                f"DB-balance={iso in grid_bal_days}"
            )
    assert not mismatches, (
        f"day-availability markers disagree with the DB for {account!r} "
        f"in grid [{grid_start}, {grid_end}]:\n"
        + "\n".join(mismatches[:10])
    )

    # The full rendered set must match the DB's grid-restricted union
    # EXACTLY (not just "some marker exists") — proves the decoration is
    # complete, not a mid-flush partial. Every DB day in the grid window
    # carries a marker (tx ∪ balance); no extra/missing day.
    expected_union = grid_tx_days | grid_bal_days
    rendered_days = set(markers.keys())
    assert rendered_days == expected_union, (
        f"rendered marker days != DB grid-window union for {account!r} "
        f"in grid [{grid_start}, {grid_end}].\n"
        f"  only-rendered: {sorted(rendered_days - expected_union)[:10]}\n"
        f"  only-in-DB:    {sorted(expected_union - rendered_days)[:10]}\n"
        f"(if only-in-DB is non-empty the read returned before the async "
        f"onDayCreate flush settled — the partial-decoration race)"
    )

    # The two channels are INDEPENDENT (fill vs ring). Assert the SPECIFIC
    # DB-derived days decorate the right channel: the both-day lights both
    # .has-transactions AND .has-balance; the carry-day lights .has-balance
    # ONLY (no .has-transactions). Landing on exact days (vs "some carry
    # day exists somewhere") makes the assertion deterministic + dialect-
    # invariant — the days were proven to be in this rendered grid.
    assert set(markers.get(expected_both_day, [])) == {
        "transactions", "balance"
    }, (
        f"expected has-both day {expected_both_day} for {account!r} to "
        f"decorate BOTH channels; got {markers.get(expected_both_day)!r} "
        f"(rendered markers: {dict(sorted(markers.items())[:8])})"
    )
    assert set(markers.get(expected_carry_day, [])) == {"balance"}, (
        f"expected balance-only carry day {expected_carry_day} for "
        f"{account!r} to decorate .has-balance ONLY (no .has-transactions); "
        f"got {markers.get(expected_carry_day)!r}. The balance-only "
        f"(weekend carry) channel must light independently of the "
        f"transaction channel (rendered markers: "
        f"{dict(sorted(markers.items())[:8])})"
    )
    dm_driver.screenshot()
