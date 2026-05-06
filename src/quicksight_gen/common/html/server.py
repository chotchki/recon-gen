"""Starlette ASGI server for the App2 (HTMX) dashboard renderer.

X.2.b shape — all-GET REST surface (no POSTs except dev-log):

- ``GET  /`` — 302 redirect to ``/dashboards``. The dashboards
  list IS the canonical entry; ``/`` is convenience.
- ``GET  /dashboards`` — landing page listing every dashboard the
  server is wired to serve. One link per dashboard, bookmarkable
  per entry.
- ``GET  /dashboards/{dashboard_id}`` — dashboard chrome + the
  served Sheet inline. 404 if the dashboard_id isn't in the
  wired ``dashboards`` mapping.
- ``GET  /dashboards/{dashboard_id}/sheets/{sheet_id}/visuals/{visual_id}/data``
  — chart data fragment for HTMX swap. Filter values arrive as
  query string. GET-not-POST means every (visual, filter-set)
  tuple is a bookmarkable URL.
- ``POST /log`` (dev-only, gated by ``dev_log=True``) — the only
  POST route. Receives forwarded HTMX + d3 click events from the
  browser for live debugging.

X.2.b.3: ``make_app`` takes a ``dashboards`` mapping so one server
can host multiple apps. Each value is a ``ServedDashboard`` carrying
its own tree, sheet, title, and data fetcher (different apps query
different matviews via different fetchers). X.2.g wires the four
QS apps (Executives / Investigation / L2 Flow Tracing / L1
Dashboard) into this mapping from one L2 instance.

Error handling (X.2.m)
----------------------

Two exception handlers wrap the app so production deploys never
return a Starlette default error page:

- ``HTTPException(404)`` (raised by route handlers when a
  dashboard_id / sheet_id slug doesn't resolve) renders a themed
  "Not found" page via ``emit_error_page``.
- Generic ``Exception`` (anything uncaught from a route handler —
  fetcher SQL crash, render-time bug, DB unreachable) returns 500
  with a themed "Something went wrong" page. ``dev_log=True``
  carries the traceback inside a collapsible ``<details>``;
  production hides it.

The HTMX ``htmx:responseError`` event in ``bootstrap.js`` surfaces
a transient toast for 4xx / 5xx responses to swap targets so a
failed visual data fetch shows context instead of a blank panel.

Pluggable data fetcher
----------------------

Each ``ServedDashboard`` owns a ``DataFetcher`` callable so the
spike + tests can run without a database:

    def stub(visual_id: VisualId, params: Mapping[str, str]) -> Any:
        return {"nodes": [...], "links": [...]}

    app = make_app(dashboards={
        "smoke": ServedDashboard(
            tree_app=app, sheet=money_trail,
            title="Smoke", data_fetcher=stub,
        ),
    })

Production deploys wire the same callable to a DB-backed factory
(see ``_db_fetcher.make_db_fetcher``).

Stateless on purpose
--------------------

No sessions, no auth, no in-process caching. Each GET executes the
fetcher fresh. Cache-Control headers (X.2.b.4) push caching to
edge / browser layers — the URL IS the cache key, by design.
"""

from __future__ import annotations

import inspect
import json
import sys
import traceback
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Union

from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from quicksight_gen.common.html.render import (
    FilterSpec,
    emit_dashboards_list,
    emit_error_page,
    emit_html,
    emit_visual_data_fragment,
)
from quicksight_gen.common.ids import VisualId
from quicksight_gen.common.l2.theme import ThemePreset
from quicksight_gen.common.tree.structure import App, Sheet


# (visual_id, filter_params) → chart data shaped for the visual's
# d3 hydrator. The renderer just JSON-serializes whatever the
# fetcher returns; the per-visual shape contract lives in the
# bootstrap.js renderXxx functions.
#
# Two shapes accepted (X.2.n.5):
#   - async: production fetcher built by make_tree_db_fetcher.
#     ``visual_data`` route awaits it directly.
#   - sync: stub fetchers in tests + the legacy _db_fetcher path.
#     ``visual_data`` wraps them in run_in_threadpool so they
#     don't block the event loop.
# ``inspect.iscoroutinefunction`` picks the dispatch at request time.
# X.2.o.3: ``VisualId`` not ``str`` so the fetcher contract ties
# back to the tree's typed visual identifier. Test stubs typed as
# ``Callable[[str, ...], ...]`` remain assignable here via Callable
# parameter contravariance (str is wider than VisualId on input).
DataFetcher = Union[
    Callable[[VisualId, Mapping[str, str]], Awaitable[Any]],
    Callable[[VisualId, Mapping[str, str]], Any],
]


