"""X.2.q — ``QsEmbedDriver``: the ``DashboardDriver`` for the embedded
QuickSight dashboard.

QuickSight's quirks — racy tab switches, below-the-fold cell
virtualization, the page-size-bump for true row counts, the
``ParameterDropDownControl`` grey-bar click — all live behind
``common/browser/helpers.py``; this driver is a thin facade that wires
those helpers to the ``DashboardDriver`` verbs and returns plain Python
(never a Playwright ``Locator`` / ``Page``), so e2e test bodies stay
renderer-agnostic.

``open(dashboard)`` takes the **deployed QuickSight DashboardId** (e.g.
``qs-gen-postgres-sasquatch_pr-l1-dashboard``), mints a fresh single-use
embed URL signed for the dashboard's region (the region match matters —
see ``generate_dashboard_embed_url``), and loads it. The ``.embed()``
factory owns the WebKit page lifecycle; it needs a live QuickSight
account and ``QS_E2E_USER_ARN`` (the test runner derives it from
``cfg.auth.aws_profile``; export it yourself for a direct ``pytest`` run).

Spike scope (X.2.q.0/q.1): the navigation + read verbs that have clean
helpers are implemented; ``table_rows`` (needs a header-cell reader —
the ``sn-table-cell-*`` automation-ids cover body cells only) and the
write verbs (``pick_filter`` / ``set_date_range`` / ``set_slider`` /
``clear_filters`` / ``cross_link``) raise ``NotImplementedError`` until
X.2.q.2.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from quicksight_gen.common.browser.helpers import (
    click_sheet_tab,
    generate_dashboard_embed_url,
    get_sheet_tab_names,
    get_visual_titles,
    read_kpi_value,
    read_table_rows_dom,
    scroll_visual_into_view,
    wait_for_dashboard_loaded,
    wait_for_visual_titles_present,
    webkit_page,
)


_TODO = "X.2.q.2 — QsEmbedDriver verb not implemented yet"

_DEFAULT_PAGE_TIMEOUT_MS = 30_000
_DEFAULT_VISUAL_TIMEOUT_MS = 15_000


class QsEmbedDriver:
    """``DashboardDriver`` over the embedded QuickSight iframe + a WebKit
    page.

    Construct via the ``QsEmbedDriver.embed(...)`` factory (a context
    manager that owns the browser); ``open()`` takes the deployed QS
    ``DashboardId``.
    """

    dialect = "qs"

    def __init__(
        self,
        *,
        page: Any,
        aws_account_id: str,
        aws_region: str,
        user_arn: str | None = None,
        page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
        visual_timeout_ms: int = _DEFAULT_VISUAL_TIMEOUT_MS,
    ) -> None:
        self._page = page
        self._account_id = aws_account_id
        self._region = aws_region
        self._user_arn = user_arn
        self._page_timeout = page_timeout_ms
        self._visual_timeout = visual_timeout_ms
        self._dashboard: str | None = None
        self._sheet: str | None = None

    # -- factories -------------------------------------------------------

    @classmethod
    @contextlib.contextmanager
    def embed(
        cls,
        *,
        aws_account_id: str,
        aws_region: str,
        user_arn: str | None = None,
        headless: bool = True,
        page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
        visual_timeout_ms: int = _DEFAULT_VISUAL_TIMEOUT_MS,
    ) -> Iterator["QsEmbedDriver"]:
        """Open a WebKit page, yield the driver, tear the browser down.

        Each ``open()`` call mints its own fresh embed URL, so the
        driver is re-usable across dashboards within one ``with`` block.
        """
        with webkit_page(headless=headless) as page:
            yield cls(
                page=page,
                aws_account_id=aws_account_id,
                aws_region=aws_region,
                user_arn=user_arn,
                page_timeout_ms=page_timeout_ms,
                visual_timeout_ms=visual_timeout_ms,
            )

    # -- navigation ------------------------------------------------------

    def open(self, dashboard: str, sheet: str | None = None) -> None:
        url = generate_dashboard_embed_url(
            aws_account_id=self._account_id,
            aws_region=self._region,
            dashboard_id=dashboard,
            user_arn=self._user_arn,
        )
        self._page.goto(url, timeout=self._page_timeout)
        wait_for_dashboard_loaded(self._page, timeout_ms=self._page_timeout)
        self._dashboard = dashboard
        self._sheet = None
        if sheet is not None:
            self.goto_sheet(sheet)
        else:
            self._settle_visuals()

    def goto_sheet(self, name: str) -> None:
        click_sheet_tab(self._page, name, self._page_timeout)
        self._sheet = name
        self._settle_visuals()

    def _settle_visuals(self) -> None:
        """Best-effort wait for the current sheet's visuals to hydrate
        their title labels. Swallows the timeout — a text-only sheet
        (e.g. ``Getting Started``) legitimately renders zero titled
        visuals, so this can't be a hard wait."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        try:
            self._page.wait_for_function(
                '() => document.querySelectorAll('
                '\'[data-automation-id="analysis_visual_title_label"]\''
                ').length >= 1',
                timeout=min(self._visual_timeout, 12_000),
            )
        except PlaywrightTimeoutError:
            pass

    # -- reads -----------------------------------------------------------

    def sheet_names(self) -> list[str]:
        return get_sheet_tab_names(self._page)

    def visual_titles(self) -> list[str]:
        return get_visual_titles(self._page)

    def wait_loaded(
        self, visual_title: str, *, timeout_ms: int = _DEFAULT_VISUAL_TIMEOUT_MS,
    ) -> None:
        # The title label hydrates roughly when the visual frame renders;
        # scrolling it into view also de-virtualizes a below-the-fold
        # table so a subsequent read sees its cells. (Tighter "not a
        # spinner" detection — the QS silent-spin footgun — is q.2.)
        wait_for_visual_titles_present(self._page, [visual_title], timeout_ms)
        scroll_visual_into_view(
            self._page, visual_title, timeout_ms, wait_for_cells=False,
        )

    def table_rows(self, visual_title: str) -> list[dict[str, str]]:
        # Headers from the `sn-table-column-N` divs (their `.title` span),
        # body cells from `sn-table-cell-{row}-{col}`, zipped by position.
        # Returns the DOM-visible window only (QS virtualizes ~10 rows) —
        # de-virtualize first by scrolling the visual on-screen. The
        # page-size-bump-for-the-full-table path is X.2.q.3 / X.2.j.
        scroll_visual_into_view(
            self._page, visual_title, self._visual_timeout,
            wait_for_cells=False,
        )
        return read_table_rows_dom(self._page, visual_title)

    def kpi_value(self, visual_title: str) -> str | None:
        try:
            return read_kpi_value(self._page, visual_title)
        except AssertionError:
            return None

    # -- writes ----------------------------------------------------------

    def pick_filter(self, label: str, values: Sequence[str]) -> None:
        raise NotImplementedError(_TODO)

    def set_date_range(self, from_: str | None, to: str | None) -> None:
        raise NotImplementedError(_TODO)

    def set_slider(
        self, label: str, lo: float | None, hi: float | None,
    ) -> None:
        raise NotImplementedError(_TODO)

    def clear_filters(self) -> None:
        raise NotImplementedError(_TODO)

    def cross_link(self, label: str) -> None:
        raise NotImplementedError(_TODO)

    # -- artifacts -------------------------------------------------------

    def screenshot(self, path: str | Path | None = None) -> bytes:
        png: bytes = self._page.screenshot(full_page=True)
        if path is not None:
            Path(path).write_bytes(png)
        return png

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        # The WebKit page is owned by the `embed()` context manager;
        # nothing to do here.
        pass
