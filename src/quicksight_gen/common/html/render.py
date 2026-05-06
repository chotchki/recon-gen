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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quicksight_gen.common.theme import DEFAULT_PRESET
from quicksight_gen.common.l2.theme import ThemePreset
from quicksight_gen.common.tree._helpers import _AutoSentinel
from quicksight_gen.common.tree.structure import App, Sheet


# X.2.d — filter primitives beyond the date-range form. All values
# round-trip via the URL per X.2.b (URL == cache key, no client form
# state); the Refresh button's ``hx-include="#filter-form"`` serializes
# every input the form contains, so adding more inputs Just Works on
# the wire.
#
# The three new shapes carry their own URL-key prefix so the server
# can route them generically: ``param_<name>`` for parameter dropdowns,
# ``filter_<column>`` for category multi-selects (comma-joined values),
# ``min_<column>`` / ``max_<column>`` for numeric ranges. The data
# fetcher (X.2.f) consumes the prefix-keyed dict.


@dataclass(frozen=True)
class ParameterDropdownSpec:
    """Single-select dropdown for a named parameter.

    Renders as a ``<select name="param_<name>">`` with a blank
    leading option (the empty string round-trips as "no
    selection"). URL key on submit: ``?param_<name>=<value>``.
    """
    name: str
    label: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class CategoryFilterSpec:
    """Multi-select check group for a column.

    Renders as a checkbox group plus a hidden ``<input
    name="filter_<column>">`` that ``wireCategoryFilters`` (in
    bootstrap.js) keeps in sync with the joined-by-comma value.
    HTMX serializes the hidden input — checkboxes themselves
    aren't named so they never reach the wire. URL key on submit:
    ``?filter_<column>=v1,v2,v3``.
    """
    column: str
    label: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class NumericRangeSpec:
    """Two number inputs for a numeric column.

    Renders as two ``<input type="number">`` named
    ``min_<column>`` + ``max_<column>``. Empty inputs serialize
    as empty strings (the data fetcher treats either as
    open-ended on that side). URL keys on submit:
    ``?min_<column>=N&max_<column>=M``.
    """
    column: str
    label: str


FilterSpec = ParameterDropdownSpec | CategoryFilterSpec | NumericRangeSpec


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
_FORM_LABEL_CLASS = (
    "flex items-center gap-2 text-sm font-medium text-primary-fg"
)
_FORM_INPUT_CLASS = (
    "px-2 py-1 border border-surface-border rounded text-sm "
    "focus:outline-none focus:ring-2 focus:ring-accent"
)


def _render_parameter_dropdown(spec: ParameterDropdownSpec) -> str:
    """Single-select ``<select name="param_<name>">``."""
    name = html.escape(spec.name)
    parts = [
        f'    <label class="{_FORM_LABEL_CLASS}">{html.escape(spec.label)} '
        f'<select name="param_{name}" class="{_FORM_INPUT_CLASS}">'
        f'<option value=""></option>'
    ]
    for opt in spec.options:
        esc = html.escape(opt)
        parts.append(f'<option value="{esc}">{esc}</option>')
    parts.append('</select></label>')
    return "".join(parts)


def _render_category_filter(spec: CategoryFilterSpec) -> str:
    """Multi-select check group + hidden joined-input the JS keeps
    in sync. The ``data-filter-name`` attribute lets the JS find
    the wrapper without coupling it to a specific column name."""
    name = html.escape(f"filter_{spec.column}")
    wrapper_class = (
        "category-filter flex items-center gap-2 text-sm "
        "text-primary-fg flex-wrap"
    )
    cb_label_class = "inline-flex items-center gap-1"
    cb_class = "accent-accent"
    parts = [
        f'    <div class="{wrapper_class}" data-filter-name="{name}">'
        f'<span class="font-medium">{html.escape(spec.label)}</span>'
        f'<input type="hidden" name="{name}" value="">'
    ]
    for opt in spec.options:
        esc = html.escape(opt)
        parts.append(
            f'<label class="{cb_label_class}">'
            f'<input type="checkbox" value="{esc}" class="{cb_class}"> {esc}'
            f'</label>'
        )
    parts.append('</div>')
    return "".join(parts)


def _render_numeric_range(spec: NumericRangeSpec) -> str:
    """Two ``<input type="number">`` named min_<col> + max_<col>."""
    col = html.escape(spec.column)
    narrow_input = _FORM_INPUT_CLASS + " w-24"
    return (
        f'    <label class="{_FORM_LABEL_CLASS}">{html.escape(spec.label)} '
        f'<input type="number" step="any" name="min_{col}" '
        f'placeholder="min" class="{narrow_input}"> '
        f'<input type="number" step="any" name="max_{col}" '
        f'placeholder="max" class="{narrow_input}">'
        f'</label>'
    )


