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

from datetime import date, timedelta

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from quicksight_gen.common.config import Config, PlantKind, ScopeKind
from quicksight_gen.common.db import AsyncConnectionPool
from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.l2.coverage import CoverageEntry, coverage_for
from quicksight_gen.common.l2.deploy_pipeline import run_deploy_pipeline
from quicksight_gen.common.l2.seed import DEFAULT_BASELINE_WINDOW_DAYS
from quicksight_gen.common.l2.tg_cache import TestGeneratorCache
from quicksight_gen.common.l2.trainer_timeline import (
    PlantHit,
    TimelineDay,
    compute_plant_timeline,
    hits_by_kind,
)
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


def _render_home_page(
    cache: L2InstanceCache, dev_log: bool, *, cfg: Config | None = None,
) -> str:
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
    prefix = escape(cfg.deployment_name if cfg is not None else cache.path.stem)
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
    <a class="nav-link" href="/data">→ data</a>
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
    cfg: Config | None = None,
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
    # Z.C — topology helpers require db_table_prefix as a keyword. Use
    # cfg.db_table_prefix when available; fall back to the deployment
    # name (or `"unbound"` sentinel) when the studio is rendering
    # topology without an attached cfg.
    db_prefix = (
        cfg.db_table_prefix if cfg is not None else "unbound"
    )
    digraph = build_topology_graph_per_rail(
        instance,
        db_table_prefix=db_prefix,
        bundle_parallel_rails=True,
        focus_node_id=focus_node_id,
        layer=layer,
    )
    dot_source: str = digraph.source

    # Counts for the chrome (uses the typed projection so they reflect
    # the underlying L2 shape, not the rendered subgraph).
    typed = topology_graph_for(instance, db_table_prefix=db_prefix)
    prefix = escape(cfg.deployment_name if cfg is not None else cache.path.stem)
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
    '<a class="nav-link" href="/data">→ data</a>'
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


_PLANT_LABELS: tuple[tuple[PlantKind, str], ...] = (
    ("drift", "Drift"),
    ("overdraft", "Overdraft"),
    ("limit_breach", "Limit breach"),
    ("stuck_pending", "Stuck pending"),
    ("stuck_unbundled", "Stuck unbundled"),
    ("supersession", "Supersession"),
)


def _build_state_url(tg_cache: TestGeneratorCache) -> str:
    """X.4.h.url — encode the trainer cache state as a /data URL.

    Bookmarkable + shareable: every knob mutation pushes this URL via
    ``HX-Push-Url`` so the browser bar reflects state, history works,
    and reload restores it. Default-valued knobs are omitted from the
    URL to keep it clean — `/data` with no params == "all defaults".
    """
    from urllib.parse import urlencode  # noqa: PLC0415

    tg = tg_cache.get()
    window_start, window_end = tg_cache.get_window()
    today = date.today()  # typing-smell: ignore[no-datetime-now]: trainer-mode URL default-detection — wall-clock today defines the omit-when-default threshold; not a determinism path
    default_window_end = today
    default_window_start = today - timedelta(
        days=DEFAULT_BASELINE_WINDOW_DAYS - 1,
    )
    params: list[tuple[str, str]] = []
    if window_start != default_window_start:
        params.append(("window_start", window_start.isoformat()))
    if window_end != default_window_end:
        params.append(("window_end", window_end.isoformat()))
    if tg.end_date is not None:
        params.append(("end_date", tg.end_date.isoformat()))
    if tg.scope != "full":
        params.append(("scope", tg.scope))
    if tg.seed is not None:
        params.append(("seed", str(tg.seed)))
    if tg.plants:
        params.append(("plants", ",".join(tg.plants)))
    if not tg_cache.is_etl_hook_enabled():
        params.append(("etl_hook", "disabled"))
    if not params:
        return "/data"
    return f"/data?{urlencode(params)}"


def _apply_state_url_to_cache(
    request: Request,
    tg_cache: TestGeneratorCache,
) -> None:
    """X.4.h.url — read /data URL query params + apply to the cache.

    Called on every ``GET /data`` so a bookmarked / reloaded URL
    restores the trainer's prior knob state. Invalid values silently
    drop (same posture as the PUT routes' validation — bad input
    leaves the cache in its prior state).

    Idempotent: applying the same URL twice yields the same cache
    state. Absent params leave the cache untouched (so the natural
    default-cache-from-from_config stays).
    """
    from typing import cast as _cast  # noqa: PLC0415

    qp = request.query_params
    new_window_start: date | None = None
    new_window_end: date | None = None
    raw_ws = qp.get("window_start")
    if raw_ws:
        try:
            new_window_start = date.fromisoformat(raw_ws)
        except ValueError:
            pass
    raw_we = qp.get("window_end")
    if raw_we:
        try:
            new_window_end = date.fromisoformat(raw_we)
        except ValueError:
            pass
    if new_window_start is not None or new_window_end is not None:
        tg_cache.update_window(
            start=new_window_start
            if new_window_start is not None
            else _UNSET_LOCAL,
            end=new_window_end
            if new_window_end is not None
            else _UNSET_LOCAL,
        )

    raw_end = qp.get("end_date")
    if raw_end is not None:  # explicit empty = clear to None
        if raw_end == "":
            tg_cache.update(end_date=None)
        else:
            try:
                tg_cache.update(end_date=date.fromisoformat(raw_end))
            except ValueError:
                pass

    raw_scope = qp.get("scope")
    if raw_scope is not None:
        from quicksight_gen.common.config import (  # noqa: PLC0415
            ScopeKind,
        )
        from typing import get_args as _get_args  # noqa: PLC0415

        if raw_scope in _get_args(ScopeKind):
            tg_cache.update(scope=_cast(ScopeKind, raw_scope))

    raw_seed = qp.get("seed")
    if raw_seed is not None:
        if raw_seed == "":
            tg_cache.update(seed=None)
        else:
            try:
                tg_cache.update(seed=int(raw_seed))
            except ValueError:
                pass

    raw_plants = qp.get("plants")
    if raw_plants is not None:
        # Empty string → clear to () == "all kinds" per SPEC.
        from typing import get_args as _get_args2  # noqa: PLC0415

        known: set[PlantKind] = set(_get_args2(PlantKind))
        if raw_plants == "":
            tg_cache.update(plants=())
        else:
            picked = tuple(
                _cast(PlantKind, p)
                for p in raw_plants.split(",")
                if p in known
            )
            tg_cache.update(plants=picked)

    raw_etl_hook = qp.get("etl_hook")
    if raw_etl_hook is not None:
        # Only "disabled" / "enabled" recognized; bad values silently
        # drop (same posture as other URL params).
        if raw_etl_hook == "disabled":
            tg_cache.set_etl_hook_enabled(False)
        elif raw_etl_hook == "enabled":
            tg_cache.set_etl_hook_enabled(True)


