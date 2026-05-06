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
from pathlib import Path
from typing import Any

from quicksight_gen.common.tree._helpers import _AutoSentinel
from quicksight_gen.common.tree.structure import App, Sheet


_HTMX_SRC = "https://unpkg.com/htmx.org@2.0.3/dist/htmx.min.js"
_D3_SRC = "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"
_D3_SANKEY_SRC = (
    "https://cdn.jsdelivr.net/npm/d3-sankey@0.12.3/dist/d3-sankey.min.js"
)

# X.2.a.1 — JS lives in standalone .js files under assets/js/ so biome
# can lint / format / minify them; render.py loads the contents at
# module-import time + inlines them into the page shell. Standalone
# files keep the JS analyzable by IDE / biome / Playwright unit tests
# without a Python string boundary in the way.
#
# Escape ``</script>`` → ``<\/script>``: any literal ``</script>`` in
# the JS (e.g. inside a comment block illustrating the HTML shape)
# would terminate the enclosing inline ``<script>`` tag at HTML-parse
# time and break execution. The backslash form parses identically as
# JS but doesn't trigger HTML's script-tag terminator.
_ASSETS_JS_DIR = Path(__file__).parent / "assets" / "js"


def _load_inline_js(name: str) -> str:
    raw = (_ASSETS_JS_DIR / name).read_text(encoding="utf-8")
    return raw.replace("</script>", "<\\/script>")


_BOOTSTRAP_JS = _load_inline_js("bootstrap.js")
_DEV_LOG_JS = _load_inline_js("dev_log.js")


_PAGE_SHELL = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
{dev_log_meta}
  <link rel="stylesheet" href="/static/output.css">
  <script src="{htmx_src}"></script>
  <script src="{d3_src}"></script>
  <script src="{d3_sankey_src}"></script>
</head>
<body class="bg-slate-50 text-slate-900 font-sans antialiased">
{body}
  <script>{bootstrap_js}</script>
  <script>{dev_log_js}</script>
