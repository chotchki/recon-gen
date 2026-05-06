"""HTML renderer for App2 — projects a tree ``Sheet`` to a complete
HTML page (HTMX + d3 dialect).

Page shell pulls HTMX + d3 + d3-sankey from CDNs; bootstrap JS
hydrates ``data-visual-kind`` fragments after every HTMX swap;
the per-visual data fetch path is GET (X.2.b) with all filter
state as query params.

Theme integration (X.2.l)
-------------------------

The page shell injects an inline ``<style>:root { --color-accent:
...; ... }</style>`` block carrying CSS-variable values from the
served L2 instance's ``ThemePreset`` (or ``DEFAULT_PRESET`` when
the L2 has no theme — silent-fallback contract from N.4.k). The
Tailwind ``@theme`` block in ``assets/input.css`` declares the
same token names with default values (build-time); the runtime
override at ``:root`` wins via the cascade. Net effect: every
``bg-accent`` / ``text-accent`` utility resolves per-instance
without rebuilding Tailwind.

Error pages (X.2.m)
-------------------

``emit_error_page`` renders a themed error shell for 4xx / 5xx
responses using the same ``_emit_theme_style`` injection — so
Starlette's exception handlers can return a styled "Something
went wrong" / "Not found" page instead of the framework default.
In dev mode the page can carry a traceback inside a collapsible
``<details>`` block; production mode hides it (operators look at
the server log).

Visual rendering shape:

    <section data-visual-kind="<kind>" data-visual-id="<id>"
             data-fetch-url="/dashboards/.../visuals/<id>/data">
      <h2>{title}</h2>
      <p class="subtitle">{subtitle}</p>
      <div id="visual-data-<id>" class="visual-data">
        <!-- HTMX swap target; server emits <script type="application/json">
             holding the chart data, bootstrap JS hydrates via d3 -->
      </div>
    </section>
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from quicksight_gen.common.theme import DEFAULT_PRESET
from quicksight_gen.common.l2.theme import ThemePreset
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
{theme_style}
  <script src="{htmx_src}"></script>
  <script src="{d3_src}"></script>
  <script src="{d3_sankey_src}"></script>
</head>
<body class="bg-surface-bg text-primary-fg font-sans antialiased">
{body}
  <script>{bootstrap_js}</script>
  <script>{dev_log_js}</script>
</body>
</html>
"""


# X.2.l — semantic-token → ThemePreset-field mapping. Every Tailwind
# utility in render.py / bootstrap.js eventually resolves to one of
# these CSS variables. Adding a new token: append the (var-name,
# preset-attr) pair here; declare ``--color-<name>`` in input.css's
# @theme block with a default; use ``bg-<name>`` / ``text-<name>``
# / ``fill-<name>`` etc. in markup.
_THEME_TOKEN_MAP: list[tuple[str, str]] = [
    ("--color-accent", "accent"),
    ("--color-accent-fg", "accent_fg"),
    ("--color-link-tint", "link_tint"),
    ("--color-surface", "primary_bg"),
    ("--color-surface-bg", "secondary_bg"),
    ("--color-primary-fg", "primary_fg"),
    ("--color-secondary-fg", "secondary_fg"),
    ("--color-danger", "danger"),
    ("--color-success", "success"),
    ("--color-warning", "warning"),
]