# Sentinel that mirrors the cache's _UNSET — needed because
# update_window's signature takes ``date | object`` for each bound
# (sentinel = "leave alone"), and we want to leave one alone when
# only the other was sent.
_UNSET_LOCAL: object = object()

_SCOPE_LABELS: tuple[tuple[ScopeKind, str, str], ...] = (
    # (value, short label, hover hint pulled from deploy_pipeline docstrings)
    (
        "full",
        "full",
        "Wipe + emit baseline (90 days, all rails) + plants. The "
        "locked-seed default — byte-identical to data apply.",
    ),
    (
        "uncovered_rails",
        "uncovered rails",
        "Fill baseline only for rails the operator's external data "
        "hasn't already covered. No plants — the operator's data is "
        "the story; we just patch the gaps.",
    ),
    (
        "exceptions_only",
        "exceptions only",
        "Plants only, no baseline. Layers L1/Investigation scenarios "
        "on top of the operator's external data.",
    ),
    (
        "only_template",
        "only template",
        "Emit baseline restricted to one TransferTemplate's leg-rails "
        "closure. Useful when iterating on a single template's "
        "lifecycle. Requires the template name in the field below.",
    ),
)


def _render_plants_strip(
    selected: tuple[PlantKind, ...] | None,
) -> str:
    """X.4.h.2 — render the plant-toggle checkbox strip.

    ``selected`` is the current ``cfg.test_generator.plants``. Empty
    tuple = "all kinds" per the SPEC (matches the
    ``filter_scenario_plants(plants=None or ())`` short-circuit), so
    every checkbox renders checked when the tuple is empty.

    Wired with HTMX: each toggle ``hx-put``s the full new selection
    to ``/data/knobs/plants`` (whole-form serialization, server is
    the source of truth for the new state). The form's
    ``hx-swap="outerHTML"`` re-paints the strip from the response so
    the on-screen state always reflects what the server holds.
    """
    select_all = not selected  # None or empty tuple = "all kinds"
    items: list[str] = []
    for kind, label in _PLANT_LABELS:
        checked = "checked " if (select_all or kind in (selected or ())) else ""
        items.append(
            f'<label class="plant-toggle">'
            f'<input type="checkbox" name="plant" value="{kind}" {checked}/>'
            f' {escape(label)}'
            f"</label>"
        )
    body = "".join(items)
    return (
        f'<form id="data-knob-plants" class="data-knob data-knob-plants" '
        f'hx-put="/data/knobs/plants" '
        f'hx-trigger="change" '
        f'hx-target="#data-knob-plants" '
        f'hx-swap="outerHTML">'
        f'<span class="data-knob-label">plants:</span>'
        f"{body}"
        f"</form>"
    )


def _render_etl_hook_strip(
    command: str | None,
    enabled: bool,
) -> str:
    """X.4.h.etl-toggle — render the upstream-re-seed enable/disable strip.

    Surfaces ``cfg.etl_hook`` (the shell command) as the visible
    representative of the "upstream re-seed" pair: step 1 (run the
    hook) + step 2 pull (copy from ``cfg.etl_datasource`` into the
    demo DB). The toggle disables BOTH for the next Deploy without
    erasing either cfg field — flip back on later to restore the
    whole pair without re-configuring. (The label says "etl hook"
    because that's the operator-facing name for the workflow; the
    implementation skips both halves.)

    Three render states:
      - ``command is None`` ⇒ disabled toggle, "(not configured)"
        text. Operator hasn't wired one in cfg.yaml; toggle is moot.
      - ``command`` set, ``enabled=True`` ⇒ active checkbox,
        ``<code>`` showing the command (truncated with ``title=`` for
        the full text on hover).
      - ``command`` set, ``enabled=False`` ⇒ unchecked checkbox,
        ``<code>`` greyed out; deploy will skip step 1.

    Wired with HTMX: the checkbox PUTs ``enabled=on`` (HTML form
    default for checked checkboxes — absence = unchecked) to
    ``/data/knobs/etl_hook``. The route flips the cache flag and
    re-renders the strip.
    """
    common_attrs = (
        'hx-put="/data/knobs/etl_hook" '
        'hx-target="#data-knob-etl-hook" '
        'hx-swap="outerHTML" '
        'hx-trigger="change"'
    )
    if command is None:
        body = (
            '<input type="checkbox" disabled '
            'aria-label="etl_hook (not configured)"/>'
            '<code class="etl-hook-command etl-hook-command--missing">'
            "(not configured)</code>"
        )
    else:
        checked = "checked " if enabled else ""
        cmd_class = (
            "etl-hook-command"
            if enabled
            else "etl-hook-command etl-hook-command--disabled"
        )
        body = (
            f'<input type="checkbox" name="enabled" value="on" '
            f'{checked}'
            f'class="etl-hook-toggle" '
            f'aria-label="Run etl_hook on next deploy" '
            f"{common_attrs}/>"
            f'<code class="{cmd_class}" title="{escape(command)}">'
            f"{escape(command)}</code>"
        )
    return (
        f'<form id="data-knob-etl-hook" class="data-knob data-knob-etl-hook">'
        f'<span class="data-knob-label">etl hook:</span>'
        f"{body}"
        f"</form>"
    )


