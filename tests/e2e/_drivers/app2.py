"""X.2.q — ``App2Driver``: the ``DashboardDriver`` for the self-hosted
HTMX/d3 renderer.

App 2's DOM is deliberately simple — visuals are
``section[data-visual-kind]`` blocks with an ``<h2>`` title and a
``.visual-data`` swap target; tables are plain ``<table class="table-data">``
with a server-side-paginated page of rows + a ``.table-pager-range``
``"X–Y of M"`` pager (``_tree_fetcher._TABLE_PAGE_SIZE`` = 50, so the DOM
holds one page; ``table_row_count`` reads the ``M`` off the pager, not
``len(rows)``); the filter form is ``#filter-form`` with
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
from collections.abc import Callable, Generator, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from recon_gen.common.browser.helpers import webkit_page
from recon_gen.common.config import Config
from recon_gen.common.html._smoke_app import (
    SMOKE_FILTER_SPECS,
    build_smoke_app,
    stub_money_trail_fetcher,
)
from recon_gen.common.env_keys import RECON_E2E_PAGE_TIMEOUT
from recon_gen.common.html._tree_fetcher import OptionsSearchFetcher
from recon_gen.common.html.render import FilterSpec
from recon_gen.common.html.server import DataFetcher
from recon_gen.common.models import DatasetParameter
from recon_gen.common.tree.structure import App, Sheet
from tests._test_helpers import make_test_config
from tests.e2e._drivers.base import query_db_via_cfg, rekey_by_columns
from tests.e2e._harness_html2 import html2_server


# Matches the per-visual data endpoint, e.g.
# /dashboards/smoke/sheets/showcase/visuals/showcase-kpi/data?...
_VISUAL_DATA_URL_RE = re.compile(r"/visuals/[^/]+/data")
# The refetch wait reads the SAME configurable page timeout the rest of the
# browser tier uses (RECON_E2E_PAGE_TIMEOUT), so it inherits the runner's
# browser-layer 60s bump on BOTH local and CI. A hardcoded 30s here did NOT —
# the runner bumps the page timeout to 60s for the browser layer but this
# refetch ceiling stayed 30s, and the slower CI box outran it on
# test_bo_1[app2] (passed locally / failed CI = the POLICY-1 divergence).
# Default 30s for a local single-worker run (1-3s typical); 60s under the
# browser-layer bump. CR.x history: 15s→30s for xdist server-load starvation.
_REFETCH_TIMEOUT_MS = RECON_E2E_PAGE_TIMEOUT.get_or_none() or 30_000

# DD.4 — matches recon_gen.common.html.auth.SESSION_COOKIE_NAME (kept as a
# local literal so the test driver doesn't import the production module
# every call; if the production constant ever changes, the unit gate
# `tests/unit/test_session_cookie_name_constant_matches.py` will catch
# the drift before a green chain misleads).
_SESSION_COOKIE_NAME = "recon_gen_session"


class App2Driver:
    """``DashboardDriver`` over a running App 2 server + a WebKit page.

    Construct via a factory (``App2Driver.smoke()``), not directly —
    the factory owns the server + browser lifecycle as a context
    manager.
    """

    dialect = "app2"

    def __init__(
        self, *, base_url: str, page: Any,
        cfg: Config,
        sheet_id_by_name: Mapping[str, str],
    ) -> None:
        self._base = base_url.rstrip("/")
        self._page = page
        self._cfg = cfg
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
    def smoke(cls, cfg: Config | None = None) -> Generator["App2Driver", None, None]:
        """Spin a local App 2 server serving the smoke app + the stub
        fetcher + ``SMOKE_FILTER_SPECS``, open a WebKit page, yield the
        driver, tear both down."""
        cfg = cfg or make_test_config()
        tree_app, sheet = build_smoke_app(cfg)
        with cls.serving(
            cfg=cfg,
            tree_app=tree_app, sheet=sheet,
            data_fetcher=stub_money_trail_fetcher,
            dashboard_id="smoke", dashboard_title="Smoke",
            filter_specs=SMOKE_FILTER_SPECS,
        ) as driver:
            yield driver

    @classmethod
    @contextlib.contextmanager
    def attached_to(
        cls, *, base_url: str, cfg: Config,
        sheet_id_by_name: Mapping[str, str] | None = None,
    ) -> Generator["App2Driver", None, None]:
        """Driver pointed at an externally-managed server (caller owns
        the uvicorn lifecycle; the driver only owns the WebKit page).

        Use case: BV.3.3 Trainer e2e — the test owns the server via
        ``tests.e2e._studio_deploy_helpers::studio_server`` so it can
        seed the DB synchronously before the server starts. The driver
        attaches to that ``base_url`` for the Playwright leg.

        ``sheet_id_by_name`` is optional — the Trainer flow navigates
        via link clicks (not ``open(sheet=...)``), so an empty mapping
        is fine. Provide it only when the test calls ``open`` /
        ``goto_sheet`` by name.
        """
        with webkit_page() as page:
            yield cls(
                base_url=base_url, page=page, cfg=cfg,
                sheet_id_by_name=sheet_id_by_name or {},
            )

    @classmethod
    @contextlib.contextmanager
    def serving(
        cls, *,
        cfg: Config,
        tree_app: App,
        sheet: Sheet,
        data_fetcher: DataFetcher,
        dashboard_id: str = "harness",  # typing-smell: ignore[bare-str-id]: dashboard_id comes from callers as raw analyst string
        dashboard_title: str = "Harness",
        filter_specs: Sequence[FilterSpec] = (),
        options_search_fetcher: OptionsSearchFetcher | None = None,
        day_availability_fetcher: Any = None,
        dev_log: bool = False,
        wire_auth: bool = False,
    ) -> Generator["App2Driver", None, None]:
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
                "tree_app.validate() first (resolves auto-IDs)."
            )
        sheet_id_by_name = {s.name: str(s.sheet_id) for s in analysis.sheets}
        with html2_server(
            tree_app=tree_app, sheet=sheet,
            data_fetcher=data_fetcher,
            dashboard_id=dashboard_id,
            dashboard_title=dashboard_title,
            filter_specs=filter_specs,
            options_search_fetcher=options_search_fetcher,
            # DM.3 — App2-only Daily Statement day-availability decoration.
            day_availability_fetcher=day_availability_fetcher,
            dev_log=dev_log,
            # DD.4 — opt-in auth wiring. Default off so existing app2 e2e
            # tests keep their no-auth code path even when cfg carries
            # the auth.oidc + auth.session blocks (which CI's cfg does).
            # The OAuth login flow test sets wire_auth=True; nothing
            # else should.
            cfg=cfg,
            wire_auth=wire_auth,
        ) as url, webkit_page() as page:
            yield cls(
                base_url=url, page=page, cfg=cfg,
                sheet_id_by_name=sheet_id_by_name,
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
        # DK.11 — fail loudly on visible literal HTML entity references.
        # A page-level scan after networkidle catches double-escaped
        # chrome (titles, top nav, banners, sheet descriptions) the
        # moment a navigation lands, before downstream verbs run their
        # own per-visual checks. Operators see "Bob&#x27;s Bank"
        # instead of "Bob's Bank" → caught here.
        from recon_gen.common.browser.helpers import (  # noqa: PLC0415
            assert_no_literal_html_entities,
        )
        assert_no_literal_html_entities(
            self._page, context=f"App2Driver.open({dashboard=!r}, {sheet=!r})",
        )

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
        # For typeahead pickers this returns the SEED PAGE (empty query).
        # For per-query searches use `typeahead_filter(label, query)` —
        # the seed-only shape was insufficient (CR.x followup 2026-06-08:
        # didn't catch the matview-direct WHERE-clause leak that surfaced
        # in Studio, since seed-page never typed anything).
        return self.typeahead_filter(label, query="")

    def typeahead_filter(self, label: str, query: str) -> list[str]:
        """Drive the typeahead picker's per-keystroke load with ``query``
        and return the SERVER-MATCHED options.

        For static-options pickers (no data-typeahead) this just returns
        the rendered options (query is ignored — they don't have a
        typeahead path).

        For typeahead-marked pickers: triggers Tom Select's `load(query)`
        which fires settings.load → fetch
        `dropdown-search/<ds>/<col>?q=<query>` → server returns matched
        options → settings.load's callback adds them to ts.options. We
        poll ts.options until populated OR timeout, then return the
        option labels.

        CR.x (2026-06-08) — added because the prior pattern called
        `s.tomselect.load('', userCallback)` assuming Tom Select would
        forward the callback to settings.load's callback. It doesn't —
        ``tomselect.load(query)`` ignores extra args. The DOM-poll
        replacement matches Tom Select's actual contract. Driving the
        REAL per-query path (not just seed) is what catches:
          (a) URL-resolution bugs (relative vs absolute — the original
              CR.x trigger).
          (b) WHERE-clause leaks (matview-direct path bypassing the
              dataset's narrowing).
          (c) Option-accumulation bugs (Tom Select's addOption merges,
              caller must clearOptions to get fresh per-query results).
        """
        import time as _time
        sel = self._filter_control(label).locator("select").first
        sel.wait_for(state="attached")
        is_typeahead = sel.evaluate(
            """(s) => s.dataset.typeahead === '1'"""
        )
        if is_typeahead:
            # Trigger Tom Select's per-query load. Tom Select fetches
            # via settings.load (bootstrap.js) which clearOptions +
            # fetch + addOption. We poll ts.options for the populated
            # state.
            # CR.x — bust Tom Select's loadedSearches cache so the
            # load() call always re-fetches (we want a clean per-query
            # exercise of the server path). Without this, the focus-
            # preload's load('') leaves loadedSearches['']=true and
            # subsequent load('') no-ops, leaving driver to read
            # whatever ts.options happens to have at that instant
            # (possibly empty if the preload-fetch's addOption hasn't
            # landed yet).
            sel.evaluate(
                """(s, q) => {
                    if (!s.tomselect) return;
                    if (s.tomselect.loadedSearches) {
                        delete s.tomselect.loadedSearches[q];
                    }
                    s.tomselect.load(q);
                }""",
                query,
            )
            # Poll for ts.options to populate. Wait for count > 0 OR
            # timeout — don't early-exit on loading === 0 because the
            # poll could land between load-completion and the
            # addOption callback firing (false-negative empty result).
            deadline = _time.monotonic() + 5.0
            while _time.monotonic() < deadline:
                count = sel.evaluate(
                    """(s) => s.tomselect
                        ? Object.keys(s.tomselect.options || {}).length
                        : 0"""
                )
                if count > 0:
                    break
                self._page.wait_for_timeout(100)
            opts = sel.evaluate(
                """(s) => {
                    if (!s.tomselect) {
                        return Array.from(s.options).map(
                            (o) => (o.text || '').trim()
                        );
                    }
                    // ts.options is keyed by valueField; the label
                    // field is what the user sees in the dropdown.
                    return Object.values(s.tomselect.options || {}).map(
                        (o) => (o.label || o.text || '').toString().trim()
                    );
                }"""
            )
        else:
            # Static-options picker: query is meaningless, return
            # rendered <option> labels.
            opts = sel.evaluate(
                """(s) => Array.from(s.options).map(
                    (o) => (o.text || '').trim()
                )"""
            )
        return [
            o for o in opts
            if o and o not in ("All", "Select all")
        ]

    def picker_endpoint_probe(
        self, label: str, *, query: str = "",
    ) -> dict[str, object]:
        """Diagnostic helper: hit the dropdown-search endpoint that
        powers the typeahead picker labeled ``label``, in-browser
        (so cookies / session / origin / form-state ``param_*`` all
        match what a live keystroke would send), and return a dict
        with ``url``, ``status``, ``body``, ``options_count``.

        Use this from a test's failure path to surface what the
        server is actually returning when a picker shows up empty
        — distinguishes "no rows in matview" from "endpoint never
        reached / params wrong / fetcher returning empty" without
        a separate curl + URL-reconstruction dance.

        Returns ``{"error": "<reason>"}`` for static (non-typeahead)
        pickers + when the control / select / typeahead URL can't
        be located — the caller pastes the dict into the failure
        message and the reason explains the no-probe outcome.
        """
        try:
            sel = self._filter_control(label).locator("select").first
            url = sel.evaluate(
                """(s) => s.dataset.typeaheadUrl || null"""
            )
            if not url:
                return {"error": "no data-typeahead-url (static picker?)"}
            # Build absolute URL + carry the live form state through
            # so cascading picker WHERE clauses (the matview-direct
            # path's optional `where_clause`) match the real request.
            probe = self._page.evaluate(
                """async ({ url, q }) => {
                    const form = document.querySelector('#filter-form');
                    const fd = form ? new FormData(form) : new FormData();
                    const usp = new URLSearchParams();
                    usp.set('q', q);
                    for (const [k, v] of fd.entries()) {
                        if (k.startsWith('param_')) usp.append(k, v);
                    }
                    const full = url + (url.includes('?') ? '&' : '?') + usp.toString();
                    const resp = await fetch(full, { credentials: 'same-origin' });
                    const text = await resp.text();
                    return { url: full, status: resp.status, body: text };
                }""",
                {"url": url, "q": query},
            )
            body = str(probe.get("body", ""))
            options_count = -1
            try:
                import json as _json
                parsed: object = _json.loads(body)
                if isinstance(parsed, dict):
                    opts: object = parsed.get("options")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]: untrusted JSON shape probed for diagnostics only
                    if isinstance(opts, list):
                        options_count = len(opts)  # pyright: ignore[reportUnknownArgumentType]: untrusted JSON shape probed for diagnostics only
            except (ValueError, TypeError):
                pass
            return {
                "url": probe.get("url"),
                "status": probe.get("status"),
                "body": body[:1000],
                "options_count": options_count,
            }
        except Exception as exc:  # pragma: no cover — diagnostic helper
            return {"error": f"{type(exc).__name__}: {exc}"}

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
        # AA.B.5.followon.skeleton — the visual is "done" when the
        # ``.visual-loading`` skeleton element is gone from the
        # ``.visual-data`` swap target (HTMX wipes it on response;
        # bootstrap.js re-injects on next request). Polling for its
        # absence catches both initial-load and refresh in one rule:
        # before this check, ``wait_loaded`` keyed off the *presence*
        # of a table/svg/kpi-value, which races on refresh — a queued
        # refresh that lands AFTER ``wait_loaded`` returns leaves the
        # test reading stale content. Skeleton-absence is the natural
        # complement to QuickSight's ``analysis_visual`` readiness
        # signal — gives the parallel App2 spec.
        section.locator(".visual-data:not(:has(.visual-loading))").first.wait_for(
            state="visible", timeout=timeout_ms,
        )
        # Defense in depth: also confirm content actually rendered (a
        # ``.visual-data`` div whose initial-load failed silently would
        # still satisfy the no-skeleton check). Tables / charts / KPIs
        # show up here — AND the BQ.1 empty-state banners count as
        # "rendered" too: when a filter narrows to zero rows, the
        # renderer paints ``.<kind>-empty-state`` IN PLACE of the
        # table/svg/kpi-value, so the original three-selector OR
        # would time out waiting for content that no longer exists
        # (regression that broke ``test_l1_dropdown_pickers_inverse_
        # excludes_anchor[app2-*]`` on the v11.26.0 deploy).
        section.locator(
            ".visual-data table, .visual-data svg, .visual-data .kpi-value, "
            ".visual-data .table-empty-state, "
            ".visual-data .bar-chart-empty-state, "
            ".visual-data .line-chart-empty-state, "
            ".visual-data .sankey-empty-state, "
            ".visual-data .force-graph-empty-state, "
            ".visual-data .kpi-empty-state"
        ).first.wait_for(state="visible", timeout=timeout_ms)
        # AA.A.9.race — freshness oracle: the visual is settled iff
        # the content rendered in the DOM was produced by the LATEST
        # request fired against it (``data-requested-params`` set in
        # bootstrap.js htmx:beforeRequest matches ``data-rendered-params``
        # mirrored in htmx:afterSwap from the response's
        # ``data-bound-params``). Closes the T2→T4 gap in
        # ``hx-sync="this:queue last"`` chains: when a queued wave's
        # in-flight response lands and clears the skeleton BUT a fresher
        # wave is already queued, the no-skeleton check returns true
        # while the rendered content still reflects the queued (stale)
        # wave. The freshness oracle catches this — requested has
        # advanced to the queued wave's params, but rendered still
        # shows the prior wave's. We wait until the queued wave's
        # response lands and rendered catches up.
        #
        # Skip when no requested-params has been stamped yet (initial
        # load on a visual where bootstrap.js's beforeRequest hasn't
        # fired the stamp yet because the request was dispatched
        # before the JS loaded — rare, but the wait_for above already
        # guarantees content is visible, so we don't gate on freshness
        # in that degraded case).
        section.locator(".visual-data").first.evaluate(
            """(el, timeoutMs) => {
                const deadline = performance.now() + timeoutMs;
                return new Promise((resolve, reject) => {
                    const tick = () => {
                        const req = el.dataset.requestedParams;
                        const ren = el.dataset.renderedParams;
                        // Pre-first-stamp: ren may be set from the
                        // initial server-render but req hasn't been
                        // stamped (no htmx event has fired). Treat
                        // that as settled — wait_for above already
                        // confirmed content is visible.
                        if (req == null) { resolve(); return; }
                        if (req === ren) { resolve(); return; }
                        if (performance.now() > deadline) {
                            reject(new Error(
                              `freshness wait timed out: req=${req} ren=${ren}`
                            ));
                            return;
                        }
                        setTimeout(tick, 50);
                    };
                    tick();
                });
            }""",
            timeout_ms,
        )

    def table_rows_full(
        self,
        visual_title: str,
        *,
        columns: Sequence[str] | None = None,
    ) -> list[dict[str, str]]:
        """App2 renders all rows in DOM (no virtualization), so the full
        set IS the visible set — delegate to `table_rows`.
        """
        return self.table_rows(visual_title, columns=columns)

    def table_rows(
        self,
        visual_title: str,
        *,
        columns: Sequence[str] | None = None,
    ) -> list[dict[str, str]]:
        section = self._section(visual_title)
        # BQ.1 follow-up — when the filter narrows the table to zero
        # rows, renderTable paints ``.table-empty-state`` IN PLACE of
        # ``table.table-data``. Pre-fix this verb waited 30s for the
        # table to appear (it never did) → every
        # ``test_*_inverse_excludes_anchor[app2-*]`` test timed out on
        # the v11.26.x release CI. Short-circuit empty-state to an
        # empty row list — the test's "table contains no offender" or
        # "0 rows after narrow" contracts hold by construction.
        if section.locator(".table-empty-state").first.count() > 0:
            return []
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
        # AA.A.995 — see ``DashboardDriver.table_rows``'s ``columns=``
        # docstring. App2's ``<th>`` text is the raw SQL column name,
        # which differs from QS's display-label header; passing
        # ``columns`` re-keys both renderers identically.
        return rekey_by_columns(rows, columns) if columns else rows

    def find_row(
        self, visual_title: str, predicate: Mapping[str, str],
    ) -> dict[str, str] | None:
        # AA.A.l2ft-rails-inverse.2.e — walk pages via the rendered
        # ".table-pager-next" anchor (hx-get + hx-push-url, fetches via
        # HTMX form-submit so picker state in #filter-form survives).
        # Early-exit on first match — broken filter → match on page 1.
        #
        # Why not just re-navigate to ``?page_size=10000``: ``pick_filter``
        # currently mutates the HTMX form without updating the URL bar
        # (filed as the "App2 URL-canonical" task), so ``self.page.url``
        # is stale relative to the actual rendered picker state. Pager-
        # next clicks go through HTMX which carries the live form state,
        # avoiding the divergence entirely.
        section = self._section(visual_title)
        # BQ.1 follow-up — empty-state banner means no table chrome
        # exists at all; there are no rows to match the predicate so
        # the absent-row contract trivially holds. ``table_rows`` would
        # return ``[]`` here too, but checking once at the entry avoids
        # the wait-for-table loop entirely.
        if section.locator(".table-empty-state").first.count() > 0:
            return None
        # Hard cap on pages walked — protects against runaway loops if
        # the pager's aria-disabled isn't surfaced correctly.
        for _ in range(200):
            for row in self.table_rows(visual_title):
                if all(row.get(k) == v for k, v in predicate.items()):
                    return row
            next_link = section.locator(".table-pager-next").first
            if next_link.count() == 0:
                return None
            if next_link.get_attribute("aria-disabled") == "true":
                return None
            # Click + wait for HTMX swap — the new <table> mounts in
            # place of the old, so re-querying ``table.table-data`` for
            # ``state=visible`` settles on the swapped DOM.
            self._click(next_link)
            section.locator("table.table-data").first.wait_for(
                state="visible",
            )
        return None

    def table_row_count(self, visual_title: str) -> int:
        # App2's Table renderer is server-side paginated
        # (``_tree_fetcher._TABLE_PAGE_SIZE`` = 50) — the DOM holds one
        # page, not the full set. The true total lives in the pager's
        # ``"X–Y of M"`` text (``.table-pager-range``; rendered even for a
        # 0-row table as ``"0–0 of 0"``). Parse ``M`` out of it; fall back
        # to ``len(table_rows())`` only when there's no pager at all (a
        # tiny single-page table the renderer didn't bother paginating).
        section = self._section(visual_title)
        # BQ.1 follow-up — empty-state banner short-circuit, mirrors
        # ``table_rows``. The banner replaces the entire table chrome
        # so there's no pager to read either.
        if section.locator(".table-empty-state").first.count() > 0:
            return 0
        pager = section.locator(".table-pager-range").first
        if pager.count() > 0:
            text = pager.inner_text()
            m = re.search(r"of\s+([\d,]+)\s*$", text.strip())
            if m is not None:
                return int(m.group(1).replace(",", ""))
        return len(self.table_rows(visual_title))

    def kpi_value(self, visual_title: str) -> str | None:
        section = self._section(visual_title)
        loc = section.locator(".kpi-value").first
        if loc.count() == 0:
            return None
        return loc.inner_text().strip()

    def filter_value(self, label: str) -> str | None:
        """DM/BR.1 — the currently-selected VALUE of the single-select
        dropdown labelled ``label``, or ``None`` when nothing is picked.

        Reads Tom Select's ``getValue()`` when the widget is wired (the
        authoritative selected value — the underlying ``<select>``'s
        ``.value`` can lag a programmatic ``setValue`` until the next
        sync); falls back to the bare ``<select>.value`` on a degraded
        (no-Tom-Select / offline-CDN) load. An empty string normalizes to
        ``None`` so a cleared / never-picked dropdown reads the same on
        both paths — the cascade-clear test asserts ``value is None`` after
        the source changes.

        Single-select only: a multi-select ``getValue()`` returns an
        array; this verb is for the Role→Account cascade where both
        pickers are SINGLE_SELECT. Returns the raw bound value (the
        ``account_display`` string for the Account picker), not the option
        label — for these pickers they're the same (label == value).
        """
        sel = self._filter_control(label).locator("select").first
        sel.wait_for(state="attached")
        raw = sel.evaluate(
            """(s) => {
                if (s.tomselect) {
                    const v = s.tomselect.getValue();
                    // Single-select getValue() is a string; guard against
                    // an unexpected array by joining (empty → '').
                    return Array.isArray(v) ? v.join(',') : (v || '');
                }
                return s.value || '';
            }"""
        )
        text = str(raw).strip()
        return text or None

    def day_availability(
        self, label: str, *, open_on: str | None = None,
    ) -> dict[str, list[str]]:
        """DM.3 — open the Flatpickr day picker labelled ``label`` and
        read each visible calendar day's availability markers.

        Returns ``{iso_date: [states]}`` where ``states`` is a subset of
        ``["transactions", "balance"]`` — derived from the
        ``.has-transactions`` / ``.has-balance`` classes the
        ``onDayCreate`` callback added per the server's day-availability
        map. Days with no marker are omitted (the picker renders them
        plain — DECORATION not restriction). The ISO date is read off
        each ``.flatpickr-day``'s ``dateObj`` via the live flatpickr
        instance's ``formatDate`` (mirrors exactly how ``onDayCreate``
        keyed the map), so the keys match the DB's per-day sets without a
        locale-fragile ``aria-label`` parse.

        Opens the picker by clicking its visible input, then — when
        ``open_on`` (``YYYY-MM-DD``) is given — jumps the calendar to that
        month via the flatpickr instance's ``jumpToDate`` + ``redraw`` so
        the visible grid lands on the seeded data window (the picker's
        DEFAULT month is the as_of-frame anchor, which for a live clock can
        be months away from a LOCKED_ANCHOR-seeded DB). ``jumpToDate``
        navigates without selecting a day, so it doesn't perturb the
        picked Business Day. The ``redraw`` re-runs ``onDayCreate`` per
        cell, re-fetching + re-decorating for the new month. Then waits for
        the decoration fetch + class-add to settle and snapshots the
        calendar. Mirrors the user gesture (open → page to the month →
        read).
        """
        ctrl = self._filter_control(label)
        visible = ctrl.locator(
            'input[data-widget="flatpickr-single"]'
        ).first
        visible.wait_for(state="attached")
        # Open the calendar (flatpickr binds on the input's focus/click).
        self._click(visible)
        # The flatpickr calendar mounts as a sibling .flatpickr-calendar.
        self._page.locator(".flatpickr-calendar.open").first.wait_for(
            state="visible", timeout=10_000,
        )
        if open_on is not None:
            # Navigate the calendar to the target month WITHOUT selecting a
            # day. redraw() re-runs onDayCreate so the new month decorates.
            visible.evaluate(
                """(el, iso) => {
                    const fp = el._flatpickr;
                    if (!fp) return;
                    fp.jumpToDate(iso, false);
                    fp.redraw();
                }""",
                open_on,
            )
        # onDayCreate's class-add runs after the day-availability fetch
        # resolves (a network round-trip). Poll until at least one day
        # carries a marker OR a short settle deadline elapses (an account
        # with zero activity in the window legitimately decorates nothing,
        # so we can't hard-require a marker — the deadline bounds that
        # case). The poll reads through the live flatpickr instance.
        return self._read_day_markers(visible)

    def _read_day_markers(self, visible_input: Any) -> dict[str, list[str]]:
        """Read ``{iso: [states]}`` off the open flatpickr calendar via
        its live instance.

        The ``onDayCreate`` decoration lands ASYNCHRONOUSLY: a
        ``redraw()`` rebuilds all 42 day cells (clearing their classes),
        then each cell's ``onDayCreate`` kicks off a network fetch and
        applies ``.has-transactions`` / ``.has-balance`` in a ``.then``
        callback. Those callbacks flush across many microtasks, so a
        snapshot taken too early catches a PARTIAL decoration — e.g. the
        leading both-days lit but the later carry days not yet, or a
        single day decorated (observed: a ``total=1`` snapshot at ~15ms
        before the full set lands at ~20ms; the window widens under CI
        xdist load + PG latency). The pre-DM-fix "return the first
        non-empty snapshot" therefore returned mid-flush partials —
        which is exactly the ``no carry day rendered`` CI flake
        (sasquatch_pr gl-1810: the both-days decorated first, the
        snapshot fired, the carry days were dropped).

        Fix: wait for the marker set to STABILIZE — return only after the
        snapshot is byte-identical across ``_STABLE_POLLS`` consecutive
        reads (the decoration flush has quiesced), bounded by a deadline.
        A genuinely-undecorated window stabilizes at ``{}`` and returns
        after the stability window elapses (no hang), so the empty-state
        contract is preserved. Robust by construction: the read doesn't
        depend on how long the async flush takes, only on it having
        settled.
        """
        import time as _time
        deadline = _time.monotonic() + 12.0
        # Number of consecutive identical reads that prove the async
        # decoration flush has quiesced. 3 × the 150ms poll gap (~450ms
        # of no change) clears the observed mid-flush partials with margin
        # even under CI load.
        _STABLE_POLLS = 3
        snapshot_js = """(el) => {
            const fp = el._flatpickr;
            if (!fp || !fp.calendarContainer) return {};
            const out = {};
            const days = fp.calendarContainer.querySelectorAll(
                '.flatpickr-day'
            );
            days.forEach((d) => {
                if (!d.dateObj) return;
                // Skip the prev/next-month spill cells so the keys
                // stay inside the displayed month (the test
                // compares against the DB's per-account day set,
                // which the overscanned window covers).
                const states = [];
                if (d.classList.contains('has-transactions')) {
                    states.push('transactions');
                }
                if (d.classList.contains('has-balance')) {
                    states.push('balance');
                }
                if (states.length === 0) return;
                const iso = fp.formatDate(d.dateObj, 'Y-m-d');
                out[iso] = states;
            });
            return out;
        }"""

        def _snapshot() -> dict[str, list[str]]:
            return visible_input.evaluate(snapshot_js)

        def _key(m: dict[str, list[str]]) -> tuple[tuple[str, tuple[str, ...]], ...]:
            return tuple(sorted((k, tuple(v)) for k, v in m.items()))

        markers = _snapshot()
        last_key = _key(markers)
        stable_count = 0
        while _time.monotonic() < deadline:
            self._page.wait_for_timeout(150)
            current = _snapshot()
            current_key = _key(current)
            if current_key == last_key:
                stable_count += 1
                # DR.7.f — only a stable NON-EMPTY snapshot counts as
                # "settled". A stable EMPTY snapshot is ambiguous: it reads
                # identically whether the async onDayCreate fetch hasn't
                # applied markers yet (transient — under xdist + PG latency
                # the first decoration can land >_STABLE_POLLS×150ms after
                # the redraw) or the window is genuinely undecorated.
                # Returning it early was the DM.3 "empty markers" flake
                # (surfaced when DR.7.a's seed shift moved the carry-richest
                # account onto a heavier-fetch account). The sole caller
                # always picks an account WITH data, so keep waiting through
                # a stable-empty; a genuinely-empty window still falls
                # through to the deadline return below (no hang, contract
                # preserved).
                if stable_count >= _STABLE_POLLS and current:
                    return current
            else:
                stable_count = 0
                last_key = current_key
            markers = current
        return markers

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

    @contextlib.contextmanager
    def _record_requests(
        self, url_needle: str,
    ) -> "Generator[list[str], None, None]":
        """Record every request whose URL contains ``url_needle`` for the
        block's duration, yielding the growing list.

        Lets a caller assert "the server call I expected actually fired"
        and SCREAM the specific gap when it didn't — instead of degrading
        into a downstream opaque timeout or a vague "the value never
        appeared". When a driver mutation is supposed to cause a server
        call, wrap it here and check the list is non-empty; an empty list
        means the expected call DID NOT HAPPEN (a no-op'd mutation / an
        unwired callback) — a different bug than the call firing and
        coming back wrong."""
        seen: list[str] = []

        def _on_req(req: Any) -> None:
            if url_needle in req.url:
                seen.append(req.url)

        self._page.on("request", _on_req)
        try:
            yield seen
        finally:
            self._page.remove_listener("request", _on_req)

    def _wait_for_refetch(self, action: Callable[[], object]) -> None:
        """Run ``action`` (which mutates a ``#filter-form`` input + fires
        a bubbling ``change``), then block until every visual on the
        page has settled.

        AA.B.5.followon.skeleton — wait until NO ``.visual-data`` div
        has a ``.visual-loading`` skeleton inside it. That's the
        complement of bootstrap.js's per-request inject-on-beforeRequest
        + HTMX's wipe-on-swap: skeleton present ⇒ a request is in
        flight or queued; skeleton absent across all visuals ⇒ every
        visual's most-recent request has landed + swapped.

        Old strategy (first-response + networkidle) raced under
        ``hx-sync="this:queue last"``: when the pick triggered some
        visuals immediately + queued others behind in-flight initial-
        loads, networkidle hit before the queued refreshes fired. The
        captured test state showed stale data on the slowest 2-3
        visuals — chain 11c02b0 had 2 [app2] failures on exactly this
        shape. Polling skeleton-absence is per-visual and ordering-
        agnostic; queue-last is invisible to the test.
        """
        try:
            with self._page.expect_response(
                _VISUAL_DATA_URL_RE, timeout=_REFETCH_TIMEOUT_MS,
            ):
                action()
        except PlaywrightTimeoutError as exc:
            # The mutation fired NO visual /data refetch — scream the gap
            # instead of surfacing Playwright's opaque "Timeout Xms exceeded
            # waiting for event 'response'". Usually a setValue no-op (picked
            # value absent from the control's option set) or an unwired
            # auto-refresh: the form serialized unchanged, so no re-query
            # fired. The expected server call did not happen.
            raise AssertionError(
                f"App2 _wait_for_refetch: the mutation fired NO visual /data "
                f"refetch within {_REFETCH_TIMEOUT_MS / 1000:.0f}s — the "
                f"control change triggered no re-query (setValue no-op / "
                f"value-not-in-options / unwired auto-refresh). Expected "
                f"server call did not happen."
            ) from exc
        # Wait for all per-visual skeletons to clear (HTMX wiped each
        # one on its swap response). 15s ceiling matches wait_loaded.
        self._page.wait_for_function(
            "() => document.querySelectorAll("
            "'.visual-data:has(.visual-loading)'"
            ").length === 0",
            timeout=15_000,
        )
        self._page.wait_for_load_state("networkidle")

    def _cascade_target_params(self, source_select: Any) -> list[str]:
        """The ``param_<name>`` of every ``#filter-form`` ``<select>`` that
        names ``source_select`` as its cascade source (carries
        ``data-cascade-source-param`` == ``source_select``'s bare param) —
        i.e. the targets this control will CLEAR when it changes.

        Returns ``[]`` when ``source_select`` is not a cascade source (the
        common case — every non-cascade picker). The driver uses a
        non-empty result to switch ``pick_filter`` from the
        ``expect_response`` wait (which can hard-hang on a cascade-source
        pick — see ``pick_filter``) to the stable-DOM settle in
        ``_wait_for_cascade_settle``.

        Read entirely in-browser off the live form so it tracks the served
        tree's cascade wiring with no Python-side mirror to drift.
        """
        return list(source_select.evaluate(
            """(s) => {
                const form = s.closest('#filter-form, form');
                if (!form) return [];
                const myParam = (s.name || '').replace(/^param_/, '');
                if (!myParam) return [];
                const out = [];
                form.querySelectorAll(
                    'select[data-cascade-source-param]'
                ).forEach((tgt) => {
                    if (tgt.dataset.cascadeSourceParam === myParam) {
                        // The hidden/underlying select's own param name —
                        // the value the driver polls for "cleared".
                        out.push(tgt.name || '');
                    }
                });
                return out.filter(Boolean);
            }"""
        ))

    def _wait_for_cascade_settle(
        self, action: Callable[[], object], target_params: Sequence[str],
    ) -> None:
        """Run ``action`` (a cascade-SOURCE pick) then block until the
        post-cascade DOM has SETTLED — robust by construction, never a hard
        hang.

        A cascade-source pick (Daily Statement Role → Account) triggers:
        (1) ``bootstrap.js``'s ``tomInstance.clear()`` on each cascade
        TARGET — synchronous, but Tom Select applies it across microtasks;
        (2) the target's own ``dropdown-options`` re-fetch (NOT a visual
        request); (3) an INDIRECT, debounced visual re-fetch that — under CI
        load + slow PG — serializes behind ``hx-sync="this:queue last"`` so
        the first ``/visuals/.../data`` response can land past 30s, or never
        fires in-window when a re-wire ``change`` keeps resetting the 300ms
        debounce. Gating on a single ``expect_response`` therefore hangs for
        the full timeout (the 3 consecutive CI failures on this test).

        Settle condition (both must hold for ``_STABLE_POLLS`` consecutive
        reads, OR the deadline elapses — graceful fallback, no hang, like
        ``_read_day_markers``):
        - every cascade TARGET's value is CLEARED (Tom Select ``getValue()``
          empty) — the clear-on-source-change the test asserts;
        - NO ``.visual-data`` carries a ``.visual-loading`` skeleton — every
          visual's most-recent (post-pick) request has landed and swapped.

        The deadline (45s) exceeds the worst-case serial-visual-fetch wall
        seen under heavy CI load + ``queue last`` re-fire (8 visuals × slow
        PG), so a genuinely-slow-but-progressing refresh still settles
        properly; a never-firing refresh falls through after the deadline
        rather than hard-hanging — and the caller's own assertions
        (``filter_value`` cleared, visuals correct) then surface the real
        failure with a readable message instead of an opaque
        ``TimeoutError waiting for response``.
        """
        import time as _time

        action()
        deadline = _time.monotonic() + 45.0
        # 3 × the 150ms poll gap (~450ms of no in-flight skeleton AND the
        # targets staying cleared) proves the cascade flush + the indirect
        # visual refresh have both quiesced.
        _STABLE_POLLS = 3
        targets = list(target_params)

        def _settled() -> bool:
            # (a) no visual skeleton in flight.
            loading = self._page.evaluate(
                "() => document.querySelectorAll("
                "'.visual-data:has(.visual-loading)'"
                ").length"
            )
            if loading:
                return False
            # (b) every cascade target's value is cleared.
            for name in targets:
                val = self._page.evaluate(
                    """(name) => {
                        const sel = document.querySelector(
                            '[name="' + name + '"]'
                        );
                        if (!sel) return '';
                        if (sel.tomselect) {
                            const v = sel.tomselect.getValue();
                            return Array.isArray(v) ? v.join(',') : (v || '');
                        }
                        return sel.value || '';
                    }""",
                    name,
                )
                if str(val).strip():
                    return False
            return True

        stable = 0
        while _time.monotonic() < deadline:
            if _settled():
                stable += 1
                if stable >= _STABLE_POLLS:
                    break
            else:
                stable = 0
            self._page.wait_for_timeout(150)
        # One final networkidle drain so any tail request (a queue-last
        # re-fire that landed right at the settle boundary) is flushed
        # before the caller reads. Bounded so a stuck poller can't wedge
        # the test indefinitely — the settle loop above is the authority.
        with contextlib.suppress(Exception):
            self._page.wait_for_load_state("networkidle", timeout=5_000)

    def _filter_control(self, label: str) -> Any:
        """Locator for the ``#filter-form`` control group whose visible
        text starts with ``label`` — a ``<label>`` for dropdown /
        multi-select / numeric-range, or a ``.category-filter`` ``<div>``
        for a CategoryFilter.

        BG.7 bug-2026-05-25: was `has_text=label` (substring + matches
        ANY descendant text). A `<label>Role <select><option>ZBA**Sub**
        Account</option>...</select></label>` matches `has_text=
        "Account"` because the descendant `<option>` text contains
        "Account". `.first` then returns the Role label by DOM order
        and `filter_options("Account")` reads the wrong dropdown.
        Anchor with a prefix-match regex so "Account" matches only the
        label whose visible text STARTS with "Account" — `<label>Account
        <select>...</select></label>` matches; `<label>Role <select>
        ...ZBASubAccount...</select></label>` doesn't."""
        import re

        anchored = re.compile(r"^\s*" + re.escape(label) + r"\b")
        in_label = self._page.locator(
            "#filter-form label", has_text=anchored,
        )
        if in_label.count() > 0:
            return in_label.first
        return self._page.locator(
            "#filter-form .category-filter", has_text=anchored,
        ).first

    def pick_filter(self, label: str, values: Sequence[str]) -> None:
        # Prefer TomSelect's setValue API when the widget is wired
        # (`select.tomselect` is the instance set by `new TomSelect(el)`).
        # Mutating `option.selected` directly + dispatching `change` on
        # the underlying <select> looks like it should work — and does
        # transiently — but TomSelect's internal `Sync` runs in
        # response to the change and overwrites the selection with its
        # own (empty) items store. Net effect: pick disappears, form
        # serializes `param_X=` empty, visuals re-query unfiltered.
        # `setValue` updates both items + underlying <select> and fires
        # its own bubbling change (per wireTomSelect's onChange), so
        # wireFilterAutoRefresh sees the new state. Fallback to the
        # direct-mutation path covers the offline-CDN degraded case
        # where TomSelect failed to load (typeof TomSelect ===
        # "undefined" in wireTomSelect).
        sel = self._filter_control(label).locator("select").first
        vals = list(values)
        # CQ.2.c — for typeahead pickers the option list starts empty;
        # ``setValue`` would silently no-op. Force a server fetch with
        # each target as the query so the option lands in s.options
        # BEFORE setValue runs.
        is_typeahead = sel.evaluate(
            """(s) => s.dataset.typeahead === '1'"""
        )
        if is_typeahead:
            # CR.x (2026-06-08) — switched from the Promise-around-
            # `tomselect.load(q, resolve)` pattern to the DOM-poll
            # pattern (mirrors ``typeahead_filter`` at line 305+).
            # Tom Select v2's ``load(query)`` is 1-arg — the second
            # ``resolve`` arg was silently dropped, so the Promise
            # only resolved via the 5s setTimeout(reject), which
            # surfaced as ``Locator.evaluate: timeout``. Compounded
            # by CQ.5's ``clearOptions()`` in bootstrap.js
            # settings.load, which wipes the seed-page options on
            # every fetch — so the value never landed in s.options
            # before setValue ran. DOM-polling avoids relying on
            # the callback contract: bust loadedSearches, fire
            # load(q), poll until the target value v is actually
            # present in ts.options.
            #
            # Why poll for v specifically (not just `count > 0`):
            # _render_parameter_dropdown emits a leading
            # `<option value=""></option>` placeholder on every
            # single-select picker, so ts.options has 1 entry
            # before any fetch lands. `count > 0` passes
            # immediately, setValue runs against a byText map of
            # `{"": ""}`, falls through to raw `v` as the value,
            # Tom Select silently no-ops on an unknown value, no
            # change fires, _wait_for_refetch times out at 30s.
            # This is the shape of the 3 CI failures on 2b9dee26.
            import time as _time
            # CX.1 — the has-option probe, reused by the fast-path skip,
            # the load poll, and the post-load assertion below.
            _has_opt_js = """(s, q) => {
                if (!s.tomselect) return false;
                const opts = s.tomselect.options || {};
                if (Object.prototype.hasOwnProperty.call(opts, q)) return true;
                for (const k of Object.keys(opts)) {
                    if (opts[k] && opts[k].text === q) return true;
                }
                return false;
            }"""
            # Budget the per-query load with the configurable page timeout
            # (60s on the browser layer), NOT a guessed 5s. A slow CI
            # dropdown-search fetch must not outrun the poll, because a miss
            # here decays into a silent ``setValue`` no-op → opaque
            # ``_wait_for_refetch`` hang. CONFIRMED shape of the
            # ``test_cq_4_e[app2-Rails-Rail]`` CI failure (0c90595f): the
            # picked option was never in the final ``<select>`` and every
            # ``/visuals/.../data`` request carried an empty
            # ``param_pL2ftRail`` — the pick fired no filtered refetch.
            load_budget_s = (
                RECON_E2E_PAGE_TIMEOUT.get_or_none() or 30_000
            ) / 1000.0
            typeahead_url = sel.evaluate("(s) => s.dataset.typeaheadUrl || ''")
            for v in vals:
                # FAST PATH — the value is already in ``ts.options`` (the
                # ``_assert_pickable`` pattern pre-loads + asserts it via
                # ``typeahead_filter`` one call earlier). Use it directly;
                # cache-busting + re-fetching it UNCONDITIONALLY is what
                # raced under CI load (the re-fetch landing past the old 5s
                # poll left ``setValue`` running against options WITHOUT the
                # value → no-op → no ``change`` → no refetch).
                if sel.evaluate(_has_opt_js, v):
                    continue
                # SLOW PATH — genuinely absent: force a per-query fetch, then
                # wait (on the page-timeout budget) for the value to land.
                # Instrument the fetch: a typeahead-wired control MUST fire a
                # dropdown-search request when we call ``load(q)``. Record
                # them so a miss screams the SPECIFIC gap (the load callback
                # never reached the endpoint — expected call did not happen)
                # rather than the generic "option absent", which conflates a
                # front-end wiring gap with a back-end option-universe gap.
                with self._record_requests("/dropdown-search/") as searches:
                    sel.evaluate(
                        """(s, q) => {
                            if (!s.tomselect) return;
                            if (s.tomselect.loadedSearches) {
                                delete s.tomselect.loadedSearches[q];
                            }
                            s.tomselect.load(q);
                        }""",
                        v,
                    )
                    deadline = _time.monotonic() + load_budget_s
                    while _time.monotonic() < deadline:
                        if sel.evaluate(_has_opt_js, v):
                            break
                        self._page.wait_for_timeout(100)
                # Observability — never fall through to a ``setValue`` that
                # silently no-ops into an opaque refetch timeout. Name which
                # half broke (chotchki: when a call is expected, check it
                # fired; if not, scream).
                if not sel.evaluate(_has_opt_js, v):
                    if not searches:
                        raise AssertionError(
                            f"App2 pick_filter({label!r}): "
                            f"tomselect.load({v!r}) fired NO dropdown-search "
                            f"request within {load_budget_s:.0f}s. The control "
                            f"IS typeahead-wired (data-typeahead-url="
                            f"{typeahead_url!r}) yet the load callback never "
                            f"hit the endpoint — the EXPECTED SERVER CALL DID "
                            f"NOT HAPPEN. (A back-end universe gap would show "
                            f"the request firing but returning without {v!r}.)"
                        )
                    raise AssertionError(
                        f"App2 pick_filter({label!r}): {len(searches)} "
                        f"dropdown-search request(s) fired to {typeahead_url!r}"
                        f" but {v!r} never landed in ts.options — the endpoint "
                        f"returned an option set WITHOUT this value (back-end "
                        f"universe / SQL gap, not a wiring gap). setValue would "
                        f"no-op."
                    )
        # DM.5 — is THIS control a cascade SOURCE? A cascade TARGET
        # ``<select>`` (e.g. Daily Statement's Account picker) carries
        # ``data-cascade-source-param="<this control's param>"`` and an
        # ``hx-trigger="change from:[name='param_<src>']"`` re-fetch of its
        # *options* (a ``dropdown-options`` request — NOT a
        # ``/visuals/.../data`` request). When the source changes,
        # ``bootstrap.js::wireTomSelect``'s listener fires
        # ``tomInstance.clear() + clearOptions()`` on the target. The
        # downstream visual re-fetch is then INDIRECT (source change →
        # target clear → form ``change`` → debounced refresh) and, under
        # CI load + slow PG, the per-visual fetches serialize behind
        # ``hx-sync="this:queue last"`` so the FIRST ``/visuals/.../data``
        # response can land well past the 30s ``expect_response`` ceiling —
        # OR a re-wire ``change`` resets the 300ms debounce and the trigger
        # never fires in-window. Either way ``_wait_for_refetch`` hard-hangs
        # for a response that may never come in time (the 3 consecutive CI
        # failures on this test). So a cascade-source pick waits for the
        # STABLE post-cascade DOM state (target cleared + visual skeletons
        # gone) on a bounded deadline with a graceful no-hang fallback —
        # the same robust-by-construction shape as ``_read_day_markers``,
        # not a fixed ``expect_response`` that may never fire.
        cascade_target_params = self._cascade_target_params(sel)
        # CT.1 — peek BEFORE the action: if cur === target, setValue() is
        # a no-op and no `change` event fires → no visual-data request →
        # `_wait_for_refetch` hangs for the full 30s timeout. This is
        # what bit the CS.2 re-light test on CI (the WSL2 seed happened
        # to put Juniper Ridge as the initial Anchor; the test's
        # explicit `pick_filter("Anchor", [Juniper Ridge])` was a no-op).
        # Local seeds put a different default so the test passed there.
        # Skip the wait when the action provably won't refetch.
        will_change = sel.evaluate(
            """(s, vals) => {
                if (!s.tomselect) {
                    // Direct-mutation fallback path always dispatches
                    // change, so a "refetch will happen" verdict here
                    // is correct even when the new state matches the
                    // current state — the change event still fires.
                    return true;
                }
                const byText = new Map();
                for (const o of s.options) {
                    byText.set(o.text, o.value);
                }
                const resolved = vals.map((v) => byText.has(v)
                    ? byText.get(v) : v);
                const target = s.multiple
                    ? resolved
                    : (resolved[0] !== undefined ? resolved[0] : '');
                const cur = s.tomselect.getValue();
                return JSON.stringify(cur) !== JSON.stringify(target);
            }""",
            vals,
        )
        action = lambda: sel.evaluate(  # noqa: E731 — single-use lambda for the action
            """(s, vals) => {
                if (s.tomselect) {
                    const byText = new Map();
                    for (const o of s.options) {
                        byText.set(o.text, o.value);
                    }
                    const resolved = vals.map((v) => byText.has(v)
                        ? byText.get(v) : v);
                    const target = s.multiple
                        ? resolved
                        : (resolved[0] !== undefined ? resolved[0] : '');
                    s.tomselect.setValue(target);
                    return;
                }
                for (const o of s.options) {
                    o.selected = vals.includes(o.value) || vals.includes(o.text);
                }
                s.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            vals,
        )
        if not will_change:
            # Already at target — fire the (no-op) setValue for
            # idempotency, but don't wait for a refetch that won't come.
            action()
        elif cascade_target_params:
            # DM.5 — cascade-source pick: the visual re-fetch is indirect
            # and may serialize past 30s (or never fire in-window), so
            # don't gate on a single ``expect_response``. Run the action,
            # then settle on the stable post-cascade DOM (target cleared +
            # no in-flight visual skeletons) with a bounded deadline +
            # graceful no-hang fallback.
            self._wait_for_cascade_settle(action, cascade_target_params)
        else:
            self._wait_for_refetch(action)

    def set_date_range(self, from_: str | None, to: str | None) -> None:
        """Phase BM — the pre-BM universal date-RANGE Flatpickr widget
        (one visible text input + two hidden ``date_from`` / ``date_to``
        inputs) dissolved. A Date From + Date To pair now renders as
        two ``ParameterDateSpec`` single-date pickers, each carrying a
        ``?param_<name>=YYYY-MM-DD`` URL key (same wire shape as Daily
        Statement's Business Day picker). Delegate to ``set_date`` for
        each leg — only one ``_wait_for_refetch`` cost regardless of
        how many bounds get written, since ``set_date`` already calls
        through the refetch waiter per write.
        """
        if from_ is not None:
            self.set_date("Date From", from_)
        if to is not None:
            self.set_date("Date To", to)

    def set_date(self, label: str, iso: str | None) -> None:
        """BG.7 (2026-05-25, per user "why wouldn't it run against
        both?" + feedback_build_verbs_not_skip): real impl that
        drives the App2 single-date picker.

        Wire shape (per `render.py:_render_parameter_date`): the
        ``ParameterDateSpec`` renders a ``<label>...<input data-widget=
        "flatpickr-single" data-target-input="param_<name>" ...></label>``
        + a sibling ``<input type="hidden" name="param_<name>" ...>``.
        Bootstrap.js's ``wireFlatpickrSingle`` writes the hidden input
        + dispatches a bubbling change → ``wireFilterAutoRefresh``'s
        300ms debounce → form refresh.

        Driver path: locate the visible flatpickr input by its label
        text, read its ``data-target-input`` attribute to find the
        hidden input's name, then write both inputs + dispatch change
        (mirrors the flatpickr → bootstrap wire without depending on
        the widget itself being responsive in a headless context).
        """
        if iso is None:
            return
        self._wait_for_refetch(lambda: self._page.evaluate(
            """({ label, iso }) => {
                const form = document.querySelector('#filter-form');
                if (!form) throw new Error('set_date: #filter-form missing');
                const labels = Array.from(form.querySelectorAll('label'));
                const match = labels.find(l => l.textContent.trim().startsWith(label));
                if (!match) {
                    throw new Error(
                        'set_date: no label starting with ' + JSON.stringify(label)
                        + ' (have: ' + labels.map(l => l.textContent.trim()).join(' | ') + ')'
                    );
                }
                const visible = match.querySelector('input[data-widget="flatpickr-single"]');
                if (!visible) {
                    throw new Error(
                        'set_date: label ' + JSON.stringify(label)
                        + ' carries no [data-widget="flatpickr-single"] input — '
                        + 'the picker spec changed shape?'
                    );
                }
                const target = visible.getAttribute('data-target-input');
                const hidden = form.querySelector('input[type="hidden"][name="' + target + '"]');
                if (!hidden) {
                    throw new Error(
                        'set_date: hidden input for ' + JSON.stringify(target)
                        + ' missing — bootstrap.js wire shape changed?'
                    );
                }
                visible.value = iso;
                hidden.value = iso;
                hidden.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            {"label": label, "iso": iso},
        ))

    def set_slider(
        self, label: str, lo: float | None, hi: float | None,
    ) -> None:
        # Two shapes share this verb (X.2.u.4.e): a column ``NumericRangeSpec``
        # — two ``min_<col>`` / ``max_<col>`` inputs (a noUiSlider may sit
        # over them) — and a ``ParameterNumberSpec`` (from a tree
        # ``ParameterSlider``) — a single ``<input name="param_<name>">``.
        # For the single-value case the protocol passes the value as ``lo``
        # (``hi=None``), so prefer ``lo`` and fall back to ``hi``. We write
        # the underlying ``<input>`` directly (the wire element HTMX
        # serializes) + dispatch a bubbling ``change``; the noUiSlider
        # handle, if present, just stays where it was — irrelevant to the
        # data the test reads.
        ctrl = self._filter_control(label)
        self._wait_for_refetch(lambda: ctrl.evaluate(
            """(el, { lo, hi }) => {
                const mn = el.querySelector('input[name^="min_"]');
                const mx = el.querySelector('input[name^="max_"]');
                if (mn || mx) {
                    if (mn) mn.value = lo === null ? '' : String(lo);
                    if (mx) mx.value = hi === null ? '' : String(hi);
                    (mn || mx).dispatchEvent(
                        new Event('change', { bubbles: true }),
                    );
                    return;
                }
                const pv = el.querySelector('input[name^="param_"]');
                if (pv) {
                    const v = lo !== null ? lo : hi;
                    pv.value = v === null ? '' : String(v);
                    pv.dispatchEvent(new Event('change', { bubbles: true }));
                    return;
                }
                throw new Error(
                    'set_slider: no min_/max_/param_ input under control ' +
                    'labelled ' + JSON.stringify(el.textContent || ''),
                );
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

    def _sync_nav_from_url(self) -> None:
        """Re-derive ``_dashboard`` / ``_sheet`` from the landed URL after
        a navigation (cross_link / row drill) so a subsequent
        ``goto_sheet`` works."""
        m = re.search(
            r"/dashboards/([^/?#]+)(?:/sheets/([^/?#]+))?", self._page.url,
        )
        if m:
            self._dashboard = m.group(1)
            self._sheet = m.group(2)

    def cross_link(self, label: str) -> None:
        self._click(self._page.locator("a", has_text=label).first)
        self._page.wait_for_load_state("networkidle")
        self._sync_nav_from_url()

    def drill_from_first_row(self, visual_title: str) -> None:
        # u.4.e.3 — App2's Table renderer makes every row with a row-level
        # drill clickable (``<tr data-row-drill>``); the click navigates
        # via the visual's *primary* drill (a ``DATA_POINT_CLICK`` one if
        # declared, else the first). It's a full-page ``location.href``
        # navigation, not an in-place swap (App2 cross-sheet drills =
        # ``location.href``, same as ``cross_link``'s ``<a>``s).
        section = self._section(visual_title)
        first_row = section.locator(
            "table.table-data tbody tr[data-row-drill]",
        ).first
        if first_row.count() == 0:
            raise NotImplementedError(
                f"App2Driver.drill_from_first_row — table {visual_title!r} "
                f"declares no row-level drill (no <tr data-row-drill>)"
            )
        # DR.7.g — drive the row's *primary* DATA_POINT_CLICK via its keyboard
        # handler, NOT a center-of-row click. Playwright's ``.click()`` targets
        # the row's geometric center; on a CLICK+MENU table (e.g. the
        # Supersession Audit's Transactions Audit, where transfer_id is a
        # ``cell-accent-menu`` MENU cell) the center lands on that menu cell,
        # whose listener ``stopPropagation()``s + opens the ctxmenu — preempting
        # the row-level ``safeNavigate`` so the DATA_POINT_CLICK self-filter
        # (the transaction_id write) never fires. bootstrap.js wires Enter/Space
        # → the same ``safeNavigate(url)`` on the ``<tr>`` inside the
        # ``if (clickDrill)`` block that also sets ``data-row-drill`` +
        # ``tabindex=0`` — so every row this locator matches is focusable and
        # Enter-navigable, position-independent.
        first_row.press("Enter")
        self._page.wait_for_load_state("networkidle")
        self._sync_nav_from_url()

    def drill_from_first_row_via_menu(
        self, visual_title: str, menu_item: str,
    ) -> None:
        # u.4.e.3 — open the first row's "⋯" button (a ``ctxmenu`` popover;
        # the same menu is bound on the row's ``contextmenu`` for QS-gesture
        # parity) and click the ``<li>`` whose label is ``menu_item``.
        section = self._section(visual_title)
        first_row = section.locator("table.table-data tbody tr").first
        first_row.wait_for(state="visible")
        btn = first_row.locator(".row-drill-menu-btn").first
        if btn.count() == 0:
            raise NotImplementedError(
                f"App2Driver.drill_from_first_row_via_menu — table "
                f"{visual_title!r} has no DATA_POINT_MENU row drill "
                f'(no "⋯" button)'
            )
        self._click(btn)
        item = self._page.locator(
            "ul.ctxmenu li", has_text=menu_item,
        ).first
        item.wait_for(state="visible", timeout=5_000)
        self._click(item)
        self._page.wait_for_load_state("networkidle")
        self._sync_nav_from_url()

    # -- metadata popup (CY.9 — App2-only per operator lock 7) ----------

    def open_metadata_panel(
        self, visual_title: str, row_index: int = 0,
    ) -> None:
        """Drive the row's ``⋯`` button → ``{} View metadata`` ctxmenu
        item → side-panel slide-in (drawer loses ``translate-x-full``).

        Wire shape (see ``bootstrap.js::openRowMenu`` line 584+): each
        ``<tr>`` inside a ``data-metadata-popup="1"`` Table gets a
        ``.row-drill-menu-btn`` button (the ``⋯`` glyph). Clicking it
        opens ``ctxmenu`` with the synthetic ``{} View metadata`` entry
        prepended; clicking that entry calls ``htmx.ajax`` against the
        ``/rows/metadata`` route (swaps into ``#side-panel-body``), then
        ``window.__sidePanelOpen()`` flips the drawer visible.
        """
        section = self._section(visual_title)
        rows = section.locator("table.table-data tbody tr")
        rows.nth(row_index).wait_for(state="visible")
        btn = rows.nth(row_index).locator(".row-drill-menu-btn").first
        if btn.count() == 0:
            raise NotImplementedError(
                f"App2Driver.open_metadata_panel — table "
                f"{visual_title!r} row {row_index} has no ⋯ button "
                f"(metadata_popup not wired?)"
            )
        self._click(btn)
        # The synthetic entry is always prepended at index 0; matching
        # by visible text lets the test signal which item it expects
        # without coupling to position.
        item = self._page.locator(
            "ul.ctxmenu li", has_text="{} View metadata",
        ).first
        item.wait_for(state="visible", timeout=5_000)
        self._click(item)
        # ``__sidePanelOpen`` removes ``translate-x-full`` synchronously
        # after the ``htmx.ajax`` promise resolves. Poll the class
        # attribute rather than waiting on a specific selector — the
        # body fragment varies (empty-state, tree) but the drawer's
        # transform flip is the single readiness signal.
        self._page.wait_for_function(
            "() => {"
            " const p = document.getElementById('side-panel');"
            " return p && !p.classList.contains('translate-x-full');"
            "}",
            timeout=5_000,
        )

    def close_metadata_panel(self) -> None:
        """Press Escape; block until the drawer re-acquires
        ``translate-x-full`` (the closed-state class)."""
        self._page.keyboard.press("Escape")
        self._page.wait_for_function(
            "() => {"
            " const p = document.getElementById('side-panel');"
            " return p && p.classList.contains('translate-x-full');"
            "}",
            timeout=5_000,
        )

    def metadata_panel_expand_all(self) -> None:
        """Click ``[data-metadata-expand-all]``. Empty-state fragments
        carry no toolbar (operator lock — see ``_EMPTY_METADATA_FRAGMENT``
        in ``_side_panel.py``); callers should only invoke this when
        the panel rendered a real payload.

        bootstrap.js's ``expandAll`` defers the open mutation into a
        ``requestAnimationFrame`` to keep the main thread responsive
        for large trees — so the DOM state isn't visible synchronously
        after the click returns. We wait one ``rAF`` tick before
        returning so the caller's subsequent ``metadata_panel_open_details_count``
        sees the post-batch state.
        """
        self._click(self._page.locator("[data-metadata-expand-all]").first)
        self._page.evaluate(
            "() => new Promise((r) => requestAnimationFrame(() => r()))"
        )

    def metadata_panel_collapse_all(self) -> None:
        """Click ``[data-metadata-collapse-all]``. Same empty-state +
        ``requestAnimationFrame`` caveats as ``metadata_panel_expand_all``."""
        self._click(self._page.locator("[data-metadata-collapse-all]").first)
        self._page.evaluate(
            "() => new Promise((r) => requestAnimationFrame(() => r()))"
        )

    def metadata_panel_text(self) -> str:
        """Return the ``[data-metadata-raw]`` ``<textarea>``'s value —
        the pretty-printed JSON the Copy button reads. Returns the empty
        string when the panel is in its empty-state branch (no textarea
        rendered)."""
        loc = self._page.locator("[data-metadata-raw]").first
        if loc.count() == 0:
            return ""
        # ``<textarea>`` content lives on ``.value``; ``inner_text``
        # would return the *DOM* text (empty for a hidden textarea).
        result = loc.evaluate("(el) => el.value")
        return str(result) if result is not None else ""

    def metadata_panel_open_details_count(self) -> int:
        """Count ``details[open][data-json-node]`` nodes — the
        default-state cardinality assertion. Returns 0 for empty-state
        panels (no tree rendered)."""
        return int(self._page.locator(
            "details[open][data-json-node]",
        ).count())

    # -- OIDC auth (DD.4 — drives Dex local-connector v2.40.0 form) ------

    def sign_in_via_oidc(self, *, email: str, password: str) -> None:
        """Drive the full OIDC code-flow login against the live Dex
        IdP — see protocol docstring for the redirect chain. Idempotent.

        Implementation notes:
        - Dex's local-connector ``password.html`` field is ``name='login'``
          (NOT ``email`` / ``username``); the visible label text comes
          from ``UsernamePrompt`` which defaults to ``Email Address``.
        - The submit button is ``#submit-login`` with visible text
          ``Login``.
        - ``approval.html`` ("Grant Access" heading) renders unconditionally
          today — ``config_writer.py`` does not set
          ``oauth2.skipApprovalScreen``. If a future commit flips that,
          the Grant Access click becomes a no-op (the wait_for is a
          ``not_visible`` check below so the verb stays robust).
        """
        # Peek-before-act idempotency — short-circuit if already signed in.
        if self._has_session_cookie():
            return

        # Kick off the redirect chain.
        self._page.goto(f"{self._base}/auth/login")

        # Wait for Dex's password page. The heading text "Log in to Your
        # Account" is a template literal in v2.40.0/web/templates/
        # password.html — not Go-templated — so it's a stable anchor.
        self._page.wait_for_selector(
            "h2:has-text('Log in to Your Account')",
            timeout=15_000,
        )

        # Fill + submit. Playwright's `.fill()` fires input + change events
        # natively; Dex's form is a plain POST (no JS overlay widget), so
        # no special evaluate() wrapper needed here (unlike pick_filter's
        # TomSelect path).
        self._fill("input[name='login']", email, what="oauth login email")
        self._fill("input[name='password']", password, what="oauth login password")

        # Wait for the navigation to either approval.html or back to the
        # callback. expect_navigation catches whichever Dex emits.
        with self._page.expect_navigation(timeout=15_000):
            self._click(self._page.locator("button#submit-login"))

        # If approval.html rendered, click Grant Access. We probe by
        # selector visibility (short timeout) — if Dex skipped the
        # approval screen we move on without waiting the full timeout.
        grant_access = self._page.locator(
            "button:has-text('Grant Access')",
        )
        if grant_access.count() > 0 and grant_access.first.is_visible():
            with self._page.expect_navigation(timeout=15_000):
                self._click(grant_access.first)

        # Settle on the post-callback landing page.
        self._page.wait_for_load_state("networkidle", timeout=15_000)

        # Verify the cookie actually landed — if it didn't, surface
        # the failure here (not as a downstream NoneType blowup).
        if not self._has_session_cookie():
            raise RuntimeError(
                "sign_in_via_oidc: callback completed but "
                "recon_gen_session cookie was not set on the page "
                "context — check JwtCookieMiddleware wiring and Dex "
                "discovery URL."
            )

    def sign_out_via_oidc(self) -> None:
        """Drive ``GET /auth/logout``. Idempotent (returns immediately
        when no session cookie is present)."""
        if not self._has_session_cookie():
            return

        # /auth/logout deletes the cookie + 302s to Dex's
        # end_session_endpoint. Dex's end_session page is plain HTML —
        # no further redirect by default — so we just wait for
        # networkidle. The cookie is gone regardless of where the
        # browser lands.
        self._page.goto(f"{self._base}/auth/logout")
        self._page.wait_for_load_state("networkidle", timeout=15_000)

    def inspect_jwt_cookie(self) -> dict[str, str] | None:
        """Return a flat ``{name, value, domain, path}`` dict for the
        ``recon_gen_session`` cookie, or ``None`` when absent. The
        Playwright Cookie typed-dict carries more fields (``expires``,
        ``httpOnly``, ``secure``, ``sameSite``) — we project to the
        four the no-Playwright-leak lint allows tests to consume."""
        for cookie in self._page.context.cookies():
            if cookie.get("name") == _SESSION_COOKIE_NAME:
                return {
                    "name": str(cookie.get("name", "")),
                    "value": str(cookie.get("value", "")),
                    "domain": str(cookie.get("domain", "")),
                    "path": str(cookie.get("path", "")),
                }
        return None

    def _has_session_cookie(self) -> bool:
        """Peek-before-act helper shared by ``sign_in`` / ``sign_out``
        — kept private to avoid leaking the boolean shape via the
        cross-renderer protocol surface (only ``inspect_jwt_cookie`` is
        in the contract)."""
        return any(
            cookie.get("name") == _SESSION_COOKIE_NAME
            for cookie in self._page.context.cookies()
        )

    # -- Snapshot (BV.3.3 trainer dogfood per-plant restore) -------------

    def snapshot_take(self, name: str) -> None:
        """POST ``/training/snapshot/take?name=<name>``. Captures the
        current v-overlay state under the given name.

        Drives the Snapshotter (``recon_gen.common.snapshotter``) through
        the Studio test server's HTTP surface so the "everything through
        the driver" invariant (X.2.q) holds even for test-harness state
        mutations. Uses httpx (not raw Playwright) — per the
        no-playwright-leak convention, the browser layer is for
        user-facing flows; snapshot/restore is server-side state and
        belongs over httpx. The driver layer itself is exempt from the
        lint, so an httpx import here is fine.

        BV.3.3 design: ``snapshot_take`` is called ONCE per worker after
        the session-scope ``trainer_start_session`` fixture lands;
        ``snapshot_restore`` then runs between plant tests (~50ms on
        DuckDB, ~150ms on PG, ~2500ms on Oracle) — vs the cumulative-
        walk pattern's full Session Start per plant (~2min Oracle ×15).

        Raises ``httpx.HTTPStatusError`` on non-204 — surfaces the
        snapshotter's actionable message in the response body rather
        than letting the test continue against an indeterminate state.
        """
        self._snapshot_post("take", name)

    def snapshot_restore(self, name: str) -> None:
        """POST ``/training/snapshot/restore?name=<name>``. Restores the
        v-overlay to the state captured by a prior ``snapshot_take(name)``.

        See ``snapshot_take`` for the full design rationale + httpx-over-
        Playwright justification.
        """
        self._snapshot_post("restore", name)

    def snapshot_drop(self, name: str) -> None:
        """POST ``/training/snapshot/drop?name=<name>``. Drops the named
        snapshot — releases the dialect-specific resources (golden-mirror
        schemas / temp files / metadata cursors) held by the Snapshotter
        for that name.

        See ``snapshot_take`` for the full design rationale + httpx-over-
        Playwright justification.
        """
        self._snapshot_post("drop", name)

    def _snapshot_post(self, verb: str, name: str) -> None:
        """Shared httpx POST for the three snapshot verbs.

        One-shot ``httpx.Client`` per call: the snapshot verbs fire at
        most a few times per test (take-once + N restores), so the
        ~1ms per-call client construction cost is dwarfed by the
        snapshotter's actual work (50ms-2.5s by dialect). Avoids
        threading a persistent client through the driver's __init__
        and the factory context-managers — cleaner ownership story.

        ``raise_for_status`` converts non-2xx to ``httpx.HTTPStatusError``
        so the test surfaces the snapshotter's actionable message
        (carried in the response body) rather than continuing against
        an indeterminate v-overlay state.

        Timeout: httpx's default is 5s — too tight for PG (matview
        refresh under restore can push past 5s when CONCURRENTLY
        serializes on the v-overlay's narrow row count) and well below
        Oracle's ~2.5s ceiling with golden-mirror restores that hit
        DBMS_MVIEW.REFRESH. 300s ceiling matches the test-harness's
        Session Start budget and aborts a genuinely-stuck snapshotter
        before a CI run wedges indefinitely.
        """
        url = f"{self._base}/training/snapshot/{verb}"
        with httpx.Client(timeout=300.0) as client:
            response = client.post(url, params={"name": name})
        if response.status_code >= 400:
            # Surface the Snapshotter's actionable message (carried in
            # the response body) — ``raise_for_status`` alone discards
            # it, leaving the test with an opaque "500 Internal Server
            # Error" line. The dialect impls deliberately format the
            # underlying SQL exception into the body for exactly this.
            body = response.text.strip()
            raise httpx.HTTPStatusError(
                (
                    f"snapshot {verb} returned "
                    f"{response.status_code}: {body}"
                ),
                request=response.request,
                response=response,
            )

    # -- Trainer (BV.4 dual-prefix flow) ---------------------------------

    def open_training(self) -> None:
        """Navigate to ``/training/`` — the Trainer landing. Waits for
        the Session Start button to mount; pre-Session-Start the page
        renders only that single button + the 25 plant cards in their
        collapsed-by-default `<details>` accordions."""
        self._page.goto(f"{self._base}/training/")
        self._page.wait_for_selector("#training-session-start-btn")

    def trainer_start_session(self, timeout_ms: int = 600_000) -> None:
        """Click Session Start; wait for the detached task to finish.

        BV.4.10.d — Session Start is now an async detached task. POST
        303s back to /training/ with `session_start_running=True`,
        rendering an in-progress banner with an htmx live-tail. When
        the task finishes the live-tail's HX-Trigger fires a JS
        reload to `/training/?status=Session+started...` (the green
        success banner).

        Wait shape:
        1. Click Session Start.
        2. Wait for the in-progress banner to render (proves the
           POST → 303 → GET landed).
        3. Wait for the live-tail's data-test-training-tail-state to
           flip to `"finished"` — that's the moment the task done +
           HX-Trigger about to fire.
        4. Wait for the success banner to render (JS-reloaded page).

        ``timeout_ms`` default is 10 minutes — Oracle Session Start
        can take ~10 min for the /etl/run leg.
        """
        self._click("#training-session-start-btn", what="trainer_start_session")
        # Step 2: in-progress banner appears.
        self._page.wait_for_selector(
            "[data-test-training-session-start-banner], "
            "[data-test-training-banner]",
            timeout=15_000,
        )
        if self._page.locator("[data-test-training-banner]").count() > 0:
            # Fast PG/sqlite case: page may have already reloaded
            # before our selector caught the in-progress banner.
            return
        # Step 3: poll the live-tail state until finished.
        self._trainer_wait_until_finished(
            mount_id="training-session-start-live-tail",
            timeout_ms=timeout_ms,
        )
        # Step 4: success banner via JS-reload.
        self._page.wait_for_selector(
            "[data-test-training-banner]",
            timeout=15_000,
        )

    def trainer_enable_plant(
        self, kind: str, family: str,
        form_values: Mapping[str, str] | None = None,
    ) -> None:
        """Expand the kind's family accordion + tick the enable
        checkbox + (optionally) override form fields. The form field
        names are ``form_<kind>_<primitive>``.

        ``family`` is the family pretty-label (``"L1 Cap"``); the
        accordion's ``data-test-training-family="<family>"`` summary
        is what we click. The page state (which accordions are open)
        resets on every render — callers must re-expand after each
        full-page navigation (Session Start / Apply / Tour).

        Clicking a `<details summary>` that's already open TOGGLES
        it closed; the L1 Conservation family opens by default, so
        always check the `open` attribute first and only click when
        we need to open it."""
        self._trainer_ensure_family_open(family)
        self._check(
            f"[data-test-training-enable-{kind}]",
            what=f"trainer_enable_plant(kind={kind!r})",
        )
        for field_name, value in (form_values or {}).items():
            self._fill(
                self._page.locator(
                    f'[name="form_{kind}_{field_name}"]'
                ).first,
                value,
                what=f"trainer_enable_plant form field {field_name!r} (kind={kind!r})",
            )

    def _trainer_ensure_family_open(self, family: str) -> None:
        """Open the family accordion if it isn't already. Clicking
        the summary of an already-open `<details>` collapses it."""
        details = self._page.locator(
            f'[data-test-training-family="{family}"]'
        ).first
        is_open = bool(details.evaluate("el => el.hasAttribute('open')"))
        if not is_open:
            self._click(details.locator("> summary").first)

    def _scream_if_disabled(self, loc: Any, label: str) -> None:
        """Raise LOUDLY + IMMEDIATELY when ``loc`` matches a present-but-
        DISABLED element, instead of eating the full Playwright click/check
        timeout ("element is not enabled", retried to the 30s deadline).

        A disabled control is a server-state signal, not a timing flake —
        e.g. the Trainer's Apply button disables when ``v_overlay_exists``
        is False / a Session-Start or Apply op is still running. The
        timeout is the SYMPTOM; this surfaces the actual shape (the
        element's outerHTML carries the disabled attr + classes) the
        instant it's detected. A not-yet-present element (count 0) is left
        to the caller's normal auto-wait so legitimately-late elements
        aren't cut short."""
        if loc.count() > 0 and loc.first.is_disabled():
            outer = loc.first.evaluate("el => el.outerHTML")
            raise AssertionError(
                f"{label}: element is present but DISABLED — cannot "
                f"click/check (server-state signal, not a flake; a bare "
                f"Playwright 30s timeout would hide it). "
                f"outerHTML: {str(outer)[:400]}"
            )

    def _click(self, target: Any, *, what: str | None = None, **kwargs: Any) -> None:
        """Driver click primitive: SCREAM on a disabled target (see
        :meth:`_scream_if_disabled`) before delegating to Playwright's
        click. Driver clicks route through here so a disabled control
        fails fast + loud everywhere, not only where a caller remembered
        a pre-check. ``target`` is a selector string or a Locator."""
        loc = self._page.locator(target) if isinstance(target, str) else target
        self._scream_if_disabled(
            loc, what or (target if isinstance(target, str) else "element"),
        )
        loc.click(**kwargs)

    def _check(self, target: Any, *, what: str | None = None) -> None:
        """Checkbox ``.check()`` primitive: SCREAM on a disabled target
        before delegating. ``.check()`` waits-for-enabled exactly like
        ``.click()``, so a disabled checkbox would otherwise burn the full
        30s timeout. ``target`` is a selector string or a Locator."""
        loc = self._page.locator(target) if isinstance(target, str) else target
        self._scream_if_disabled(
            loc, what or (target if isinstance(target, str) else "element"),
        )
        loc.check()

    def _fill(self, target: Any, value: str, *, what: str | None = None) -> None:
        """Input ``.fill()`` primitive: SCREAM on a disabled target before
        delegating. ``.fill()`` waits-for-editable (a disabled input is not
        editable), so a disabled field would otherwise burn the full 30s
        timeout. ``target`` is a selector string or a Locator."""
        loc = self._page.locator(target) if isinstance(target, str) else target
        self._scream_if_disabled(
            loc, what or (target if isinstance(target, str) else "element"),
        )
        loc.fill(value)

    def _assert_clickable(self, selector: str, *, what: str) -> None:
        """Standalone 'scream if disabled' guard, kept per operator request
        for any interaction that doesn't route through :meth:`_click` /
        :meth:`_check` / :meth:`_fill` (all three bake the same guard in)."""
        self._scream_if_disabled(self._page.locator(selector), what)

    def trainer_apply(self, timeout_ms: int = 300_000) -> None:
        """Click Apply; wait for the detached task to finish.

        BV.4.10.d.2 — Apply mirrors Session Start: POST 303s back
        to /training/ with an apply-in-progress banner + live-tail.
        On completion the HX-Trigger fires JS reload to /training/.

        Wait shape mirrors `trainer_start_session` (in-progress
        banner → live-tail finished state → JS-reloaded last-apply
        banner). Default timeout 5 min — DuckDB Apply finishes in
        seconds, PG slow-path reclone takes ~30s, but Oracle's matview
        refresh runs a few min and got heavier once DK added the
        `data_anchor` singleton matview to the refresh set. The prior
        2 min ceiling flaked `test_trainer_dogfood_per_kind[or-*]` —
        the apply's `_trainer_wait_until_finished` hit 120s on the
        Oracle refresh (it passed in CI but timed out locally, where
        Oracle is slower). `trainer_start_session` already runs a 10 min
        ceiling for the same matview-refresh reason; this is its
        smaller sibling.

        CF.1 — terminal-state detection waits on the unified
        `data-test-last-apply-banner` attr (carried by all three
        post-Apply banner colors: green all-succeeded, amber
        partial, red all-failed) so partial-failure scenarios
        don't time out the way they would against the legacy
        green-only `data-test-training-banner` selector.
        """
        self._click("#training-apply-btn", what="trainer_apply")
        # In-progress banner appears (or page already reloaded for
        # very-fast no-op applies — the last-apply banner is the
        # CF.1 post-reload terminal state).
        self._page.wait_for_selector(
            "[data-test-training-apply-banner], "
            "[data-test-last-apply-banner]",
            timeout=15_000,
        )
        if self._page.locator("[data-test-last-apply-banner]").count() > 0:
            return
        self._trainer_wait_until_finished(
            mount_id="training-apply-live-tail",
            timeout_ms=timeout_ms,
        )
        self._page.wait_for_selector(
            "[data-test-last-apply-banner]",
            timeout=15_000,
        )

    def _trainer_wait_until_finished(
        self, *, mount_id: str, timeout_ms: int,
    ) -> None:
        """Poll the live-tail mount's ``data-test-training-tail-state``
        until it flips to ``"finished"``. The mount is HTMX-polled
        every 1s by the page itself; we don't drive it — we just
        observe. When the state lands at "finished" the page's
        inline HX-Trigger script will reload `/training/` so the
        caller waits for the success-banner selector after this."""
        self._page.wait_for_function(
            (
                f"() => {{"
                f" const el = document.getElementById('{mount_id}');"
                f" return el && el.dataset.testTrainingTailState === 'finished';"
                f"}}"
            ),
            timeout=timeout_ms,
        )

    def trainer_take_violation_tour(
        self, kind: str, family: str,
    ) -> None:
        """Re-expand the kind's family + click the Violation Tour
        link (the ``?prefix=<base>_v`` one). Waits for the dashboard
        sheet to settle (HTMX visual auto-loads count toward
        networkidle)."""
        self._trainer_ensure_family_open(family)
        tour_link = self._page.locator(
            f'[data-test-tour-violation-{kind}]'
        ).first
        if tour_link.count() == 0:
            raise AssertionError(
                f"Violation Tour link missing on the {kind!r} card "
                f"(data-test-tour-violation-{kind})"
            )
        self._click(tour_link)
        self._page.wait_for_load_state("networkidle")

    def dashboard_table_inner_html(self) -> str:
        """Read the rendered ``.table-data`` block(s) inner HTML —
        the cheap read used by BV.3.3's planted-row signature
        assertions. Caller diffs the v overlay's matview to identify
        the planted row's account_id then ``assert acc in inner_html``.

        Concatenates HTML of EVERY ``.table-data`` on the page —
        some sheets carry multiple tables (drift has main + summary)
        and the signature could legitimately surface in any of them.
        Waits for at least one row to render (the renderer auto-loads
        via ``hx-trigger="load"`` on page open); table-only sheets
        with 0 rendered rows yield an empty string.

        BV.3.3.c.bug2 — re-navigate to the current URL with
        ``page_size=10000`` so the planted row surfaces regardless of
        its position in the underlying matview (default 50-row page +
        unsorted matview rows + plant inserted last → planted row
        falls beyond page 1). The test asserts presence anywhere in
        the table-data text; the operator-facing page-size default is
        independent (50 rows fits the dashboard layout)."""
        self._page.wait_for_selector(
            ".table-data tbody tr", timeout=15_000,
        )
        cur_url = self._page.url
        sep = "&" if "?" in cur_url else "?"
        if "page_size=" not in cur_url:
            self._page.goto(f"{cur_url}{sep}page_size=10000")
            self._page.wait_for_load_state("networkidle")
            self._page.wait_for_selector(
                ".table-data tbody tr", timeout=15_000,
            )
        chunks = self._page.locator(".table-data").all_inner_texts()
        return "\n".join(str(c) for c in chunks)

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