@dataclass(frozen=True)
class ServedDashboard:
    """One dashboard's wiring for the App2 server.

    Each App2 server holds a mapping ``{dashboard_id: ServedDashboard}``
    so one process can serve multiple apps from one L2 instance
    (X.2.g wires Executives + Investigation + L2FT + L1 from one
    L2). Per-dashboard fetcher means apps that query different
    matviews don't have to share a routing layer.

    Attributes:
        tree_app: tree ``App`` node owning the analysis the sheet
            lives in. Internal IDs are resolved on first emit
            (idempotent).
        sheet: tree ``Sheet`` rendered at ``/dashboards/{id}``. Must
            belong to ``tree_app.analysis.sheets``.
        title: human-readable name for the ``/dashboards`` listing.
        data_fetcher: per-dashboard fetcher invoked on every GET to
            the visual data path. Returns d3-shaped chart data.
        theme: per-dashboard ``ThemePreset`` injected as CSS
            variables in the page shell. ``None`` falls back to
            ``DEFAULT_PRESET`` (silent-fallback per N.4.k, mirrors
            QS dialect's CLASSIC fallback). Multi-dashboard servers
            usually share a single theme since the listing page
            renders one palette across all entries.
    """
    tree_app: App
    sheet: Sheet
    title: str
    data_fetcher: DataFetcher
    theme: ThemePreset | None = None
    filter_specs: tuple[FilterSpec, ...] = ()


