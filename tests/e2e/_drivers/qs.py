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
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from recon_gen.common.config import Config
from recon_gen.common.models import DatasetParameter
from tests.e2e._drivers.base import query_db_via_cfg, rekey_by_columns

from recon_gen.common.browser.helpers import (
    bump_table_page_size_to_10000,
    visual_error_text,
    visual_is_empty,
    click_context_menu_item,
    click_first_row_of_visual,
    click_sheet_tab,
    count_table_rows,
    count_table_total_rows,
    generate_dashboard_embed_url,
    get_sheet_tab_names,
    get_visual_titles,
    find_row_in_table_via_scroll,
    read_kpi_value,
    read_table_rows_dom,
    right_click_first_row_of_visual,
    scroll_visual_into_view,
    set_dropdown_value,
    set_multi_select_values,
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


def _table_has_scroll_overflow(page: Any, visual_title: str) -> bool:
    """AA.A.l2ft-rails-inverse.2 — does the visual's inner
    ``.grid-container`` have rows below the fold?

    QS Tables come in two non-paginated shapes:

    - Fits-in-viewport (e.g. L1 Drift with 10 rows on a tall sheet):
      ``scrollHeight ≤ clientHeight``. The DOM holds every row;
      ``count_table_rows`` is the exact total.
    - Infinite-scroll virtualized (e.g. L2FT Rails with 88+ matching
      rows): ``scrollHeight > clientHeight``. Only the visible window
      is mounted (~10-50 rows); ``count_table_rows`` undercounts. The
      caller must scroll-accumulate via ``count_table_total_rows``.

    This is a cheap read — no clicks, no waits. Returns False if the
    visual or its grid-container isn't on the page (let the cheap-path
    return whatever ``count_table_rows`` sees).
    """
    return page.evaluate(
        """(title) => {
            const visuals = document.querySelectorAll('[data-automation-id="analysis_visual"]');
            for (const v of visuals) {
                const t = v.querySelector('[data-automation-id="analysis_visual_title_label"]');
                if (!t || t.innerText.trim() !== title) continue;
                const c = v.querySelector('.grid-container');
                if (!c) return false;
                return c.scrollHeight > c.clientHeight + 1;
            }
            return false;
        }""",
        visual_title,
    )


@dataclass(frozen=True)
class WsSnapshot:
    """AA.A.race.3 — frozen snapshot of ``_QsWsActivityTracker`` state
    at a point in time. Captured BEFORE an action fires so the settle
    loop can ask "what's NEW since I started?" rather than "what's the
    current absolute state?".

    Two fields suffice:

    - ``total_starts`` — counter at snapshot time. Detects "did any
      new START_VIS fire?" via ``current.total_starts - snap.total_starts``.
    - ``pending`` — frozen set of in-flight cids at snapshot time.
      Detects "are the new cids done?" via
      ``(current.pending - snap.pending) == set()``. This frame
      identifies cids the snapshot saw vs cids fired during the wait,
      independent of how many cids were already draining from prior work.
    """
    total_starts: int
    pending: frozenset[str]


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
      seen since construction
    - ``last_start_at: float`` — wall-clock of the most recent START
      (the "no new START in N ms" guard against the two-burst case)

    AA.A.race.3 — added ``snapshot()`` for snapshot-then-wait. The
    App2 cache-vs-network bug surfaced the failure shape where a
    pick *appears* to fire no fetch (Playwright's `expect_response`
    didn't fire on cache hits); the QS analogue would be QS's own
    client deciding not to fire ``START_VIS`` for a parameter-write
    whose result is already on-screen. The pre-AA.A.race.3
    ``_settle_after_param_change`` keyed off ``total_starts > baseline``
    which would spin until timeout in that case; with snapshots the
    settle can distinguish "new cids fired and drained" from "no new
    cids — cache-equivalent fast-path."

    Best-effort: malformed payloads, binary frames, or missing keys
    are swallowed (the tracker degrades to "saw nothing" — better than
    crashing the test).

    AA.A.qs-triage.1 — optional ``frame_sink`` accumulates every
    framesent text payload (prefixed with ``[framesent]``, monotonic-
    timestamped to disambiguate ordering when the same cid round-trips
    multiple times in one test). ``QsEmbedDriver`` wires this into the
    same ``trigger_failure_capture`` path the console / network / DOM
    sinks use, so a failing QS-side e2e drops ``ws_frames.txt`` in the
    per-test artifact dir alongside ``console.txt`` and ``network.txt``.
    Mirror of the App2 race.1 root-cause path — the JS-side console
    tracer made the cache-vs-network asymmetry obvious in retrospect;
    same shape on the QS side means the next "QS WHERE clause didn't
    match my picked value" investigation reads ``ws_frames.txt`` and
    sees the actual START_VIS payload QS sent rather than re-deriving
    via instrumented re-runs.
    """

    def __init__(self, page: Any, *, frame_sink: list[str] | None = None) -> None:
        self._pending: set[str] = set()
        self._total_starts: int = 0
        self._last_start_at: float = 0.0
        self._frame_sink = frame_sink
        page.on("websocket", self._on_ws)

    def _on_ws(self, ws: Any) -> None:
        ws.on("framesent", self._on_frame)

    def _on_frame(self, payload: Any) -> None:
        # First: append the raw frame to the artifact sink (when wired).
        # Non-string payloads (binary frames — QS uses these for heartbeats /
        # protocol-level bookkeeping we don't care about) get a one-line
        # marker rather than the bytes themselves so the artifact stays
        # text-grepable. Sink-append is wrapped in a broad except so a
        # misbehaving sink (full disk during list growth?) never aborts
        # the page-event lifecycle.
        sink = self._frame_sink
        if sink is not None:
            try:
                if isinstance(payload, str):
                    sink.append(f"[framesent] {payload}")
                else:
                    length = len(payload) if hasattr(payload, "__len__") else "?"
                    sink.append(f"[framesent-binary len={length}]")
            except Exception:
                pass
        # Bytes / non-JSON frames: silently ignore for the parsed
        # tracker state. QS sends JSON text for the data-layer protocol;
        # binary frames are likely heartbeats or something we don't care
        # about.
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

    def snapshot(self) -> WsSnapshot:
        """AA.A.race.3 — capture an immutable snapshot of tracker state.

        Call BEFORE the action whose effect you want to settle on.
        After the action, compare current state to the snapshot to ask
        "did new cids fire?" and "did all new cids drain?" independent
        of how many cids were already in-flight at snapshot time.
        """
        return WsSnapshot(
            total_starts=self._total_starts,
            pending=frozenset(self._pending),
        )

    def new_pending_since(self, snap: WsSnapshot) -> frozenset[str]:
        """Cids currently in-flight that weren't in-flight at ``snap``.

        ``current.pending - snap.pending`` — these are the cids the
        action triggered (and any other cids that started during the
        wait window). Empty iff all post-snapshot fetches have drained.
        """
        return frozenset(self._pending) - snap.pending

    def new_starts_since(self, snap: WsSnapshot) -> int:
        """Count of START_VIS frames since ``snap``.

        Zero iff no new fetches fired — the cache-equivalent case the
        race.3 fast-path detects.
        """
        return self._total_starts - snap.total_starts


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
        cfg: Config,
        aws_account_id: str,
        aws_region: str,
        user_arn: str | None = None,
        page_timeout_ms: int = _DEFAULT_PAGE_TIMEOUT_MS,
        visual_timeout_ms: int = _DEFAULT_VISUAL_TIMEOUT_MS,
    ) -> None:
        self._page = page
        self._cfg = cfg
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
        # AA.A.qs-triage.1 — also accumulate every framesent payload
        # into a sink list; attach the sink to the page so
        # ``trigger_failure_capture`` can dump it to ``ws_frames.txt``
        # in the per-test artifact dir on test failure (same pattern as
        # ``_qs_gen_console_sink`` / ``_qs_gen_network_sink``).
        self._ws_frames: list[str] = []
        self._ws_tracker = _QsWsActivityTracker(page, frame_sink=self._ws_frames)
        page._qs_gen_ws_frames_sink = self._ws_frames  # type: ignore[attr-defined]: monkey-attach sink for trigger_failure_capture, matches _qs_gen_console_sink pattern

    # -- factories -------------------------------------------------------

    @classmethod
    @contextlib.contextmanager
    def embed(
        cls,
        *,
        cfg: Config,
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
                cfg=cfg,
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

        # AA.A.8 — hard-fail on per-visual QS error overlay. Pre-AA.A.8
        # a broken visual silently timed out the wait above and the
        # caller had to puzzle out "why empty?" from the failure
        # artifacts; now the actual QS error text surfaces in the
        # exception message at the call site. Spike resolution locked
        # 2026-05-17 (PLAN.md AA.A.8): zero existing tests depend on
        # the silent-timeout path (the only ``qs_errors`` codepath is
        # the post-failure capture, not a wait predicate). The
        # Pending Aging "duplicate columns" bug surfaces here today.
        err = visual_error_text(self._page, visual_title)
        if err is not None:
            raise RuntimeError(
                f"QuickSight visual {visual_title!r} rendered with "
                f"error overlay: {err}"
            )

    def table_rows(
        self,
        visual_title: str,
        *,
        columns: Sequence[str] | None = None,
    ) -> list[dict[str, str]]:
        # Headers from the `sn-table-column-N` divs (their `.title` span),
        # body cells from `sn-table-cell-{row}-{col}`, zipped by position.
        # Returns the DOM-visible window only (QS virtualizes ~10 rows) —
        # de-virtualize first by scrolling the visual on-screen. The
        # page-size-bump-for-the-full-table path is X.2.q.3 / X.2.j.
        scroll_visual_into_view(
            self._page, visual_title, self._visual_timeout,
            wait_for_cells=False,
        )
        rows = read_table_rows_dom(self._page, visual_title)
        # AA.A.995 — caller-supplied column names override the rendered
        # ``.title`` text. QS stamps ``column.human_name`` on the visible
        # header which differs from App2's raw-SQL stamping; passing
        # ``columns`` lets the test key rows by a single canonical
        # identity (typically the raw SQL column name). Zip positionally
        # against the rendered header order.
        return rekey_by_columns(rows, columns) if columns else rows

    def find_row(
        self, visual_title: str, predicate: Mapping[str, str],
    ) -> dict[str, str] | None:
        # AA.A.l2ft-rails-inverse.2.e — scroll the inner grid container
        # checking each newly-mounted row against the predicate, early-
        # exit on first match. ~500ms wall on the "fast-fail" path
        # (broken filter → match on the visible window); ~1-3s on the
        # "filter works" path (scroll to bottom of 100-row table at
        # 120ms/step). Page-size bump deliberately skipped — the scroll
        # loop reads virtualized rows as they mount, no need to flatten
        # via pagination first.
        scroll_visual_into_view(
            self._page, visual_title, self._visual_timeout,
            wait_for_cells=False,
        )
        return find_row_in_table_via_scroll(
            self._page, visual_title, dict(predicate),
        )

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
        # AA.A.l2ft-rails-inverse.2 — three table shapes QS uses, three
        # paths to the true row count:
        #
        # 1. **Paginated.** A ``simplePagedDisplayNav_dropdown_pageSize``
        #    dropdown is in the DOM. Bump page size to 10000, WS-settle,
        #    then scroll-accumulate. (The original AA.H.11 path.)
        # 2. **Infinite-scroll virtualized.** No pagination dropdown but
        #    the inner ``.grid-container`` scrollHeight > clientHeight
        #    (more rows below the fold). ``count_table_rows`` returns
        #    only the visible window (~10–50 rows depending on viewport).
        #    Scroll-accumulate via ``count_table_total_rows`` to walk
        #    every mounted row. This is the shape L2FT Rails uses at
        #    spec_example data volumes (88+ matching rows) — the original
        #    AA.H.11 code fell straight to ``count_table_rows`` for the
        #    non-paginated case and silently saturated at the visible
        #    window, masking broken narrows as ``visible == visible``.
        # 3. **Fits-in-viewport.** No pagination dropdown AND no scroll
        #    overflow (the entire result set is mounted). ``count_table_rows``
        #    is the exact total — no scrolling needed.
        if bump_table_page_size_to_10000(
            self._page, visual_title, self._visual_timeout,
        ):
            self._settle_after_param_change()
        elif not _table_has_scroll_overflow(self._page, visual_title):
            return max(0, count_table_rows(self._page, visual_title))
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

    def query_db(
        self,
        sql: str,
        *,
        binds: Mapping[str, str] | None = None,
        dataset_parameters: Sequence[DatasetParameter] = (),
    ) -> list[dict[str, Any]]:  # typing-smell: ignore[explicit-any]: ground-truth row dicts; same justification as the Protocol method
        return query_db_via_cfg(
            self._cfg, sql, binds=binds, dataset_parameters=dataset_parameters,
        )

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

        AA.A.race.3 — snapshot-then-wait. Capture an immutable snapshot
        BEFORE the caller's action fires; the settle loop asks "did
        anything new happen since the snapshot, and is it done?" rather
        than "is the current absolute state quiet?". Two paths through
        the loop:

        - **New cids fired** (``new_starts_since(snap) > 0``): wait until
          every cid issued since the snapshot has STOPped
          (``new_pending_since(snap) == set()``) AND a 300 ms quiet
          window has passed since the most recent START. The quiet
          window straddles the two-burst pattern the X.2.r spike caught
          (pick → immediate START_VIS round, then debounced follow-up
          ~2 s later).
        - **No new cids fired** after the 500 ms grace period
          (``min_wait_ms``): the QS client decided the parameter write
          didn't need a fresh fetch (own-cache hit, or the param didn't
          actually change a dataset binding). Return immediately rather
          than spinning to the full 18 s timeout. This is the QS analogue
          of the App2 cache-equivalent case race.1 root-caused.

        Capped at ``timeout_ms`` (default 18 s); swallowed on timeout
        (best-effort contract — callers re-read).

        Replaces the X.2.q.3 sleep-and-poll heuristic (1.2 s upfront +
        700 ms-spaced cell-text-stability poll, capped 18 s) AND the
        pre-race.3 absolute-state heuristic (``total_starts > baseline``
        which spun until timeout if QS never fired a new START_VIS).
        """
        # Snapshot must happen BEFORE the caller's mutating action.
        # Callers invoke this AFTER the action; we capture immediately
        # on entry. Two-thread interleaving caveat: a STOP_VIS for a
        # previously-pending cid could land between action fire + this
        # snapshot, shrinking ``snap.pending``. That's fine — the
        # ``new_pending_since`` set will just be slightly wider than
        # strictly necessary; the wait still terminates correctly.
        snap = self._ws_tracker.snapshot()
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        # Quiet window (ms) past the most recent START before we accept
        # "all new cids settled". 300 ms straddles the two-burst case.
        quiet_ms = 300.0
        # Cache-equivalent grace period (s). If no new START_VIS fires
        # within this window, conclude QS served the post-pick state
        # from its own cache (or the pick was a no-op) and return.
        min_wait_deadline = time.monotonic() + 0.500
        while time.monotonic() < deadline:
            new_starts = self._ws_tracker.new_starts_since(snap)
            new_pending = self._ws_tracker.new_pending_since(snap)
            if new_starts > 0:
                # New cids fired — wait for ALL of them to drain + quiet.
                if (
                    not new_pending
                    and self._ws_tracker.ms_since_last_start >= quiet_ms
                ):
                    return
            elif time.monotonic() > min_wait_deadline:
                # No new cids since snapshot, past 500 ms grace.
                # Cache-equivalent: nothing to wait for, return.
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
