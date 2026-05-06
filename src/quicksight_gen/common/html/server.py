"""Starlette ASGI server for the App2 (HTMX) dashboard renderer.

X.2.b shape — all-GET REST surface (no POSTs except dev-log):

- ``GET  /`` — full sheet HTML via ``emit_html``.
- ``GET  /dashboards/{dashboard_id}/sheets/{sheet_id}/visuals/{visual_id}/data``
  — chart data fragment for HTMX swap. The fragment carries a
  ``<script type="application/json" class="chart-data">`` payload;
  the page-shell bootstrap script hydrates d3 from it after every
  swap. Filter values arrive as query string (``?date_from=...``).
  GET-not-POST means every (visual, filter-set) tuple is a
  bookmarkable URL — paste it back, see the same chart.
- ``POST /log`` (dev-only, gated by ``dev_log=True``) — the only
  POST route. Receives forwarded HTMX + d3 click events from the
  browser for live debugging.

The path mirrors the X.2.b REST shape: dashboards / sheets /
visuals nested for future routing (X.2.b.2 lands the index +
listing routes; X.2.b.3 wires multiple dashboards). Today
``dashboard_id`` is a single fixed string passed at server
construction; the validation enforces the path matches what was
wired so a stale-URL request gets a clean 404 instead of a
silently-mismatched response.

Pluggable data fetcher
----------------------

The server takes a ``DataFetcher`` callable so the spike + tests
can run without a database:

    def stub_fetcher(visual_id: str, params: dict[str, str]) -> Any:
        return {"nodes": [...], "links": [...]}

    app = make_app(
        tree_app=app, sheet=money_trail,
        dashboard_id="smoke", data_fetcher=stub_fetcher,
    )

Production deploys wire the same callable to a DB-backed factory
(see ``_db_fetcher.make_db_fetcher``).

Stateless on purpose
--------------------

No sessions, no auth, no in-process caching. Each GET executes the
fetcher fresh. Cache-Control headers (X.2.b.4) push caching to
edge / browser layers — the URL IS the cache key, by design.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from quicksight_gen.common.html.render import (
    emit_html,
    emit_visual_data_fragment,
)
from quicksight_gen.common.tree.structure import App, Sheet


# (visual_id, filter_params) → chart data shaped for the visual's
# d3 hydrator. The renderer just JSON-serializes whatever the
# fetcher returns; the per-visual shape contract lives in the
# bootstrap.js renderXxx functions.
DataFetcher = Callable[[str, dict[str, str]], Any]


def make_app(
    *,
    tree_app: App,
    sheet: Sheet,
    dashboard_id: str,
    data_fetcher: DataFetcher,
    dev_log: bool = False,
) -> Starlette:
    """Build a Starlette ASGI app that serves a single tree Sheet.

    Args:
        tree_app: tree ``App`` node owning the analysis the sheet
            lives in. Internal IDs are resolved on the first
            ``emit_html`` call (idempotent thereafter).
        sheet: tree ``Sheet`` to serve at ``/``. Must belong to
            ``tree_app.analysis.sheets`` — emit_html raises
            otherwise.
        dashboard_id: URL slug for this dashboard. Used in the
            ``/dashboards/{dashboard_id}/...`` data path. The route
            handler validates the inbound path matches this string;
            mismatched ids 404. X.2.b.3 will replace the single
            dashboard_id with a mapping when multi-app wiring lands.
        data_fetcher: callable invoked on every GET to the visual
            data path. Receives the visual_id and a flat dict of
            query-string params (e.g. ``{"date_from":
            "2026-01-01", "date_to": "2026-05-05"}``). Returns
            d3-shaped chart data.
        dev_log: when True, the page emits a ``<meta
            name="dev-log">`` tag that activates the client-side
            event forwarder + a ``POST /log`` route is registered
            that prints each forwarded event to stderr. Off by
            default — keeps production deploys silent and zero-
            overhead. The developer tool / smoke server enables it.

    Returns:
        A ``starlette.Starlette`` ASGI application.
    """
    sheet_id = str(sheet.sheet_id)

    async def index(_request: Request) -> HTMLResponse:
        return HTMLResponse(emit_html(
            tree_app, sheet,
            dashboard_id=dashboard_id, dev_log=dev_log,
        ))

    async def visual_data(request: Request) -> Response:
        # 404 on stale URLs — the path's dashboard_id / sheet_id
        # MUST match what this server is wired for. The visual_id
        # gets validated implicitly (the fetcher raises for
        # unknown ids; that's the per-fetcher contract).
        if request.path_params["dashboard_id"] != dashboard_id:
            return Response(status_code=404)
        if request.path_params["sheet_id"] != sheet_id:
            return Response(status_code=404)
        visual_id = request.path_params["visual_id"]
        params: dict[str, str] = {}
        for key, value in request.query_params.items():
            params[str(key)] = str(value)
        data = data_fetcher(visual_id, params)
        return HTMLResponse(emit_visual_data_fragment(visual_id, data))

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

    # Tailwind CSS lives next to this module in assets/; built by
    # ``.venv/bin/tailwindcss -i .../assets/input.css -o
    # .../assets/output.css``. Page shell links it as
    # ``/static/output.css``. Tracked in git so the spike runs
    # without forcing the user to build CSS first.
    assets_dir = Path(__file__).parent / "assets"

    routes: list[Route | Mount] = [
        Route("/", index, methods=["GET"]),
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
    return Starlette(debug=False, routes=routes)
