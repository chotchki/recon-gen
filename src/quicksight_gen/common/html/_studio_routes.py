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

import json
from html import escape
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.l2.topology import (
    TopologyGraph,
    build_topology_graph,
    topology_graph_for,
)
from quicksight_gen.common.l2.topology import (
    _render_to_graphviz,  # spike-grade reach into the renderer (X.4.b.3)
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


def _filter_orphan_role_nodes(g: TopologyGraph) -> TopologyGraph:
    """Drop role nodes that don't appear as the source or target of any edge.

    Accounts / account templates declared in the L2 YAML but never
    referenced by a rail / template-leg / chain are visually noise in
    the diagram — they appear as floating boxes disconnected from the
    main connectivity story. Filtering them at render time keeps the
    topology projection itself unchanged (other consumers + tests stay
    stable) while giving the diagram surface the quieter default.
    """
    referenced: set[str] = set()
    for edge in g.edges:
        referenced.add(edge.source)
        referenced.add(edge.target)
    filtered = tuple(
        n for n in g.nodes
        if n.kind != "role" or n.id in referenced
    )
    return TopologyGraph(
        instance_name=g.instance_name,
        nodes=filtered,
        edges=g.edges,
    )


def _dev_log_head_snippets(dev_log: bool) -> tuple[str, str]:
    """Return ``(meta_tag, script_tag)`` to inject when ``dev_log=True``.

    Both ``""`` when off so production pages stay zero-overhead.
    The meta gates ``dev_log.js``'s installation (the script body is
    a no-op if the meta is absent — see the script's first line).
    """
    if not dev_log:
        return ("", "")
    return (
        '<meta name="dev-log">\n',
        '<script src="/static/js/dev_log.js" defer></script>\n',
    )


def _render_landing_placeholder(cache: L2InstanceCache, dev_log: bool) -> str:
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
    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)
    return (
        "<!doctype html>\n"
        "<html lang=\"en\"><head>\n"
        f"<title>Studio — {prefix}</title>\n"
        "<meta charset=\"utf-8\">\n"
        f"{devlog_meta}"
        "<style>body{font-family:system-ui;max-width:48rem;"
        "margin:2rem auto;padding:0 1rem;color:#1e293b}"
        "h1{color:#1f4e79}a{color:#1f4e79}code{background:#e0e7f0;"
        "padding:1px 4px;border-radius:3px}</style>"
        f"{devlog_script}"
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


def _render_diagram_page(cache: L2InstanceCache, dev_log: bool) -> str:
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
    # X.4.b.3 spike — drop role nodes with no incident edges. Accounts
    # declared in YAML but never referenced by a rail / template / chain
    # are truly disconnected in the graph (the L2's "I declared this
    # but nobody uses it yet" surface). Filtering them out before
    # rendering keeps the diagram focused on the connectivity story.
    typed = _filter_orphan_role_nodes(typed)
    # Render the *filtered* typed graph (not the original L2Instance) so
    # the DOT we ship to wasm-graphviz reflects the chrome filter. Reach
    # into the renderer directly since build_topology_graph(instance)
    # always rebuilds from the L2Instance.
    digraph = _render_to_graphviz(typed)
    dot_source: str = digraph.source

    prefix = escape(str(instance.instance))
    # Role-scope split (X.4.b chrome iteration D — institutional perimeter).
    n_role_internal = sum(
        1 for n in typed.nodes
        if n.kind == "role" and n.scope == "internal"
    )
    n_role_external = sum(
        1 for n in typed.nodes
        if n.kind == "role" and n.scope == "external"
    )
    n_rail = sum(1 for n in typed.nodes if n.kind == "rail")
    n_template = sum(1 for n in typed.nodes if n.kind == "template")
    n_chain = sum(1 for e in typed.edges if e.kind == "chain")
    n_bundle = sum(1 for e in typed.edges if e.kind == "rail_bundle")
    n_self_loop = sum(1 for e in typed.edges if e.kind == "self_loop")
    n_control_parent = sum(1 for e in typed.edges if e.kind == "control_parent")
    n_template_role = sum(1 for e in typed.edges if e.kind == "template_role")

    # Sidecar metadata for the JS shim — graphviz doesn't surface
    # node-scope through the SVG, so we ship a small map the post-
    # processor merges in (data-scope per role node). Mode-overlay stubs
    # also hang off this sidecar — coverage / trainer modes read it for
    # the per-node payload (X.4.b chrome iteration B — stub data is fine
    # for the spike since the real coverage/trainer data lands in
    # X.4.c.5 / X.4.c.6).
    role_meta: dict[str, dict[str, Any]] = {
        n.id: {"scope": n.scope, "templated": n.templated}
        for n in typed.nodes
        if n.kind == "role" and n.scope is not None
    }
    sidecar = json.dumps(  # typing-smell: ignore[json-indent]: inline page payload — compact saves bytes
        {"role_meta": role_meta},
    )

    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)
    # Engines exposed in the chrome — same set the legacy
    # ``render_topology`` accepts. Hot-swappable via ?engine=. The
    # active-state ``data-engine`` attr lets the JS shim mark the link
    # matching ``window.location.search`` as ``.active`` on page load.
    engines = ("dot", "neato", "sfdp", "fdp", "circo", "twopi")
    engine_links = " ".join(
        f'<a class="engine-link" data-engine="{e}" href="?engine={e}">{e}</a>'
        for e in engines
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio diagram — {prefix}</title>
  {devlog_meta}<link rel="stylesheet" href="/studio/static/diagram.css">
  {devlog_script}</head>
<body>
  <header class="studio-header">
    <h1>Studio · diagram</h1>
    <span class="instance">{prefix}</span>
    <a class="nav-link" href="/">← landing</a>
    <a class="nav-link" href="/dashboards">→ dashboards</a>
  </header>

  <div class="diagram-chrome">
    <label>mode:
      <select id="mode-select">
        <option value="default" selected>Default (integrator)</option>
        <option value="coverage">Coverage (ETL · STUB)</option>
        <option value="trainer">Trainer (planted · STUB)</option>
      </select>
    </label>
    <span class="layer-stepper" role="radiogroup" aria-label="Conceptual layers">
      layer:
      <button type="button" data-layer="1" class="layer-btn">1 · Roles + structure</button>
      <button type="button" data-layer="2" class="layer-btn">+ Rails</button>
      <button type="button" data-layer="3" class="layer-btn active">+ Chains&nbsp;&amp;&nbsp;Templates</button>
    </span>
    <button id="toggle-reset">Reset</button>
    <span class="knob">engine: {engine_links}</span>
    <span class="status" id="diagram-status">loading…</span>
  </div>

  <div class="diagram-chrome">
    <strong class="chrome-section-label">Show:</strong>
    <label>
      <input type="checkbox" id="toggle-role-internal" checked>
      Internal roles <span class="count">({n_role_internal})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-role-external" checked>
      External roles <span class="count">({n_role_external})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-rail" checked>
      Rails <span class="count">({n_rail})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-template" checked>
      Templates <span class="count">({n_template})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-chain" checked>
      Chains <span class="count">({n_chain})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-control_parent" checked>
      Control hierarchy <span class="count">({n_control_parent})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-template_role" checked>
      Template→role links <span class="count">({n_template_role})</span>
    </label>
    <strong class="chrome-section-label">Edge labels:</strong>
    <label>
      <input type="checkbox" id="toggle-edge-label-rail_bundle" checked>
      Bundles <span class="count">({n_bundle})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-edge-label-self_loop" checked>
      Self-loops <span class="count">({n_self_loop})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-edge-label-chain" checked>
      Chain badges <span class="count">({n_chain})</span>
    </label>
    <label>
      <input type="checkbox" id="toggle-edge-label-control_parent" checked>
      Control labels
    </label>
    <label>
      <input type="checkbox" id="toggle-edge-label-template_role" checked>
      Template-role labels
    </label>
  </div>

  <div class="diagram-viewport">
    <div id="diagram-target"></div>
  </div>

  <template id="topology-dot">{escape(dot_source)}</template>
  <script id="topology-meta" type="application/json">{sidecar}</script>

  <script type="module" src="/studio/static/diagram.js"></script>
</body>
</html>
"""


def make_studio_routes(
    cache: L2InstanceCache,
    dev_log: bool = False,
) -> list[Route | Mount]:
    """Build the Studio route list bound to ``cache``.

    Spliced into ``make_app(..., studio_routes=...)`` BEFORE the
    Dashboards routes so Studio's ``GET /`` overrides the
    ``GET / → /dashboards`` redirect that ``make_app`` installs in
    Dashboards-only mode.

    Args:
        cache: The shared in-memory ``L2InstanceCache`` every Studio
            route reads from (and X.4.d.3+ writes to).
        dev_log: When True, the diagram + landing pages emit
            ``<meta name="dev-log">`` + load ``/static/js/dev_log.js``
            so client-side console errors / uncaught exceptions /
            unhandled promise rejections / HTMX events POST to
            ``/log`` (which ``make_app`` mounts when ``dev_log=True``).
            Default False so a production-style ``quicksight-gen
            studio`` invocation stays silent.
    """
    async def landing(_request: Request) -> HTMLResponse:
        return HTMLResponse(_render_landing_placeholder(cache, dev_log))

    async def diagram(_request: Request) -> HTMLResponse:
        return HTMLResponse(_render_diagram_page(cache, dev_log))

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
