"""X.2.q.0 spike — port one e2e check onto the ``DashboardDriver``
protocol, proving a single test body reads cleanly through the driver
(no Playwright in the test).

Two legs:

- The ``driver`` fixture (App 2 only) drives the bundled *smoke app* —
  no DB, no AWS — exercising every renderer through ``App2Driver``.
- The ``qs_driver`` fixture (QuickSight only) drives a *deployed*
  dashboard through ``QsEmbedDriver``, proving the QS facade works
  against a live embed. (Needs a live QuickSight account +
  ``QS_E2E_USER_ARN`` — skips cleanly without.)

X.2.q.3 will fold a real app (L1) onto a single ``@parametrize(["qs",
"app2"])`` fixture so one body verifies both renderers. Gated by
``QS_GEN_E2E`` like every e2e (``conftest.py`` matches on path) and
skips cleanly without Playwright.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")

from tests.e2e._drivers import App2Driver, DashboardDriver, QsEmbedDriver

# `qs_driver` lives in conftest.py — shared with the other QS browser
# e2e tests (X.2.q.3).


@pytest.fixture
def driver() -> Iterator[DashboardDriver]:
    """The App 2 leg — the bundled smoke app, no DB, no AWS."""
    with App2Driver.smoke() as d:
        yield d


def test_showcase_table_rows(driver: DashboardDriver) -> None:
    """The Showcase sheet's Account Balances table shows page 1 — 10
    rows of {account_id, account_name, balance, status}, starting at
    acct-001. (Pure assertion on wrapped data — no DOM in the test.)"""
    driver.open("smoke", sheet="Showcase")
    driver.wait_loaded("Account Balances")
    rows = driver.table_rows("Account Balances")
    assert len(rows) == 10
    assert list(rows[0].keys()) == [
        "account_id", "account_name", "balance", "status",
    ]
    assert rows[0]["account_id"] == "acct-001"
    assert rows[0]["status"] == "closed"


def test_showcase_kpi_renders_a_value(driver: DashboardDriver) -> None:
    driver.open("smoke", sheet="Showcase")
    driver.wait_loaded("Open Exceptions")
    value = driver.kpi_value("Open Exceptions")
    assert value is not None
    assert value.strip() != ""


def test_showcase_lists_every_visual(driver: DashboardDriver) -> None:
    """Showcase = every renderer in one place (Sankey / ForceGraph /
    KPI / BarChart / LineChart / Table)."""
    driver.open("smoke", sheet="Showcase")
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


# -- App2 leg: write verbs (X.2.q.2) -----------------------------------------
#
# The stub fetcher echoes filter params into the visual data, so a
# round-trip is observable without a DB: `_showcase_kpi`'s headline value
# = 47 + (sum of ord(c) for the selected `view` value) % 50 — so picking
# View=detail moves it 47 → 74, and clearing puts it back. The other
# write verbs (set_date_range / set_slider / cross_link) don't feed a
# value the protocol can read in the smoke app, so they're smoke-tested
# for "runs + the page survives the re-fetch"; the "filter narrows
# table_rows" assertion lands against a real app in X.2.q.3 / X.2.l.4.d.


def test_app2_pick_filter_changes_kpi(driver: DashboardDriver) -> None:
    driver.open("smoke", sheet="Showcase")
    driver.wait_loaded("Open Exceptions")
    before = driver.kpi_value("Open Exceptions")
    driver.pick_filter("View", ["detail"])
    driver.wait_loaded("Open Exceptions")
    after = driver.kpi_value("Open Exceptions")
    assert before is not None and after is not None
    assert after != before, f"KPI unchanged after pick_filter: {before!r}"


def test_app2_clear_filters_resets_kpi(driver: DashboardDriver) -> None:
    driver.open("smoke", sheet="Showcase")
    driver.wait_loaded("Open Exceptions")
    base = driver.kpi_value("Open Exceptions")
    driver.pick_filter("View", ["detail"])
    driver.wait_loaded("Open Exceptions")
    assert driver.kpi_value("Open Exceptions") != base
    driver.clear_filters()
    driver.wait_loaded("Open Exceptions")
    assert driver.kpi_value("Open Exceptions") == base


def test_app2_set_date_range_and_slider_survive_refetch(
    driver: DashboardDriver,
) -> None:
    driver.open("smoke", sheet="Showcase")
    driver.set_date_range("2030-01-01", "2030-01-31")
    driver.set_slider("Amount", 1000, 50_000)
    # Both verbs block on the re-fetch; the page is still a live dashboard.
    assert "Daily Volume" in driver.visual_titles()


def test_app2_pick_filter_persists_in_underlying_select(
    driver: DashboardDriver,
) -> None:
    """Regression — after ``pick_filter``, the underlying
    ``<select name="param_view">``'s ``.value`` matches the pick.

    Tom Select's MutationObserver/Sync re-runs when we touch the
    underlying ``<select>``'s options, and (pre-fix) would overwrite a
    manual ``option.selected = true`` with its own (empty) ``items``
    store. Net effect: the pick disappeared, the form serialised
    ``param_X=`` empty, and visuals re-queried unfiltered. Fixed by
    routing through ``select.tomselect.setValue(...)`` when the widget
    is wired. This test pins the contract directly against the
    underlying form element — ``pick_filter`` must leave
    ``select.value`` equal to the picked value, full stop. (The KPI-
    delta assertion in ``test_app2_pick_filter_changes_kpi`` proves
    the round-trip; this proves the form element itself.)
    """
    driver.open("smoke", sheet="Showcase")
    driver.wait_loaded("Open Exceptions")
    driver.pick_filter("View", ["detail"])
    driver.wait_loaded("Open Exceptions")
    page = driver.page  # type: ignore[attr-defined]  # WHY: DashboardDriver protocol doesn't expose `page` -- this test reaches into the App2Driver escape hatch (smoke-only) to assert against the underlying DOM
    value = page.evaluate(
        """() => {
            const s = document.querySelector('select[name="param_view"]');
            return s ? s.value : null;
        }"""
    )
    assert value == "detail", (
        f"After pick_filter('View', ['detail']), the underlying "
        f"select.value should be 'detail'; got {value!r}. TomSelect "
        f"sync likely overwrote the manual selection — see "
        f"App2Driver.pick_filter's setValue fallback comment."
    )


def test_app2_filter_options_lists_dropdown_values(
    driver: DashboardDriver,
) -> None:
    driver.open("smoke", sheet="Showcase")
    # The smoke app's "View" ParameterDropdown advertises these three.
    assert driver.filter_options("View") == ["summary", "detail", "drill"]
    # Multi-select reads the same way.
    assert set(driver.filter_options("Rails")) == {
        "ach", "wire", "check", "internal", "zba",
    }


def test_app2_goto_sheet(driver: DashboardDriver) -> None:
    driver.open("smoke", sheet="MoneyTrail")
    assert "Money Trail — Chain Sankey" in driver.visual_titles()
    driver.goto_sheet("Showcase")
    assert "Account Balances" in driver.visual_titles()


# -- QuickSight leg ----------------------------------------------------------
#
# Drives a *deployed* L1 dashboard through QsEmbedDriver — proves the QS
# facade (open / goto_sheet / visual_titles / wait_loaded / screenshot)
# works against a live embed. The L1 dashboard's deployed DashboardId
# derives from cfg + the targeted L2 instance via the shared e2e
# `l1_dashboard_id` fixture (conftest). No assertion on a *specific*
# visual title — a stale deploy may have renamed one; the point is the
# verbs work, returning plain Python.

@pytest.mark.e2e
@pytest.mark.browser
def test_qs_l1_dashboard_drift_sheet_lists_visuals(
    qs_driver: QsEmbedDriver, l1_dashboard_id: str,
) -> None:
    qs_driver.open(l1_dashboard_id, sheet="Drift")
    titles = qs_driver.visual_titles()
    assert titles, f"L1 dashboard {l1_dashboard_id!r} Drift sheet rendered no visual titles"
    # Exercise wait_loaded against whatever rendered — must not raise.
    qs_driver.wait_loaded(titles[0])


@pytest.mark.e2e
@pytest.mark.browser
def test_qs_l1_dashboard_screenshot(
    qs_driver: QsEmbedDriver, l1_dashboard_id: str, tmp_path,
) -> None:
    qs_driver.open(l1_dashboard_id)
    png = qs_driver.screenshot(tmp_path / "l1_initial.png")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (tmp_path / "l1_initial.png").exists()


@pytest.mark.e2e
@pytest.mark.browser
def test_qs_table_rows_well_formed(
    qs_driver: QsEmbedDriver, l1_dashboard_id: str,
) -> None:
    """QsEmbedDriver.table_rows reads a deployed table as header-keyed
    dicts. Data is whatever's seeded against the deployed dashboard's DB
    — assert structure; if rows came back, their keys are the column
    headers and the dicts are non-empty."""
    qs_driver.open(l1_dashboard_id, sheet="Info")
    qs_driver.wait_loaded("Matview Status")
    rows = qs_driver.table_rows("Matview Status")
    assert isinstance(rows, list)
    if rows:
        assert all(isinstance(r, dict) and r for r in rows)
        assert all(isinstance(k, str) and k for k in rows[0])
