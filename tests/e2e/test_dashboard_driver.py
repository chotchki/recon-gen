"""X.2.q.0 spike — port one e2e check onto the ``DashboardDriver``
protocol, proving a single test body reads cleanly through the driver
(no Playwright in the test).

The ``driver`` fixture (App 2) drives the bundled *smoke app* — no DB,
no AWS — exercising the renderer through ``App2Driver``.

DW.6 (2026-06-27) — QuickSight removed; the prior QS leg (``qs_driver``
+ ``QsEmbedDriver``, deployed-dashboard embed) is gone. App2 is the sole
renderer. e2e tests collect by default (DJ.1 retired the RECON_GEN_E2E
gate) and skip cleanly without Playwright.
"""

from __future__ import annotations

from typing import Any, cast

from collections.abc import Iterator

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")

from tests._marks import Need, Tier, needs, tier
from tests.e2e._drivers import App2Driver, DashboardDriver


pytestmark = [
    tier(Tier.APP2),
    needs(Need.PLAYWRIGHT),
]


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


def test_app2_set_slider_survives_refetch(
    driver: DashboardDriver,
) -> None:
    """Phase BM — pre-BM the smoke app's Showcase sheet rendered the
    universal hidden ``date_from`` / ``date_to`` inputs on every
    data-bearing sheet, so ``set_date_range`` had a target. Post-BM
    each ParameterDateTimePicker is its own ParameterDateSpec
    (``param_<name>=YYYY-MM-DD`` URL key) gated on
    ``mapped_dataset_params`` — the smoke app declares no date
    pickers, so ``set_date_range`` has nothing to drive there. The
    slider half of the original test still pins the refetch contract.
    """
    driver.open("smoke", sheet="Showcase")
    driver.set_slider("Amount", 1000, 50_000)
    # The slider write blocks on re-fetch; the page is still a live dashboard.
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
    page = cast("Any", driver).page  # WHY Any: DashboardDriver protocol doesn't expose page; App2Driver escape hatch (smoke-only)
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
    assert set(driver.filter_options("Rails")) == {  # typing-smell: ignore[no-inline-production-constants]: smoke-app filter label declared inline at common/html/_smoke_app.py; coincidentally matches _RAILS_NAME (L2FT sheet name) — different surface
        "ach", "wire", "check", "internal", "zba",  # typing-smell: ignore[no-raw-enum-equality]: smoke-app rail label, not Scope; coincides with the SCOPE_INTERNAL literal but lives in the rail filter universe
    }


def test_app2_goto_sheet(driver: DashboardDriver) -> None:
    driver.open("smoke", sheet="MoneyTrail")
    assert "Money Trail — Chain Sankey" in driver.visual_titles()
    driver.goto_sheet("Showcase")
    assert "Account Balances" in driver.visual_titles()