def _render_window_strip(window_start: date, window_end: date) -> str:
    """X.4.h.3.window — render the scenario-window picker.

    Two date inputs (start / end) plus a "last 90 days" reset.
    Defines the trainer's scenario bounds — purely a UI concept
    (does NOT round-trip through the generator). The timeline panel
    renders one row per day in ``[window_start, window_end]``; the
    operator scrubs ``up_to`` (= ``tg.end_date``) within those bounds.

    Both inputs PUT to ``/data/knobs/window``; the route accepts
    ``window_start=<ISO>`` and/or ``window_end=<ISO>`` (either or
    both). The "reset" button sends ``reset=1`` to snap back to the
    default (last 90 days from today).
    """
    common_attrs = (
        'hx-put="/data/knobs/window" '
        'hx-target="#data-knob-window" '
        'hx-swap="outerHTML"'
    )
    return (
        f'<form id="data-knob-window" class="data-knob data-knob-window">'
        f'<span class="data-knob-label">window:</span>'
        f'<input type="date" name="window_start" '
        f'value="{escape(window_start.isoformat())}" '
        f'class="window-input" '
        f'aria-label="Window start date" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<span class="window-sep">→</span>'
        f'<input type="date" name="window_end" '
        f'value="{escape(window_end.isoformat())}" '
        f'class="window-input" '
        f'aria-label="Window end date" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<button type="button" class="window-reset" '
        f'title="Reset to last 90 days from today" '
        f"{common_attrs} "
        f"hx-vals='{{\"reset\": \"1\"}}'>last 90 days</button>"
        f"</form>"
    )


def _render_up_to_strip(
    up_to: date,
    window_start: date,
    window_end: date,
) -> str:
    """X.4.h.3 — render the "up to" scrub-head day-stepper.

    UI: ``[←] [date input] [→] [snap to end]``. ``←`` / ``→`` step
    ±1 day (clamped to the window); the date input commits on change;
    "snap to end" sets up_to = window_end (the most-data position).

    The cache stores up_to as ``tg.end_date``; the renderer always
    receives a concrete date (None resolves to window_end before
    arriving here).

    Wired with HTMX: each control PUTs to ``/data/knobs/end_date``
    (kept the legacy URL — internal-only rename to "up_to" in the UI;
    the generator field stays ``end_date``). Server-side delta
    handler clamps results to ``[window_start, window_end]``.
    """
    iso = up_to.isoformat()
    common_attrs = (
        'hx-put="/data/knobs/end_date" '
        'hx-target="#data-knob-end-date" '
        'hx-swap="outerHTML"'
    )
    snap_payload = f'{{"end_date": "{escape(window_end.isoformat())}"}}'
    return (
        f'<form id="data-knob-end-date" class="data-knob data-knob-end-date">'
        f'<span class="data-knob-label">up to:</span>'
        f'<button type="button" class="end-date-step" '
        f'title="Step back 1 day (within window)" '
        f"{common_attrs} "
        f"hx-vals='{{\"delta\": \"-1\"}}'>←</button>"
        f'<input type="date" name="end_date" value="{escape(iso)}" '
        f'min="{escape(window_start.isoformat())}" '
        f'max="{escape(window_end.isoformat())}" '
        f'class="end-date-input" '
        f'aria-label="Pick simulation cutoff date" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<button type="button" class="end-date-step" '
        f'title="Step forward 1 day (within window)" '
        f"{common_attrs} "
        f"hx-vals='{{\"delta\": \"1\"}}'>→</button>"
        f'<button type="button" class="end-date-reset" '
        f'title="Snap to window end ({escape(window_end.isoformat())})" '
        f"{common_attrs} "
        f"hx-vals='{snap_payload}'>snap to end</button>"
        f'<span class="end-date-current" '
        f'aria-label="Current up_to">{escape(iso)}</span>'
        f"</form>"
    )


def _render_seed_strip(selected: int | None) -> str:
    """X.4.h.4 — render the random-seed input + roll/clear buttons.

    UI: ``[number input] [roll] [clear]``. The number input commits
    on change; "roll" asks the server for a fresh random uint32 and
    pins it; "clear" resets to None (the locked-default sentinel —
    generator side falls back to ``_BASELINE_BASE_SEED = 42``).

    Wired with HTMX: the input PUTs ``seed=<int>``; "roll" sends
    ``roll=1`` (server picks the random value, returns the rendered
    strip with the new value showing); "clear" sends ``seed=`` (empty
    string → clear to None — same form-encoding the date stepper uses
    for its "today" reset).

    The trailing chip surfaces the current value so the operator
    sees the cached state at a glance even when typing into the input.
    """
    val_str = str(selected) if selected is not None else ""
    pretty = str(selected) if selected is not None else "(default)"
    common_attrs = (
        'hx-put="/data/knobs/seed" '
        'hx-target="#data-knob-seed" '
        'hx-swap="outerHTML"'
    )
    return (
        f'<form id="data-knob-seed" class="data-knob data-knob-seed">'
        f'<span class="data-knob-label">seed:</span>'
        f'<input type="number" name="seed" value="{escape(val_str)}" '
        f'min="0" max="4294967295" '
        f'class="seed-input" '
        f'aria-label="Pin a random seed (uint32)" '
        f'placeholder="(default)" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<button type="button" class="seed-roll" '
        f'title="Pick a fresh random seed" '
        f"{common_attrs} "
        f"hx-vals='{{\"roll\": \"1\"}}'>roll</button>"
        f'<button type="button" class="seed-clear" '
        f'title="Clear to default (None ⇒ locked _BASELINE_BASE_SEED)" '
        f"{common_attrs} "
        f"hx-vals='{{\"seed\": \"\"}}'>clear</button>"
        f'<span class="seed-current" '
        f'aria-label="Current seed">{escape(pretty)}</span>'
        f"</form>"
    )


def _render_scope_strip(selected: ScopeKind) -> str:
    """X.4.h.5 — render the scope-selector radio group.

    UI: three radio buttons (one per ``ScopeKind``). The cached value
    renders pre-selected; clicking another radio PUTs ``scope=<value>``
    via HTMX. The full descriptive hover hint (lifted from the deploy
    pipeline's per-scope docstrings) sits in the ``title=`` attribute
    so the operator can hover-discover the difference between
    ``full`` / ``uncovered_rails`` / ``exceptions_only`` without
    bouncing back to the SPEC.

    Each radio's ``hx-trigger="change"`` is what the form-encoded
    ``scope`` field carries — the input's own value, no hx-vals
    needed (unlike the date-stepper deltas / seed-roll which need a
    second key in the payload).
    """
    common_attrs = (
        'hx-put="/data/knobs/scope" '
        'hx-target="#data-knob-scope" '
        'hx-swap="outerHTML" '
        'hx-trigger="change"'
    )
    items: list[str] = []
    for value, short, hint in _SCOPE_LABELS:
        checked = "checked " if value == selected else ""
        items.append(
            f'<label class="scope-radio" title="{escape(hint)}">'
            f'<input type="radio" name="scope" value="{escape(value)}" '
            f'{checked}{common_attrs}/>'
            f' {escape(short)}'
            f"</label>"
        )
    body = "".join(items)
    return (
        f'<form id="data-knob-scope" class="data-knob data-knob-scope">'
        f'<span class="data-knob-label">scope:</span>'
        f"{body}"
        f"</form>"
    )