</body>
</html>
"""



# Form template — emits a date-range filter at the top of the page.
# X.2.b: each Refresh button uses ``hx-get`` against the per-visual
# nested REST URL (``/dashboards/{d}/sheets/{s}/visuals/{v}/data``).
# ``hx-include="#filter-form"`` serializes form fields as the query
# string for the GET; ``hx-push-url="true"`` keeps the browser URL
# in sync with the date filter so back/forward + bookmark Just Work.
def _render_filter_form(
    visual_fetch_urls: list[tuple[str, str]],
) -> str:
    """Render the date-range form with one Refresh button per visual.

    ``visual_fetch_urls`` is a list of ``(visual_id, fetch_url)``
    tuples. Each Refresh button targets ``#visual-data-{visual_id}``
    and GETs the URL. The visual_id appears twice (target id +
    URL) — different roles, different sources of truth, so kept
    explicit instead of derived from one or the other.
    """
    form_class = (
        "flex flex-wrap items-center gap-3 mx-8 mb-6 p-4 "
        "bg-white rounded-lg shadow-sm border border-slate-200"
    )
    label_class = "flex items-center gap-2 text-sm font-medium text-slate-700"
    input_class = (
        "px-2 py-1 border border-slate-300 rounded text-sm "
        "focus:outline-none focus:ring-2 focus:ring-blue-500"
    )
    button_class = (
        "px-3 py-1 bg-blue-600 text-white text-sm font-medium "
        "rounded hover:bg-blue-700 active:bg-blue-800 "
        "transition-colors cursor-pointer"
    )
    parts = [f'  <form id="filter-form" class="{form_class}">']
    parts.append(
        f'    <label class="{label_class}">From '
        f'<input type="date" name="date_from" class="{input_class}"></label>'
    )
    parts.append(
        f'    <label class="{label_class}">To '
        f'<input type="date" name="date_to" class="{input_class}"></label>'
    )
    for vid, url in visual_fetch_urls:
        # One Refresh button per visual. Triggered on click (button's
        # default trigger). Date-input ``change`` was tried on the
        # button via ``from:#filter-form`` but didn't fire reliably
        # in the spike; click is the floor — phase.1 can layer auto-
        # refresh-on-change via SSE or a less-clever trigger config.
        esc_id = html.escape(vid)
        esc_url = html.escape(url)
        parts.append(
            f'    <button type="button"'
            f' hx-get="{esc_url}"'
            f' hx-target="#visual-data-{esc_id}"'
            f' hx-include="#filter-form"'
            f' hx-push-url="true"'
            f' class="{button_class}">Refresh</button>'
        )
    parts.append('  </form>')
    return "\n".join(parts)


def _visual_fetch_url(
    dashboard_id: str, sheet_id: str, visual_id: str,
) -> str:
    """Build the GET data URL for one visual.

    Single source of truth — every site that needs the URL (form
    Refresh button, section ``data-fetch-url`` attribute, future
    deep-link generators) calls this. Mirrors the route template
    in ``server.py::make_app`` exactly; if either drifts, the path
    constraint check on the server returns 404.
    """
    return (
        f"/dashboards/{dashboard_id}"
        f"/sheets/{sheet_id}"
        f"/visuals/{visual_id}/data"
    )


def emit_html(
    app: App, sheet: Sheet, *,
    dashboard_id: str,
    dev_log: bool = False,
) -> str:
    """Render a tree ``Sheet`` as a standalone HTML page.

    Page shell pulls HTMX + d3 + d3-sankey, emits a date-range form
    at the top of the body that GETs each visual's data endpoint on
    Refresh, plus one ``<section>`` per visual carrying its title,
    subtitle, and the swap-target div.

    Takes both the App and the Sheet so internal-id resolution
    (``app.resolve_auto_ids()``) can run. The Sheet alone has no
    parent ref — without the App we'd emit ``data-visual-id=
    "_AutoSentinel.AUTO"`` for any visual constructed with the
    standard ``visual_id=AUTO`` default. The data URLs key off
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
        dashboard_id: URL slug for this dashboard. Embedded in
            every visual's data-fetch URL + the Refresh button's
            ``hx-get`` so the path matches what ``server.make_app``
            wired its route for.

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
    sheet_id = str(sheet.sheet_id)

    title_class = "text-3xl font-bold mt-8 mx-8 mb-2"
    desc_class = "mx-8 mb-6 text-slate-600"
    body_parts: list[str] = [
        f'  <h1 class="{title_class}">{html.escape(sheet.title)}</h1>',
    ]
    if sheet.description:
        body_parts.append(
            f'  <p class="{desc_class}">{html.escape(sheet.description)}</p>'
        )
    visual_fetch_urls = [
        (
            str(getattr(v, "visual_id", "")),
            _visual_fetch_url(
                dashboard_id, sheet_id, str(getattr(v, "visual_id", "")),
            ),
        )
        for v in sheet.visuals
    ]
    body_parts.append(_render_filter_form(visual_fetch_urls))
    for visual in sheet.visuals:
        body_parts.append(_render_visual(visual, dashboard_id, sheet_id))
    return _PAGE_SHELL.format(
        title=html.escape(sheet.title),
        body="\n".join(body_parts),
        htmx_src=_HTMX_SRC,
        d3_src=_D3_SRC,
        d3_sankey_src=_D3_SANKEY_SRC,
        bootstrap_js=_BOOTSTRAP_JS,
        dev_log_js=_DEV_LOG_JS,
        dev_log_meta=(
            '  <meta name="dev-log" content="1">' if dev_log else ""
        ),
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


def _render_visual(
    visual: Any, dashboard_id: str, sheet_id: str,
) -> str:
    """Render one visual as an HTML ``<section>``.

    Title + optional subtitle + swap-target div tagged with the
    visual's class name (``data-visual-kind``) so the bootstrap
    script dispatches d3 hydration per kind. The inner div carries
    ``id="visual-data-<visual_id>"`` so HTMX's ``hx-target``
    selector matches.

    The section also carries ``data-fetch-url`` — the full GET URL
    for this visual's data. The bootstrap JS reads it when an
    in-chart click (e.g. Sankey node) needs to fire a swap, so the
    URL-construction authority stays server-side.

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
    fetch_url = _visual_fetch_url(dashboard_id, sheet_id, visual_id)
    esc_url = html.escape(fetch_url)
    section_class = (
        "mx-8 mb-6 p-4 bg-white rounded-lg shadow-sm "
        "border border-slate-200"
    )
    h2_class = "text-xl font-semibold text-slate-800 mb-1"
    subtitle_class = "subtitle text-sm text-slate-500 mb-4"

    parts: list[str] = []
    parts.append(
        f'  <section data-visual-kind="{html.escape(kind)}"'
        f' data-visual-id="{esc_id}"'
        f' data-fetch-url="{esc_url}"'
        f' class="{section_class}">'
    )
    parts.append(f'    <h2 class="{h2_class}">{html.escape(title)}</h2>')
    if subtitle:
        parts.append(
            f'    <p class="{subtitle_class}">{html.escape(subtitle)}</p>'
        )
    parts.append(
        f'    <div id="visual-data-{esc_id}" class="visual-data">'
        f'<!-- HTMX swap target; populated by GET {esc_url} -->'
        f'</div>'
    )
    parts.append("  </section>")
    return "\n".join(parts)
