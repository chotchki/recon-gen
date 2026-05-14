"""Studio route builder.

``make_studio_routes(cache)`` returns the ``Route``/``Mount`` list that
``cli.studio`` splices into ``make_app(... studio_routes=...)``. Each
returned route closes over the supplied ``L2InstanceCache`` so Studio's
read/write paths share one in-memory instance per server.

Routes (current):

- ``GET /`` — unified Studio home page (X.4.f.7). Diagram pane on
  top (iframe of ``/diagram``) + per-kind ``<details>`` sections
  below, each lazy-loaded via ``hx-get`` of the editor route's
  ``?embed=1`` fragment. HX-Trigger ``l2-cascade-reload`` fans
  out to refresh the diagram + every section after any save/delete.
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
import secrets
from collections.abc import Mapping
from html import escape
from pathlib import Path
from typing import Any


# X.4.e cache-bust — boot-time random hex appended as `?cb=…` to every
# Studio asset URL the rendered pages emit. Stays stable for the
# lifetime of the process; restart the server to force every browser
# to refetch (no `Cmd+Shift+R` needed). Static-asset cache headers
# (Starlette's StaticFiles ETag/Last-Modified) still revalidate
# between server restarts; this just guarantees a fresh URL when the
# server itself bumps.
_BOOT_ID: str = secrets.token_hex(4)


def asset_url(path: str) -> str:
    """Versioned URL for a Studio asset.

    ``asset_url("diagram.css")`` → ``/studio/static/diagram.css?cb=<boot>``
    ``asset_url("/studio/wasm-graphviz/index.js")`` →
        ``/studio/wasm-graphviz/index.js?cb=<boot>`` (absolute path
    passes through unchanged except for the cb suffix).
    """
    if path.startswith("/"):
        return f"{path}?cb={_BOOT_ID}"
    return f"/studio/static/{path}?cb={_BOOT_ID}"

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from quicksight_gen.common.config import Config
from quicksight_gen.common.db import AsyncConnectionPool
from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.l2.coverage import CoverageEntry, coverage_for
from quicksight_gen.common.l2.deploy_pipeline import run_deploy_pipeline
from quicksight_gen.common.l2.topology import (
    build_topology_graph_per_rail,
    topology_graph_for,
    visible_entities_for,
)
from quicksight_gen.common.l2.trainer import plants_per_node
from quicksight_gen.common.sql.dialect import Dialect
from quicksight_gen.common.html.render import _emit_theme_style


def studio_theme_head(instance: object) -> str:
    """X.4.f.13 — App2 Tailwind output.css link + L2 theme override block.

    Every studio HTML page links App2's compiled Tailwind sheet (which
    declares ``--color-accent`` / ``--color-surface`` / etc. with
    build-time defaults via ``input.css``'s ``@theme`` block) AND
    injects a per-L2-instance ``:root { --color-accent: ...; }``
    override so the studio inherits the active institution's brand
    palette. ``_studio_assets/diagram.css``'s ``--studio-*`` tokens
    alias the ``--color-*`` tokens (X.4.f.13) so the existing editor
    CSS picks up the override automatically.

    Pass the L2Instance (``cache.get()``) — its optional ``theme``
    attribute drives the override. ``None`` falls back to
    ``DEFAULT_PRESET`` per the silent-fallback contract (N.4.k).
    """
    theme = getattr(instance, "theme", None)
    return (
        f'<link rel="stylesheet" href="/static/output.css?cb={_BOOT_ID}">\n'
        f'  {_emit_theme_style(theme)}'
    )


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
        f'<script src="{asset_url("/static/js/dev_log.js")}" defer></script>\n',
    )


_HOME_SECTIONS: tuple[tuple[str, str, str], ...] = (
    # (kind, label, accessor on L2Instance)
    ("account", "Accounts", "accounts"),
    ("account_template", "Account templates", "account_templates"),
    ("rail", "Rails", "rails"),
    ("transfer_template", "Transfer templates", "transfer_templates"),
    ("chain", "Chains", "chains"),
    ("limit_schedule", "Limit schedules", "limit_schedules"),
)

# X.4.f.12 — singleton kinds get their own home-page section format
# (no list, no +Add — just an Edit link landing on the singleton form).
_HOME_SINGLETONS: tuple[tuple[str, str, str], ...] = (
    # (kind, label, attr on L2Instance — None means "not set yet")
    ("theme", "Theme", "theme"),
    ("persona", "Persona", "persona"),
)


def _render_home_page(cache: L2InstanceCache, dev_log: bool) -> str:
    """X.4.f.7 — unified Studio home page (diagram + every entity kind).

    Composes one page with:

    - Header (matching the chrome on ``/diagram`` and ``/l2_shape/<kind>/``).
    - Diagram pane — ``<iframe src="/diagram?layer=1">`` so the
      wasm-graphviz render stays self-contained (its own document
      context; no double-load of the module script when the cascade
      forces a refresh).
    - Per-kind entity sections — ``<details>`` with lazy-loaded
      ``hx-get`` content (the editor route's ``?embed=1`` fragment).
      First section open; the rest collapsed so a 7-rail / 30-account
      L2 isn't an unbroken wall on first paint. Each section also
      links out (``↗``) to the dedicated per-kind page (deep-link
      target preserved from X.4.e — handy for sharing a URL).

    Cascade fan-out: every editor save/delete returns
    ``HX-Trigger: l2-cascade-reload``. Each section's inner ``<div>``
    declares ``hx-trigger="load, l2-cascade-reload from:body"`` so it
    refetches its fragment. The iframe is in its own document context
    and HTMX doesn't forward HX-Trigger events across that boundary;
    a small parent-page JS listener catches the same custom event and
    bumps ``iframe.src = iframe.src`` to force a reload.
    """
    instance = cache.get()
    prefix = escape(str(instance.instance))
    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)

    section_blocks: list[str] = []
    for idx, (kind, label, accessor) in enumerate(_HOME_SECTIONS):
        n = len(getattr(instance, accessor))
        open_attr = " open" if idx == 0 else ""
        body_id = f"home-section-body-{kind}"
        section_blocks.append(
            f'<details class="home-section" data-kind="{escape(kind)}"{open_attr}>'
            f"<summary>{escape(label)} "
            f'<span class="count">({n})</span> '
            f'<a class="home-section-add" '
            f'href="/l2_shape/{kind}/new" '
            # Stop the click from triggering the surrounding <details>
            # toggle (preventDefault). The browser still follows the
            # href so the operator lands on the dedicated create page.
            f'onclick="event.stopPropagation()" '
            f'title="Create a new {escape(kind)}">+ Add</a>'
            f'<a class="home-section-link" href="/l2_shape/{kind}/" '
            f'onclick="event.stopPropagation()" '
            f'title="Open in dedicated page">↗</a>'
            f"</summary>"
            f'<div class="home-section-body" id="{body_id}" '
            f'hx-get="/l2_shape/{kind}/?embed=1" '
            f'hx-trigger="load, l2-cascade-reload from:body" '
            f'hx-swap="innerHTML">'
            f'<p class="home-section-loading">loading…</p>'
            f"</div>"
            f"</details>"
        )
    # X.4.f.12 — singleton sections at the bottom of the home page
    # (cosmetic / less-frequently-edited than the entity collections).
    # No list, no +Add — just an Edit link to the singleton form.
    for kind, label, attr in _HOME_SINGLETONS:
        is_set = getattr(instance, attr, None) is not None
        status = "set" if is_set else "not set"
        section_blocks.append(
            f'<details class="home-section" data-kind="{escape(kind)}">'
            f'<summary>{escape(label)} '
            f'<span class="count">({escape(status)})</span> '
            f'<a class="home-section-add" '
            f'href="/l2_shape/{kind}/" '
            f'onclick="event.stopPropagation()" '
            f'title="Edit {escape(label)} (single YAML block)">'
            f"Edit</a>"
            f"</summary>"
            f'<div class="home-section-body">'
            f'<p class="home-section-loading">'
            f'{escape(label)} is a single YAML block — '
            f'click <strong>Edit</strong> to view / change it.'
            f"</p>"
            f"</div>"
            f"</details>"
        )
    sections_html = "\n    ".join(section_blocks)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio — {prefix}</title>
  {devlog_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram.css")}">
  <link rel="stylesheet" href="{asset_url("editor.css")}">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <script>
    // X.4.e.5 — swap 4xx response bodies (validator returns 400 + the
    // re-rendered form fragment). 5xx still treated as errors.
    document.addEventListener('htmx:beforeSwap', function(evt) {{
      var status = evt.detail.xhr.status;
      if (status >= 400 && status < 500) {{
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
      }}
    }});
    // X.4.f.7 — HX-Trigger fires `l2-cascade-reload` on document.body
    // (bubbles to document). The diagram iframe is its own document
    // context and HTMX doesn't forward triggers across that boundary;
    // reassign iframe.src to force a same-origin reload.
    document.addEventListener('l2-cascade-reload', function() {{
      var f = document.getElementById('diagram-frame');
      if (f) {{ f.src = f.src; }}
    }});

    // X.4.f.8 — diagram-click → entity-card filter. The diagram page
    // already navigates its own URL to ?focus=<node_id> when a node is
    // clicked. The iframe is same-origin, so we read its location on
    // load, fetch the visible-entity set from the server, and toggle a
    // CSS class on cards whose id isn't in the set. Re-applied on
    // htmx:afterSettle so cascade-reload doesn't drop the filter.
    var lastVisibleByKind = null;  // null = no filter
    var lastFocus = null;
    function applyFocusFilter() {{
      var cards = document.querySelectorAll(
        '#home-entities .entity-card[data-kind][data-entity-id]'
      );
      cards.forEach(function(card) {{
        var hidden = false;
        if (lastVisibleByKind !== null) {{
          var k = card.dataset.kind;
          var id = card.dataset.entityId;
          var ids = lastVisibleByKind[k];
          hidden = !ids || ids.indexOf(id) === -1;
        }}
        card.classList.toggle('is-hidden-by-focus', hidden);
      }});
      // Per-section "(N shown)" indicator update.
      document.querySelectorAll('details.home-section').forEach(function(d) {{
        var visible = d.querySelectorAll(
          '.entity-card:not(.is-hidden-by-focus)'
        ).length;
        var total = d.querySelectorAll('.entity-card').length;
        var summary = d.querySelector('summary');
        if (!summary) return;
        var ind = summary.querySelector('.focus-filter-indicator');
        if (lastVisibleByKind === null || total === 0 || visible === total) {{
          if (ind) ind.remove();
        }} else {{
          if (!ind) {{
            ind = document.createElement('span');
            ind.className = 'focus-filter-indicator';
            summary.appendChild(ind);
          }}
          ind.textContent = ' · ' + visible + ' shown';
        }}
      }});
    }}
    function refreshFocusFromIframe() {{
      var f = document.getElementById('diagram-frame');
      if (!f || !f.contentWindow) return;
      var sp;
      try {{
        sp = new URLSearchParams(f.contentWindow.location.search);
      }} catch (e) {{
        return;  // cross-origin (shouldn't happen — iframe is same-origin)
      }}
      var focus = sp.get('focus') || null;
      if (focus === lastFocus) {{ applyFocusFilter(); return; }}
      lastFocus = focus;
      if (!focus) {{
        lastVisibleByKind = null;
        applyFocusFilter();
        return;
      }}
      fetch('/diagram/visible?focus=' + encodeURIComponent(focus))
        .then(function(r) {{ return r.json(); }})
        .then(function(j) {{
          lastVisibleByKind = j;
          applyFocusFilter();
        }})
        .catch(function() {{}});
    }}
    document.addEventListener('DOMContentLoaded', function() {{
      var f = document.getElementById('diagram-frame');
      if (f) f.addEventListener('load', refreshFocusFromIframe);
    }});
    // Re-apply after every HTMX swap (section refetch on cascade-reload
    // brings back fresh cards without our hide class).
    document.addEventListener('htmx:afterSettle', applyFocusFilter);

    // X.4.f.8.reverse — click an entity-card title → focus the diagram
    // on that entity's node. Navigates the iframe to ?focus=<node_id>;
    // its load event fires our existing iframe-focus listener which
    // fetches /diagram/visible and re-runs applyFocusFilter — so this
    // is purely a "set focus on the diagram, let the existing pipeline
    // do the rest" hop. Event delegation on #home-entities catches
    // titles from cards added by hx-get refetches too.
    function _focusDiagramOnNode(nodeId) {{
      var f = document.getElementById('diagram-frame');
      if (!f || !f.contentWindow) return;
      var url;
      try {{
        url = new URL(f.contentWindow.location.href);
      }} catch (e) {{
        url = new URL('/diagram', window.location.origin);
        url.searchParams.set('layer', '1');
      }}
      url.searchParams.set('focus', nodeId);
      f.contentWindow.location.href = url.toString();
    }}
    function _maybeFocusFromTitle(target) {{
      var el = target;
      while (el && el !== document.body) {{
        if (el.classList && el.classList.contains('entity-card-title')) {{
          var nodeId = el.dataset.focusNode;
          if (nodeId) {{ _focusDiagramOnNode(nodeId); return true; }}
          return false;
        }}
        el = el.parentNode;
      }}
      return false;
    }}
    document.addEventListener('click', function(evt) {{
      _maybeFocusFromTitle(evt.target);
    }});
    document.addEventListener('keydown', function(evt) {{
      if (evt.key !== 'Enter' && evt.key !== ' ') return;
      if (_maybeFocusFromTitle(evt.target)) {{ evt.preventDefault(); }}
    }});
  </script>
  {devlog_script}</head>
<body class="home-page">
  <header class="studio-header">
    <h1>Studio</h1>
    <span class="instance">{prefix}</span>
    <a class="nav-link" href="/diagram">→ diagram (full)</a>
    <a class="nav-link" href="/dashboards">→ dashboards</a>
    <button id="deploy-btn" class="deploy-btn" type="button"
            onclick="quicksightDeploy()">Deploy changes</button>
    <span id="deploy-status" class="deploy-status" aria-live="polite"></span>
  </header>
  <script>
    // X.4.g.14 — Studio "Deploy changes" button. POSTs /deploy, swaps
    // the deploy-status span to reflect the result.
    function quicksightDeploy() {{
      var btn = document.getElementById('deploy-btn');
      var status = document.getElementById('deploy-status');
      btn.disabled = true;
      status.className = 'deploy-status deploy-status--running';
      status.textContent = 'Deploying…';
      fetch('/deploy', {{ method: 'POST' }})
        .then(function(resp) {{
          return resp.json().then(function(body) {{
            return {{ ok: resp.ok, status: resp.status, body: body }};
          }});
        }})
        .then(function(result) {{
          btn.disabled = false;
          if (result.body.halted) {{
            status.className = 'deploy-status deploy-status--halted';
            status.textContent = 'Halted: ' + result.body.halt_reason;
          }} else if (result.ok) {{
            var s3 = result.body.step3_generator;
            status.className = 'deploy-status deploy-status--ok';
            status.textContent = (
              'Deployed (gen ' + result.body.step5_data_generation_id +
              ', ' + s3.transactions_after + ' tx)'
            );
          }} else {{
            status.className = 'deploy-status deploy-status--error';
            status.textContent = 'Failed: HTTP ' + result.status;
          }}
        }})
        .catch(function(err) {{
          btn.disabled = false;
          status.className = 'deploy-status deploy-status--error';
          status.textContent = 'Failed: ' + (err && err.message || err);
        }});
    }}
  </script>

  <section class="home-diagram">
    <iframe id="diagram-frame" src="/diagram?layer=1&amp;embed=1"
            title="L2 topology diagram"></iframe>
  </section>

  <section class="home-entities" id="home-entities">
    {sections_html}
  </section>
</body>
</html>
"""


def _render_diagram_page(
    cache: L2InstanceCache,
    dev_log: bool,
    focus_node_id: str | None = None,
    layer: int = 1,
    *,
    coverage_available: bool = False,
    embed: bool = False,
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
  {devlog_meta}{coverage_meta}{trainer_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram.css")}">
  {devlog_script}</head>
<body class="{"diagram-embed" if embed else ""}">
  {("" if embed else (
    '<header class="studio-header">'
    f'<h1>Studio · diagram</h1>'
    f'<span class="instance">{prefix}</span>'
    '<a class="nav-link" href="/">← landing</a>'
    '<a class="nav-link" href="/dashboards">→ dashboards</a>'
    '</header>'
  ))}

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

  <script type="module" src="{asset_url("diagram.js")}"></script>
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
    cfg: Config | None = None,
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
        cfg: Full Config dataclass; required for the X.4.g.13
            ``POST /deploy`` route (the deploy pipeline reads
            ``cfg.etl_hook`` / ``cfg.etl_datasource`` /
            ``cfg.test_generator`` plus DB connection knobs).
            None ⇒ POST /deploy is silently omitted (unit-test
            surface that doesn't exercise the pipeline).
    """
    async def landing(_request: Request) -> HTMLResponse:
        return HTMLResponse(_render_home_page(cache, dev_log))

    async def diagram(request: Request) -> HTMLResponse:
        focus_node_id = request.query_params.get("focus") or None
        layer_raw = request.query_params.get("layer", "1")
        try:
            layer = max(1, min(3, int(layer_raw)))
        except ValueError:
            layer = 1
        # X.4.f.8.embed-chrome — when embedded inside the home page's
        # iframe, drop the studio-header so the page doesn't carry two
        # nav bars (the home's + the diagram's).
        embed = request.query_params.get("embed") == "1"
        return HTMLResponse(
            _render_diagram_page(
                cache, dev_log, focus_node_id, layer,
                coverage_available=db_pool is not None,
                embed=embed,
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

    # X.4.f.8 — visible-entities map for the home page's diagram-click
    # filter. ``?focus=<node_id>`` returns the entity IDs reachable from
    # that focus subgraph; absent / unknown focus returns the full set.
    async def visible(request: Request) -> JSONResponse:
        focus = request.query_params.get("focus") or None
        instance = cache.get()
        by_kind = visible_entities_for(instance, focus)
        return JSONResponse(
            {kind: sorted(ids) for kind, ids in by_kind.items()},
        )

    routes.append(Route("/diagram/visible", visible, methods=["GET"]))

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

    # X.4.g.13 — POST /deploy: orchestrate steps 1→5 of the deploy
    # pipeline against the cached L2 + the operator-supplied cfg.
    # Mounted only when cfg is wired (Studio CLI passes it; the
    # bare-cache unit-test surface omits it).
    if cfg is not None:
        bound_cfg = cfg

        async def deploy(_request: Request) -> JSONResponse:
            instance = cache.get()
            summary = await run_deploy_pipeline(
                bound_cfg, instance, dev_log=None,
            )
            status = 503 if summary.halted else 200
            return JSONResponse(summary.to_json(), status_code=status)

        routes.append(Route("/deploy", deploy, methods=["POST"]))

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
