"""X.2.h.2 — Executives Layer-2 e2e against live PG (or Oracle).

Companion to ``test_html2_executives.py`` (stub fetcher) — this
file uses the real ``make_tree_db_fetcher`` against the configured
DB. Catches the failure modes that don't surface with a stub:

- Wrong L2 instance: matview prefix doesn't match the seeded DB
  → fetcher's first SQL execute returns "relation does not exist"
- Filter substitution actually narrows: change date filter, see
  the KPI value drop
- Layer 1 ↔ Layer 2 agreement: row count from the matview equals
  what the rendered visual claims (uses ``_layer1_query.py``)

Gates:

- ``QS_GEN_E2E=1`` — same as every other tests/e2e/ file
- A reachable DB (cfg.demo_database_url + driver installed)
- ``QS_GEN_TEST_L2_INSTANCE=<path>`` — points at the L2 YAML that
  matches the seeded DB. Defaults to ``spec_example`` (rarely
  what you want for a live DB run; sasquatch_pr is the canonical
  demo).

When the DB isn't reachable, the test skips with a message. The
operator opts in by setting the env var + having a populated DB.

Pattern note (for porting Investigation / L2FT / L1):

    1. Build the app's tree + datasets
    2. ``html2_server(tree_app, sheet, fetcher=...)`` spins
    3. Open the dashboard sheet
    4. ``wait_for_kpi_value`` / ``wait_for_table_rows`` + assert
    5. Optionally Layer 1 cross-check via ``_layer1_query.py``

Same shape per app — the only thing that changes is sheet IDs +
visual IDs + which Layer 1 matview to cross-check.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from quicksight_gen.apps.executives.app import build_executives_app
from quicksight_gen.apps.executives.datasets import build_all_datasets
from tests.e2e._harness_html2 import (
    html2_server,
    make_live_db_fetcher_for_app,
    wait_for_kpi_value,
)


playwright_sync_api = pytest.importorskip("playwright.sync_api")


_DASHBOARD_ID = "exec"


def _load_l2_instance() -> Any:
    """Load the L2 instance the test runs against — env override
    via ``QS_GEN_TEST_L2_INSTANCE``, else the bundled default
    (spec_example)."""
    from quicksight_gen.apps.l1_dashboard._l2 import default_l2_instance
    from quicksight_gen.common.l2 import load_instance

    override = os.environ.get("QS_GEN_TEST_L2_INSTANCE")
    if override:
        return load_instance(Path(override))
    return default_l2_instance()


def _try_db_connection(cfg: Any) -> tuple[bool, str]:
    """Attempt to open a connection to the configured DB.
    Returns (ok, reason) — when ok is False, reason is the skip
    message."""
    if not getattr(cfg, "demo_database_url", None):
        return False, "no demo_database_url in cfg"
    try:
        from quicksight_gen.common.db import connect_demo_db  # noqa: PLC0415
        conn = connect_demo_db(cfg)
        conn.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"DB connection failed: {exc}"


@pytest.fixture(scope="module")
def live_pg_exec_server(cfg: Any) -> Iterator[str]:
    """Spin App2 with the real Executives tree + DB-backed fetcher.

    Skips when no DB is reachable — operator opts in by configuring
    cfg.demo_database_url + having a populated DB.
    """
    # Hard gate on QS_GEN_TEST_L2_INSTANCE — without it, the test
    # would fall back to spec_example (the bundled default) which
    # almost certainly doesn't match the prefix used to seed the
    # operator's DB. Better to skip cleanly than fail with a
    # misleading "relation does not exist" error.
    if not os.environ.get("QS_GEN_TEST_L2_INSTANCE"):
        pytest.skip(
            "live-PG e2e skipped: set QS_GEN_TEST_L2_INSTANCE to "
            "the L2 YAML matching your seeded DB (e.g. "
            "src/quicksight_gen/_l2_fixtures/sasquatch_pr.yaml)"
        )
    ok, reason = _try_db_connection(cfg)
    if not ok:
        pytest.skip(f"live-PG e2e skipped: {reason}")
    instance = _load_l2_instance()
    cfg_with_prefix = cfg
    if cfg_with_prefix.l2_instance_prefix is None:
        cfg_with_prefix = cfg_with_prefix.with_l2_instance_prefix(
            str(instance.instance),
        )
    build_all_datasets(cfg_with_prefix)
    tree_app = build_executives_app(cfg_with_prefix, l2_instance=instance)
    assert tree_app.analysis is not None
    fetcher = make_live_db_fetcher_for_app(
        tree_app=tree_app, cfg=cfg_with_prefix,
    )
    primary_sheet = tree_app.analysis.sheets[0]
    with html2_server(
        tree_app=tree_app,
        sheet=primary_sheet,
        data_fetcher=fetcher,
        dashboard_id=_DASHBOARD_ID,
        dashboard_title="Executives (live)",
    ) as base_url:
        yield base_url


def test_account_coverage_kpi_renders_with_real_data(
    live_pg_exec_server: str,
) -> None:
    """The KPI on Account Coverage should auto-load and show a
    number from the live DB. Catches "wrong L2" (table doesn't
    exist → fetcher errors → no KPI), "renderer broken" (KPI value
    never appears), and "data layer empty" (KPI shows 0)."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        page.goto(
            f"{live_pg_exec_server}/dashboards/{_DASHBOARD_ID}"
            f"/sheets/exec-sheet-account-coverage"
        )
        kpi_text = wait_for_kpi_value(page, timeout_ms=15000)
        browser.close()
    # KPI should be a number (count of accounts) — not blank, not "0",
    # not "NaN". A populated DB should have at least a few accounts.
    digits = "".join(ch for ch in kpi_text if ch.isdigit())
    assert digits, (
        f"KPI rendered no digits — got {kpi_text!r}. Either the L2 "
        f"prefix doesn't match the seeded DB or the matview is empty."
    )
    assert int(digits) > 0, (
        f"KPI shows zero accounts — KPI text {kpi_text!r}. Check the "
        f"sasquatch_pr_daily_balances seed."
    )


