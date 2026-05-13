"""Studio route builder.

``make_studio_routes(cache)`` returns the ``Route``/``Mount`` list that
``cli.studio`` splices into ``make_app(... studio_routes=...)``. Each
returned route closes over the supplied ``L2InstanceCache`` so Studio's
read/write paths share one in-memory instance per server.

Routes (current):

- ``GET /`` — landing placeholder. Will become the real Studio
  landing once X.4.c (unified diagram) + X.4.e (editor list) land.
- ``GET /diagram`` — the L2 topology rendered via post-processed
  graphviz SVG with rails as first-class nodes (the X.4.b dot pivot;
  spike locked 2026-05-13). Reads the per-rail Digraph builder,
  inlines its DOT source, and a small JS shim does the wasm-graphviz
  render + ``data-kind`` / ``data-id`` annotation + chrome wiring.
  Knobs: ``?engine=`` flips the layout binary (dot / neato / sfdp /
  …); ``?focus=<node_id>`` filters to that node + its
  ``_smart_focus_hops``-deep neighborhood (server-side re-render, dot
  re-lays out the smaller subgraph cleanly).
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
from collections.abc import Mapping
from html import escape
from pathlib import Path
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from quicksight_gen.common.db import AsyncConnectionPool
from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.l2.coverage import CoverageEntry, coverage_for
from quicksight_gen.common.l2.topology import (
    build_topology_graph_per_rail,
    topology_graph_for,
)
from quicksight_gen.common.l2.trainer import plants_per_node
from quicksight_gen.common.sql.dialect import Dialect


_STUDIO_ASSETS_DIR = Path(__file__).parent / "_studio_assets"
# wasm-graphviz vendored once under docs/stylesheets/ for the docs site
# (Phase T). For Studio, mounted at /studio/wasm-graphviz/ so the diagram
# shim can ``await import()`` it without a second 800KB copy.
_WASM_GRAPHVIZ_DIR = (
    Path(__file__).parent.parent.parent / "docs" / "stylesheets"
    / "wasm-graphviz"
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
        "<ul>\n"
        "<li><a href=\"/diagram\">→ Topology diagram</a></li>\n"
        "<li><a href=\"/dashboards\">→ Dashboards</a></li>\n"
        "</ul>\n"
        "<h2>Edit L2 entities</h2>\n"
        "<ul>\n"
        "<li><a href=\"/l2_shape/account/\">Accounts</a></li>\n"
        "<li><a href=\"/l2_shape/account_template/\">Account templates</a></li>\n"
        "<li><a href=\"/l2_shape/rail/\">Rails</a></li>\n"
        "<li><a href=\"/l2_shape/transfer_template/\">Transfer templates</a></li>\n"
        "<li><a href=\"/l2_shape/chain/\">Chains</a></li>\n"
        "<li><a href=\"/l2_shape/limit_schedule/\">Limit schedules</a></li>\n"
        "</ul>\n"
        "<p><em>Editor lands in X.4.e/f; Deploy pipeline in X.4.g.</em></p>\n"
        "</body></html>\n"
    )


def _render_diagram_page(
    cache: L2InstanceCache,
    dev_log: bool,
    focus_node_id: str | None = None,
    layer: int = 1,
    *,
    coverage_available: bool = False,
) -> str:
    """Render the L2 topology diagram (per-rail / dot, X.4.b spike winner).

    Strategy: build the graphviz ``Digraph`` server-side via
    ``build_topology_graph_per_rail`` (rails as first-class nodes;
    bundle nodes for parallel pure-connectivity rails; templates as
    clusters around their leg-rails). Inline the DOT source into a
    ``<template>`` block; a JS shim (``diagram.js``) does the
    wasm-graphviz render + post-processes the SVG to add ``data-kind``
    / ``data-id`` attrs. The shim wires the chrome (toggle checkboxes,
    layer stepper, click-to-focus → URL navigation) by toggling CSS
    classes — no DOM mutation per interaction.

    Why server-side DOT, client-side render: the Python graphviz
    wrapper handles DOT construction (it's in the docs extra; Studio
    inherits via the same install). The wasm-graphviz binary handles
    layout + SVG emission in the browser — no system ``dot``
    dependency, same approach Phase T's docs use.

    ``focus_node_id`` (optional) filters the graph to the focused
    node + its ``_smart_focus_hops``-deep neighborhood (roles/
    templates default to 2 to cross a rail; rails/bundles default
    to 1). Server re-emits a smaller DOT; dot re-lays out the
    subgraph cleanly. Click-empty-canvas / Esc / Reset all drop
    the param to restore the full picture.
    """
    instance = cache.get()
    digraph = build_topology_graph_per_rail(
        instance,
        bundle_parallel_rails=True,
        focus_node_id=focus_node_id,
        layer=layer,
    )
    dot_source: str = digraph.source

    # Counts for the chrome (uses the typed projection so they reflect
    # the underlying L2 shape, not the rendered subgraph).
    typed = topology_graph_for(instance)
    prefix = escape(str(instance.instance))
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

    # Sidecar metadata for the JS shim — graphviz doesn't surface
    # node-scope through the SVG, so we ship a small map the post-
    # processor merges in (data-scope per role node).
    role_meta: dict[str, dict[str, Any]] = {
        n.id: {"scope": n.scope, "templated": n.templated}
        for n in typed.nodes
        if n.kind == "role" and n.scope is not None
    }
    sidecar = json.dumps(  # typing-smell: ignore[json-indent]: inline page payload — compact saves bytes
        {"role_meta": role_meta},
    )

    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)
    # X.4.c.5.b — surface pool availability to the JS shim. The chrome
    # toggle (X.4.c.5.d) reads this meta tag to decide whether to mount
    # the Coverage checkbox; absent ⇒ no toggle (graceful degrade).
    coverage_meta = (
        '<meta name="diagram-coverage-available" content="1">\n'
        if coverage_available
        else ""
    )
    # X.4.c.6 — trainer overlay is always available (pure scenario
    # walk, no DB). The meta tag mirrors the coverage shape so the JS
    # shim's gate is symmetrical.
    trainer_meta = '<meta name="diagram-trainer-available" content="1">\n'
    # Build URL fragments so layer / focus links preserve the other
    # param. Order: focus first, then layer (matches the natural read).
    def _qs(*, layer_val: int, focus_val: str | None) -> str:
        bits: list[str] = [f"layer={layer_val}"]
        if focus_val:
            bits.append(f"focus={escape(focus_val)}")
        return "?" + "&".join(bits)

    layer_links = " ".join(
        f'<a class="layer-btn{" active" if n == layer else ""}" '
        f'href="{_qs(layer_val=n, focus_val=focus_node_id)}">{label}</a>'
        for n, label in (
            (1, "1 · Roles + structure"),
            (2, "+ Rails"),
            (3, "+ Chains&nbsp;&amp;&nbsp;Templates"),
        )
    )

    # X.4.c.5.d — Coverage toggle. Mounted only when the demo-DB pool
    # is wired (which the JS shim also gates by reading the
    # diagram-coverage-available meta). Off by default — clean diagram;
    # on overlays presence/absence tint per node.
    coverage_toggle_html = (
        '<label class="chrome-coverage-toggle">'
        '<input type="checkbox" id="toggle-coverage">'
        ' Coverage'
        '</label>'
        if coverage_available
        else ""
    )
    # X.4.c.6 — Trainer toggle. Always available (pure scenario walk).
    # Off by default; on overlays per-plant-kind badges per node.
    trainer_toggle_html = (
        '<label class="chrome-trainer-toggle">'
        '<input type="checkbox" id="toggle-trainer">'
        ' Trainer'
        '</label>'
    )

    # Focus indicator + clear link. Visible only when ?focus= is set.
    # Clear preserves the current layer.
    if focus_node_id is not None:
        focus_indicator = (
            f'<span class="knob focus-indicator">focused: '
            f'<code>{escape(focus_node_id)}</code> '
            f'<a class="engine-link" href="{_qs(layer_val=layer, focus_val=None)}">clear</a></span>'
        )
    else:
        focus_indicator = ""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio diagram — {prefix}</title>
  {devlog_meta}{coverage_meta}{trainer_meta}<link rel="stylesheet" href="/studio/static/diagram.css">
  {devlog_script}</head>
<body>
  <header class="studio-header">
    <h1>Studio · diagram</h1>
    <span class="instance">{prefix}</span>
    <a class="nav-link" href="/">← landing</a>
    <a class="nav-link" href="/dashboards">→ dashboards</a>
  </header>

  <div class="diagram-chrome">
    <span class="layer-stepper" aria-label="Conceptual layers">
      layer: {layer_links}
    </span>
    <a id="toggle-reset" href="?">Reset</a>
    {coverage_toggle_html}
    {trainer_toggle_html}
    {focus_indicator}
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
    db_pool: AsyncConnectionPool | None = None,
    *,
    dialect: Dialect | None = None,
    prefix_override: str | None = None,
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
        db_pool: Optional ``AsyncConnectionPool`` against the demo DB.
            When set, X.4.c.5's ``GET /diagram/coverage`` route mounts
            and the diagram chrome surfaces a Coverage toggle. When
            None, coverage is silently absent — the only UX impact is
            the missing chrome toggle, no broken behavior. Studio's
            CLI always provides a pool (``cli/studio.py`` rejects
            ``--stub`` / smoke); ``None`` is the unit-test surface.
        dialect: SQL dialect; required when ``db_pool`` is set
            (the coverage fetcher's column-name case folds via
            ``column_name(...)``). When ``db_pool`` is None, this is
            ignored.
        prefix_override: Optional override for the ``<prefix>_transactions``
            schema prefix; usually omitted (defaults to the L2
            instance's own ``instance`` field). Equivalent to
            ``cfg.l2_instance_prefix`` in the demo CLI flow.
    """
    async def landing(_request: Request) -> HTMLResponse:
        return HTMLResponse(_render_landing_placeholder(cache, dev_log))

    async def diagram(request: Request) -> HTMLResponse:
        focus_node_id = request.query_params.get("focus") or None
        layer_raw = request.query_params.get("layer", "1")
        try:
            layer = max(1, min(3, int(layer_raw)))
        except ValueError:
            layer = 1
        return HTMLResponse(
            _render_diagram_page(
                cache, dev_log, focus_node_id, layer,
                coverage_available=db_pool is not None,
            ),
        )

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

    # X.4.e + X.4.f — editor routes (list / read / edit / save / delete
    # for every entity kind). Pure scenario over the cached L2 — no
    # pool needed, always mounted alongside the diagram.
    from quicksight_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        make_editor_routes,
    )
    routes.extend(make_editor_routes(cache))

    # X.4.c.6 — trainer JSON route. Always mounted (no DB needed —
    # the scenario walk is pure Python over the cached L2).
    async def trainer(_request: Request) -> JSONResponse:
        instance = cache.get()
        tm = plants_per_node(instance)
        return JSONResponse(
            {"nodes": {k: dict(v) for k, v in tm.by_node_id.items()}},
        )

    routes.append(Route("/diagram/trainer", trainer, methods=["GET"]))

    # X.4.c.5.c — coverage JSON route. Mounted only when a pool exists
    # (Studio CLI always provides one; the unit-test surface skips this).
    if db_pool is not None:
        if dialect is None:
            raise ValueError(
                "make_studio_routes: db_pool requires dialect "
                "(coverage_for needs column_name() to case-fold per dialect)."
            )
        # Capture pool + dialect by closure for the route handler.
        bound_pool = db_pool
        bound_dialect = dialect
        bound_prefix_override = prefix_override

        async def coverage(_request: Request) -> JSONResponse:
            instance = cache.get()
            prefix = bound_prefix_override or str(instance.instance)
            cov = await coverage_for(
                bound_pool, prefix, instance, dialect=bound_dialect,
            )
            return JSONResponse(_coverage_to_json(cov.by_node_id, cov.by_chain_edge_id))

        routes.append(Route("/diagram/coverage", coverage, methods=["GET"]))

    return routes


def _coverage_to_json(
    by_node_id: "Mapping[str, CoverageEntry]",
    by_chain_edge_id: "Mapping[str, CoverageEntry]",
) -> dict[str, Any]:
    """Shape the JSON payload the diagram chrome consumes.

    Top-level keys ``nodes`` and ``chain_edges`` so the JS shim can
    paint nodes vs edges separately. Each value is a flat
    ``{id: {present, count}}`` map — boolean `present` keeps the JSON
    payload trivially debug-printable.
    """
    return {
        "nodes": {
            node_id: {"present": e.present, "count": e.count}
            for node_id, e in by_node_id.items()
        },
        "chain_edges": {
            edge_id: {"present": e.present, "count": e.count}
            for edge_id, e in by_chain_edge_id.items()
        },
    }
