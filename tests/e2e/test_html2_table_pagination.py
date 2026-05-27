# pyright: reportUnknownLambdaType=false, reportUnknownMemberType=false
# BF.4/F: Playwright `expect_response` lambda receives a `Response` whose `.url`
# is `str`, but Playwright's stubs leak `Unknown` through the lambda parameter.
# These tests exercise raw Playwright as App2Driver escape-hatch; suppressing
# Unknown keeps the lambda calls readable.
"""X.2.h.5 — App 2's Table sort + pagination round-trip (browser).

The Table renderer (``bootstrap.js::renderTable``) renders the server's
page of rows + a "1–N of M" pager + sortable headers; clicking
Prev/Next or a header fires an HTMX swap with new ``?page_offset`` /
``?page_size`` / ``?sort_column=<col>:<asc|desc>`` query params and
re-renders. The *server* side (``_tree_fetcher`` wraps the dataset SQL
with ``LIMIT/OFFSET`` + ``COUNT(*) OVER ()`` + ``ORDER BY`` honoring
those params) is unit-tested in ``test_html_tree_fetcher.py``; this
file is the **browser round-trip** — click → refetch-with-the-right-
params → re-render — driven through ``App2Driver`` against the bundled
smoke app's Showcase "Account Balances" table (its stub fetcher honors
``page_offset`` / ``page_size`` / ``sort_column``, so the demo is
interactive without a DB / AWS). App2-only — QuickSight does its own
client-side virtualization, not server-side page-offset, so there's no
``[qs, app2]`` parametrization here.

Behind ``RECON_GEN_E2E`` like every ``tests/e2e/`` file; skips cleanly
without Playwright. Runs in the runner's ``app2`` layer.
"""

from __future__ import annotations

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")  # noqa: F841

from tests.e2e._drivers import App2Driver


_TABLE = "Account Balances"
# The smoke stub serves 10 rows per page, starting at acct-001 in the
# default (insertion) order; offset 10 is the second page → acct-011.
_PAGE_SIZE = 10


def _open_showcase(d: App2Driver) -> None:
    d.open("smoke", sheet="Showcase")
    d.wait_loaded(_TABLE)


def test_table_pagination_round_trip() -> None:
    with App2Driver.smoke() as d:
        _open_showcase(d)
        page1 = d.table_rows(_TABLE)
        assert len(page1) == _PAGE_SIZE
        assert page1[0]["account_id"] == "acct-001"
        # The "X–Y of TOTAL" pager — the win over QS's "page 1 of N".
        pager = d.page.locator(".table-pager-range").first.inner_text()
        assert " of " in pager, pager

        # Next page → refetch carrying ?page_offset=10, rows change.
        with d.page.expect_response(
            lambda r: "/data" in r.url and "page_offset=10" in r.url,
        ) as info:
            d.page.locator(".table-pager-next").first.click()
        assert info.value.ok
        d.page.wait_for_load_state("networkidle")
        page2 = d.table_rows(_TABLE)
        assert len(page2) == _PAGE_SIZE
        assert page2[0]["account_id"] == "acct-011"
        assert page2[0]["account_id"] != page1[0]["account_id"]

        # Prev page → back to page 1 (?page_offset=0).
        with d.page.expect_response(
            lambda r: "/data" in r.url and "page_offset=0" in r.url,
        ) as info:
            d.page.locator(".table-pager-prev").first.click()
        assert info.value.ok
        d.page.wait_for_load_state("networkidle")
        assert d.table_rows(_TABLE)[0]["account_id"] == "acct-001"


def test_table_sort_round_trip() -> None:
    with App2Driver.smoke() as d:
        _open_showcase(d)
        first_col = next(iter(d.table_rows(_TABLE)[0]))  # "account_id"

        # Click the first header → sort ascending; refetch carries
        # ?sort_column=account_id:asc (the ':' may arrive %3A-encoded).
        with d.page.expect_response(
            lambda r: "/data" in r.url
            and (f"sort_column={first_col}:asc" in r.url
                 or f"sort_column={first_col}%3Aasc" in r.url),
        ) as info:
            d.page.locator("th .table-sort-link").first.click()
        assert info.value.ok
        d.page.wait_for_load_state("networkidle")
        asc = d.table_rows(_TABLE)
        assert asc[0]["account_id"] == "acct-001"  # account_id asc
        # The sort badge (▲/▼) renders next to the active column.
        badge = d.page.locator("th .table-sort-badge").first.inner_text()
        assert badge.strip() != "", "active-column sort badge should render"

        # Click the same header again → flips to descending.
        with d.page.expect_response(
            lambda r: "/data" in r.url
            and (f"sort_column={first_col}:desc" in r.url
                 or f"sort_column={first_col}%3Adesc" in r.url),
        ) as info:
            d.page.locator("th .table-sort-link").first.click()
        assert info.value.ok
        d.page.wait_for_load_state("networkidle")
        desc = d.table_rows(_TABLE)
        assert desc[0]["account_id"] != "acct-001"
        assert desc[0]["account_id"] != asc[0]["account_id"]
