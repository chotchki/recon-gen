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
from collections.abc import Callable, Mapping
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

    ``asset_url("diagram-svg.css")`` → ``/studio/static/diagram-svg.css?cb=<boot>``
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

from recon_gen.common.config import Config, PlantKind, ScopeKind
from recon_gen.common.html._studio_assets.tw_classes import (
    chrome_button_classes,
    compact_input_classes,
    ghost_button_classes,
    knob_wrapper_classes,
    timeline_chip_base_classes,
    timeline_day_classes,
)
from recon_gen.common.db import AsyncConnectionPool
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.coverage import CoverageEntry, coverage_for
from recon_gen.common.l2.deploy_pipeline import run_deploy_pipeline
from recon_gen.common.l2.seed import DEFAULT_BASELINE_WINDOW_DAYS
from recon_gen.common.l2.tg_cache import TestGeneratorCache
from recon_gen.common.l2.trainer_timeline import (
    PlantHit,
    compute_plant_timeline,
    hits_by_kind,
)
from recon_gen.common.l2.topology import (
    build_topology_graph_per_rail,
    topology_graph_for,
    visible_entities_for,
)
from recon_gen.common.l2.trainer import plants_per_node
from recon_gen.common.sql.dialect import Dialect
from recon_gen.common.html._studio_training import render_training_pane
from recon_gen.common.html.render import _emit_theme_style


