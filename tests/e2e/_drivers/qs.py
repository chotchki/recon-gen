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
account and ``RECON_E2E_USER_ARN`` (the test runner derives it from
``cfg.auth.aws_profile``; export it yourself for a direct ``pytest`` run).

Implemented: all the navigation + read verbs (incl. ``table_rows`` via
the ``sn-table-column-*`` header divs + ``sn-table-cell-*`` body cells,
and ``filter_options`` via the dropdown popover), plus the write verbs
that have clean helpers — ``pick_filter`` (multi-select dropdown),
``set_date_range`` (the two "Date From"/"Date To" pickers),
``clear_filters`` (re-mint the embed URL). Still ``NotImplementedError``:
``set_slider`` (no DOM helper for ``ParameterSliderControl`` yet) and
``cross_link`` (the cross-sheet-drill click) — both X.2.q follow-ons.

Post-write settle (X.2.r): ``_settle_after_param_change()`` keys off
QS's WebSocket data layer (``_QsWsActivityTracker`` watches
``START_VIS`` / ``STOP_VIS`` frames). No fixed sleeps — see
``docs/audits/x_2_r_event_wait_spike.md`` for the wire-shape capture
that drove the design.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from recon_gen.common.browser.helpers import (
    bump_table_page_size_to_10000,
    visual_is_empty,
    click_context_menu_item,
    click_first_row_of_visual,
    click_sheet_tab,
    count_table_rows,
    count_table_total_rows,
    generate_dashboard_embed_url,
    get_sheet_tab_names,
    get_visual_titles,
    read_kpi_value,
    read_table_rows_dom,
    right_click_first_row_of_visual,
    scroll_visual_into_view,
    set_dropdown_value,
    set_multi_select_values,
    table_is_paginated,
    set_parameter_datetime_value,
    set_parameter_slider_value,
    sheet_control_titles,
    wait_for_dashboard_loaded,
    wait_for_dropdown_options_present,
    wait_for_visual_titles_present,
    webkit_page,
)


_TODO = "X.2.q — QsEmbedDriver verb not implemented yet"

_DEFAULT_PAGE_TIMEOUT_MS = 30_000
_DEFAULT_VISUAL_TIMEOUT_MS = 15_000


