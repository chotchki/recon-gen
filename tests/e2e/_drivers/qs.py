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

Implemented: all the navigation + read verbs (incl. ``table_rows`` via
the ``sn-table-column-*`` header divs + ``sn-table-cell-*`` body cells,
and ``filter_options`` via the dropdown popover), plus the write verbs
that have clean helpers — ``pick_filter`` (multi-select dropdown),
``set_date_range`` (the two "Date From"/"Date To" pickers),
``clear_filters`` (re-mint the embed URL). Still ``NotImplementedError``:
``set_slider`` (no DOM helper for ``ParameterSliderControl`` yet) and
``cross_link`` (the cross-sheet-drill click) — both X.2.q follow-ons.
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
    set_multi_select_values,
    set_parameter_datetime_value,
    sheet_control_titles,
    wait_for_dashboard_loaded,
    wait_for_dropdown_options_present,
    wait_for_visual_titles_present,
    webkit_page,
)


_TODO = "X.2.q — QsEmbedDriver verb not implemented yet"

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

    def filter_labels(self) -> list[str]:
        return sheet_control_titles(self._page)

    def filter_options(self, label: str) -> list[str]:
        # Opens the ParameterDropDownControl popover, reads the option
        # labels, dismisses it. Polls until ≥1 option is present so a
        # cold-rendered control (or a cascade-repopulated downstream
        # dropdown) gets a beat to fill — returns [] if it never does.
        return wait_for_dropdown_options_present(
            self._page, label, timeout_ms=self._visual_timeout,
        )

    def wait_loaded(
        self, visual_title: str, *, timeout_ms: int = _DEFAULT_VISUAL_TIMEOUT_MS,
    ) -> None:
        # 1. Title label hydrates when the visual frame renders (hard
        #    wait — if it never appears the visual literally isn't there).
        wait_for_visual_titles_present(self._page, [visual_title], timeout_ms)
        # 2. Scroll on-screen so a below-the-fold table de-virtualizes.
        scroll_visual_into_view(
            self._page, visual_title, timeout_ms, wait_for_cells=False,
        )
        # 3. For a *table* visual, wait for its first body cell to render
        #    (the column-header divs appear with the table chrome, so we
        #    can tell it's a table before the rows land). Non-table
        #    visuals: title + scroll is enough. Best-effort — a
        #    legitimately-empty table never renders ``sn-table-cell-0-0``,
        #    so the wait times out and is swallowed; callers tell
        #    empty-from-populated by reading ``table_rows`` afterward.
        #    Capped so an empty-table sheet doesn't burn the full L1
        #    timeout per visual.
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        try:
            self._page.wait_for_function(
                """(title) => {
                    const vs = document.querySelectorAll('[data-automation-id="analysis_visual"]');
                    for (const v of vs) {
                        const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                        if (!t || t.innerText.trim() !== title) continue;
                        const isTable = v.querySelector('[data-automation-id^="sn-table-column-"]') !== null;
                        if (!isTable) return true;
                        return v.querySelector('[data-automation-id="sn-table-cell-0-0"]') !== null;
                    }
                    return false;
                }""",
                arg=visual_title,
                timeout=min(timeout_ms, 12_000),
            )
        except PlaywrightTimeoutError:
            pass

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

    def _settle_after_param_change(self, *, timeout_ms: int = 18_000) -> None:
        """Block until the active sheet's table visuals have re-fetched
        after a parameter write.

        QS gives no clean network signal for the post-parameter dataset
        re-query, and the prior page's rows linger in the DOM until the
        re-fetch lands — so a naive "wait for ``sn-table-cell-0-0``"
        returns on the *stale* rows, then they get cleared, and the next
        read sees the spinner gap (zero rows) → spurious "filter emptied
        the table". Instead: (1) give the re-fetch ~1.2s to *start*
        clearing the old rows, (2) then poll the tables' first few cell
        texts until they hold steady across two ~0.7s-apart reads (the
        re-fetch has landed and stopped mutating the DOM). Best-effort —
        capped and swallowed: a pathologically-slow QS shouldn't hard-fail
        the write verb (callers re-read afterward and surface a real
        empty result themselves)."""
        page = self._page
        page.wait_for_timeout(1_200)
        snapshot_js = """() => Array.from(
            document.querySelectorAll('[data-automation-id="analysis_visual"]')
        ).map((v) => {
            let s = '';
            for (let r = 0; r < 3; r++) {
                const c = v.querySelector(
                    `[data-automation-id="sn-table-cell-${r}-0"]`);
                s += (c ? c.innerText.trim() : '\\u2205') + '~';
            }
            return s;
        }).join('||')"""
        prev = page.evaluate(snapshot_js)
        stable = 0
        for _ in range(max(1, timeout_ms // 700)):
            page.wait_for_timeout(700)
            cur = page.evaluate(snapshot_js)
            if cur == prev:
                stable += 1
                if stable >= 2:
                    return
            else:
                stable = 0
                prev = cur

    def pick_filter(self, label: str, values: Sequence[str]) -> None:
        # Post-Y.2.g the L1 / L2FT dropdowns are multi-select
        # ParameterDropDownControls — set_multi_select_values deselects
        # whatever's checked then ticks exactly ``values`` (a one-element
        # list for a single-select pick), which is the protocol's
        # "set the control to ``values``" semantics. Then block until the
        # dataset re-query lands (per the protocol's "block until the
        # affected visuals re-fetch").
        set_multi_select_values(
            self._page, label, list(values), self._page_timeout,
        )
        self._settle_after_param_change()

    def set_date_range(self, from_: str | None, to: str | None) -> None:
        # The universal date filter renders as two ParameterDateTimePicker
        # sheet controls titled "Date From" / "Date To" (consistent
        # across all four apps — see ``add_parameter_datetime_picker``
        # call sites). QS's picker text format is ``YYYY/MM/DD``; the
        # protocol takes ISO ``YYYY-MM-DD``, so translate. ``None`` on a
        # side leaves that picker untouched.
        touched = False
        for control_title, iso in (("Date From", from_), ("Date To", to)):
            if iso is None:
                continue
            set_parameter_datetime_value(
                self._page, control_title, iso.replace("-", "/"),
                self._page_timeout,
            )
            touched = True
        if touched:
            self._settle_after_param_change()

    def set_slider(
        self, label: str, lo: float | None, hi: float | None,
    ) -> None:
        # Investigation's sliders are single-value ParameterSliderControls
        # (not RANGE FilterSliderControls), and there's no DOM helper to
        # drive that widget yet — the X.2.q follow-on. Until then this
        # raises; the one test that wants it (test_inv_filters) is skipped.
        raise NotImplementedError(_TODO + " (ParameterSliderControl)")

    def clear_filters(self) -> None:
        # Re-mint the embed URL and re-navigate — QS parameter controls
        # reset to their defaults on a fresh load. (Mirrors App2Driver's
        # bare-URL reset.) Re-opens the sheet we were on, if any.
        if self._dashboard is None:
            raise RuntimeError("QsEmbedDriver.clear_filters() called before open()")
        self.open(self._dashboard, sheet=self._sheet)

    def cross_link(self, label: str) -> None:
        raise NotImplementedError(_TODO + " (cross-sheet drill click)")

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