def studio_theme_head(instance: object) -> str:
    """X.4.f.13 — App2 Tailwind output.css link + L2 theme override block.

    Every studio HTML page links App2's compiled Tailwind sheet (which
    declares ``--color-accent`` / ``--color-surface`` / etc. with
    build-time defaults via ``input.css``'s ``@theme`` block) AND
    injects a per-L2-instance ``:root { --color-accent: ...; }``
    override so the studio inherits the active institution's brand
    palette. Post-AM, every chrome surface reads ``--color-*`` tokens
    directly via Tailwind utilities (`bg-accent`, `text-primary-fg`,
    etc.) — the ``--studio-*`` alias layer in the retired
    ``editor.css`` / ``data.css`` / ``diagram.css`` chrome rules is
    gone with those files. Only ``diagram-svg.css`` remains, and its
    SVG-only rules use literal hex values inside ``!important``
    overrides where Tailwind utilities can't reach SVG attribute
    selectors per AM.0 lock L4.

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
    # AI.2.c — top-level instance settings (description +
    # role_business_day_offsets) as one YAML block. The attr field is a
    # placeholder; the is_set check below branches for this kind because
    # it spans two fields.
    ("instance", "Instance settings", "description"),
)


def _demo_mode_banner(demo_mode: bool) -> str:
    """AH.4 — top-of-page read-only banner for ``studio --demo-mode``.

    Cosmetic only. The load-bearing lockdown is the route-level skip of
    every mutation route (+ the sandbox-exec deny-write on the L2 yaml);
    this just tells a visitor the surface is read-only and pairs with
    the suppressed Deploy / editor-mutation affordances. Empty string
    when not in demo-mode. Inline-styled so it needs no stylesheet /
    Tailwind-utility rebuild.
    """
    if not demo_mode:
        return ""
    return (
        '<div class="demo-banner" role="status" '
        'style="background:#fff3cd;border-bottom:1px solid #ffe69c;'
        'color:#664d03;padding:0.6rem 1rem;font-size:0.9rem;text-align:center">'
        "<strong>Read-only demo</strong> — editing and deploy are disabled. "
        '<a href="https://chotchki.github.io/recon-gen/" target="_blank" '
        'rel="noopener" style="color:#664d03;text-decoration:underline">'
        "Learn more</a>."
        "</div>"
    )


def _render_home_page(
    cache: L2InstanceCache, dev_log: bool, *, cfg: Config | None = None,
    demo_mode: bool = False, top_nav_html: str = "",
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
        # AH.4 — hide the create affordance in demo-mode (the /new route
        # is 404'd there anyway; the button shouldn't tease it).
        # AM.2 step 2: section chrome migrated. `.home-section` /
        # `.home-section-add` / `.home-section-link` / `.home-section-body`
        # / `.home-section-loading` semantic classes retired in favor
        # of raw Tailwind utilities. `data-kind` stays as the JS hook
        # the home-page focus-filter listener uses.
        add_link = "" if demo_mode else (
            f'<a class="ml-2 text-accent no-underline font-semibold text-sm hover:underline" '
            f'href="/l2_shape/{kind}/new" '
            # Stop the click from triggering the surrounding <details>
            # toggle. The browser still follows the href to the create page.
            f'onclick="event.stopPropagation()" '
            f'title="Create a new {escape(kind)}">+ Add</a>'
        )
        section_blocks.append(
            f'<details class="bg-white border border-surface-border '
            f'rounded-md mb-3 overflow-hidden" '
            f'data-kind="{escape(kind)}"{open_attr}>'
            f'<summary class="cursor-pointer px-4 py-2 font-semibold '
            f'text-accent bg-surface-bg select-none hover:bg-link-tint">'
            f"{escape(label)} "
            f'<span class="text-xs text-secondary-fg font-normal">({n})</span> '
            f"{add_link}"
            f'<a class="ml-2 text-accent no-underline font-normal text-sm hover:underline" '
            f'href="/l2_shape/{kind}/" '
            f'onclick="event.stopPropagation()" '
            f'title="Open in dedicated page">↗</a>'
            f"</summary>"
            f'<div id="{body_id}" '
            f'hx-get="/l2_shape/{kind}/?embed=1" '
            f'hx-trigger="load, l2-cascade-reload from:body" '
            f'hx-swap="innerHTML">'
            f'<p class="p-4 text-secondary-fg italic m-0">loading…</p>'
            f"</div>"
            f"</details>"
        )
    # X.4.f.12 — singleton sections at the bottom of the home page
    # (cosmetic / less-frequently-edited than the entity collections).
    # No list, no +Add — just an Edit link to the singleton form.
    for kind, label, attr in _HOME_SINGLETONS:
        if kind == "instance":
            # AI.2.c — two-field singleton: "set" when EITHER top-level
            # field is populated.
            is_set = (
                getattr(instance, "description", None) is not None
                or getattr(instance, "role_business_day_offsets", None)
                is not None
            )
        else:
            is_set = getattr(instance, attr, None) is not None
        status = "set" if is_set else "not set"
        # AH.4 — demo-mode hides the singleton Edit affordance (its form
        # route is 404'd) and drops the "click Edit" prompt.
        # BF.7+BF.8 (2026-05-25): theme + persona are structured forms
        # now (per-field controls, not yaml blocks). Only `instance`
        # is still a single YAML block (two top-level scalars don't
        # warrant a decomposed form). Title + body reflect the
        # actual editor surface so the home-page prose doesn't lie.
        singleton_form_kind = (
            "structured form" if kind in ("theme", "persona")
            else "single YAML block"
        )
        singleton_link = "" if demo_mode else (
            f'<a class="ml-2 text-accent no-underline font-semibold text-sm hover:underline" '
            f'href="/l2_shape/{kind}/" '
            f'onclick="event.stopPropagation()" '
            f'title="Edit {escape(label)} ({singleton_form_kind})">Edit</a>'
        )
        singleton_body = (
            f"{escape(label)} is a {singleton_form_kind}."
            if demo_mode else
            f"{escape(label)} is a {singleton_form_kind} — "
            f"click <strong>Edit</strong> to view / change it."
        )
        section_blocks.append(
            f'<details class="bg-white border border-surface-border '
            f'rounded-md mb-3 overflow-hidden" '
            f'data-kind="{escape(kind)}">'
            f'<summary class="cursor-pointer px-4 py-2 font-semibold '
            f'text-accent bg-surface-bg select-none hover:bg-link-tint">'
            f"{escape(label)} "
            f'<span class="text-xs text-secondary-fg font-normal">({escape(status)})</span> '
            f"{singleton_link}"
            f"</summary>"
            f"<div>"
            f'<p class="p-4 text-secondary-fg italic m-0">{singleton_body}</p>'
            f"</div>"
            f"</details>"
        )
    sections_html = "\n    ".join(section_blocks)
    demo_banner = _demo_mode_banner(demo_mode)
    deploy_controls = "" if demo_mode else (
        '<button id="deploy-btn" class="ml-auto bg-accent text-accent-fg border '
        'border-accent px-3 py-1 rounded-sm cursor-pointer text-sm '
        'hover:opacity-85 disabled:opacity-60 disabled:cursor-not-allowed" '
        'type="button"\n'
        '            onclick="quicksightDeploy()">Deploy changes</button>\n'
        '    <span id="deploy-status" class="text-xs text-secondary-fg" '
        'aria-live="polite"></span>'
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio — {prefix}</title>
  {devlog_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram-svg.css")}">
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
      // AM.2 step 2 (2026-05-25): scope by the stable [data-kind]
      // [data-entity-id] attribute pair (semantic — encodes app
      // domain, not styling). Use the native HTML `hidden` attribute
      // instead of an `is-hidden-by-focus` class toggle so the
      // browser's accessibility tree picks up the visibility change
      // automatically (assistive tech reads `hidden`).
      var cards = document.querySelectorAll(
        '#home-entities [data-kind][data-entity-id]'
      );
      cards.forEach(function(card) {{
        var hide = false;
        if (lastVisibleByKind !== null) {{
          var k = card.dataset.kind;
          var id = card.dataset.entityId;
          var ids = lastVisibleByKind[k];
          hide = !ids || ids.indexOf(id) === -1;
        }}
        card.hidden = hide;
      }});
      // Per-section "(N shown)" indicator update.
      document.querySelectorAll('details[data-kind]').forEach(function(d) {{
        var entities = d.querySelectorAll('[data-kind][data-entity-id]');
        var total = entities.length;
        var visible = 0;
        entities.forEach(function(e) {{ if (!e.hidden) visible += 1; }});
        var summary = d.querySelector('summary');
        if (!summary) return;
        var ind = summary.querySelector('[data-role="focus-indicator"]');
        if (lastVisibleByKind === null || total === 0 || visible === total) {{
          if (ind) ind.remove();
        }} else {{
          if (!ind) {{
            ind = document.createElement('span');
            ind.dataset.role = 'focus-indicator';
            ind.className = 'text-secondary-fg font-normal text-sm';
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
      // AM.2 step 2 (2026-05-25): detect by the stable
      // `data-focus-node` attribute (semantic — what the JS
      // actually reads next) instead of the `.entity-card-title`
      // marker class. `data-focus-node` is set on the read-card
      // h3 by `_render_read_card` precisely so the focus-on-click
      // affordance has a target; checking for it here means the
      // class is no longer load-bearing.
      var el = target;
      while (el && el !== document.body) {{
        if (el.dataset && el.dataset.focusNode) {{
          _focusDiagramOnNode(el.dataset.focusNode);
          return true;
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
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {demo_banner}
  <header class="flex items-center gap-4 px-4 py-2 border-b border-surface-border bg-white shrink-0">
    <h1>Studio</h1>
    <span class="text-sm text-secondary-fg font-mono">{prefix}</span>
    <!-- BS.3 part 3 (2026-05-29): shared top-nav injected before this
         page-local header. Per-page navigation links live in
         {top_nav_html} above; the page-local header now only carries
         the Studio title + prefix + deploy controls. -->
    {deploy_controls}
  </header>
  <script>
    // X.4.g.14 — Studio "Deploy changes" button. POSTs /deploy, swaps
    // the deploy-status span to reflect the result.
    function quicksightDeploy() {{
      var btn = document.getElementById('deploy-btn');
      var status = document.getElementById('deploy-status');
      btn.disabled = true;
      status.className = 'text-xs text-secondary-fg';
      status.dataset.state = 'running';
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
            status.className = 'text-xs text-warning font-semibold';
            status.dataset.state = 'halted';
            status.textContent = 'Halted: ' + result.body.halt_reason;
          }} else if (result.ok) {{
            var s3 = result.body.step3_generator;
            status.className = 'text-xs text-success';
            status.dataset.state = 'ok';
            status.textContent = (
              'Deployed (gen ' + result.body.step5_data_generation_id +
              ', ' + s3.transactions_after + ' tx)'
            );
          }} else {{
            status.className = 'text-xs text-danger font-semibold';
            status.dataset.state = 'error';
            status.textContent = 'Failed: HTTP ' + result.status;
          }}
        }})
        .catch(function(err) {{
          btn.disabled = false;
          status.className = 'text-xs text-danger font-semibold';
          status.dataset.state = 'error';
          status.textContent = 'Failed: ' + (err && err.message || err);
        }});
    }}
  </script>

  <section class="border-b border-surface-border bg-white h-[50vh] min-h-96">
    <iframe id="diagram-frame" src="/diagram?layer=1&amp;embed=1"
            title="L2 topology diagram" class="w-full h-full border-0 block"></iframe>
  </section>

  <section class="px-4 pt-2 pb-8" id="home-entities">
    {sections_html}
  </section>
</body>
</html>
"""