class _QsWsActivityTracker:
    """X.2.r — track QuickSight's WebSocket data layer for event-driven settle.

    Per the X.2.r spike (``docs/audits/x_2_r_event_wait_spike.md``), QS
    embedded dashboards run every dataset query as a JSON text frame
    over a single long-lived WebSocket. The wire shape:

    - Client → server, start a visual's query:
      ``{"type":"START_VIS","cid":"<uuid>","request":{...}}``
    - Client → server, visual finished — tear down server-side state:
      ``{"type":"STOP_VIS","cids":["<uuid>", ...]}``

    The client sends ``STOP_VIS`` only after it's processed the response
    + torn down its rendering pipeline for that visual — so the set
    difference ``sent_START - sent_STOP`` is exactly the in-flight
    re-query count.

    This tracker hooks ``page.on("websocket")`` + ``ws.on("framesent")``
    at driver construction, parses every text frame, and maintains:

    - ``pending: set[str]`` — cids currently in-flight (START sent, no
      matching STOP yet)
    - ``total_starts: int`` — monotonic counter of START_VIS frames
      seen since construction (the baseline a settle compares against)
    - ``last_start_at: float`` — wall-clock of the most recent START
      (the "no new START in N ms" guard against the two-burst case)

    Best-effort: malformed payloads, binary frames, or missing keys
    are swallowed (the tracker degrades to "saw nothing" — better than
    crashing the test).
    """

    def __init__(self, page: Any) -> None:
        self._pending: set[str] = set()
        self._total_starts: int = 0
        self._last_start_at: float = 0.0
        page.on("websocket", self._on_ws)

    def _on_ws(self, ws: Any) -> None:
        ws.on("framesent", self._on_frame)

    def _on_frame(self, payload: Any) -> None:
        # Bytes / non-JSON frames: silently ignore. QS sends JSON text
        # for the data-layer protocol; binary frames are likely
        # heartbeats or something we don't care about.
        if not isinstance(payload, str):
            return
        try:
            msg = json.loads(payload)
        except (ValueError, TypeError):
            return
        kind = msg.get("type") if isinstance(msg, dict) else None
        if kind == "START_VIS":
            cid = msg.get("cid")
            if isinstance(cid, str):
                self._pending.add(cid)
                self._total_starts += 1
                self._last_start_at = time.monotonic()
        elif kind == "STOP_VIS":
            cids = msg.get("cids", [])
            if isinstance(cids, list):
                for cid in cids:
                    if isinstance(cid, str):
                        self._pending.discard(cid)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def total_starts(self) -> int:
        return self._total_starts

    @property
    def ms_since_last_start(self) -> float:
        if self._last_start_at == 0.0:
            return float("inf")
        return (time.monotonic() - self._last_start_at) * 1000.0


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
        # X.2.r — hook QS's WebSocket data layer at driver construction
        # so the activity tracker captures every START_VIS / STOP_VIS
        # frame from now on. Used by ``_settle_after_param_change`` for
        # event-driven settle (no sleeps / DOM polls).
        self._ws_tracker = _QsWsActivityTracker(page)

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
        viewport: tuple[int, int] = (1600, 1000),
        page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
        visual_timeout_ms: int = _DEFAULT_VISUAL_TIMEOUT_MS,
    ) -> Iterator["QsEmbedDriver"]:
        """Open a WebKit page, yield the driver, tear the browser down.

        Each ``open()`` call mints its own fresh embed URL, so the
        driver is re-usable across dashboards within one ``with`` block.

        Pass a tall ``viewport`` (e.g. ``(1600, 4000)``) for tests that
        ``table_row_count`` stacked-layout sheets where the detail table
        sits below the fold — the page-size-bump path needs the table's
        ``.grid-container`` close enough to the viewport to scroll into,
        and the default 1000-tall viewport sometimes leaves it parked
        too far down for QS to mount cells.
        """
        with webkit_page(headless=headless, viewport=viewport) as page:
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

    def table_row_count(self, visual_title: str) -> int:
        # AA.H.11 — orchestrate {scroll-into-view → detect pagination → bump
        # + WS-settle on overflow → count}. Pre-AA.H.11, a bundled helper
        # ALWAYS did the bump + a fixed 500 ms wait, which raced the
        # post-bump re-fetch on cold sheets and returned 0 for tables
        # that actually had 2+ rows (the audit-agreement
        # ``qs_count=0`` failure). The fix:
        #
        # 1. Scroll the visual into view so cells mount.
        # 2. Cheap path: read DOM cells via ``count_table_rows`` —
        #    correct for small tables (no pagination overflow). No
        #    clicks, no re-render risk.
        # 3. Bump path: if ``table_is_paginated`` returns True, the
        #    table has more rows than fit on the current page — bump
        #    page-size to 10000, ``_settle_after_param_change`` (WS
        #    frames, NOT a fixed wait — user direction: time-based
        #    waits are a major smell), then scroll-accumulate the now-
        #    fully-paginated table.
        #
        # AA.H.11.followon — `scroll_visual_into_view` now waits for
        # EITHER table cells (`sn-table-cell-0-0`) OR the QS empty-state
        # overlay (`visual-overlay-title[data-automation-context="No
        # data"]`) — both are positive signals that the visual finished
        # resolving. Pre-followon: only waited for cells, so empty
        # tables timed out at `self._visual_timeout` (15s) and the test
        # died. Now both populated AND empty visuals settle in their
        # natural render time (typically <1s for empty, a few s for
        # populated). The user direction: "is there something we could
        # do to avoid those timeouts? something to watch on?" → yes,
        # watch the overlay marker QS already mounts on empty visuals.
        scroll_visual_into_view(
            self._page, visual_title, self._visual_timeout,
        )
        # `visual_is_empty` is a cheap DOM read (no waits) — short-
        # circuits the empty case at 0 without paying any pagination/
        # bump cost. Test callers like `_assert_anchor_present_and_populated`
        # rely on this returning 0 to decide whether to `pytest.skip`.
        if visual_is_empty(self._page, visual_title):
            return 0
        if not table_is_paginated(self._page, visual_title):
            return max(0, count_table_rows(self._page, visual_title))
        if bump_table_page_size_to_10000(
            self._page, visual_title, self._visual_timeout,
        ):
            self._settle_after_param_change()
        total = count_table_total_rows(
            self._page, visual_title, self._visual_timeout,
        )
        if total == -2:
            # ``.grid-container`` absent — single-row table, DOM-cell count
            # IS the full set (same fallback as pre-AA.H.11).
            return max(0, count_table_rows(self._page, visual_title))
        return max(0, total)

    def kpi_value(self, visual_title: str) -> str | None:
        try:
            return read_kpi_value(self._page, visual_title)
        except AssertionError:
            return None

    # -- writes ----------------------------------------------------------

    def _settle_after_param_change(self, *, timeout_ms: int = 18_000) -> None:
        """Block until QS's WebSocket data layer has settled after a
        parameter write.

        X.2.r — keys off the START_VIS / STOP_VIS frames QS sends over
        its long-lived WebSocket (see ``docs/audits/x_2_r_event_wait_spike.md``
        for the wire shape + capture). The ``_QsWsActivityTracker``
        attached at driver construction maintains a ``pending`` set
        (sent START minus sent STOP — the in-flight re-query count) and
        a monotonic ``total_starts`` counter.

        Algorithm:

        1. Snapshot ``baseline = total_starts`` *before* the caller's
           action fires.
        2. Spin (Playwright ``wait_for_function`` would be tighter but
           tracker state lives in Python; the Python-side spin polls
           cheap in-process state, no IPC). Each iteration sleeps a
           tiny ``120 ms`` and checks:

           - **Re-fetch fired**: ``total_starts > baseline``. If we
             never see this within ``timeout_ms``, the write didn't
             trigger a re-query — caller's read will surface what's
             actually on screen, swallow.
           - **Drained**: ``pending_count == 0``.
           - **Settled past the burst**: ``ms_since_last_start >= 300``
             (the X.2.r spike caught a two-burst pattern: pick →
             immediate START_VIS round, then debounced follow-up ~2 s
             later. The 300 ms guard waits past both bursts before
             returning).

        3. When all three are true, return. Capped at ``timeout_ms``
           (default 18 s — same budget as the X.2.q.3 sleep-and-poll
           it replaces); swallowed on timeout (best-effort contract —
           callers re-read).

        Replaces the X.2.q.3 sleep-and-poll heuristic (1.2 s upfront +
        700 ms-spaced cell-text-stability poll, capped 18 s) which was
        a content-stabilization workaround for the lack of an event
        signal. Now we have one.
        """
        # NOTE: this MUST be called AFTER the caller's mutating action
        # fires the START_VIS frames. The baseline is captured here
        # because even a STOP_VIS-only burst from leftover prior work
        # could fool a pre-action snapshot.
        baseline = self._ws_tracker.total_starts
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        # Quiet window (ms) the tracker must show with no new START
        # before we accept "settled". 300 ms straddles the two-burst
        # case the X.2.r spike caught.
        quiet_ms = 300.0
        while time.monotonic() < deadline:
            if (
                self._ws_tracker.total_starts > baseline
                and self._ws_tracker.pending_count == 0
                and self._ws_tracker.ms_since_last_start >= quiet_ms
            ):
                return
            # Tight in-process loop — no IPC. The tracker state mutates
            # on Playwright's event-loop callback; this short sleep
            # gives that loop room to deliver any queued frames.
            self._page.wait_for_timeout(80)

    def pick_filter(self, label: str, values: Sequence[str]) -> None:
        # Post-AA.A.3 the L1 / L2FT dropdowns are single-select
        # ParameterDropDownControls (pre-AA.A.3 they were multi-select on
        # the back of the X.2.t.2 sentinel-guard pattern). The protocol's
        # "set the control to ``values``" semantics collapse to a single
        # ``set_dropdown_value`` for ``len(values) == 1``; multi-element
        # callers fall through to ``set_multi_select_values`` for the
        # remaining genuine MULTI_SELECT controls (none in L1/L2FT after
        # AA.A but the verb stays general for future compare-N keepers).
        # Then block until the dataset re-query lands (per the protocol's
        # "block until the affected visuals re-fetch").
        # ``set_dropdown_value`` transparently handles both the simple
        # and search-enabled (MUI Autocomplete) variants — the driver
        # encapsulates the typing dance so tests stay renderer-agnostic
        # (AA.H.8).
        vals = list(values)
        if len(vals) == 1:
            set_dropdown_value(
                self._page, label, vals[0], self._page_timeout,
            )
        else:
            set_multi_select_values(
                self._page, label, vals, self._page_timeout,
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

    def set_date(self, label: str, iso: str | None) -> None:
        # AA.B.5.followon — single-value DateTimePicker control (currently
        # only L1 Daily Statement's "Business Day"). Reuses
        # ``set_parameter_datetime_value`` (the same helper the universal
        # date-range pickers use, just card-scoped by title instead of
        # bare-id). QS accepts ``YYYY/MM/DD`` text; translate the ISO
        # form. Block until the dataset re-query lands.
        if iso is None:
            return
        set_parameter_datetime_value(
            self._page, label, iso.replace("-", "/"), self._page_timeout,
        )
        self._settle_after_param_change()

    def set_slider(
        self, label: str, lo: float | None, hi: float | None,
    ) -> None:
        # Investigation's sliders are single-value ParameterSliderControls
        # (not two-bound RANGE FilterSliderControls). The protocol passes
        # the value as ``lo`` (``hi=None``) for a one-handle slider — drive
        # it via the card's typable text box (commits on focus-loss), then
        # block until the dataset re-query lands.
        value = lo if lo is not None else hi
        if value is None:
            raise ValueError(
                f"set_slider({label!r}): no value — pass it as ``lo`` "
                f"(``hi`` is unused for a single-handle ParameterSlider)."
            )
        set_parameter_slider_value(
            self._page, label, value, self._page_timeout,
        )
        self._settle_after_param_change()

    def clear_filters(self) -> None:
        # Re-mint the embed URL and re-navigate — QS parameter controls
        # reset to their defaults on a fresh load. (Mirrors App2Driver's
        # bare-URL reset.) Re-opens the sheet we were on, if any.
        if self._dashboard is None:
            raise RuntimeError("QsEmbedDriver.clear_filters() called before open()")
        self.open(self._dashboard, sheet=self._sheet)

    def cross_link(self, label: str) -> None:
        raise NotImplementedError(_TODO + " (cross-sheet drill click)")

    def drill_from_first_row(self, visual_title: str) -> None:
        # Left-click the first row's cell-0-0 to fire a DATA_POINT_CLICK
        # drill (typically a same-sheet param write — Account Network's
        # walk-the-flow is the K.4.8 case). Then settle on the re-fetch.
        click_first_row_of_visual(
            self._page, visual_title, self._page_timeout,
        )
        self._settle_after_param_change()

    def drill_from_first_row_via_menu(
        self, visual_title: str, menu_item: str,
    ) -> None:
        # Right-click → DATA_POINT_MENU → click the entry whose visible
        # text is ``menu_item`` (the drill action's `Name` from the
        # Python builder appears verbatim there). Doesn't settle: the
        # drill may navigate to a different sheet (the v8.5.7
        # cross-sheet-drill-with-date-widening case), in which case the
        # caller's ``wait_loaded(target_visual)`` is what knows when the
        # destination is ready; for an in-place drill the caller still
        # ``wait_loaded``\\s the source visual to confirm the re-render.
        right_click_first_row_of_visual(
            self._page, visual_title, self._page_timeout,
        )
        click_context_menu_item(
            self._page, menu_item, self._page_timeout,
        )

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