def _render_only_template_strip(selected: str | None) -> str:
    """X.4.i.3 — text input for ``cfg.test_generator.only_template``.

    Wires ``cfg.test_generator.scope = "only_template"`` to a concrete
    TransferTemplate name. Operator types the template name into the
    input; commit-on-change PUTs the value. Empty string clears to None
    (which the only_template scope arm rejects with a loud-fail at
    deploy time — surfacing the "you selected only_template but haven't
    picked one" footgun loudly is intentional).
    """
    val_str = selected if selected is not None else ""
    pretty = selected if selected else "(none)"
    common_attrs = (
        'hx-put="/data/knobs/only_template" '
        'hx-target="#data-knob-only-template" '
        'hx-swap="outerHTML"'
    )
    return (
        f'<form id="data-knob-only-template" '
        f'class="data-knob data-knob-only-template">'
        f'<span class="data-knob-label">only_template:</span>'
        f'<input type="text" name="only_template" '
        f'value="{escape(val_str)}" '
        f'class="only-template-input" '
        f'aria-label="TransferTemplate name to scope to" '
        f'placeholder="(none — required for scope=only_template)" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<span class="only-template-current" '
        f'aria-label="Current only_template">{escape(pretty)}</span>'
        f"</form>"
    )


def _render_derive_balances_strip(
    enabled: bool, roles: tuple[str, ...] | None,
) -> str:
    """X.4.i.3 — derive_balances flag + per-account-role narrowing.

    UI: checkbox + read-only chip showing the active role set. The
    role list is operator-configurable per L2 in cfg.yaml, but Studio
    today surfaces just the on/off toggle — the narrowing field stays
    yaml-driven (rarely tweaked per-deploy, and editing it in the
    panel would crowd the chrome strip). When the toggle is on, the
    chip shows the resolved role set so the trainer sees what's
    being derived; when off, the chip is empty.
    """
    checked = "checked " if enabled else ""
    if enabled:
        if roles is None:
            chip = "control accounts (default)"
        else:
            chip = ", ".join(roles)
    else:
        chip = "(disabled)"
    common_attrs = (
        'hx-put="/data/knobs/derive_balances" '
        'hx-target="#data-knob-derive-balances" '
        'hx-swap="outerHTML" '
        'hx-trigger="change"'
    )
    return (
        f'<form id="data-knob-derive-balances" '
        f'class="data-knob data-knob-derive-balances">'
        f'<label class="data-knob-label">'
        f'<input type="checkbox" name="enabled" '
        f'{checked}{common_attrs}/>'
        f' derive_balances'
        f"</label>"
        f'<span class="derive-balances-current" '
        f'title="Account roles being derived; configure per-L2 via '
        f'test_generator.derive_balances_account_roles" '
        f'aria-label="Derive scope">{escape(chip)}</span>'
        f"</form>"
    )


_PLANT_KIND_ABBRV: tuple[tuple[PlantKind, str], ...] = (
    # 2-3 char abbreviation for the per-day chip — keeps the column
    # visually scannable when 6+ plant kinds land on the same day.
    ("drift", "DR"),
    ("overdraft", "OD"),
    ("limit_breach", "LB"),
    ("stuck_pending", "SP"),
    ("stuck_unbundled", "SU"),
    ("supersession", "SS"),
)
_PLANT_KIND_LABELS: Mapping[PlantKind, str] = {
    kind: label for kind, label in _PLANT_LABELS
}


