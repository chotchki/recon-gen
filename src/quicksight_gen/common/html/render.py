"""X.2.spike — HTML renderer for tree ``Sheet`` objects.

spike.1 produces a static page from a ``Sheet`` node. spike.2 layers
HTMX swap + d3 hydration on top: the page shell pulls HTMX + d3 +
d3-sankey from CDNs, a date-range form at the top of the page posts
to each visual's data endpoint on change, and a bootstrap script
hydrates ``data-visual-kind`` fragments after each swap.

Visual rendering shape:

    <section data-visual-kind="<kind>" data-visual-id="<id>">
      <h2>{title}</h2>
      <p class="subtitle">{subtitle}</p>
      <div id="visual-data-<id>" class="visual-data">
        <!-- HTMX swap target; server emits <script type="application/json">
             holding the chart data, bootstrap JS hydrates via d3 -->
      </div>
    </section>

The hydration contract for spike.2 is:

- Server renders ``<script type="application/json" class="chart-data">
  {…d3-sankey shape…}</script>`` inside the ``visual-data-<id>`` div.
- After ``htmx:afterSwap``, the bootstrap script walks any newly-
  inserted ``[data-visual-kind]`` and dispatches by kind.

spike.2 supports ``Sankey`` only — adding more kinds is one ``case``
arm in the bootstrap. spike.3 brings the harness's Layer 2 against
this surface; spike.4 is the decision gate.
"""

from __future__ import annotations

import html
import json
from typing import Any

from quicksight_gen.common.tree._helpers import _AutoSentinel
from quicksight_gen.common.tree.structure import App, Sheet


_HTMX_SRC = "https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js"
_D3_SRC = "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"
_D3_SANKEY_SRC = (
    "https://cdn.jsdelivr.net/npm/d3-sankey@0.12.3/dist/d3-sankey.min.js"
)


# Bootstrap JS — runs on initial page load AND after every HTMX swap.
# Hydration model:
#
#   <section data-visual-kind="Sankey" data-visual-id="X">
#     <div id="visual-data-X" class="visual-data">  ← HTMX swap target
#       <script type="application/json" class="chart-data">{...}</script>
#     </div>
#   </section>
#
# After swap, ``evt.detail.target`` is the .visual-data div. Walk UP
# to its enclosing ``[data-visual-kind]`` section and dispatch by kind.
# The script tag with the JSON payload sits inside the swap target.
#
# Currently supports ``Sankey`` only (spike.2 scope). New kinds add
# one ``case`` arm.
#
# The ``htmx:afterSwap`` event is the X.4 future-proofing hook — that
# phase's swap-on-edit pattern reuses this exact dispatch.
_BOOTSTRAP_JS = """\
(function() {
  function hydrateSection(section) {
    var dataScript = section.querySelector('script.chart-data');
    if (!dataScript) return;
    var kind = section.getAttribute('data-visual-kind');
    var data;
    try { data = JSON.parse(dataScript.textContent); }
    catch (e) { console.error('bad chart data', e); return; }
    var target = section.querySelector('.visual-data');
    if (!target) return;
    target.querySelectorAll('svg').forEach(function(s) { s.remove(); });
    switch (kind) {
      case 'Sankey':
        renderSankey(target, data);
        break;
      default:
        console.warn('no hydrator for kind', kind);
    }
  }

  function hydrate(root) {
    // Handle both initial-load (root = body, scan inside) and
    // post-swap (root = .visual-data div, walk up to section) cases.
    if (root.matches && root.matches('[data-visual-kind]')) {
      hydrateSection(root);
      return;
    }
    var section = root.closest && root.closest('[data-visual-kind]');
    if (section) {
      hydrateSection(section);
      return;
    }
    if (root.querySelectorAll) {
      root.querySelectorAll('[data-visual-kind]').forEach(hydrateSection);
    }
  }

  function renderSankey(target, data) {
    var width = target.clientWidth || 800;
    var height = 400;
    var svg = d3.select(target).append('svg')
      .attr('width', width).attr('height', height);
    var sankey = d3.sankey()
      .nodeWidth(15).nodePadding(10)
      .extent([[1, 1], [width - 1, height - 6]]);
    var graph = sankey({
      nodes: data.nodes.map(function(d) { return Object.assign({}, d); }),
      links: data.links.map(function(d) { return Object.assign({}, d); }),
    });
    svg.append('g').selectAll('rect')
      .data(graph.nodes).enter().append('rect')
      .attr('x', function(d) { return d.x0; })
      .attr('y', function(d) { return d.y0; })
      .attr('height', function(d) { return d.y1 - d.y0; })
      .attr('width', function(d) { return d.x1 - d.x0; })
      .attr('fill', '#4682b4');
    svg.append('g').attr('fill', 'none')
      .selectAll('path').data(graph.links).enter().append('path')
      .attr('d', d3.sankeyLinkHorizontal())
      .attr('stroke', '#999').attr('stroke-opacity', 0.4)
      .attr('stroke-width', function(d) { return Math.max(1, d.width); });
    svg.append('g').selectAll('text')
      .data(graph.nodes).enter().append('text')
      .attr('x', function(d) { return d.x0 < width / 2 ? d.x1 + 6 : d.x0 - 6; })
      .attr('y', function(d) { return (d.y1 + d.y0) / 2; })
      .attr('dy', '0.35em')
      .attr('text-anchor', function(d) {
        return d.x0 < width / 2 ? 'start' : 'end';
      })
      .text(function(d) { return d.name; })
      .style('font', '11px sans-serif');
  }

  document.addEventListener('htmx:afterSwap', function(evt) {
    hydrate(evt.detail.target);
  });
  document.addEventListener('DOMContentLoaded', function() {
    hydrate(document.body);
  });
})();
"""