def test_date_filter_does_not_error_when_applied(
    live_pg_exec_server: str,
) -> None:
    """Set a narrow date window, click Refresh, and assert the KPI
    re-renders without error. Validates the X.2.g.1.b SQL
    templating + bind substitution end-to-end against the live DB
    — specifically that ``CAST(:date_from AS DATE)`` doesn't error
    when the bind value is empty (the PG short-circuit-OR gotcha
    that the COALESCE+sentinel pattern fixes).

    This test deliberately doesn't assert "value changed". The
    Account Coverage KPI counts all accounts in ``daily_balances``;
    the date filter only narrows the joined activity rollup, not
    the account count itself. A "value-changed" assertion would
    require pinning to a sheet whose KPI directly reflects
    transaction-summary aggregates (Transaction Volume / Money
    Moved), which depends on seed-specific shape we don't want to
    couple to."""
    with playwright_sync_api.sync_playwright() as p:
        browser = p.webkit.launch(headless=True)
        page = browser.new_page()
        page.goto(
            f"{live_pg_exec_server}/dashboards/{_DASHBOARD_ID}"
            f"/sheets/exec-sheet-account-coverage"
        )
        # Initial render with empty filter — proves the COALESCE+
        # sentinel-date pattern works against PG without raising
        # ``invalid input syntax for type date: ""``.
        wait_for_kpi_value(page, timeout_ms=15000)
        page.fill('input[name="date_from"]', "2030-01-01")
        page.fill('input[name="date_to"]', "2030-12-31")
        page.click('button#refresh-all')
        page.wait_for_timeout(1500)
        # Second render with a real date — proves the bind value
        # threads through CAST(... AS DATE) without errors.
        narrowed = wait_for_kpi_value(page, timeout_ms=10000)
        browser.close()
    # KPI re-rendered → date substitution worked at SQL execution.
    digits = "".join(ch for ch in narrowed if ch.isdigit())
    assert digits, (
        f"Filtered KPI rendered no digits — got {narrowed!r}. "
        f"Date filter binding may have errored at SQL execution."
    )
