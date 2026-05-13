"""Studio route builder (X.4.a.4 + X.4.b.3 spike arm B).

``make_studio_routes(cache)`` returns the ``Route``/``Mount`` list that
``cli.studio`` splices into ``make_app(... studio_routes=...)``. Each
returned route closes over the supplied ``L2InstanceCache`` so Studio's
read/write paths share one in-memory instance per server.

Routes (current):

- ``GET /`` — landing placeholder. Will become the real Studio
  landing once X.4.c (unified diagram) + X.4.e (editor list) land.
- ``GET /diagram`` — X.4.b.3 spike arm B: the L2 topology rendered
  via post-processed graphviz SVG. Reads the typed projection
  (``topology_graph_for(cache.get())``), inlines the graphviz DOT
  source, and a small JS shim does the wasm-graphviz render +
  ``data-kind`` / ``data-id`` annotation + chrome wiring. Knobs
  exposed via URL query params (``?engine=`` / ``?focus=`` /
  ``?show-edge-labels=``) so the spike can be iterated against
  ``sasquatch_pr`` without re-deploying.
- ``Mount /studio/static`` — Studio-specific JS / CSS (the diagram
  shim + stylesheet). Sibling to the existing ``/static`` mount
  Dashboards owns; namespaced so a future renderer-replacement
  doesn't collide.
- ``Mount /studio/wasm-graphviz`` — the ``@hpcc-js/wasm-graphviz``
  module reused from ``docs/stylesheets/wasm-graphviz/``. No
  duplicated copy under ``assets/vendor/`` for the spike phase;
  the production vendoring decision lands at X.4.c.1 once the
  renderer is locked.

Severability: this module is Studio-only. ``cli.dashboards`` calls
``make_app`` with ``studio_routes=None`` and never imports this file.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.l2.topology import (
    build_topology_graph,
    topology_graph_for,
)


_STUDIO_ASSETS_DIR = Path(__file__).parent / "_studio_assets"
# wasm-graphviz vendored once under docs/stylesheets/ for the docs site
# (Phase T). For the X.4.b spike, Studio mounts the same dir at
# /studio/wasm-graphviz/ so the diagram shim can ``await import()`` it
# without a second 800KB copy. X.4.c.1 will revisit (vendor under
# assets/vendor/ for production) once the renderer is locked.
_WASM_GRAPHVIZ_DIR = (
    Path(__file__).parent.parent.parent / "docs" / "stylesheets"
    / "wasm-graphviz"
)


def _render_landing_placeholder(cache: L2InstanceCache) -> str:
    """Minimal landing page proving the mount + cache wiring resolve.

    Replaced by the real Studio landing once X.4.c (unified diagram)
    + X.4.e (editor list) land. Carries the L2 instance prefix so a
    deploy mistake (wrong YAML wired) is visible in the page body.
    """
    instance = cache.get()
    prefix = escape(str(instance.instance))
    accounts_n = len(instance.accounts)
    rails_n = len(instance.rails)
    chains_n = len(instance.chains)
    templates_n = len(instance.transfer_templates)
    return (
        "<!doctype html>\n"
        "<html lang=\"en\"><head>\n"
        f"<title>Studio — {prefix}</title>\n"
        "<meta charset=\"utf-8\">\n"
        "<style>body{font-family:system-ui;max-width:48rem;"
        "margin:2rem auto;padding:0 1rem;color:#1e293b}"
        "h1{color:#1f4e79}a{color:#1f4e79}code{background:#e0e7f0;"
        "padding:1px 4px;border-radius:3px}</style>"
        "</head><body>\n"
        f"<h1>Studio</h1>\n"
        f"<p>L2 instance: <code>{prefix}</code></p>\n"
        "<ul>\n"
        f"<li>Accounts: {accounts_n}</li>\n"
        f"<li>Rails: {rails_n}</li>\n"
        f"<li>Chains: {chains_n}</li>\n"
        f"<li>Templates: {templates_n}</li>\n"
        "</ul>\n"
        "<h2>Spike: diagram renderer</h2>\n"
        "<ul>\n"
        "<li><a href=\"/diagram\">→ Topology diagram (arm B — graphviz)</a>"
        " &nbsp; "
        "<a href=\"/diagram?engine=neato\">[neato]</a> "
        "<a href=\"/diagram?engine=sfdp\">[sfdp]</a> "
        "<a href=\"/diagram?engine=fdp\">[fdp]</a> "
        "<a href=\"/diagram?engine=circo\">[circo]</a> "
        "<a href=\"/diagram?engine=twopi\">[twopi]</a></li>\n"
        "<li><a href=\"/dashboards\">→ Dashboards</a></li>\n"
        "</ul>\n"
        "<p><em>Studio is in spike phase; the editor + Deploy pipeline "
        "land in X.4.c through X.4.g.</em></p>\n"
        "</body></html>\n"
    )


def _render_diagram_page(cache: L2InstanceCache) -> str:
    """X.4.b.3 — the post-processed-graphviz spike arm.

    Strategy: build the graphviz ``Digraph`` server-side (reuses the
    Phase Y typed projection — same walk both spike arms consume),
    inline its ``.source`` (DOT) into a ``<template>`` block, and let
    the JS shim do the wasm-graphviz render + post-process the SVG to
    add ``data-kind`` / ``data-id`` attrs from the graphviz titles.
    The shim wires the chrome (toggle checkboxes + click-to-focus)
    by toggling CSS classes — no DOM mutation per interaction.

    Why server-side DOT, client-side render: we already have the
    Python graphviz wrapper for DOT construction (it's in the docs
    extra; Studio inherits via the same install). The wasm-graphviz
    binary handles the layout + SVG emission in the browser — no
    system ``dot`` dependency, same approach Phase T's docs use.
    """
    instance = cache.get()
    typed = topology_graph_for(instance)
    # build_topology_graph internally consumes the typed projection.
    # Its .source is the DOT string we hand to wasm-graphviz client-side.
    digraph = build_topology_graph(instance)
    dot_source: str = digraph.source

    prefix = escape(str(instance.instance))
    n_role = sum(1 for n in typed.nodes if n.kind == "role")
    n_rail = sum(1 for n in typed.nodes if n.kind == "rail")
    n_template = sum(1 for n in typed.nodes if n.kind == "template")
    n_chain = sum(1 for e in typed.edges if e.kind == "chain")

    # Engines exposed in the chrome — same set the legacy
    # ``render_topology`` accepts. Hot-swappable via ?engine=...
    engines = ("dot", "neato", "sfdp", "fdp", "circo", "twopi")
    engine_links = " ".join(
        f'<a href="?engine={e}">{e}</a>' for e in engines
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio diagram — {prefix}</title>
  <link rel="stylesheet" href="/studio/static/diagram.css">
</head>
<body>
  <header class="studio-header">
    <h1>Studio · diagram</h1>
    <span class="instance">{prefix}</span>
    <a class="nav-link" href="/">← landing</a>
    <a class="nav-link" href="/dashboards">→ dashboards</a>
  </header>

  <div class="diagram-chrome">
    <label>
      <input type="checkbox" id="toggle-role" checked>
      Roles <span class="count" id="count-role">({n_role})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-rail" checked>
      Rails <span class="count" id="count-rail">({n_rail})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-template" checked>
      Templates <span class="count" id="count-template">({n_template})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-chain" checked>
      Chains <span class="count" id="count-chain">({n_chain})</span>
    </label>
    <button id="toggle-reset">Reset</button>
    <span class="knob">engine: {engine_links}</span>
    <span class="status" id="diagram-status">loading…</span>
  </div>

  <div class="diagram-viewport">
    <div id="diagram-target"></div>
  </div>

  <template id="topology-dot">{escape(dot_source)}</template>

  <script type="module" src="/studio/static/diagram.js"></script>
</body>
</html>
"""


def make_studio_routes(cache: L2InstanceCache) -> list[Route | Mount]:
    """Build the Studio route list bound to ``cache``.

    Spliced into ``make_app(..., studio_routes=...)`` BEFORE the
    Dashboards routes so Studio's ``GET /`` overrides the
    ``GET / → /dashboards`` redirect that ``make_app`` installs in
    Dashboards-only mode.
    """
    async def landing(_request: Request) -> HTMLResponse:
        return HTMLResponse(_render_landing_placeholder(cache))

    async def diagram(_request: Request) -> HTMLResponse:
        return HTMLResponse(_render_diagram_page(cache))

    routes: list[Route | Mount] = [
        Route("/", landing, methods=["GET"]),
        Route("/diagram", diagram, methods=["GET"]),
        Mount(
            "/studio/static",
            app=StaticFiles(directory=str(_STUDIO_ASSETS_DIR)),
            name="studio_static",
        ),
        Mount(
            "/studio/wasm-graphviz",
            app=StaticFiles(directory=str(_WASM_GRAPHVIZ_DIR)),
            name="studio_wasm_graphviz",
        ),
    ]
    return routes