_PAGE_SHELL = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <script src="{htmx_src}"></script>
  <script src="{d3_src}"></script>
  <script src="{d3_sankey_src}"></script>
</head>
<body>
{body}
  <script>{bootstrap_js}</script>
</body>
</html>
"""


# Form template — emits a date-range filter at the top of the page.
# ``hx-post`` targets each visual's data endpoint. For spike.2 with
# one visual (Money Trail Sankey), this fires one POST per change;
# multi-visual pages would need ``hx-include`` or an
# ``htmx:configRequest`` hook to fan out the filter values.
def _render_filter_form(visual_ids: list[str]) -> str:
    parts = ['  <form id="filter-form">']
    parts.append('    <label>From <input type="date" name="date_from"></label>')
    parts.append('    <label>To <input type="date" name="date_to"></label>')
    for vid in visual_ids:
        # One Refresh button per visual. Triggered on click (button's
        # default trigger). Date-input ``change`` was tried on the
        # button via ``from:#filter-form`` but didn't fire reliably
        # in the spike; click is the floor — phase.1 can layer auto-
        # refresh-on-change via SSE or a less-clever trigger config.
        # ``hx-include="#filter-form"`` collects the date inputs.
        # ``hx-indicator`` visualises the request — a CSS rule on
        # ``.htmx-request`` would normally style the button while the
        # POST is in flight; without one, browsers still show their
        # native pending-request indicator.
        esc = html.escape(vid)
        parts.append(
            f'    <button type="button"'
            f' hx-post="/visual/{esc}/data"'
            f' hx-target="#visual-data-{esc}"'
            f' hx-include="#filter-form">'
            f'Refresh</button>'
        )
    parts.append('  </form>')
    return "\n".join(parts)


def emit_html(app: App, sheet: Sheet) -> str:
    """Render a tree ``Sheet`` as a standalone HTML page.

    spike.2 scope: page shell pulls HTMX + d3 + d3-sankey, emits a
    date-range form at the top of the body that posts to each
    visual's data endpoint on change, plus one ``<section>`` per
    visual carrying its title, subtitle, and the swap-target div.

    Takes both the App and the Sheet so internal-id resolution
    (``app.resolve_auto_ids()``) can run. The Sheet alone has no
    parent ref — without the App we'd emit ``data-visual-id=
    "_AutoSentinel.AUTO"`` for any visual constructed with the
    standard ``visual_id=AUTO`` default. The hx-post URLs key off
    ``data-visual-id``, so unresolved IDs would silently break the
    swap dispatch.

    The renderer still never touches disk — the App + Sheet are
    in-memory tree objects. X.4's stateful editor stays unblocked.

    Args:
        app: tree ``App`` node owning the analysis the sheet lives
            in. ``app.resolve_auto_ids()`` is called before render
            (idempotent).
        sheet: tree ``Sheet`` node. Must be one of
            ``app.analysis.sheets``; raises ``ValueError`` if not.

    Returns:
        A complete, well-formed HTML document as a string. Title +
        body content are HTML-escaped at the leaf level.
    """
    if app.analysis is None or sheet not in app.analysis.sheets:
        raise ValueError(
            f"Sheet {sheet.name!r} is not part of App {app.name!r}'s "
            f"analysis — emit_html needs the owning App to resolve "
            f"internal IDs (visual_id, control_id, etc.)."
        )
    app.resolve_auto_ids()

    body_parts: list[str] = [f"  <h1>{html.escape(sheet.title)}</h1>"]
    if sheet.description:
        body_parts.append(f"  <p>{html.escape(sheet.description)}</p>")
    visual_ids = [str(getattr(v, "visual_id", "")) for v in sheet.visuals]
    body_parts.append(_render_filter_form(visual_ids))
    for visual in sheet.visuals:
        body_parts.append(_render_visual(visual))
    return _PAGE_SHELL.format(
        title=html.escape(sheet.title),
        body="\n".join(body_parts),
        htmx_src=_HTMX_SRC,
        d3_src=_D3_SRC,
        d3_sankey_src=_D3_SANKEY_SRC,
        bootstrap_js=_BOOTSTRAP_JS,
    )


def emit_visual_data_fragment(visual_id: str, data: Any) -> str:
    """Server-side fragment HTMX swaps into ``#visual-data-<visual_id>``.

    Carries the d3-shaped chart data as a ``<script
    type="application/json" class="chart-data">``. The bootstrap JS
    finds the script after swap, parses, and dispatches to the
    right d3 renderer by ``data-visual-kind`` on the parent
    section.

    The fragment is the script tag ALONE (no wrapper div). HTMX's
    default ``hx-swap="innerHTML"`` drops it inside the
    ``visual-data-<id>`` placeholder — wrapping in another div with
    the same id would create duplicate IDs after the swap.

    The ``visual_id`` argument is currently used only for log /
    debug context (it doesn't appear in the rendered fragment),
    but kept in the signature so future callers can attach it as
    a ``data-`` attribute on the script tag if needed.

    JSON serialization uses ``json.dumps`` — caller is responsible
    for shaping the payload (d3-sankey wants
    ``{"nodes": [...], "links": [...]}``; a future TimeSeries kind
    would shape differently).
    """
    del visual_id  # reserved for future debug/diagnostic use
    payload = json.dumps(data)
    return (
        f'<script type="application/json" class="chart-data">{payload}</script>'
    )


def _render_visual(visual: Any) -> str:
    """Render one visual as an HTML ``<section>``.

    spike.2: title + optional subtitle + swap-target div tagged
    with the visual's class name (``data-visual-kind``) so the
    bootstrap script dispatches d3 hydration per kind. The inner
    div carries ``id="visual-data-<visual_id>"`` so HTMX's
    ``hx-target`` selector matches.

    Visuals satisfy ``VisualLike`` (Protocol) — they all carry
    ``title`` and most carry ``subtitle``. Read attributes
    defensively via ``getattr`` so the renderer works against any
    future ``VisualLike`` subtype without per-kind branching here.
    """
    title = getattr(visual, "title", "(untitled)")
    subtitle = getattr(visual, "subtitle", None)
    kind = type(visual).__name__
    raw_visual_id = getattr(visual, "visual_id", "")
    assert not isinstance(raw_visual_id, _AutoSentinel), (
        f"visual_id wasn't resolved on {kind} {title!r} — "
        f"emit_html should have called app.resolve_auto_ids() before "
        f"this point."
    )
    visual_id = str(raw_visual_id)
    esc_id = html.escape(visual_id)

    parts: list[str] = []
    parts.append(
        f'  <section data-visual-kind="{html.escape(kind)}"'
        f' data-visual-id="{esc_id}">'
    )
    parts.append(f"    <h2>{html.escape(title)}</h2>")
    if subtitle:
        parts.append(
            f'    <p class="subtitle">{html.escape(subtitle)}</p>'
        )
    parts.append(
        f'    <div id="visual-data-{esc_id}" class="visual-data">'
        f'<!-- HTMX swap target; populated by /visual/{esc_id}/data -->'
        f'</div>'
    )
    parts.append("  </section>")
    return "\n".join(parts)