def make_app(
    *,
    dashboards: Mapping[str, ServedDashboard],
    dev_log: bool = False,
    visual_data_cache_max_age_s: int = 60,
) -> Starlette:
    """Build a Starlette ASGI app serving multiple dashboards.

    Args:
        dashboards: ``{dashboard_id: ServedDashboard}`` mapping.
            One entry per dashboard. The server validates inbound
            path slugs against this mapping; unknown ids 404.
        dev_log: when True, the page emits a ``<meta
            name="dev-log">`` tag that activates the client-side
            event forwarder + a ``POST /log`` route is registered
            that prints each forwarded event to stderr. Off by
            default — keeps production deploys silent and zero-
            overhead. The developer tool / smoke server enables it.
            Also sets ``Cache-Control: no-store`` on visual data
            responses so dev iteration sees fresh data on every
            reload (no surprise stale fragments).
        visual_data_cache_max_age_s: ``Cache-Control: public,
            max-age=N`` on visual-data responses. URL == cache
            key (X.2.b's GET-shape contract), so any (visual,
            filter-set) tuple stays cacheable for ``N`` seconds
            at the edge / browser. Conservative default of 60s
            since matviews refresh on ETL cycles (minutes-to-
            hours); production can dial up. Ignored when
            ``dev_log=True`` (cache is bypassed for dev runs).

    Returns:
        A ``starlette.Starlette`` ASGI application.
    """
    if not dashboards:
        raise ValueError(
            "make_app requires at least one dashboard in the "
            "`dashboards` mapping."
        )

    # Cache header is the same string on every visual-data
    # response — pre-compute it once instead of formatting per
    # request.
    if dev_log:
        # Dev runs bypass the cache so the developer sees fresh
        # data when reloading a swap. Cache-Control: no-store
        # tells every layer (browser, edge, intermediate proxy)
        # not to keep the response.
        visual_data_cache_header = "no-store"
    else:
        visual_data_cache_header = (
            f"public, max-age={visual_data_cache_max_age_s}"
        )

    # X.2.e — every analysis-attached sheet is reachable as a tab.
    # Snapshot the {dashboard_id: {sheet_id: Sheet}} mapping so the
    # /sheets/:s route can resolve a sheet without walking the tree
    # on every request, and so the 404 path is fast (dict lookup).
    all_sheets: dict[str, dict[str, "Sheet"]] = {}
    for dash_id, d in dashboards.items():
        analysis = d.tree_app.analysis
        if analysis is None:
            all_sheets[dash_id] = {str(d.sheet.sheet_id): d.sheet}
        else:
            all_sheets[dash_id] = {
                str(s.sheet_id): s for s in analysis.sheets
            }
    listing: list[tuple[str, str]] = [
        (dash_id, d.title) for dash_id, d in dashboards.items()
    ]
    # Use the first dashboard's theme for the listing page — if any
    # server hosts dashboards with different themes the listing
    # picks the alphabetically-first one's. That edge case isn't
    # the design target (one L2 instance → one theme); flagging it
    # as a comment so a future multi-tenant story sees the seam.
    listing_theme = next(iter(dashboards.values())).theme

    async def index(_request: Request) -> RedirectResponse:
        # ``/`` is a convenience redirect; ``/dashboards`` is the
        # canonical list page. Status 302 (temporary) since which
        # dashboard a future multi-tenant home would land on
        # could shift per-user.
        return RedirectResponse("/dashboards", status_code=302)

    async def dashboards_list(_request: Request) -> HTMLResponse:
        return HTMLResponse(
            emit_dashboards_list(listing, theme=listing_theme),
        )

    async def dashboard_view(request: Request) -> Response:
        dash_id = request.path_params["dashboard_id"]
        served = dashboards.get(dash_id)
        if served is None:
            # Raise so the themed 404 handler renders the page,
            # not Starlette's default plain-text "Not Found" body.
            raise HTTPException(status_code=404)
        # Tab strip across the top — every analysis sheet becomes a tab.
        # Single-sheet dashboards get an empty tab strip (suppressed
        # by ``_render_sheet_tabs``).
        sheets = tuple(all_sheets[dash_id].values())
        return HTMLResponse(emit_html(
            served.tree_app, served.sheet,
            dashboard_id=dash_id, dev_log=dev_log,
            theme=served.theme,
            all_sheets=sheets,
            filter_specs=served.filter_specs,
        ))

    async def sheet_view(request: Request) -> Response:
        """X.2.e — render a specific sheet by id.

        Plain-anchor sheet tabs target this route. The dashboard's
        analysis must contain a sheet with the matching id; unknown
        ids 404 (themed via the same handler the dashboard route
        uses).
        """
        dash_id = request.path_params["dashboard_id"]
        served = dashboards.get(dash_id)
        if served is None:
            raise HTTPException(status_code=404)
        sheet_id = request.path_params["sheet_id"]
        sheet_for_dash = all_sheets[dash_id].get(sheet_id)
        if sheet_for_dash is None:
            raise HTTPException(status_code=404)
        sheets = tuple(all_sheets[dash_id].values())
        return HTMLResponse(emit_html(
            served.tree_app, sheet_for_dash,
            dashboard_id=dash_id, dev_log=dev_log,
            theme=served.theme,
            all_sheets=sheets,
            filter_specs=served.filter_specs,
        ))

    async def visual_data(request: Request) -> Response:
        # 404 on stale URLs — both ids must resolve. The visual_id
        # gets validated implicitly (the fetcher raises for
        # unknown ids; that's the per-fetcher contract).
        dash_id = str(request.path_params["dashboard_id"])
        served = dashboards.get(dash_id)
        if served is None:
            raise HTTPException(status_code=404)
        # X.2.e — any analysis sheet's visual is fetchable, not just
        # the served (default landing) sheet. The fetcher resolves
        # the visual_id; the sheet_id check protects against typos
        # in the URL pattern.
        if str(request.path_params["sheet_id"]) not in all_sheets[dash_id]:
            raise HTTPException(status_code=404)
        # X.2.o.3 — wrap path-extracted str into VisualId at the
        # route boundary so the fetcher sees the typed identifier
        # the DataFetcher contract requires. Path params come back
        # as ``Any`` from Starlette; ``str()`` narrows then
        # ``VisualId(...)`` brands.
        visual_id = VisualId(str(request.path_params["visual_id"]))
        # ``Mapping[str, str]`` interface — built mutable for the
        # short construction window, then handed to the fetcher
        # contract that promises read-only access.
        params: dict[str, str] = {}
        for key, value in request.query_params.items():
            params[str(key)] = str(value)
        # X.2.n.5 — dispatch async fetchers directly so the asyncio
        # loop stays free across the SQL roundtrip; only sync stub
        # fetchers (tests + legacy _db_fetcher) get the threadpool
        # offload. The threadpool fallback keeps the contract
        # backward-compatible without forcing every test to become
        # async.
        if inspect.iscoroutinefunction(served.data_fetcher):
            data = await served.data_fetcher(visual_id, params)
        else:
            data = await run_in_threadpool(
                served.data_fetcher, visual_id, params,
            )
        return HTMLResponse(
            emit_visual_data_fragment(visual_id, data),
            headers={"Cache-Control": visual_data_cache_header},
        )

    async def log_event(request: Request) -> Response:
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            payload = {"event": "dev-log:bad-json"}
        # Print to stderr so it interleaves cleanly with uvicorn's
        # access log on stdout. The ``DEV-LOG`` prefix makes
        # forwarded events grep-friendly.
        print(f"DEV-LOG {json.dumps(payload)}", file=sys.stderr, flush=True)
        return Response(status_code=204)

    # X.2.m — themed error pages for 4xx / 5xx. The handlers reuse
    # ``listing_theme`` so the error page inherits the per-dashboard
    # theme (the same picking convention as the ``/dashboards``
    # listing — the first dashboard's theme wins). A future
    # multi-tenant story that mixes themes per request would route
    # the per-request theme through here; flagged as a comment.
    async def not_found_handler(
        _request: Request, exc: Exception,
    ) -> Response:
        # Subtitle differs slightly when the URL pattern matched a
        # dashboard route vs. a generic path — but we can't always
        # tell from the exception alone (Starlette routes that
        # don't match at all also raise 404). Single message keeps
        # the contract simple: "the URL didn't resolve, here's
        # the way back."
        del exc
        return HTMLResponse(
            emit_error_page(
                status_code=404,
                headline="Not found",
                subtitle=(
                    "We couldn't find that dashboard or sheet. "
                    "Bookmarks may be stale; the link below goes "
                    "back to the dashboards list."
                ),
                theme=listing_theme,
            ),
            status_code=404,
        )

    async def server_error_handler(
        _request: Request, exc: Exception,
    ) -> Response:
        # Dev-mode carries the traceback inside <details>; production
        # hides it. ``traceback.format_exception`` gives the same
        # shape Python prints to stderr when an exception goes
        # uncaught — easiest for an operator to recognize when the
        # page lands in a screenshot.
        if dev_log:
            tb_text = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
        else:
            tb_text = None
        return HTMLResponse(
            emit_error_page(
                status_code=500,
                headline="Something went wrong",
                subtitle=(
                    "We hit an error rendering this dashboard. Try "
                    "again, or contact your admin if it persists."
                ),
                traceback_text=tb_text,
                theme=listing_theme,
            ),
            status_code=500,
        )

    # Tailwind CSS lives next to this module in assets/; built by
    # ``.venv/bin/tailwindcss -i .../assets/input.css -o
    # .../assets/output.css``. Page shell links it as
    # ``/static/output.css``. Tracked in git so the spike runs
    # without forcing the user to build CSS first.
    assets_dir = Path(__file__).parent / "assets"

    routes: list[Route | Mount] = [
        Route("/", index, methods=["GET"]),
        Route("/dashboards", dashboards_list, methods=["GET"]),
        Route(
            "/dashboards/{dashboard_id}",
            dashboard_view, methods=["GET"],
        ),
        Route(
            "/dashboards/{dashboard_id}/sheets/{sheet_id}",
            sheet_view, methods=["GET"],
        ),
        Route(
            "/dashboards/{dashboard_id}/sheets/{sheet_id}"
            "/visuals/{visual_id}/data",
            visual_data,
            methods=["GET"],
        ),
        Mount(
            "/static",
            app=StaticFiles(directory=str(assets_dir)),
            name="static",
        ),
    ]
    if dev_log:
        routes.append(Route("/log", log_event, methods=["POST"]))
    # exception_handlers maps status code (HTTPException) OR exception
    # class (everything else) → handler. 404 goes via status code so
    # it catches both raises from our route handlers AND Starlette's
    # own "no route matched" 404. Generic ``Exception`` catches any
    # uncaught throw from a fetcher / render path so production never
    # returns the framework default page.
    return Starlette(
        debug=False,
        routes=routes,
        exception_handlers={
            404: not_found_handler,
            Exception: server_error_handler,
        },
    )
