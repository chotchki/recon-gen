"""X.2.spike.2 — Starlette ASGI server for the HTML dashboard renderer.

Wires two routes around a tree ``App`` + ``Sheet``:

- ``GET  /``                       — full sheet HTML via ``emit_html``.
- ``POST /visual/{visual_id}/data`` — chart data fragment for HTMX
  swap. The fragment carries a ``<script type="application/json"
  class="chart-data">`` payload; the page-shell bootstrap script
  hydrates d3 from it after every swap.

Pluggable data fetcher
----------------------

The server takes a ``DataFetcher`` callable so the spike can run
without a database:

    def stub_fetcher(visual_id: str, params: dict[str, str]) -> Any:
        return {"nodes": [...], "links": [...]}

    app = make_app(tree_app=app, sheet=money_trail, data_fetcher=stub_fetcher)

In a phase.1 deploy the same callable wraps a real DB query against
``<prefix>_inv_money_trail_edges`` keyed off the date params. In tests
we pass an in-memory stub so the unit suite stays DB-free.

Stateless on purpose
--------------------

No sessions, no auth, no caching. Each POST executes the fetcher
fresh. spike.2 validates the swap-on-mutation pattern; durability /
auth / cache are X.2.phase.1+ concerns.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from quicksight_gen.common.html.render import (
    emit_html,
    emit_visual_data_fragment,
)
from quicksight_gen.common.tree.structure import App, Sheet


# (visual_id, filter_params) → chart data shaped for the visual's
# d3 hydrator. spike.2 supports Sankey, which expects
# ``{"nodes": [...], "links": [...]}``. Other kinds will define
# their own payload shapes; the renderer just JSON-serializes
# whatever the fetcher returns.
DataFetcher = Callable[[str, dict[str, str]], Any]


def make_app(
    *,
    tree_app: App,
    sheet: Sheet,
    data_fetcher: DataFetcher,
) -> Starlette:
    """Build a Starlette ASGI app that serves a single tree Sheet.

    Args:
        tree_app: tree ``App`` node owning the analysis the sheet
            lives in. Internal IDs are resolved on the first
            ``emit_html`` call (idempotent thereafter).
        sheet: tree ``Sheet`` to serve at ``/``. Must belong to
            ``tree_app.analysis.sheets`` — emit_html raises
            otherwise.
        data_fetcher: callable invoked on every POST to
            ``/visual/{visual_id}/data``. Receives the visual_id
            and a flat dict of form-submitted filter params (e.g.
            ``{"date_from": "2026-01-01", "date_to": "2026-05-05"}``).
            Returns d3-shaped chart data.

    Returns:
        A ``starlette.Starlette`` ASGI application.
    """

    async def index(_request: Request) -> HTMLResponse:
        return HTMLResponse(emit_html(tree_app, sheet))

    async def visual_data(request: Request) -> HTMLResponse:
        visual_id = request.path_params["visual_id"]
        form = await request.form()
        params: dict[str, str] = {}
        for key, value in form.items():
            params[str(key)] = str(value)
        data = data_fetcher(visual_id, params)
        return HTMLResponse(emit_visual_data_fragment(visual_id, data))

    return Starlette(
        debug=False,
        routes=[
            Route("/", index, methods=["GET"]),
            Route(
                "/visual/{visual_id}/data",
                visual_data,
                methods=["POST"],
            ),
        ],
    )