def _render_timeline_section(
    instance: object,
    tg_cache: TestGeneratorCache | None,
) -> str:
    """X.4.h.6.b/c — render the vertical plant-timeline column.

    Dense-render every day in the trainer's scenario window
    (``[window_start, window_end]``, oldest→newest). Days
    ``<= up_to`` are "data" days — show chips for plants that hit
    them. Days ``> up_to`` are "future" — dimmed, no chips, but still
    clickable to advance the scrub head. The ``up_to`` row carries
    ``.timeline-day--anchor`` + auto-scrolls into view on render.

    Returns the entire ``<section id="data-timeline">`` block so the
    HTMX refresh (``hx-get="/data/timeline"`` triggered by every knob
    PUT via ``HX-Trigger: trainer-knobs-changed``) can swap it as one
    unit with ``hx-swap="outerHTML"``.

    Each row carries ``hx-put`` to ``/data/knobs/end_date`` so a click
    jumps up_to there (without touching the window). The PUT then fires
    the same ``trainer-knobs-changed`` trigger which re-renders the
    timeline (closing the loop).

    ``tg_cache=None`` ⇒ renders against TestGeneratorConfig() defaults
    + a default last-90-days window; that's the unit-test page-shell
    surface that omits Studio's knob mutation routes.
    """
    if tg_cache is not None:
        effective_tg = tg_cache.get()
        window_start, window_end = tg_cache.get_window()
        up_to = tg_cache.get_up_to()
    else:
        from quicksight_gen.common.config import (  # noqa: PLC0415
            TestGeneratorConfig,
        )
        effective_tg = TestGeneratorConfig()
        window_end = date.today()  # typing-smell: ignore[no-datetime-now]: trainer-mode page-shell default — wall-clock today is the operator-friendly anchor for "last 90 days"; not a determinism path
        window_start = window_end - timedelta(
            days=DEFAULT_BASELINE_WINDOW_DAYS - 1,
        )
        up_to = window_end

    # Anchor the plant projection on window_end, NOT up_to. Plants
    # stay at fixed calendar positions while the scrub head slides
    # within the window — the trainer's mental model is "scenario is
    # fixed, I'm choosing how far through it to view". Without this,
    # `default_scenario_for(today=up_to)` would shift every plant
    # backward as up_to moves earlier, which the trainer experiences
    # as "plants move backwards when I click an earlier day" — the
    # bug the user reported.
    #
    # KNOWN MISMATCH: Deploy still anchors at tg.end_date (= up_to)
    # via deploy_pipeline.py. Until Deploy gets a separate (anchor,
    # cutoff) split, the dashboards Deploy emits will NOT match this
    # preview when up_to < window_end. Tracking as a follow-up — the
    # generator needs an "anchor at window_end, truncate emission at
    # cutoff" mode for full end-to-end alignment.
    import dataclasses as _dc  # noqa: PLC0415

    plant_projection_tg = _dc.replace(
        effective_tg, end_date=window_end,
    )
    sparse_timeline = compute_plant_timeline(instance, plant_projection_tg)  # type: ignore[arg-type]: instance shape from L2InstanceCache.get is Any-ish, but compute_plant_timeline narrows internally
    hits_by_day: dict[date, tuple[PlantHit, ...]] = {
        td.day: td.hits for td in sparse_timeline
    }

    # Dense window: window_start … window_end inclusive.
    n_days = (window_end - window_start).days + 1
    window_days: list[date] = [
        window_start + timedelta(days=i) for i in range(n_days)
    ]
    n_data_days = sum(1 for d in window_days if d <= up_to)
    n_future_days = n_days - n_data_days

    # Header: total plants across the FULL window — what's "available"
    # in the scenario, regardless of where the scrub head sits. The
    # trainer needs to know "12 plants are in this window" so they
    # know what they can scrub forward to find. Filtering by up_to
    # would shrink/grow the count as they click around, which is
    # disorienting.
    kind_counts = hits_by_kind(sparse_timeline)
    total = sum(kind_counts.values())
    n_hit_days = len(sparse_timeline)
    if effective_tg.scope == "uncovered_rails":
        kind_summary = "(scope=uncovered_rails ⇒ no plants emitted)"
    elif total == 0:
        kind_summary = "(no plants in current scenario)"
    else:
        kind_summary = " · ".join(
            f"{_PLANT_KIND_LABELS.get(k, k)} {n}"
            for k, n in kind_counts.items()
        )
    header_html = (
        f'<header class="timeline-header">'
        f'<span class="timeline-total">{total} '
        f'plant{"" if total == 1 else "s"} across '
        f'{n_hit_days} day{"" if n_hit_days == 1 else "s"} '
        f'<span class="timeline-window-note">'
        f"(window: {escape(window_start.isoformat())} → "
        f"{escape(window_end.isoformat())} · "
        f"{n_data_days} day{'' if n_data_days == 1 else 's'} of data, "
        f"{n_future_days} future)"
        f"</span></span>"
        f'<span class="timeline-kinds">{escape(kind_summary)}</span>'
        f"</header>"
    )

    rows: list[str] = []
    put_attrs = (
        'hx-put="/data/knobs/end_date" '
        'hx-target="#data-knob-end-date" '
        'hx-swap="outerHTML"'
    )
    for day in window_days:
        iso = day.isoformat()
        is_anchor = day == up_to
        is_future = day > up_to
        hits = hits_by_day.get(day, ())
        # Per-day chips: ALWAYS render at their calendar position so
        # the trainer sees the full plant set across the window —
        # answers "what can I scrub to?" without depending on where
        # the scrub head currently sits. Plants past up_to are still
        # legitimate parts of the scenario; they just haven't been
        # emitted yet at the current cutoff.
        day_kinds: dict[PlantKind, int] = {}
        for hit in hits:
            day_kinds[hit.kind] = day_kinds.get(hit.kind, 0) + 1
        chip_html: list[str] = []
        for kind, abbrv in _PLANT_KIND_ABBRV:
            if kind not in day_kinds:
                continue
            n = day_kinds[kind]
            count_suffix = f" {n}" if n > 1 else ""
            title = _PLANT_KIND_LABELS.get(kind, kind)
            chip_html.append(
                f'<span class="timeline-chip timeline-chip--{kind}" '
                f'title="{escape(title)} ×{n}">'
                f"{escape(abbrv)}{escape(count_suffix)}"
                f"</span>"
            )
        chips = "".join(chip_html)

        classes = ["timeline-day"]
        if is_future:
            classes.append("timeline-day--future")
        elif not hits:
            classes.append("timeline-day--empty")
        if is_anchor:
            classes.append("timeline-day--anchor")
        cls_attr = " ".join(classes)
        # Anchor row gets a stable id so the JS scrollIntoView can find
        # it after every HTMX swap.
        id_attr = ' id="timeline-anchor-row"' if is_anchor else ""
        if is_anchor:
            title_text = f"up to = {iso} (current scrub head)"
        elif is_future:
            title_text = f"Click to advance up_to → {iso}"
        else:
            title_text = f"Click to rewind up_to → {iso}"
        rows.append(
            f'<button type="button" class="{cls_attr}"{id_attr} '
            f'title="{escape(title_text)}" '
            f"{put_attrs} "
            f"hx-vals='{{\"end_date\": \"{escape(iso)}\"}}'>"
            f'<span class="timeline-day-date">{escape(iso)}</span>'
            f'<span class="timeline-day-chips">{chips}</span>'
            f"</button>"
        )
    rows_html = "".join(rows)
    body = (
        f"{header_html}"
        f'<div class="timeline-rows">{rows_html}</div>'
        # Scroll the anchor row into view ONLY when it's not already
        # visible. On initial /data load the anchor is at the bottom of
        # a 90-row column so we need to scroll it in. On a click swap
        # the anchor is almost always already in view (operator clicked
        # a visible row) — scrolling would jump the viewport jarringly,
        # so skip it. The check uses the row + scroll container's
        # rects; htmx executes inline <script> in swapped fragments so
        # this runs after every render.
        f'<script>(function() {{'
        f'var a = document.getElementById("timeline-anchor-row");'
        f'if (!a) return;'
        f'var c = a.closest(".timeline-rows");'
        f'if (!c) return;'
        f'var ar = a.getBoundingClientRect();'
        f'var cr = c.getBoundingClientRect();'
        f'if (ar.top < cr.top || ar.bottom > cr.bottom) {{'
        f'  a.scrollIntoView({{block: "center", behavior: "auto"}});'
        f'}}'
        f'}})();</script>'
    )

    return (
        f'<section class="data-timeline" id="data-timeline" '
        f'aria-label="Plant timeline" '
        f'hx-get="/data/timeline" '
        f'hx-trigger="trainer-knobs-changed from:body" '
        f'hx-swap="outerHTML">'
        f"{body}"
        f"</section>"
    )


