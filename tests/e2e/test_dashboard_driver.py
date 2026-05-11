"""X.2.q.0 spike — port one e2e check onto the ``DashboardDriver``
protocol, proving a single test body reads cleanly through the driver
(no Playwright in the test).

Today the ``driver`` fixture only yields the App 2 leg (``["app2"]``);
when ``QsEmbedDriver`` lands (X.2.q.1) the ``"qs"`` param gets added and
the same bodies verify both renderers. Gated by ``QS_GEN_E2E`` like
every e2e (``conftest.py`` matches on path) and skips cleanly without
Playwright.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")

from tests.e2e._drivers import App2Driver, DashboardDriver


@pytest.fixture(params=["app2"])
def driver(request: pytest.FixtureRequest) -> Iterator[DashboardDriver]:
    if request.param == "app2":
        with App2Driver.smoke() as d:
            yield d
    else:  # "qs" — X.2.q.1: QsEmbedDriver
        pytest.skip("QsEmbedDriver not implemented yet (X.2.q.1)")


def test_showcase_table_rows(driver: DashboardDriver) -> None:
    """The Showcase sheet's Account Balances table shows page 1 — 10
    rows of {account_id, account_name, balance, status}, starting at
    acct-001. (Pure assertion on wrapped data — no DOM in the test.)"""
    driver.open("smoke", sheet="showcase")
    driver.wait_loaded("Account Balances")
    rows = driver.table_rows("Account Balances")
    assert len(rows) == 10
    assert list(rows[0].keys()) == [
        "account_id", "account_name", "balance", "status",
    ]
    assert rows[0]["account_id"] == "acct-001"
    assert rows[0]["status"] == "closed"


def test_showcase_kpi_renders_a_value(driver: DashboardDriver) -> None:
    driver.open("smoke", sheet="showcase")
    driver.wait_loaded("Open Exceptions")
    value = driver.kpi_value("Open Exceptions")
    assert value is not None
    assert value.strip() != ""


def test_showcase_lists_every_visual(driver: DashboardDriver) -> None:
    """Showcase = every renderer in one place (Sankey / ForceGraph /
    KPI / BarChart / LineChart / Table)."""
    driver.open("smoke", sheet="showcase")
    titles = driver.visual_titles()
    for expected in (
        "Chain Sankey",
        "Account Topology — Force Layout",
        "Open Exceptions",
        "Activity by Status",
        "Daily Volume",
        "Account Balances",
    ):
        assert expected in titles, expected