# -- BT.1 — /studio/etl landing page ----------------------------------------


# Per BT.0.5 design mockup §5 navigation flow: 3-card index, one per
# child page + one-line description. Title + summary + "coming in BT.N"
# hint pulled from the mockup doc; the cards link to the eventual route
# targets so when BT.2/3/4 land they go live automatically — no edit
# back to this list. Order: Probe (investigate) → Run (execute) →
# Triage (find + fix) — matches the operator's natural flow per the
# mockup's narrative.
_ETL_LANDING_CARDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "Probe",
        "/etl/probe",
        "BT.2",
        "Investigate one L2 slice — pick a rail, template, or chain "
        "and see L2-declared column expectations side-by-side with "
        "the runtime rows that match.",
    ),
    (
        "Run",
        "/etl/run",
        "BT.3",
        "Execute the ETL pipeline (wipe → hook → matview refresh) and "
        "render a per-kind coverage tally so you can confirm every "
        "declared primitive landed at least one row.",
    ),
    (
        "Triage",
        "/etl/triage",
        "BT.4",
        "Find + fix gaps — diff declared contracts against observed "
        "runtime; each gap renders a card with the diagnosis + a deep "
        "link to the relevant L2 editor page.",
    ),
)


def _render_etl_landing_page(
    cache: L2InstanceCache,
    dev_log: bool,
    *,
    cfg: Config | None = None,
    demo_mode: bool = False,
    top_nav_html: str = "",
) -> str:
    """BT.1 — ``/studio/etl`` landing page.

    3-card index of the Phase BT ETL Support surfaces per the BT.0.5
    design mockup (§5 cross-page navigation flow). Mirrors the home
    page's chrome (top-nav + Studio header) so the operator's mental
    model stays consistent across `/`, `/data`, `/diagram`, `/etl/`.

    Each card links to its target sub-route. BT.2/3/4 land those
    routes — until then, a click 404s; the "coming in BT.N" hint on
    the card primes the operator that the destination isn't ready.

    Demo-mode keeps the same surface (operators reading a deployed
    dashboards-only instance see the same landing if Studio is
    enabled; no destructive affordance on this page to gate). The
    sub-pages will make their own demo-mode decisions when they land.
    """
    instance = cache.get()
    prefix = escape(cfg.deployment_name if cfg is not None else cache.path.stem)
    devlog_meta, devlog_script = _dev_log_head_snippets(dev_log)
    demo_banner = _demo_mode_banner(demo_mode)

    card_blocks: list[str] = []
    for title, href, phase, description in _ETL_LANDING_CARDS:
        card_blocks.append(
            '<a class="block p-5 bg-white border border-surface-border '
            'rounded-md shadow-sm hover:border-accent hover:shadow-md '
            'transition-shadow no-underline text-primary-fg" '
            f'href="{escape(href)}">'
            f'<h2 class="text-xl font-semibold text-accent m-0 mb-1">{escape(title)}</h2>'
            f'<p class="text-xs text-secondary-fg font-mono m-0 mb-2">'
            f'{escape(href)} · coming in {escape(phase)}</p>'
            f'<p class="text-sm text-primary-fg m-0">{escape(description)}</p>'
            "</a>"
        )
    cards_html = "\n    ".join(card_blocks)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio · ETL Support — {prefix}</title>
  {devlog_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram-svg.css")}">
  {devlog_script}</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {demo_banner}
  <header class="flex items-center gap-4 px-4 py-2 border-b border-surface-border bg-white shrink-0">
    <h1>Studio · ETL Support</h1>
    <span class="text-sm text-secondary-fg font-mono">{prefix}</span>
  </header>
  <section class="px-8 pt-6 pb-3">
    <p class="text-sm text-secondary-fg max-w-3xl m-0">
      Three workflows for getting your customer's ETL feed landing
      cleanly: <strong>Probe</strong> one slice, <strong>Run</strong>
      the pipeline + score, <strong>Triage</strong> gaps + link back
      to the L2 editor to close them.
    </p>
  </section>
  <section class="px-8 pb-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3" id="etl-landing-cards">
    {cards_html}
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
    demo_mode: bool = False,
    top_nav_html: str = "",
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
    # Build URL fragments so layer / focus / clear-focus links
    # preserve the other params, INCLUDING ``embed=1`` (2026-05-25
    # user dogfood: clicking a layer link inside the home-page
    # iframe was dropping the embed flag, so the iframe re-rendered
    # with the standalone diagram's full studio chrome stacked
    # below the home page's own chrome → two nav bars).
    def _qs(*, layer_val: int, focus_val: str | None) -> str:
        bits: list[str] = [f"layer={layer_val}"]
        if focus_val:
            bits.append(f"focus={escape(focus_val)}")
        if embed:
            bits.append("embed=1")
        return "?" + "&".join(bits)

    # AM.2 step 3 — chrome buttons share the chrome_button_classes()
    # helper. Active variant overrides the hover state with a solid
    # accent fill so the current layer is visually pinned.
    btn_base = chrome_button_classes()
    btn_active = f"{btn_base} bg-accent text-accent-fg border-accent hover:bg-accent hover:text-accent-fg"
    layer_links = " ".join(
        f'<a class="{btn_active if n == layer else btn_base}" '
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
    toggle_label_cls = "inline-flex items-center gap-1 cursor-pointer text-sm text-primary-fg"
    coverage_toggle_html = (
        f'<label class="{toggle_label_cls}">'
        '<input type="checkbox" id="toggle-coverage">'
        ' Coverage'
        '</label>'
        if coverage_available
        else ""
    )
    # X.4.c.6 — Trainer toggle. Always available (pure scenario walk).
    # Off by default; on overlays per-plant-kind badges per node.
    trainer_toggle_html = (
        f'<label class="{toggle_label_cls}">'
        '<input type="checkbox" id="toggle-trainer">'
        ' Trainer'
        '</label>'
    )

    # Focus indicator + clear link. Visible only when ?focus= is set.
    # Clear preserves the current layer.
    if focus_node_id is not None:
        focus_indicator = (
            f'<span class="inline-flex items-center gap-1 ml-2 text-sm '
            f'text-secondary-fg">focused: '
            f'<code class="font-mono text-primary-fg">{escape(focus_node_id)}</code> '
            f'<a class="{chrome_button_classes()}" '
            f'href="{_qs(layer_val=layer, focus_val=None)}">clear</a></span>'
        )
    else:
        focus_indicator = ""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio diagram — {prefix}</title>
  {devlog_meta}{coverage_meta}{trainer_meta}{studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram-svg.css")}">
  {devlog_script}</head>
<body class="{"flex flex-col m-0 p-0 font-sans bg-surface-bg text-primary-fg h-screen" if embed else "flex flex-col font-sans bg-surface-bg text-primary-fg h-screen"}">
  {top_nav_html}
  {_demo_mode_banner(demo_mode and not embed)}
  {("" if embed else (
    '<header class="flex items-center gap-4 px-4 py-2 border-b border-surface-border bg-white shrink-0">'
    f'<h1>Studio · diagram</h1>'
    f'<span class="text-sm text-secondary-fg font-mono">{prefix}</span>'
    # BS.3 part 3 (2026-05-29): shared top-nav now injected above this
    # page-local header (when not in iframe-embed mode). Per-page
    # cross-links live in {top_nav_html}; the page-local header carries
    # only the diagram title + prefix.
    '</header>'
  ))}

  <div class="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-surface-border bg-white">
    <span class="inline-flex items-center gap-2 text-sm text-secondary-fg" aria-label="Conceptual layers">
      layer: {layer_links}
    </span>
    <a id="toggle-reset" class="{chrome_button_classes()}" href="{"?embed=1" if embed else "?"}">Reset</a>
    {coverage_toggle_html}
    {trainer_toggle_html}
    {focus_indicator}
    <span class="text-xs text-secondary-fg ml-auto" id="diagram-status">loading…</span>
  </div>

  <div class="flex flex-wrap items-center gap-3 px-4 py-2 border-b border-surface-border bg-white">
    <strong class="font-semibold text-sm text-primary-fg mr-1">Show:</strong>
    <label class="{toggle_label_cls}">
      <input type="checkbox" id="toggle-role-internal" checked>
      Internal roles <span class="text-xs text-secondary-fg font-normal">({n_role_internal})</span>
    </label>
    <label class="{toggle_label_cls}">
      <input type="checkbox" id="toggle-role-external" checked>
      External roles <span class="text-xs text-secondary-fg font-normal">({n_role_external})</span>
    </label>
    <label class="{toggle_label_cls}">
      <input type="checkbox" id="toggle-rail" checked>
      Rails <span class="text-xs text-secondary-fg font-normal">({n_rail})</span>
    </label>
    <label class="{toggle_label_cls}">
      <input type="checkbox" id="toggle-template" checked>
      Templates <span class="text-xs text-secondary-fg font-normal">({n_template})</span>
    </label>
    <label class="{toggle_label_cls}">
      <input type="checkbox" id="toggle-chain" checked>
      Chains <span class="text-xs text-secondary-fg font-normal">({n_chain})</span>
    </label>
    <label class="{toggle_label_cls}">
      <input type="checkbox" id="toggle-control_parent" checked>
      Control hierarchy <span class="text-xs text-secondary-fg font-normal">({n_control_parent})</span>
    </label>
    <strong class="font-semibold text-sm text-primary-fg mr-1">Edge labels:</strong>
    <label class="{toggle_label_cls}">
      <input type="checkbox" id="toggle-edge-label-rail_bundle" checked>
      Bundles <span class="text-xs text-secondary-fg font-normal">({n_bundle})</span>
    </label>
    <label class="{toggle_label_cls}">
      <input type="checkbox" id="toggle-edge-label-self_loop" checked>
      Self-loops <span class="text-xs text-secondary-fg font-normal">({n_self_loop})</span>
    </label>
    <label class="{toggle_label_cls}">
      <input type="checkbox" id="toggle-edge-label-chain" checked>
      Chain badges <span class="text-xs text-secondary-fg font-normal">({n_chain})</span>
    </label>
    <label class="{toggle_label_cls}">
      <input type="checkbox" id="toggle-edge-label-control_parent" checked>
      Control labels
    </label>
  </div>

  <!-- AM.2 step 3 fix-up (2026-05-25): viewport needs `flex` +
       `min-h-0` (without min-h-0 a flex child falls back to its
       intrinsic content size — graphviz's native SVG pixel size —
       and the diagram overflows the iframe). `#diagram-target`
       likewise needs its own `flex-1 min-h-0 min-w-0` so the
       injected SVG can fit to the viewBox via preserveAspectRatio. -->
  <div class="flex flex-1 min-h-0 overflow-hidden p-4 bg-white">
    <div id="diagram-target" class="flex-1 min-h-0 min-w-0"></div>
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
    _window = tg_cache.get_window()
    window_start, window_end = _window.start, _window.end
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
        from recon_gen.common.config import (  # noqa: PLC0415
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
            # BF.1.S2: `if p in known` narrows `p` to `PlantKind` since
            # `known: set[PlantKind]`; previous `_cast(PlantKind, p)` was
            # redundant — pyright would now flag it.
            picked = tuple(
                p
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
            f'<label class="inline-flex items-center gap-1 cursor-pointer text-sm text-primary-fg">'
            f'<input type="checkbox" name="plant" value="{kind}" {checked}/>'
            f' {escape(label)}'
            f"</label>"
        )
    body = "".join(items)
    return (
        f'<form id="data-knob-plants" class="{knob_wrapper_classes()} flex-wrap" '
        f'hx-put="/data/knobs/plants" '
        f'hx-trigger="change" '
        f'hx-target="#data-knob-plants" '
        f'hx-swap="outerHTML">'
        f'<span class="font-mono text-sm text-secondary-fg">plants:</span>'
        f"{body}"
        f"</form>"
    )


def _render_etl_hook_strip(
    command: str | None,
    enabled: bool,
) -> str:
    """X.4.h.etl-toggle — render the etl-hook enable/disable strip.

    Surfaces ``cfg.etl_hook`` (the shell command). The toggle disables
    the hook for the next Deploy without erasing the cfg field — flip
    back on later to restore it. BS.4 (2026-05-29) dropped the legacy
    ``etl_datasource`` half of the pair; the etl_hook is the sole ETL
    contract now (writes directly to demo_db, no upstream copy).

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
    code_base = "font-mono text-xs px-2 py-0.5 rounded-sm bg-surface-bg"
    if command is None:
        cmd_class = f"{code_base} text-secondary-fg italic"
        body = (
            '<input type="checkbox" disabled '
            'aria-label="etl_hook (not configured)"/>'
            f'<code class="{cmd_class}">(not configured)</code>'
        )
    else:
        checked = "checked " if enabled else ""
        cmd_class = (
            f"{code_base} text-primary-fg"
            if enabled
            else f"{code_base} text-secondary-fg line-through"
        )
        body = (
            f'<input type="checkbox" name="enabled" value="on" '
            f'{checked}'
            f'aria-label="Run etl_hook on next deploy" '
            f"{common_attrs}/>"
            f'<code class="{cmd_class}" title="{escape(command)}">'
            f"{escape(command)}</code>"
        )
    return (
        f'<form id="data-knob-etl-hook" class="{knob_wrapper_classes()}">'
        f'<span class="font-mono text-sm text-secondary-fg">etl hook:</span>'
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
    input_cls = compact_input_classes()
    return (
        f'<form id="data-knob-window" class="{knob_wrapper_classes()}">'
        f'<span class="font-mono text-sm text-secondary-fg">window:</span>'
        f'<input type="date" name="window_start" '
        f'value="{escape(window_start.isoformat())}" '
        f'class="{input_cls}" '
        f'aria-label="Window start date" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<span class="text-secondary-fg">→</span>'
        f'<input type="date" name="window_end" '
        f'value="{escape(window_end.isoformat())}" '
        f'class="{input_cls}" '
        f'aria-label="Window end date" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<button type="button" class="{ghost_button_classes()}" '
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
    btn_cls = ghost_button_classes()
    input_cls = compact_input_classes()
    return (
        f'<form id="data-knob-end-date" class="{knob_wrapper_classes()}">'
        f'<span class="font-mono text-sm text-secondary-fg">up to:</span>'
        f'<button type="button" class="{btn_cls}" '
        f'title="Step back 1 day (within window)" '
        f"{common_attrs} "
        f"hx-vals='{{\"delta\": \"-1\"}}'>←</button>"
        f'<input type="date" name="end_date" value="{escape(iso)}" '
        f'min="{escape(window_start.isoformat())}" '
        f'max="{escape(window_end.isoformat())}" '
        f'class="{input_cls}" '
        f'aria-label="Pick simulation cutoff date" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<button type="button" class="{btn_cls}" '
        f'title="Step forward 1 day (within window)" '
        f"{common_attrs} "
        f"hx-vals='{{\"delta\": \"1\"}}'>→</button>"
        f'<button type="button" class="{btn_cls}" '
        f'title="Snap to window end ({escape(window_end.isoformat())})" '
        f"{common_attrs} "
        f"hx-vals='{snap_payload}'>snap to end</button>"
        f'<span class="font-mono text-xs text-primary-fg tabular-nums" '
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
    btn_cls = ghost_button_classes()
    input_cls = compact_input_classes()
    roll_btn_cls = f"{btn_cls} border-accent"
    return (
        f'<form id="data-knob-seed" class="{knob_wrapper_classes()}">'
        f'<span class="font-mono text-sm text-secondary-fg">seed:</span>'
        f'<input type="number" name="seed" value="{escape(val_str)}" '
        f'min="0" max="4294967295" '
        f'class="{input_cls} w-[12ch] tabular-nums" '
        f'aria-label="Pin a random seed (uint32)" '
        f'placeholder="(default)" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<button type="button" class="{roll_btn_cls}" '
        f'title="Pick a fresh random seed" '
        f"{common_attrs} "
        f"hx-vals='{{\"roll\": \"1\"}}'>roll</button>"
        f'<button type="button" class="{btn_cls}" '
        f'title="Clear to default (None ⇒ locked _BASELINE_BASE_SEED)" '
        f"{common_attrs} "
        f"hx-vals='{{\"seed\": \"\"}}'>clear</button>"
        f'<span class="font-mono text-xs text-primary-fg tabular-nums" '
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
    radio_label_cls = "inline-flex items-center gap-1 cursor-pointer text-sm text-primary-fg"
    for value, short, hint in _SCOPE_LABELS:
        checked = "checked " if value == selected else ""
        items.append(
            f'<label class="{radio_label_cls}" title="{escape(hint)}">'
            f'<input type="radio" name="scope" value="{escape(value)}" '
            f'{checked}{common_attrs}/>'
            f' {escape(short)}'
            f"</label>"
        )
    body = "".join(items)
    return (
        f'<form id="data-knob-scope" class="{knob_wrapper_classes()} flex-wrap">'
        f'<span class="font-mono text-sm text-secondary-fg">scope:</span>'
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
        f'class="{knob_wrapper_classes()}">'
        f'<span class="font-mono text-sm text-secondary-fg">only_template:</span>'
        f'<input type="text" name="only_template" '
        f'value="{escape(val_str)}" '
        f'class="{compact_input_classes()} w-64" '
        f'aria-label="TransferTemplate name to scope to" '
        f'placeholder="(none — required for scope=only_template)" '
        f'hx-trigger="change" '
        f"{common_attrs}/>"
        f'<span class="font-mono text-xs text-primary-fg" '
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
        f'class="{knob_wrapper_classes()}">'
        f'<label class="inline-flex items-center gap-1 cursor-pointer font-mono text-sm text-primary-fg">'
        f'<input type="checkbox" name="enabled" '
        f'{checked}{common_attrs}/>'
        f' derive_balances'
        f"</label>"
        f'<span class="text-xs text-secondary-fg italic" '
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
        _window = tg_cache.get_window()
        window_start, window_end = _window.start, _window.end
        up_to = tg_cache.get_up_to()
    else:
        from recon_gen.common.config import (  # noqa: PLC0415
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
        f'<header class="flex flex-col gap-1 px-3 py-2 border-b border-surface-border bg-surface-bg">'
        f'<span class="text-sm font-semibold text-primary-fg">{total} '
        f'plant{"" if total == 1 else "s"} across '
        f'{n_hit_days} day{"" if n_hit_days == 1 else "s"} '
        f'<span class="text-xs text-secondary-fg font-normal">'
        f"(window: {escape(window_start.isoformat())} → "
        f"{escape(window_end.isoformat())} · "
        f"{n_data_days} day{'' if n_data_days == 1 else 's'} of data, "
        f"{n_future_days} future)"
        f"</span></span>"
        f'<span class="text-xs text-secondary-fg">{escape(kind_summary)}</span>'
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
        chip_base = timeline_chip_base_classes()
        chip_kind_variants: dict[PlantKind, str] = {
            "drift": " bg-accent/12 text-accent border-accent/25",
            "overdraft": " bg-danger/12 text-danger border-danger/25",
            "limit_breach": " bg-danger/12 text-danger border-danger/25",
            "stuck_pending": " bg-warning/12 text-warning border-warning/25",
            "stuck_unbundled": " bg-warning/12 text-warning border-warning/25",
            "supersession": " bg-success/12 text-success border-success/25",
        }
        chip_html: list[str] = []
        for kind, abbrv in _PLANT_KIND_ABBRV:
            if kind not in day_kinds:
                continue
            n = day_kinds[kind]
            count_suffix = f" {n}" if n > 1 else ""
            title = _PLANT_KIND_LABELS.get(kind, kind)
            variant = chip_kind_variants.get(kind, "")
            chip_html.append(
                f'<span class="{chip_base}{variant}" '
                f'title="{escape(title)} ×{n}">'
                f"{escape(abbrv)}{escape(count_suffix)}"
                f"</span>"
            )
        chips = "".join(chip_html)

        cls_attr = timeline_day_classes()
        if is_future:
            cls_attr += " py-px px-2 border-transparent text-secondary-fg"
        elif not hits:
            cls_attr += " py-px px-2 border-transparent text-secondary-fg"
        if is_anchor:
            cls_attr += (
                " border-accent border-2 px-1.5 py-1.5 bg-accent/6 "
                "font-semibold relative hover:bg-accent/10"
            )
        # Anchor row gets a stable id so the JS scrollIntoView can find
        # it after every HTMX swap.
        id_attr = ' id="timeline-anchor-row"' if is_anchor else ""
        if is_anchor:
            title_text = f"up to = {iso} (current scrub head)"
        elif is_future:
            title_text = f"Click to advance up_to → {iso}"
        else:
            title_text = f"Click to rewind up_to → {iso}"
        # AM.2 step 4: data-role + data-state attributes give tests a
        # stable, styling-free hook for "is this a timeline-day row?"
        # + "is this row the anchor / future / data?"
        if is_anchor:
            day_state = "anchor"
        elif is_future:
            day_state = "future"
        elif not hits:
            day_state = "empty"
        else:
            day_state = "data"
        rows.append(
            f'<button type="button" data-role="timeline-day" '
            f'data-state="{day_state}" class="{cls_attr}"{id_attr} '
            f'title="{escape(title_text)}" '
            f"{put_attrs} "
            f"hx-vals='{{\"end_date\": \"{escape(iso)}\"}}'>"
            f'<span class="font-mono text-xs tabular-nums shrink-0">{escape(iso)}</span>'
            f'<span class="flex flex-wrap items-center gap-1">{chips}</span>'
            f"</button>"
        )
    rows_html = "".join(rows)
    body = (
        f"{header_html}"
        f'<div class="flex flex-col gap-1 p-2 overflow-y-auto max-h-[60vh]">{rows_html}</div>'
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
        # AM.2 step 4 (2026-05-25): scroll-container detection now
        # walks to the parent .flex.flex-col wrapper via parentElement
        # since the `.timeline-rows` semantic class was retired.
        f'var c = a.parentElement;'
        f'if (!c) return;'
        f'var ar = a.getBoundingClientRect();'
        f'var cr = c.getBoundingClientRect();'
        f'if (ar.top < cr.top || ar.bottom > cr.bottom) {{'
        f'  a.scrollIntoView({{block: "center", behavior: "auto"}});'
        f'}}'
        f'}})();</script>'
    )

    return (
        f'<section class="bg-white border border-surface-border rounded-md '
        f'overflow-hidden" id="data-timeline" '
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
    demo_mode: bool = False,
    top_nav_html: str = "",
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
        _window = tg_cache.get_window()
        window_start, window_end = _window.start, _window.end
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
    training_pane = render_training_pane()
    demo_banner = _demo_mode_banner(demo_mode)
    deploy_controls = "" if demo_mode else (
        '<button id="deploy-btn" class="ml-auto bg-accent text-accent-fg border '
        'border-accent px-3 py-1 rounded-sm cursor-pointer text-sm '
        'hover:opacity-85 disabled:opacity-60 disabled:cursor-not-allowed" '
        'type="button"\n'
        '            onclick="quicksightDeploy()">Deploy changes</button>\n'
        '    <span id="deploy-status" class="text-xs text-secondary-fg" '
        'aria-live="polite"></span>'
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio · data — {prefix}</title>
  {devlog_meta}{studio_theme_head(instance)}
  {devlog_script}</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {demo_banner}
  <header class="flex items-center gap-4 px-4 py-2 border-b border-surface-border bg-white shrink-0">
    <h1>Studio · data shaping</h1>
    <span class="text-sm text-secondary-fg font-mono">{prefix}</span>
    <!-- BS.3 part 3 (2026-05-29): shared top-nav injected above; this
         header now only carries the page title + prefix + deploy. -->
    {deploy_controls}
  </header>
  <script>
    // X.4.h.1 — Deploy button mirrors the home page's. POSTs /deploy,
    // swaps the deploy-status span to reflect the result.
    function quicksightDeploy() {{
      var btn = document.getElementById('deploy-btn');
      var status = document.getElementById('deploy-status');
      btn.disabled = true;
      status.className = 'text-xs text-secondary-fg';
      status.dataset.state = 'running';
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
            status.className = 'text-xs text-warning font-semibold';
            status.dataset.state = 'halted';
            status.textContent = 'Halted: ' + result.body.halt_reason;
          }} else if (result.ok) {{
            var s3 = result.body.step3_generator;
            status.className = 'text-xs text-success';
            status.dataset.state = 'ok';
            status.textContent = (
              'Deployed (gen ' + result.body.step5_data_generation_id +
              ', ' + s3.transactions_after + ' tx)'
            );
          }} else {{
            status.className = 'text-xs text-danger font-semibold';
            status.dataset.state = 'error';
            status.textContent = 'Failed: HTTP ' + result.status;
          }}
        }})
        .catch(function(err) {{
          btn.disabled = false;
          status.className = 'text-xs text-danger font-semibold';
          status.dataset.state = 'error';
          status.textContent = 'Failed: ' + (err && err.message || err);
        }});
    }}
  </script>

  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <div class="flex flex-col gap-2 px-4 py-3 border-b border-surface-border bg-surface-bg" id="data-knobs">
    {etl_hook_strip}
    {scope_strip}
    {only_template_strip}
    {derive_balances_strip}
    {window_strip}
    {end_date_strip}
    {seed_strip}
    {plants_strip}
  </div>

  <main class="grid grid-cols-1 lg:[grid-template-columns:24rem_1fr] gap-4 max-w-7xl mx-auto p-4">
    {timeline_section}
    <section id="data-training" aria-label="Training pane" class="bg-white border border-surface-border rounded-md p-4 overflow-auto">
{training_pane}
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
    demo_mode: bool = False,
    top_nav_fn: Callable[[str], str] | None = None,
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
            Default False so a production-style ``recon-gen
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
            ``cfg.etl_hook`` / ``cfg.test_generator`` plus DB
            connection knobs). None ⇒ POST /deploy is silently
            omitted (unit-test surface that doesn't exercise the
            pipeline).
    """
    def _top_nav_html(active_href: str) -> str:
        """BS.3 part 3: closure-wrap top_nav_fn so handlers stay terse.

        Returns the shared top-nav HTML for the active page, or "" when
        no factory was provided (unit-test surface / dashboards-only
        embedding paths). The renderers' default ``top_nav_html=""``
        kwarg keeps the layout valid in either case.
        """
        if top_nav_fn is None:
            return ""
        return top_nav_fn(active_href)

    async def landing(_request: Request) -> HTMLResponse:
        return HTMLResponse(
            _render_home_page(
                cache, dev_log, cfg=cfg, demo_mode=demo_mode,
                top_nav_html=_top_nav_html("/"),
            ),
        )

    async def etl_landing(_request: Request) -> HTMLResponse:
        # BT.1 — /etl/ landing index. 3-card pointer to BT.2/3/4 sub-pages.
        return HTMLResponse(
            _render_etl_landing_page(
                cache, dev_log, cfg=cfg, demo_mode=demo_mode,
                top_nav_html=_top_nav_html("/etl/"),
            ),
        )

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
            demo_mode=demo_mode,
            top_nav_html=_top_nav_html("/data"),
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
        # BS.3 part 3 — embedded diagram (inside the home iframe) skips
        # the top nav too; the host page already carries it.
        nav_html = "" if embed else _top_nav_html("/diagram")
        return HTMLResponse(
            _render_diagram_page(
                cache, dev_log, focus_node_id, layer,
                coverage_available=db_pool is not None,
                embed=embed,
                cfg=cfg,
                demo_mode=demo_mode,
                top_nav_html=nav_html,
            ),
        )

    routes: list[Route | Mount] = [
        Route("/", landing, methods=["GET"]),
        Route("/data", data, methods=["GET"]),
        Route("/data/timeline", data_timeline, methods=["GET"]),
        Route("/diagram", diagram, methods=["GET"]),
        Route("/etl/", etl_landing, methods=["GET"]),
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
    #
    # AE.2.b: in `--demo-mode`, ``make_editor_routes(cache, demo_mode=True)``
    # keeps only the read-only GETs (list + read card) and strips the
    # new/edit-form GETs + POST create + PUT save + DELETE delete.
    # Visitors can browse the L2 yaml's accounts / rails / templates /
    # chains but can't mutate.
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415
        make_editor_routes,
    )
    routes.extend(make_editor_routes(cache, demo_mode=demo_mode))

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
            form = await request.form()
            # The form serializes only checked checkboxes (HTML form
            # default — `unchecked` boxes don't appear in the payload),
            # so the incoming list IS the new selection. Filter to
            # known PlantKind values to ignore any junk a curl test
            # might send; bad values silently drop rather than 500.
            # BF.1.S2: `raw in known` narrows `raw` to `PlantKind` since
            # `known: set[PlantKind]`; the previous `_cast(PlantKind, raw)`
            # call is now flagged unnecessary by pyright.
            known: set[PlantKind] = {kind for kind, _ in _PLANT_LABELS}
            new_plants_set: set[PlantKind] = set()
            for raw in form.getlist("plant"):
                if isinstance(raw, str) and raw in known:
                    new_plants_set.add(raw)
            # BF.1.S2: explicit tuple[PlantKind, ...] — without the
            # annotation pyright widens the generator's element type to
            # `str` after the `if kind in new_plants_set` membership
            # narrowing collapses against the Literal-union shape.
            new_plants: tuple[PlantKind, ...] = tuple(
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
            _window = bound_tg.get_window()
            window_start, window_end = _window.start, _window.end
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
            _cur_window = bound_tg.get_window()
            cur_start, cur_end = _cur_window.start, _cur_window.end

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
            _new_window = bound_tg.get_window()
            new_window_start, new_window_end = _new_window.start, _new_window.end
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
                # uint32 range matches RECON_GEN_FUZZ_SEED's contract
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
            form = await request.form()
            current = bound_tg.get().scope
            new_scope: ScopeKind = current

            scope_raw = form.get("scope")
            known: set[ScopeKind] = {value for value, _, _ in _SCOPE_LABELS}
            if isinstance(scope_raw, str) and scope_raw in known:
                # BF.1.S2: `scope_raw in known` narrows to ScopeKind via
                # the `set[ScopeKind]` membership test.
                new_scope = scope_raw
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

        # AE.2.b: etl_hook PUT triggers the operator's shell command
        # (cfg.etl_hook) — that's an arbitrary shell-exec surface no
        # public-demo should expose. Skip mounting in demo-mode.
        if not demo_mode:
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

        # AE.2.b: /deploy orchestrates the AWS QuickSight deploy pipeline
        # — schema apply + data apply + matview refresh + json apply
        # against the operator-supplied AWS account. No public-demo
        # should ever execute that. Skip mounting in demo-mode (the
        # sandbox-exec profile also denies the outbound network the
        # boto3 calls would need, but route-level skip is the cleaner
        # cut so the Deploy button never appears to do anything).
        if not demo_mode:
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