def _render_data_page(
    cache: L2InstanceCache,
    dev_log: bool,
    *,
    tg_cache: TestGeneratorCache | None = None,
    etl_hook_command: str | None = None,
    cfg: Config | None = None,
) -> str:
    """X.4.h.1 — Studio "trainer mode" data-shaping panel shell.

    Page-shell + h.2 plant-toggle + h.3 day-stepper + h.4 seed input
    + h.5 scope selector + h.6 plant-timeline column wired through
    the ``TestGeneratorCache``. Training pane stays placeholder
    here (h.9).

    ``tg_cache`` is None for the unit-test surface that exercises the
    page shell without the full studio cfg wiring; in that mode the
    plant-toggle strip renders from the SPEC default ("all kinds")
    and the PUT route is absent (h.2's tests cover both modes).

    The same Deploy button + status span the home page surfaces are
    spliced in so the trainer can re-deploy without bouncing back to
    ``/``. Listener pattern mirrors home — defines a top-level
    ``quicksightDeploy()`` JS helper bound to the button's onclick.
    """
    instance = cache.get()
    prefix = escape(cfg.deployment_name if cfg is not None else cache.path.stem)
    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)
    selected_plants = (
        tg_cache.get().plants if tg_cache is not None else ()
    )
    plants_strip = _render_plants_strip(selected_plants)
    if tg_cache is not None:
        window_start, window_end = tg_cache.get_window()
        up_to = tg_cache.get_up_to()
    else:
        # Unit-test page-shell surface — no cache wired. Materialize
        # the same defaults from_config would use so the strips render
        # something sensible (operator-friendly date pickers, not
        # blank slots that look broken).
        from datetime import timedelta as _td  # noqa: PLC0415

        window_end = date.today()  # typing-smell: ignore[no-datetime-now]: trainer-mode page-shell default — wall-clock today is the operator-friendly anchor for "last 90 days"; not a determinism path
        window_start = window_end - _td(days=DEFAULT_BASELINE_WINDOW_DAYS - 1)
        up_to = window_end
    window_strip = _render_window_strip(window_start, window_end)
    end_date_strip = _render_up_to_strip(up_to, window_start, window_end)
    selected_seed = (
        tg_cache.get().seed if tg_cache is not None else None
    )
    seed_strip = _render_seed_strip(selected_seed)
    selected_scope: ScopeKind = (
        tg_cache.get().scope if tg_cache is not None else "full"
    )
    scope_strip = _render_scope_strip(selected_scope)
    selected_only_template = (
        tg_cache.get().only_template if tg_cache is not None else None
    )
    only_template_strip = _render_only_template_strip(selected_only_template)
    derive_enabled = (
        tg_cache.get().derive_balances if tg_cache is not None else False
    )
    derive_roles = (
        tg_cache.get().derive_balances_account_roles
        if tg_cache is not None else None
    )
    derive_balances_strip = _render_derive_balances_strip(
        derive_enabled, derive_roles,
    )
    etl_hook_enabled = (
        tg_cache.is_etl_hook_enabled() if tg_cache is not None else True
    )
    etl_hook_strip = _render_etl_hook_strip(
        etl_hook_command, etl_hook_enabled,
    )
    timeline_section = _render_timeline_section(instance, tg_cache)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio · data — {prefix}</title>
  {devlog_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram.css")}">
  <link rel="stylesheet" href="{asset_url("data.css")}">
  {devlog_script}</head>
<body class="data-page">
  <header class="studio-header">
    <h1>Studio · data shaping</h1>
    <span class="instance">{prefix}</span>
    <a class="nav-link" href="/">← landing</a>
    <a class="nav-link" href="/diagram">→ diagram</a>
    <a class="nav-link" href="/dashboards">→ dashboards</a>
    <button id="deploy-btn" class="deploy-btn" type="button"
            onclick="quicksightDeploy()">Deploy changes</button>
    <span id="deploy-status" class="deploy-status" aria-live="polite"></span>
  </header>
  <script>
    // X.4.h.1 — Deploy button mirrors the home page's. POSTs /deploy,
    // swaps the deploy-status span to reflect the result.
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

  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <div class="data-knobs" id="data-knobs">
    {etl_hook_strip}
    {scope_strip}
    {only_template_strip}
    {derive_balances_strip}
    {window_strip}
    {end_date_strip}
    {seed_strip}
    {plants_strip}
  </div>

  <main class="data-main">
    {timeline_section}
    <section class="data-training" id="data-training" aria-label="Training pane">
      <p class="data-empty">training pane lands in X.4.h.9</p>
    </section>
  </main>
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
    tg_cache: TestGeneratorCache | None = None,
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
            schema prefix; usually omitted (defaults to ``cfg.db_table_prefix``).
            When ``cfg`` is also None and the override is omitted, the
            coverage route's prefix-resolve raises — that combination
            is only valid for the unit-test surface that doesn't mount
            the coverage route (``db_pool=None``).
        cfg: Full Config dataclass; required for the X.4.g.13
            ``POST /deploy`` route (the deploy pipeline reads
            ``cfg.etl_hook`` / ``cfg.etl_datasource`` /
            ``cfg.test_generator`` plus DB connection knobs).
            None ⇒ POST /deploy is silently omitted (unit-test
            surface that doesn't exercise the pipeline).
    """
    async def landing(_request: Request) -> HTMLResponse:
        return HTMLResponse(_render_home_page(cache, dev_log, cfg=cfg))

    async def data(request: Request) -> HTMLResponse:
        # X.4.h.url — read URL query params into the cache so a
        # bookmarked / reloaded /data?... restores trainer state.
        # Absent params leave the cache alone (so a bare /data still
        # picks up wherever the operator left off in this session).
        if tg_cache is not None:
            _apply_state_url_to_cache(request, tg_cache)
        etl_hook_command = cfg.etl_hook if cfg is not None else None
        return HTMLResponse(_render_data_page(
            cache, dev_log,
            tg_cache=tg_cache,
            etl_hook_command=etl_hook_command,
            cfg=cfg,
        ))

    async def data_timeline(_request: Request) -> HTMLResponse:
        """X.4.h.6.c — refresh just the timeline section.

        Triggered by HTMX when any knob PUT response carries
        ``HX-Trigger: trainer-knobs-changed`` (the ``hx-trigger`` on
        the timeline section listens via ``from:body``). Returns the
        full ``<section id="data-timeline">`` block; the section's
        own ``hx-swap="outerHTML"`` swaps it.
        """
        return HTMLResponse(_render_timeline_section(cache.get(), tg_cache))

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
                cfg=cfg,
            ),
        )

    routes: list[Route | Mount] = [
        Route("/", landing, methods=["GET"]),
        Route("/data", data, methods=["GET"]),
        Route("/data/timeline", data_timeline, methods=["GET"]),
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

    # X.4.h.2 — plant-toggle PUT route. Mounted only when the
    # TestGeneratorCache is wired (which Studio CLI always provides;
    # the unit-test surface that omits tg_cache also omits this route,
    # which is correct — without the cache there's nothing to mutate).
    if tg_cache is not None:
        bound_tg = tg_cache

        async def put_plants(request: Request) -> HTMLResponse:
            from typing import cast as _cast  # noqa: PLC0415
            form = await request.form()
            # The form serializes only checked checkboxes (HTML form
            # default — `unchecked` boxes don't appear in the payload),
            # so the incoming list IS the new selection. Filter to
            # known PlantKind values to ignore any junk a curl test
            # might send; bad values silently drop rather than 500.
            known: set[PlantKind] = {kind for kind, _ in _PLANT_LABELS}
            new_plants_set: set[PlantKind] = set()
            for raw in form.getlist("plant"):
                if isinstance(raw, str) and raw in known:
                    new_plants_set.add(_cast(PlantKind, raw))
            new_plants = tuple(
                kind for kind, _ in _PLANT_LABELS if kind in new_plants_set
            )
            bound_tg.update(plants=new_plants)
            return HTMLResponse(
                _render_plants_strip(new_plants),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(Route("/data/knobs/plants", put_plants, methods=["PUT"]))

        async def put_end_date(request: Request) -> HTMLResponse:
            """X.4.h.3 — apply a delta or absolute date to the up_to knob.

            "up_to" is the simulation cutoff (= ``tg.end_date`` in the
            generator's vocabulary). Always clamped to the current
            scenario window so the trainer can't accidentally scrub
            outside its bounds. Window changes don't touch up_to —
            the trainer redefines bounds, the next click re-anchors.

            Form contract:
                - ``delta=<int>`` → step relative to current up_to
                  (clamped to ``[window_start, window_end]``).
                - ``end_date=<YYYY-MM-DD>`` → set absolute (clamped).
                  Empty string snaps to ``window_end`` (the most-data
                  position).
                - Both present: ``delta`` wins (defensive).
                - Invalid date string: silently drop (cache holds prior).
            """
            form = await request.form()
            window_start, window_end = bound_tg.get_window()
            current = bound_tg.get_up_to()

            delta_raw = form.get("delta")
            new_up_to: date = current
            if isinstance(delta_raw, str) and delta_raw.strip():
                try:
                    delta_days = int(delta_raw)
                except ValueError:
                    delta_days = 0
                if delta_days:
                    new_up_to = current + timedelta(days=delta_days)
            else:
                date_raw = form.get("end_date")
                if isinstance(date_raw, str):
                    if date_raw == "":
                        # Snap to window_end (the canonical "most data"
                        # position). Stored as window_end explicitly so
                        # subsequent reads stay stable even if the
                        # window shifts.
                        new_up_to = window_end
                    else:
                        try:
                            new_up_to = date.fromisoformat(date_raw)
                        except ValueError:
                            new_up_to = current  # silent drop
            # Clamp to window bounds.
            if new_up_to < window_start:
                new_up_to = window_start
            elif new_up_to > window_end:
                new_up_to = window_end
            bound_tg.update(end_date=new_up_to)
            return HTMLResponse(
                _render_up_to_strip(new_up_to, window_start, window_end),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route("/data/knobs/end_date", put_end_date, methods=["PUT"]),
        )

        async def put_window(request: Request) -> HTMLResponse:
            """X.4.h.3.window — set the trainer's scenario window.

            Form contract:
                - ``reset=1`` → snap to last 90 days from today.
                - ``window_start=<YYYY-MM-DD>`` and/or
                  ``window_end=<YYYY-MM-DD>`` → set either bound;
                  the other is preserved. Invalid ISO silently drops.
                - Window-end < window-start: ``update_window`` swaps
                  them (preserves intent over rejection).

            Window changes do NOT touch up_to. If the new window
            excludes the current up_to, the renderer + the next
            put_end_date call will clamp.
            """
            form = await request.form()
            cur_start, cur_end = bound_tg.get_window()

            reset_raw = form.get("reset")
            if isinstance(reset_raw, str) and reset_raw.strip():
                end = date.today()  # typing-smell: ignore[no-datetime-now]: trainer-mode reset — wall-clock today is the "last 90 days" anchor; not a determinism path
                start = end - timedelta(days=DEFAULT_BASELINE_WINDOW_DAYS - 1)
                bound_tg.update_window(start=start, end=end)
            else:
                new_start: date | object = cur_start
                new_end: date | object = cur_end
                start_raw = form.get("window_start")
                end_raw = form.get("window_end")
                if isinstance(start_raw, str) and start_raw:
                    try:
                        new_start = date.fromisoformat(start_raw)
                    except ValueError:
                        pass  # silent drop
                if isinstance(end_raw, str) and end_raw:
                    try:
                        new_end = date.fromisoformat(end_raw)
                    except ValueError:
                        pass  # silent drop
                bound_tg.update_window(start=new_start, end=new_end)
            new_window_start, new_window_end = bound_tg.get_window()
            return HTMLResponse(
                _render_window_strip(new_window_start, new_window_end),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route("/data/knobs/window", put_window, methods=["PUT"]),
        )

        async def put_seed(request: Request) -> HTMLResponse:
            """X.4.h.4 — set / roll / clear the random-seed knob.

            Form contract:
                - ``roll=1`` → server picks a fresh ``random.randint(0,
                  2**32 - 1)`` and pins it. Wins over ``seed=`` when
                  both present (defensive — UI never sends both).
                - ``seed=<int>`` → set absolute value. Empty string
                  clears to None ("clear" reset).
                - Invalid int (non-digit string): silently drop —
                  same posture as the date stepper / plant toggle.
            """
            import random  # noqa: PLC0415

            form = await request.form()
            current = bound_tg.get().seed
            new_seed: int | None = current

            roll_raw = form.get("roll")
            if isinstance(roll_raw, str) and roll_raw.strip():
                # uint32 range matches QS_GEN_FUZZ_SEED's contract
                # (CLAUDE.md: "runner rolls a fresh random uint32 per
                # invocation"). Trainer-mode UI is not a determinism
                # path, so an unseeded random call is honest here.
                new_seed = random.randint(0, 2**32 - 1)
            else:
                seed_raw = form.get("seed")
                if isinstance(seed_raw, str):
                    if seed_raw == "":
                        new_seed = None
                    else:
                        try:
                            new_seed = int(seed_raw)
                        except ValueError:
                            new_seed = current  # silent drop
            bound_tg.update(seed=new_seed)
            return HTMLResponse(
                _render_seed_strip(new_seed),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route("/data/knobs/seed", put_seed, methods=["PUT"]),
        )

        async def put_scope(request: Request) -> HTMLResponse:
            """X.4.h.5 — set the test_generator.scope knob.

            Form contract:
                - ``scope=<full|uncovered_rails|exceptions_only>`` →
                  set absolute value.
                - Unknown / missing scope: silently keep current
                  cached value — same posture as the other knobs.

            No "clear" payload — scope has no None sentinel; the
            generator's default is ``"full"``, set explicitly via
            ``TestGeneratorConfig.scope`` default.
            """
            from typing import cast as _cast  # noqa: PLC0415

            form = await request.form()
            current = bound_tg.get().scope
            new_scope: ScopeKind = current

            scope_raw = form.get("scope")
            known: set[ScopeKind] = {value for value, _, _ in _SCOPE_LABELS}
            if isinstance(scope_raw, str) and scope_raw in known:
                new_scope = _cast(ScopeKind, scope_raw)
            bound_tg.update(scope=new_scope)
            return HTMLResponse(
                _render_scope_strip(new_scope),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route("/data/knobs/scope", put_scope, methods=["PUT"]),
        )

        async def put_etl_hook(request: Request) -> HTMLResponse:
            """X.4.h.etl-toggle — flip the etl_hook enable/disable knob.

            Form contract:
                - ``enabled=on`` (HTML form default for checked
                  checkboxes) → enable. Absence → disable.

            The toggle is meaningful even when ``cfg.etl_hook is None``
            (the renderer surfaces it as disabled + "(not configured)"),
            but the cache flag is still respected — Deploy ignores it
            because the cfg field is None either way.
            """
            form = await request.form()
            enabled_raw = form.get("enabled")
            new_enabled = (
                isinstance(enabled_raw, str) and enabled_raw == "on"
            )
            bound_tg.set_etl_hook_enabled(new_enabled)
            etl_hook_command = (
                cfg.etl_hook if cfg is not None else None
            )
            return HTMLResponse(
                _render_etl_hook_strip(etl_hook_command, new_enabled),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route("/data/knobs/etl_hook", put_etl_hook, methods=["PUT"]),
        )

        async def put_only_template(request: Request) -> HTMLResponse:
            """X.4.i.3 — set the test_generator.only_template knob.

            Form contract:
                - ``only_template=<name>`` → set the template name.
                - ``only_template=`` (empty string) → clear to None.

            No validation against the L2's actual TransferTemplates
            here — the deploy-time `_only_template_rails` lookup
            loud-fails with the declared list when the operator typed
            a name that doesn't exist. UI is forgiving so the
            in-progress trainer can hold the cfg in an inconsistent
            state without each keystroke kicking back an error.
            """
            form = await request.form()
            raw = form.get("only_template")
            new_value: str | None = None
            if isinstance(raw, str):
                stripped = raw.strip()
                new_value = stripped if stripped else None
            bound_tg.update_only_template(new_value)
            return HTMLResponse(
                _render_only_template_strip(new_value),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route(
                "/data/knobs/only_template",
                put_only_template,
                methods=["PUT"],
            ),
        )

        async def put_derive_balances(
            request: Request,
        ) -> HTMLResponse:
            """X.4.i.3 — flip the test_generator.derive_balances flag.

            Form contract:
                - ``enabled=on`` (HTML form default for checked
                  checkboxes) → enable.
                - Absence → disable.

            The role-narrowing field
            (``derive_balances_account_roles``) stays cfg-yaml-only —
            edited per-L2, not per-deploy. Surface in the chip so the
            operator sees what's currently in scope.
            """
            form = await request.form()
            enabled_raw = form.get("enabled")
            new_enabled = (
                isinstance(enabled_raw, str) and enabled_raw == "on"
            )
            bound_tg.update_derive_balances(new_enabled)
            roles = (
                bound_tg.get().derive_balances_account_roles
            )
            return HTMLResponse(
                _render_derive_balances_strip(new_enabled, roles),
                headers={
                    "HX-Trigger": "trainer-knobs-changed",
                    "HX-Push-Url": _build_state_url(bound_tg),
                },
            )

        routes.append(
            Route(
                "/data/knobs/derive_balances",
                put_derive_balances,
                methods=["PUT"],
            ),
        )

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
            # Z.C — prefix resolution order:
            # 1) explicit prefix_override (operator wires per-call)
            # 2) cfg.db_table_prefix (cfg-bound studio session)
            # 3) cache.path.stem (yaml file basename — fallback for
            #    studio sessions wired without a cfg, e.g. unit tests)
            prefix = (
                bound_prefix_override
                or (cfg.db_table_prefix if cfg is not None else None)
                or cache.path.stem
            )
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
        # X.4.h.2 — if a TestGeneratorCache is wired, patch each deploy
        # invocation with the latest knob state. Absent cache (unit
        # surface) ⇒ deploy reads the startup-time cfg.test_generator
        # unchanged, preserving today's behavior.
        bound_tg_for_deploy = tg_cache

        async def deploy(_request: Request) -> JSONResponse:
            instance = cache.get()
            effective_cfg = (
                bound_tg_for_deploy.patched_config(bound_cfg)
                if bound_tg_for_deploy is not None
                else bound_cfg
            )
            summary = await run_deploy_pipeline(
                effective_cfg, instance, dev_log=None,
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