def _render_filter_form(
    visual_fetch_urls: list[tuple[str, str]],
    filter_specs: Sequence[FilterSpec] = (),
) -> str:
    """Render the filter form with one Refresh button per visual.

    The form starts with the always-on date-range inputs, then
    appends a control per ``filter_specs`` entry (X.2.d). Each
    Refresh button targets ``#visual-data-{visual_id}`` and GETs
    the URL with ``hx-include="#filter-form"`` — every named
    input in the form (including the ones added via
    ``filter_specs``) lands as a query param.
    """
    form_class = (
        "flex flex-wrap items-center gap-3 mx-8 mb-6 p-4 "
        "bg-surface rounded-lg shadow-sm border border-surface-border"
    )
    button_class = (
        "px-3 py-1 bg-accent text-accent-fg text-sm font-medium "
        "rounded hover:opacity-90 active:opacity-80 "
        "transition-opacity cursor-pointer"
    )
    parts = [f'  <form id="filter-form" class="{form_class}">']
    parts.append(
        f'    <label class="{_FORM_LABEL_CLASS}">From '
        f'<input type="date" name="date_from" class="{_FORM_INPUT_CLASS}"></label>'
    )
    parts.append(
        f'    <label class="{_FORM_LABEL_CLASS}">To '
        f'<input type="date" name="date_to" class="{_FORM_INPUT_CLASS}"></label>'
    )
    for spec in filter_specs:
        if isinstance(spec, ParameterDropdownSpec):
            parts.append(_render_parameter_dropdown(spec))
        elif isinstance(spec, CategoryFilterSpec):
            parts.append(_render_category_filter(spec))
        elif isinstance(spec, NumericRangeSpec):
            parts.append(_render_numeric_range(spec))
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


def _render_sheet_tabs(
    dashboard_id: str,
    sheets: Sequence[Sheet],
    active_sheet_id: str,
) -> str:
    """Render sheet tabs across the top of the dashboard page (X.2.e).

    Plain ``<a href>`` per sheet — simpler than HTMX swap and the
    full-page reload is cheap once Tailwind / HTMX / d3 are cached
    (only the page chrome reloads). The URL is always honored, no
    fragment-vs-full-page distinction.

    The active sheet's tab carries the accent background so the
    user can see where they are. Inactive tabs use the surface
    border color to look like tab dividers without screaming.
    """
    if len(sheets) <= 1:
        # Single-sheet dashboards don't need a tab strip.
        return ""
    nav_class = (
        "flex flex-wrap gap-1 mx-8 mt-4 mb-2 border-b border-surface-border"
    )
    active_tab_class = (
        "px-4 py-2 text-sm font-medium bg-accent text-accent-fg "
        "rounded-t-md no-underline"
    )
    inactive_tab_class = (
        "px-4 py-2 text-sm font-medium text-secondary-fg "
        "hover:text-primary-fg hover:bg-surface "
        "rounded-t-md no-underline transition-colors"
    )
    parts = [f'  <nav class="{nav_class}">']
    for s in sheets:
        sid = str(s.sheet_id)
        href = (
            f"/dashboards/{html.escape(dashboard_id)}"
            f"/sheets/{html.escape(sid)}"
        )
        cls = active_tab_class if sid == active_sheet_id else inactive_tab_class
        parts.append(
            f'    <a href="{href}" class="{cls}">{html.escape(s.name)}</a>'
        )
    parts.append('  </nav>')
    return "\n".join(parts)


def emit_html(
    app: App, sheet: Sheet, *,
    dashboard_id: str,
    dev_log: bool = False,
    theme: ThemePreset | None = None,
    filter_specs: Sequence[FilterSpec] = (),
    all_sheets: Sequence[Sheet] = (),
) -> str:
    """Render a tree ``Sheet`` as a standalone HTML page.

    Page shell pulls HTMX + d3 + d3-sankey, optionally emits a sheet
    tab strip (when ``all_sheets`` carries more than one), then a
    filter form (date-range plus any ``filter_specs`` controls) at
    the top of the body that GETs each visual's data endpoint on
    Refresh, plus one ``<section>`` per visual carrying its title,
    subtitle, and the swap-target div.

    ``all_sheets`` (X.2.e): when provided and longer than one, the
    page renders sheet tabs at the top. Each tab links to
    ``/dashboards/{dashboard_id}/sheets/{sheet_id}`` (plain
    anchors — full page reload). The currently-rendered ``sheet``
    is highlighted as active. Pass ``app.analysis.sheets`` to wire
    every sheet in the analysis as a tab. Default ``()`` keeps
    single-sheet behavior for callers that haven't migrated.

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
    if all_sheets:
        body_parts.append(_render_sheet_tabs(
            dashboard_id, all_sheets, sheet_id,
        ))
    body_parts.append(_render_filter_form(visual_fetch_urls, filter_specs))
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
