"""X.2.q — ``App2Driver``: the ``DashboardDriver`` for the self-hosted
HTMX/d3 renderer.

App 2's DOM is deliberately simple — visuals are
``section[data-visual-kind]`` blocks with an ``<h2>`` title and a
``.visual-data`` swap target; tables are plain ``<table class="table-data">``
(no virtualization); the filter form is ``#filter-form`` with
``data-widget``-marked controls. So most verbs are a direct DOM read or
a write-into-the-underlying-element-plus-dispatch-``change`` (the same
HTMX wire shape the Tom Select / Flatpickr / noUiSlider widgets produce
when a user drives them — the widget chrome is a fidelity concern for
the ``tests/js`` unit harness, not for a driver expressing test intent).

Two factories own the page+server lifecycle:

- ``App2Driver.smoke()`` — bundled smoke app (fixed shape, deterministic
  stub fetcher, ``SMOKE_FILTER_SPECS``). Use for the protocol parity
  tests in ``tests/e2e/test_dashboard_driver.py``.
- ``App2Driver.serving(tree_app=, sheet=, data_fetcher=, ...)`` — any
  tree + fetcher you build. Use for the per-app App2 tests that build
  Executives / L2FT / Investigation / Money Trail trees and need the
  fetcher to be either stub or live-DB. Same context-manager shape as
  ``smoke()``.

Both expose ``driver.base_url`` (so tests can build cross-sheet URLs
themselves) and ``driver.page`` (escape hatch for App2-internal
assertions — ``page.route`` for HTTP intercept, ``page.expect_response``
for refetch checks, ``page.evaluate`` for DOM probes — the kind of
wire-shape assertions that don't translate to renderer-agnostic verbs).

**Re-fetch contract.** A ``change`` on a ``#filter-form`` input →
``wireFilterAutoRefresh``'s 300 ms debounce → ``htmx.trigger(body,
'refresh')`` → every visual section re-issues its ``hx-get`` →
``.visual-data`` swaps → ``bootstrap.js`` re-hydrates. The write verbs
run their mutation inside ``_wait_for_refetch``, which blocks on the
first ``/visuals/.../data`` response and then ``networkidle`` (the
remaining visuals), so by the time a write verb returns the DOM
reflects the new state.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from quicksight_gen.common.browser.helpers import webkit_page
from quicksight_gen.common.config import Config
from quicksight_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from quicksight_gen.common.html.render import FilterSpec
from quicksight_gen.common.html.server import DataFetcher
from quicksight_gen.common.tree.structure import App, Sheet
from tests._test_helpers import make_test_config
from tests.e2e._harness_html2 import html2_server


# Matches the per-visual data endpoint, e.g.
# /dashboards/smoke/sheets/showcase/visuals/showcase-kpi/data?...
_VISUAL_DATA_URL_RE = re.compile(r"/visuals/[^/]+/data")
_REFETCH_TIMEOUT_MS = 15_000


class App2Driver:
    """``DashboardDriver`` over a running App 2 server + a WebKit page.

    Construct via a factory (``App2Driver.smoke()``), not directly —
    the factory owns the server + browser lifecycle as a context
    manager.
    """

    dialect = "app2"

    def __init__(
        self, *, base_url: str, page: Any,
        sheet_id_by_name: Mapping[str, str],
    ) -> None:
        self._base = base_url.rstrip("/")
        self._page = page
        # name → SheetId, from the served tree. The protocol's `sheet`
        # arg (open/goto_sheet) is a sheet *name* (matches the QS impl,
        # which matches tab text); App2's route segment is the SheetId,
        # so the driver translates here.
        self._sheet_id_by_name = dict(sheet_id_by_name)
        self._dashboard: str | None = None
        self._sheet: str | None = None

    # -- factories -------------------------------------------------------

    @classmethod
    @contextlib.contextmanager
    def smoke(cls, cfg: Config | None = None) -> Iterator["App2Driver"]:
        """Spin a local App 2 server serving the smoke app + the stub
        fetcher + ``SMOKE_FILTER_SPECS``, open a WebKit page, yield the
        driver, tear both down."""
        cfg = cfg or make_test_config()
        tree_app, sheet = build_smoke_app(cfg)
        with cls.serving(
            tree_app=tree_app, sheet=sheet,
            data_fetcher=stub_money_trail_fetcher,
            dashboard_id="smoke", dashboard_title="Smoke",
            filter_specs=SMOKE_FILTER_SPECS,
        ) as driver:
            yield driver

    @classmethod
    @contextlib.contextmanager
    def serving(
        cls, *,
        tree_app: App,
        sheet: Sheet,
        data_fetcher: DataFetcher,
        dashboard_id: str = "harness",
        dashboard_title: str = "Harness",
        filter_specs: Sequence[FilterSpec] = (),
        dev_log: bool = False,
    ) -> Iterator["App2Driver"]:
        """Spin a local App 2 server serving any tree + fetcher and yield
        a driver pointed at it.

        The general-purpose factory behind ``smoke()`` — use directly
        when the test builds its own tree (Executives / L2FT /
        Investigation / Money Trail) and supplies its own fetcher (stub
        or live-DB via ``make_live_db_fetcher_for_app``). Same context-
        manager shape: server + browser tear down on exit.

        ``driver.base_url`` exposes the server's base URL so tests can
        construct cross-sheet URLs themselves (``f"{base_url}/dashboards/
        {dashboard_id}/sheets/{sheet_id}"``); ``driver.page`` is the
        escape hatch for App2-internal assertions (HTTP intercept via
        ``page.route``, refetch checks via ``page.expect_response``,
        DOM probes via ``page.evaluate``) — the wire-shape kind of
        assertion that doesn't translate to renderer-agnostic verbs.
        """
        analysis = tree_app.analysis
        if analysis is None:
            raise RuntimeError(
                "App2Driver.serving() needs an emitted tree — call "
                "tree_app.emit_analysis() first (resolves auto-IDs)."
            )
        sheet_id_by_name = {s.name: str(s.sheet_id) for s in analysis.sheets}
        with html2_server(
            tree_app=tree_app, sheet=sheet,
            data_fetcher=data_fetcher,
            dashboard_id=dashboard_id,
            dashboard_title=dashboard_title,
            filter_specs=filter_specs,
            dev_log=dev_log,
        ) as url, webkit_page() as page:
            yield cls(
                base_url=url, page=page, sheet_id_by_name=sheet_id_by_name,
            )

    # -- raw access (escape hatch for App2-internal assertions) ---------

    @property
    def page(self) -> Any:
        """The underlying Playwright ``Page`` — escape hatch for
        App2-internal assertions (``page.route`` for HTTP intercept,
        ``page.expect_response`` for refetch checks, ``page.evaluate``
        for DOM probes). Tests that only need renderer-agnostic verbs
        should NOT touch ``page``."""
        return self._page

    @property
    def base_url(self) -> str:
        """The App 2 server's bound base URL (``http://127.0.0.1:<port>``)
        — for tests that need to construct cross-sheet URLs the protocol
        verbs don't expose."""
        return self._base

    # -- navigation ------------------------------------------------------

    def open(self, dashboard: str, sheet: str | None = None) -> None:
        # `sheet` is a sheet *name* (protocol contract); App2's route
        # segment is the SheetId — translate via the served tree's map.
        self._dashboard = dashboard
        path = f"/dashboards/{dashboard}"
        if sheet is not None:
            try:
                sheet_id = self._sheet_id_by_name[sheet]
            except KeyError:
                raise KeyError(
                    f"no sheet named {sheet!r} in this dashboard — "
                    f"have {sorted(self._sheet_id_by_name)}"
                ) from None
            path += f"/sheets/{sheet_id}"
        self._sheet = sheet
        self._page.goto(self._base + path)
        # Visual sections auto-load via hx-trigger="load" — those AJAX
        # GETs count toward network activity, so networkidle waits them out.
        self._page.wait_for_load_state("networkidle")

    def goto_sheet(self, name: str) -> None:
        # App 2 routing is stateless — a sheet switch is just a new URL.
        # Re-navigating produces the right state (and blocks on the new
        # sheet's auto-load) just like a tab click would.
        if self._dashboard is None:
            raise RuntimeError("App2Driver.goto_sheet() called before open()")
        self.open(self._dashboard, sheet=name)

    # -- reads -----------------------------------------------------------

    def _section(self, visual_title: str) -> Any:
        """Locator for the ``section[data-visual-kind]`` whose ``<h2>``
        text is exactly ``visual_title``."""
        return self._page.locator(
            f'section[data-visual-kind]:has(h2:text-is("{visual_title}"))'
        ).first

    def sheet_names(self) -> list[str]:
        # App2's sheet-tab strip is a top-level ``<nav>`` of ``<a>``s
        # whose text is each ``Sheet.name`` (render.py ``_render_sheet_tabs``;
        # a single-sheet dashboard renders no strip → []).
        return [
            t.strip()
            for t in self._page.locator("nav > a").all_inner_texts()
            if t.strip()
        ]

    def filter_labels(self) -> list[str]:
        # ``#filter-form`` is ``<label>{title} <select|input>…</label>``
        # for dropdown / multi-select / slider / date, plus
        # ``<div class="category-filter"><span>{title}</span>…</div>`` for
        # a CategoryFilter. The label text is the leading text nodes (the
        # control element itself is a child, not a text node).
        return list(self._page.evaluate(
            """() => {
                const form = document.querySelector('#filter-form');
                if (!form) return [];
                const out = [];
                form.querySelectorAll(':scope > label').forEach((lbl) => {
                    const txt = Array.from(lbl.childNodes)
                        .filter((n) => n.nodeType === 3)
                        .map((n) => n.textContent.trim())
                        .filter(Boolean).join(' ').trim();
                    if (txt) out.push(txt);
                });
                form.querySelectorAll('.category-filter > span').forEach((sp) => {
                    const t = sp.textContent.trim();
                    if (t) out.push(t);
                });
                return out;
            }"""
        ))

    def filter_options(self, label: str) -> list[str]:
        # Read the underlying ``<select>``'s option text directly (Tom
        # Select decorates it but the real <option>s stay in the DOM).
        # Filter the same sentinels QS's reader drops so the two
        # renderers' option universes line up.
        sel = self._filter_control(label).locator("select").first
        sel.wait_for(state="attached")
        opts = sel.evaluate(
            """(s) => Array.from(s.options).map((o) => (o.text || '').trim())"""
        )
        return [
            o for o in opts
            if o and o not in ("All", "Select all")
        ]

    def visual_titles(self) -> list[str]:
        return [
            t.strip()
            for t in self._page.locator(
                "section[data-visual-kind] h2"
            ).all_inner_texts()
        ]

    def wait_loaded(
        self, visual_title: str, *, timeout_ms: int = 15_000,
    ) -> None:
        section = self._section(visual_title)
        # The .visual-data swap target is filled by HTMX with a chart /
        # table / KPI after the per-visual GET — wait for *something*
        # inside it.
        section.locator(
            ".visual-data table, .visual-data svg, .visual-data .kpi-value"
        ).first.wait_for(state="visible", timeout=timeout_ms)

    def table_rows(self, visual_title: str) -> list[dict[str, str]]:
        section = self._section(visual_title)
        table = section.locator("table.table-data").first
        table.wait_for(state="visible")
        # Header <th>s carry a sort badge (▲/▼) + a clickable <a>; the
        # column name is the leading text token.
        headers = [
            h.split("\n")[0].strip().rstrip("▲▼ ").strip()
            for h in table.locator("thead th").all_inner_texts()
        ]
        rows: list[dict[str, str]] = []
        for tr in table.locator("tbody tr").all():
            cells = [c.strip() for c in tr.locator("td").all_inner_texts()]
            rows.append(dict(zip(headers, cells, strict=False)))
        return rows

    def table_row_count(self, visual_title: str) -> int:
        # App2 renders every row in DOM (no virtualization), so the
        # window IS the full count — no page-size-bump needed.
        return len(self.table_rows(visual_title))

    def kpi_value(self, visual_title: str) -> str | None:
        section = self._section(visual_title)
        loc = section.locator(".kpi-value").first
        if loc.count() == 0:
            return None
        return loc.inner_text().strip()

    # -- writes ----------------------------------------------------------

    def _wait_for_refetch(self, action: Callable[[], object]) -> None:
        """Run ``action`` (which mutates a ``#filter-form`` input + fires
        a bubbling ``change``), then block until the resulting visual
        re-fetch settles — the first ``/visuals/.../data`` response, then
        ``networkidle`` for the remaining visuals + the bootstrap.js
        re-hydration that runs synchronously on each swap."""
        with self._page.expect_response(
            _VISUAL_DATA_URL_RE, timeout=_REFETCH_TIMEOUT_MS,
        ):
            action()
        self._page.wait_for_load_state("networkidle")

    def _filter_control(self, label: str) -> Any:
        """Locator for the ``#filter-form`` control group whose visible
        text contains ``label`` — a ``<label>`` for dropdown / multi-select
        / numeric-range, or a ``.category-filter`` ``<div>`` for a
        CategoryFilter."""
        in_label = self._page.locator(
            "#filter-form label", has_text=label,
        )
        if in_label.count() > 0:
            return in_label.first
        return self._page.locator(
            "#filter-form .category-filter", has_text=label,
        ).first

    def pick_filter(self, label: str, values: Sequence[str]) -> None:
        sel = self._filter_control(label).locator("select").first
        vals = list(values)
        self._wait_for_refetch(lambda: sel.evaluate(
            """(s, vals) => {
                for (const o of s.options) {
                    o.selected = vals.includes(o.value) || vals.includes(o.text);
                }
                s.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            vals,
        ))

    def set_date_range(self, from_: str | None, to: str | None) -> None:
        self._wait_for_refetch(lambda: self._page.evaluate(
            """({ f, t }) => {
                const form = document.querySelector('#filter-form');
                const df = form.querySelector('input[name="date_from"]');
                const dt = form.querySelector('input[name="date_to"]');
                if (df) df.value = f || '';
                if (dt) dt.value = t || '';
                if (df) df.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            {"f": from_, "t": to},
        ))

    def set_slider(
        self, label: str, lo: float | None, hi: float | None,
    ) -> None:
        ctrl = self._filter_control(label)
        self._wait_for_refetch(lambda: ctrl.evaluate(
            """(el, { lo, hi }) => {
                const mn = el.querySelector('input[name^="min_"]');
                const mx = el.querySelector('input[name^="max_"]');
                if (mn) mn.value = lo === null ? '' : String(lo);
                if (mx) mx.value = hi === null ? '' : String(hi);
                if (mn) mn.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            {"lo": lo, "hi": hi},
        ))

    def clear_filters(self) -> None:
        # App 2 filter state lives entirely in the URL query string, so
        # "clear every filter" is just re-loading the bare sheet path —
        # which also re-inits the Tom Select / Flatpickr / noUiSlider
        # widgets fresh, not just the underlying form controls.
        if self._dashboard is None:
            raise RuntimeError("App2Driver.clear_filters() called before open()")
        self.open(self._dashboard, sheet=self._sheet)

    def cross_link(self, label: str) -> None:
        self._page.locator("a", has_text=label).first.click()
        self._page.wait_for_load_state("networkidle")
        # Re-derive nav state from the landed URL so a subsequent
        # goto_sheet() works.
        m = re.search(
            r"/dashboards/([^/?#]+)(?:/sheets/([^/?#]+))?", self._page.url,
        )
        if m:
            self._dashboard = m.group(1)
            self._sheet = m.group(2)

    def drill_from_first_row(self, visual_title: str) -> None:
        # App2's table renderer doesn't wire row clicks to drill actions
        # today (cross-sheet navigation goes through ``cross_link``'s
        # ``<a>`` clicks, not row clicks). When that lands, the impl is a
        # ``section[data-visual-kind] table.table-data tbody tr:first-child``
        # click + ``_wait_for_refetch``.
        raise NotImplementedError(
            "App2Driver.drill_from_first_row — App2's table renderer "
            "doesn't wire row-level drill actions yet"
        )

    def drill_from_first_row_via_menu(
        self, visual_title: str, menu_item: str,
    ) -> None:
        raise NotImplementedError(
            "App2Driver.drill_from_first_row_via_menu — App2 has no "
            "right-click context menu on table rows"
        )

    # -- artifacts -------------------------------------------------------

    def screenshot(self, path: str | Path | None = None) -> bytes:
        png: bytes = self._page.screenshot(full_page=True)
        if path is not None:
            Path(path).write_bytes(png)
        return png

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        # The server + page are owned by the context manager in the
        # factory (``.smoke()``); nothing to do here.
        pass