def _emit_theme_style(theme: ThemePreset | None) -> str:
    """Render the per-instance ``<style>:root { ... }</style>`` block.

    Falls back to ``DEFAULT_PRESET`` when the L2 has no theme —
    same silent-fallback contract as the QS dialect (N.4.k). The
    block lives AFTER the Tailwind stylesheet so its ``:root``
    declarations override the build-time defaults from input.css's
    ``@theme`` block via cascade order.

    Returns the ``<style>`` element as a string suitable for
    embedding in the page ``<head>``.
    """
    resolved = theme or DEFAULT_PRESET
    decls = "\n".join(
        f"    {var}: {getattr(resolved, attr)};"
        for var, attr in _THEME_TOKEN_MAP
    )
    return f'  <style>\n  :root {{\n{decls}\n  }}\n  </style>'


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
    and GETs the URL.
    """
    form_class = (
        "flex flex-wrap items-center gap-3 mx-8 mb-6 p-4 "
        "bg-surface rounded-lg shadow-sm border border-surface-border"
    )
    label_class = (
        "flex items-center gap-2 text-sm font-medium text-primary-fg"
    )
    input_class = (
        "px-2 py-1 border border-surface-border rounded text-sm "
        "focus:outline-none focus:ring-2 focus:ring-accent"
    )
    button_class = (
        "px-3 py-1 bg-accent text-accent-fg text-sm font-medium "
        "rounded hover:opacity-90 active:opacity-80 "
        "transition-opacity cursor-pointer"
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


def emit_dashboards_list(
    dashboards: list[tuple[str, str]],
    *,
    theme: ThemePreset | None = None,
) -> str:
    """Render the ``/dashboards`` landing page.

    ``dashboards`` is a list of ``(dashboard_id, title)`` tuples in
    display order. Each entry renders as a link to
    ``/dashboards/{dashboard_id}`` so the URL surface stays the
    bookmarkable layer (no JS required to navigate the list).

    ``theme`` controls the CSS-variable values injected into the
    page shell. When ``None``, ``DEFAULT_PRESET`` wins via
    silent-fallback (N.4.k). Multi-dashboard servers should pass
    a single shared theme — the listing is one page, one palette.
    """
    title_class = "text-3xl font-bold mt-8 mx-8 mb-2"
    desc_class = "mx-8 mb-6 text-secondary-fg"
    list_class = "mx-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
    item_class = (
        "block p-4 bg-surface rounded-lg shadow-sm border "
        "border-surface-border hover:border-accent hover:shadow-md "
        "transition-shadow text-primary-fg"
    )
    body_parts: list[str] = [
        f'  <h1 class="{title_class}">Dashboards</h1>',
        f'  <p class="{desc_class}">'
        f'Pick a dashboard to view its sheets.'
        f'</p>',
        f'  <nav class="{list_class}">',
    ]
    for dash_id, dash_title in dashboards:
        esc_id = html.escape(dash_id)
        esc_title = html.escape(dash_title)
        body_parts.append(
            f'    <a href="/dashboards/{esc_id}" class="{item_class}">'
            f'<span class="text-lg font-semibold">{esc_title}</span>'
            f'</a>'
        )
    body_parts.append('  </nav>')
    return _PAGE_SHELL.format(
        title="Dashboards",
        body="\n".join(body_parts),
        htmx_src=_HTMX_SRC,
        d3_src=_D3_SRC,
        d3_sankey_src=_D3_SANKEY_SRC,
        bootstrap_js=_BOOTSTRAP_JS,
        dev_log_js=_DEV_LOG_JS,
        dev_log_meta="",
        theme_style=_emit_theme_style(theme),
    )


def emit_error_page(
    *,
    status_code: int,
    headline: str,
    subtitle: str,
    traceback_text: str | None = None,
    theme: ThemePreset | None = None,
) -> str:
    """Render a themed error page for 4xx / 5xx responses (X.2.m).

    Used by Starlette exception handlers in ``server.make_app`` so
    every error surface — sheet-not-found, dashboard-not-found,
    uncaught render exception, DB unreachable — lands on a page that
    fits the rest of the app: same theme tokens, same shell, link
    back to the dashboards listing.

    Args:
        status_code: HTTP status this page is being returned with.
            Shown in small text under the headline (so operators on
            the phone with a user can confirm "yes that's the 500
            page" without dev tools).
        headline: short error label (e.g. "Something went wrong",
            "Not found"). HTML-escaped.
        subtitle: longer human-readable explanation + next-step
            guidance. HTML-escaped.
        traceback_text: when set, included inside a collapsible
            ``<details>`` block. Caller is expected to gate this on
            whatever dev-mode flag governs traceback exposure
            (``dev_log`` in the server today). Production callers
            pass ``None`` so internals don't leak.
        theme: same ``ThemePreset`` contract as ``emit_html`` —
            ``None`` falls back to ``DEFAULT_PRESET``.

    Returns:
        A complete HTML document as a string.
    """
    title_class = "text-3xl font-bold mt-8 mx-8 mb-2 text-danger"
    code_class = "mx-8 mb-4 text-sm text-secondary-fg tabular-nums"
    desc_class = "mx-8 mb-6 text-primary-fg"
    link_class = (
        "inline-block mx-8 mb-6 px-3 py-1 bg-accent text-accent-fg "
        "text-sm font-medium rounded hover:opacity-90 active:opacity-80 "
        "transition-opacity cursor-pointer"
    )
    body_parts: list[str] = [
        f'  <h1 class="{title_class}">{html.escape(headline)}</h1>',
        f'  <p class="{code_class}">HTTP {int(status_code)}</p>',
        f'  <p class="{desc_class}">{html.escape(subtitle)}</p>',
        f'  <a href="/dashboards" class="{link_class}">Back to dashboards</a>',
    ]
    if traceback_text:
        details_class = (
            "mx-8 mb-6 p-4 bg-surface rounded-lg border "
            "border-surface-border text-sm text-primary-fg"
        )
        summary_class = "cursor-pointer font-semibold text-secondary-fg"
        pre_class = (
            "mt-3 overflow-x-auto text-xs tabular-nums text-primary-fg"
        )
        body_parts.append(
            f'  <details class="{details_class}">'
            f'<summary class="{summary_class}">Traceback (dev mode)</summary>'
            f'<pre class="{pre_class}">{html.escape(traceback_text)}</pre>'
            f'</details>'
        )
    return _PAGE_SHELL.format(
        title=html.escape(headline),
        body="\n".join(body_parts),
        htmx_src=_HTMX_SRC,
        d3_src=_D3_SRC,
        d3_sankey_src=_D3_SANKEY_SRC,
        bootstrap_js=_BOOTSTRAP_JS,
        dev_log_js=_DEV_LOG_JS,
        dev_log_meta="",
        theme_style=_emit_theme_style(theme),
    )


def emit_html(
    app: App, sheet: Sheet, *,
    dashboard_id: str,
    dev_log: bool = False,
    theme: ThemePreset | None = None,
) -> str:
    """Render a tree ``Sheet`` as a standalone HTML page.

    Page shell pulls HTMX + d3 + d3-sankey, emits a date-range form
    at the top of the body that GETs each visual's data endpoint on
    Refresh, plus one ``<section>`` per visual carrying its title,
    subtitle, and the swap-target div.

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
        theme: ``ThemePreset`` to inject as CSS variables. When
            ``None``, falls back to ``DEFAULT_PRESET`` (silent-
            fallback per N.4.k, mirrors the QS dialect's CLASSIC
            fallback).

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
    desc_class = "mx-8 mb-6 text-secondary-fg"
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
        theme_style=_emit_theme_style(theme),
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
        "mx-8 mb-6 p-4 bg-surface rounded-lg shadow-sm "
        "border border-surface-border"
    )
    h2_class = "text-xl font-semibold text-primary-fg mb-1"
    subtitle_class = "subtitle text-sm text-secondary-fg mb-4"

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
