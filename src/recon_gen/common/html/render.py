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

import datetime as _dt
import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


def _json_default(obj: Any) -> Any:
    """JSON encoder hook for SQL-row types ``json.dumps`` rejects.

    DB drivers return ``Decimal`` for NUMERIC and ``date`` / ``datetime``
    for DATE / TIMESTAMP — the d3 renderers want plain numbers and
    ISO-8601 strings, so coerce here at the serialization boundary.
    """
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    raise TypeError(
        f"Object of type {type(obj).__name__} is not JSON serializable"
    )

from recon_gen.common.theme import DEFAULT_PRESET
from recon_gen.common.l2.theme import ThemePreset
from recon_gen.common.tree._helpers import _AutoSentinel
from recon_gen.common.tree.structure import App, Sheet
from recon_gen.common.tree.actions import Drill
from recon_gen.common.tree.fields import Dim, Measure
from recon_gen.common.tree.calc_fields import resolve_column


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

    ``options_dataset`` / ``options_column`` (X.2.u.4.b): when the
    option universe is a dataset column rather than a static list (a
    tree ``ParameterDropdown(type="SINGLE_SELECT",
    selectable_values=LinkedValues(...))``), the spec carries the
    source instead of inlined ``options`` (which stays ``()`` until
    the server resolves it — `server.py::_resolve_linked_options`
    runs ``SELECT DISTINCT <col> FROM (<dataset SQL>)`` and replaces
    ``options`` before rendering). When ``options_dataset`` is None
    the static ``options`` are authoritative.

    ``selected`` (u.4.e.4): when the sheet page URL carries
    ``?param_<name>=<v>`` — a cross-sheet drill that walked an anchor,
    or a bookmarked filter state — ``server.py::_apply_url_param_overrides``
    sets ``selected`` to that value, so ``_render_parameter_dropdown``
    pre-marks the matching ``<option>``. Because every visual loads via
    ``hx-include="#filter-form"``, a pre-marked option makes the *initial*
    fetch already narrowed (the destination renders filtered, no manual
    re-pick). Empty string (the default) = blank leading option active.
    """
    name: str
    label: str
    options: tuple[str, ...]
    options_dataset: str | None = None
    options_column: str | None = None
    selected: str = ""


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
    """Min/max range filter for a numeric column.

    Renders two ``<input type="number">`` named ``min_<column>`` +
    ``max_<column>`` (empty = open-ended on that side); URL keys on
    submit: ``?min_<column>=N&max_<column>=M``. When ``lo`` + ``hi``
    bounds are supplied, a noUiSlider two-handle widget is also emitted
    over the inputs (X.2.l.4) — ``step`` is the optional snap interval.
    Bounds are caller-supplied (a column min/max query, or sensible
    defaults); without them the widget is number-inputs-only since a
    slider needs a range.
    """
    column: str
    label: str
    lo: float | None = None
    hi: float | None = None
    step: float | None = None


@dataclass(frozen=True)
class ParameterNumberSpec:
    """Single numeric value bound to a named parameter — the App2
    counterpart of a tree ``ParameterSlider`` node (X.2.u.4.e).

    Renders an ``<input type="number" name="param_<name>">`` (the wire
    element — submits a single ``?param_<name>=<value>`` key, which
    ``_sql_executor`` translates to a ``:param_<name>`` *scalar* bind for
    a ``<<$pName>>`` placeholder; NOT the repeated-key shape
    ``ParameterMultiSelectSpec`` produces) plus a one-handle noUiSlider
    over it (``wireNoUiSlider``'s single-handle mode — driven by
    ``data-value-input`` rather than the two-handle ``data-min-input`` /
    ``data-max-input``). The widget's initial value is ``default`` when
    set, else ``minimum`` — which for the Investigation thresholds is the
    no-narrowing position (σ≥1 / hop≥$0). Empty → no key → the executor's
    static-default fallback (= the dataset param's declared default),
    mirroring QuickSight's "slider untouched ⇒ analysis default".
    ``server.py::_apply_url_param_overrides`` overwrites ``default`` from a
    ``?param_<name>=<v>`` page-URL key (u.4.e.4) so a drill / bookmark
    lands on the right slider position.
    """
    name: str
    label: str
    minimum: float
    maximum: float
    step: float
    default: float | None = None


@dataclass(frozen=True)
class ParameterMultiSelectSpec:
    """Multi-select dropdown for a MULTI_VALUED named parameter
    (Y.2.app2.cde.l2ft-wiring.b).

    Renders as a ``<select multiple name="param_<name>">`` — each
    selected ``<option>`` serializes as its own ``param_<name>=<value>``
    pair, so HTMX submits ``?param_<name>=A&param_<name>=B`` (repeated
    key, NOT comma-joined — that's the shape ``_sql_executor``'s
    multi-valued dataset-param expansion consumes). Nothing selected →
    no ``param_<name>`` key at all → the executor falls back to the
    dataset param's declared-value default (= no narrowing), matching
    QuickSight's "empty the dropdown reverts to default" behaviour
    (Y.2.c.0). This is the App2 counterpart of a tree ``ParameterDropdown(
    type="MULTI_SELECT", selectable_values=StaticValues(...) | LinkedValues(...))``
    node — derived from the tree by ``make_filter_specs_for_sheet``.

    ``options_dataset`` / ``options_column`` (X.2.u.4.b): for a
    ``LinkedValues`` source the spec carries the dataset identifier +
    column instead of an inlined ``options`` list (which stays ``()``
    until ``server.py::_resolve_linked_options`` runs ``SELECT DISTINCT
    <col> FROM (<dataset SQL>)`` and replaces ``options`` pre-render).

    ``selected`` (u.4.e.4): the repeated ``?param_<name>=A&param_<name>=B``
    keys from the sheet page URL — ``server.py::_apply_url_param_overrides``
    fills it so the matching ``<option>``s render pre-selected and the
    visuals' ``hx-include="#filter-form"`` load fetch is already narrowed.
    ``()`` (the default) = nothing pre-selected (= the executor's
    static-default fallback = no narrowing).
    """
    name: str
    label: str
    options: tuple[str, ...]
    options_dataset: str | None = None
    options_column: str | None = None
    selected: tuple[str, ...] = ()


FilterSpec = (
    ParameterDropdownSpec
    | CategoryFilterSpec
    | NumericRangeSpec
    | ParameterNumberSpec
    | ParameterMultiSelectSpec
)


# Third-party browser libs — served from ``/static/vendor/...`` (the
# existing ``assets/`` static mount), NOT a CDN. The dist files are
# committed under ``common/html/assets/vendor/{js,css}/`` and shipped via
# ``package-data`` so ``pip install recon-gen[serve] && recon-gen
# dashboards`` works with zero internet (X.2.p). Provenance + the
# refresh recipe live in ``assets/vendor/vendor.lock`` +
# ``scripts/vendor_js_deps.py``; ``tests/unit/test_vendor_assets.py``
# asserts the committed bytes match the lock AND that no ``<script>`` /
# ``<link>`` the page shell emits points at an external URL.
_HTMX_SRC = "/static/vendor/js/htmx.min.js"
_D3_SRC = "/static/vendor/js/d3.min.js"
_D3_SANKEY_SRC = "/static/vendor/js/d3-sankey.min.js"

# X.2.l.4 — filter-widget libs. Each *enhances an existing* ``<select>`` /
# ``<input>`` and keeps its ``.value`` in sync — so the HTMX wire shape
# (URL keys, form serialization) is unchanged; only the widget chrome
# changes.
#
#   - Tom Select  — multi-select chips + search (replaces the native
#                   ``<select multiple>`` and the checkbox group; QS's
#                   multi-select is the widget App 2 was furthest from)
#                   and single-select with typeahead.
#   - Flatpickr   — date-range popover + preset shortcuts (replaces the
#                   two browser-native ``<input type="date">``).
#   - noUiSlider  — draggable two-handle min/max slider with value
#                   bubbles (over the native number inputs).
_TOM_SELECT_CSS = "/static/vendor/css/tom-select.min.css"
_TOM_SELECT_JS = "/static/vendor/js/tom-select.complete.min.js"
_FLATPICKR_CSS = "/static/vendor/css/flatpickr.min.css"
_FLATPICKR_JS = "/static/vendor/js/flatpickr.min.js"
_NOUISLIDER_CSS = "/static/vendor/css/nouislider.min.css"
_NOUISLIDER_JS = "/static/vendor/js/nouislider.min.js"

# X.2.u.4.e.3 — ctxMenu: a zero-dep right-click / on-demand popup menu
# (the *standalone* build — an IIFE that sets ``window.ctxmenu``; the
# package's other build is CommonJS, no good for a plain ``<script>``).
# Powers App 2's row-level ``DATA_POINT_MENU`` drills: a table row that
# carries menu-drills gets a "⋯" trigger button (and ``contextmenu`` on
# the row, for QS-gesture parity) → ``ctxmenu`` shows the drill list. It
# injects its own ``<style id="ctxmenu">`` at runtime, so it ships no CSS
# file — ``widgets-theme.css`` re-skins ``.ctxmenu`` with ``!important``
# (its injected sheet loads *after* our override sheet).
_CTXMENU_JS = "/static/vendor/js/ctxmenu.min.js"

# Built once at import: the full ``<head>`` asset blocks. ``_VENDOR_CSS``
# lands right after ``output.css`` (so the per-instance ``:root`` theme
# ``<style>`` — which follows it — still wins the cascade for the
# ``--color-*`` vars these libs read; the X.2.l.4.c override sheet then
# maps the libs' own hooks onto those vars). ``_VENDOR_JS`` lands at the
# bottom of ``<head>`` where htmx / d3 already loaded.
_VENDOR_CSS = "\n".join(
    f'  <link rel="stylesheet" href="{href}">'
    for href in (_TOM_SELECT_CSS, _FLATPICKR_CSS, _NOUISLIDER_CSS)
)
_VENDOR_JS = "\n".join(
    f'  <script src="{src}"></script>'
    for src in (
        _HTMX_SRC, _D3_SRC, _D3_SANKEY_SRC,
        _TOM_SELECT_JS, _FLATPICKR_JS, _NOUISLIDER_JS, _CTXMENU_JS,
    )
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
{dev_log_meta}{data_generation_meta}
  <link rel="stylesheet" href="/static/output.css">
{vendor_css}
  <link rel="stylesheet" href="/static/widgets-theme.css">
{theme_style}
{vendor_js}
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
    "px-3 py-2 bg-surface border border-surface-border rounded-md text-sm "
    "text-primary-fg shadow-sm transition-colors "
    "hover:border-accent focus:outline-none focus:ring-2 focus:ring-accent "
    "focus:border-accent"
)
_DATE_INPUT_CLASS = _FORM_INPUT_CLASS + " cursor-pointer"
_DATE_INPUT_STYLE = "min-width: 10rem;"


# X.2.l.4 — ``data-widget="..."`` attributes mark the elements that
# ``wireFilterWidgets`` (bootstrap.js) enhances with Tom Select /
# Flatpickr / noUiSlider. The underlying ``<select>`` / ``<input>`` is
# still the wire element (HTMX serializes it; the URL keys are
# unchanged) — the widget is chrome that writes back into it. If a lib
# fails to load the plain control stays.
_TOM_SELECT_ATTR = ' data-widget="tomselect"'
_TS_SELECT_CLASS = _FORM_INPUT_CLASS + " min-w-48"


def _render_parameter_dropdown(spec: ParameterDropdownSpec) -> str:
    """Single-select ``<select name="param_<name>">`` enhanced by Tom
    Select (search + clear). The blank leading option round-trips as
    "no selection" (``?param_<name>=``). ``spec.selected`` (set by the
    server from a ``?param_<name>=<v>`` page-URL key — u.4.e.4) pre-marks
    that ``<option>`` so the visuals' ``hx-include="#filter-form"`` load
    fetch is already narrowed; a value not in the (resolved) option list
    is still rendered as a selected ``<option>`` so the form submits it
    (stale-bookmark / sibling-dataset case)."""
    name = html.escape(spec.name)
    sel = spec.selected
    parts = [
        f'    <label class="{_FORM_LABEL_CLASS}">{html.escape(spec.label)} '
        f'<select name="param_{name}" class="{_TS_SELECT_CLASS}"'
        f'{_TOM_SELECT_ATTR}>'
        f'<option value=""></option>'
    ]
    rendered_sel = False
    for opt in spec.options:
        esc = html.escape(opt)
        if opt == sel:
            rendered_sel = True
            parts.append(f'<option value="{esc}" selected>{esc}</option>')
        else:
            parts.append(f'<option value="{esc}">{esc}</option>')
    if sel and not rendered_sel:
        esc = html.escape(sel)
        parts.append(f'<option value="{esc}" selected>{esc}</option>')
    parts.append('</select></label>')
    return "".join(parts)


def _render_category_filter(spec: CategoryFilterSpec) -> str:
    """Multi-select column filter: a ``<select multiple>`` (Tom Select
    chips + search) with NO ``name`` — HTMX won't serialize it — feeding
    a hidden ``<input name="filter_<column>">`` that ``wireCategoryFilters``
    keeps as the comma-joined selected values. URL key on submit:
    ``?filter_<column>=v1,v2,v3``. (Distinct from ``ParameterMultiSelectSpec``,
    whose ``<select multiple name="param_X">`` serializes as repeated keys
    for ``_sql_executor``'s IN-bind expansion.)"""
    name = html.escape(f"filter_{spec.column}")
    wrapper_class = (
        "category-filter flex items-center gap-2 text-sm "
        "text-primary-fg flex-wrap"
    )
    parts = [
        f'    <div class="{wrapper_class}" data-filter-name="{name}">'
        f'<span class="font-medium">{html.escape(spec.label)}</span>'
        f'<input type="hidden" name="{name}" value="">'
        f'<select multiple class="{_TS_SELECT_CLASS}" data-category-select'
        f'{_TOM_SELECT_ATTR}>'
    ]
    for opt in spec.options:
        esc = html.escape(opt)
        parts.append(f'<option value="{esc}">{esc}</option>')
    parts.append('</select></div>')
    return "".join(parts)


def _render_parameter_multiselect(spec: ParameterMultiSelectSpec) -> str:
    """Multi-select ``<select multiple name="param_<name>">`` enhanced by
    Tom Select (chips + search).

    No blank leading option — for a multi-select, "nothing selected"
    already means "all" (the executor's static-default fallback). Each
    selected option serializes as its own ``param_<name>=<value>`` pair
    (repeated key — the shape ``_sql_executor``'s multi-valued
    dataset-param expansion consumes). ``change`` events bubble to the
    form, which ``wireFilterAutoRefresh`` debounces into a ``refresh``.

    ``spec.selected`` (set by the server from the page URL's repeated
    ``?param_<name>=A&param_<name>=B`` keys — u.4.e.4) pre-marks those
    ``<option>``s (any not in the resolved option list are appended as
    selected ``<option>``s) so the load fetch is already narrowed.
    """
    name = html.escape(spec.name)
    sel = set(spec.selected)
    parts = [
        f'    <label class="{_FORM_LABEL_CLASS}">'
        f'{html.escape(spec.label)} '
        f'<select name="param_{name}" multiple class="{_TS_SELECT_CLASS}"'
        f'{_TOM_SELECT_ATTR}>'
    ]
    rendered: set[str] = set()
    for opt in spec.options:
        esc = html.escape(opt)
        if opt in sel:
            rendered.add(opt)
            parts.append(f'<option value="{esc}" selected>{esc}</option>')
        else:
            parts.append(f'<option value="{esc}">{esc}</option>')
    for extra in spec.selected:
        if extra not in rendered:
            esc = html.escape(extra)
            parts.append(f'<option value="{esc}" selected>{esc}</option>')
    parts.append('</select></label>')
    return "".join(parts)


def _render_numeric_range(spec: NumericRangeSpec) -> str:
    """``min_<col>`` / ``max_<col>`` number inputs. When the spec carries
    ``lo`` + ``hi`` bounds, also emit a noUiSlider ``<div>`` over them
    (two draggable handles + value bubbles; ``wireNoUiSlider`` syncs the
    number inputs on drag and vice-versa). Without bounds we can't size a
    slider, so it's number-inputs-only — still functional, just plainer.
    Empty inputs serialize as ``""`` (open-ended on that side)."""
    col = html.escape(spec.column)
    narrow_input = _FORM_INPUT_CLASS + " w-24"
    inputs = (
        f'<input type="number" step="any" name="min_{col}" '
        f'placeholder="min" class="{narrow_input}"> '
        f'<input type="number" step="any" name="max_{col}" '
        f'placeholder="max" class="{narrow_input}">'
    )
    if spec.lo is None or spec.hi is None:
        return (
            f'    <label class="{_FORM_LABEL_CLASS}">'
            f'{html.escape(spec.label)} {inputs}</label>'
        )
    step_attr = f' data-step="{spec.step}"' if spec.step is not None else ""
    slider = (
        f'<div data-widget="nouislider" data-min="{spec.lo}" '
        f'data-max="{spec.hi}" data-min-input="min_{col}" '
        f'data-max-input="max_{col}"{step_attr} '
        f'style="width: 10rem; margin: 0 1.25rem;"></div>'
    )
    return (
        f'    <label class="{_FORM_LABEL_CLASS} items-center">'
        f'{html.escape(spec.label)} {slider} {inputs}</label>'
    )


def _num_attr(value: float) -> str:
    """Render a numeric DOM-attribute value without a spurious trailing
    ``.0`` — so ``?param_<name>=4`` matches the integer literal shape QS
    substitutes for the same parameter (and the bind round-trips clean
    through the SQL executor)."""
    f = float(value)
    return str(int(f)) if f.is_integer() else repr(f)


def _render_parameter_number(spec: ParameterNumberSpec) -> str:
    """``<input type="number" name="param_<name>">`` + a one-handle
    noUiSlider over it (``wireNoUiSlider`` single-handle mode via
    ``data-value-input``). Initial value = ``default`` else ``minimum``.
    The number input is the wire element (HTMX serializes it); the
    slider writes back into it on drag. If noUiSlider fails to load the
    number input stays usable (degraded, not broken)."""
    name = html.escape(spec.name)
    start = spec.default if spec.default is not None else spec.minimum
    lo, hi, step, val = (
        _num_attr(spec.minimum), _num_attr(spec.maximum),
        _num_attr(spec.step), _num_attr(start),
    )
    narrow_input = _FORM_INPUT_CLASS + " w-24"
    number_input = (
        f'<input type="number" name="param_{name}" '
        f'min="{lo}" max="{hi}" step="{step}" value="{val}" '
        f'class="{narrow_input}">'
    )
    slider = (
        f'<div data-widget="nouislider" data-min="{lo}" data-max="{hi}" '
        f'data-start-min="{val}" data-value-input="param_{name}" '
        f'data-step="{step}" style="width: 10rem; margin: 0 1.25rem;"></div>'
    )
    return (
        f'    <label class="{_FORM_LABEL_CLASS} items-center">'
        f'{html.escape(spec.label)} {slider} {number_input}</label>'
    )


def _render_filter_form(
    visual_fetch_urls: list[tuple[str, str]],
    filter_specs: Sequence[FilterSpec] = (),
) -> str:
    """Render the filter form (X.2.g.1.e: no buttons — auto-refresh).

    Filter inputs only — no Refresh button. ``wireFilterAutoRefresh``
    in bootstrap.js listens for ``change`` events on the form and
    broadcasts a ``refresh`` custom event after a 300ms debounce.
    Each visual section's ``hx-trigger="load, refresh from:body"``
    re-fires its hx-get with the new form state.

    Why no button: X.2.g.1.a auto-loads visuals on page load; the
    Refresh button was only needed after a filter change, which the
    auto-refresh now handles. Filter changes are intent enough.
    """
    del visual_fetch_urls  # broadcast pattern doesn't need per-URL list
    form_class = (
        "flex flex-wrap items-center gap-3 mx-8 mb-6 p-4 "
        "bg-surface rounded-lg shadow-sm border border-surface-border"
    )
    parts = [f'  <form id="filter-form" class="{form_class}">']
    # X.2.l.4 — one Flatpickr range popover (the visible, un-named text
    # input) feeding two hidden ``date_from`` / ``date_to`` inputs (the
    # wire-serialized ones — URL keys unchanged). ``readonly`` so the
    # field can't take typed garbage; Flatpickr opens it on click. If
    # Flatpickr fails to load the text input just sits inert and the
    # hidden inputs stay empty → no date narrowing (degraded, not broken).
    parts.append(
        f'    <label class="{_FORM_LABEL_CLASS}">Date range '
        f'<input type="text" data-widget="flatpickr-range" readonly '
        f'placeholder="All dates" class="{_DATE_INPUT_CLASS}"'
        f' style="{_DATE_INPUT_STYLE}"></label>'
        f'<input type="hidden" name="date_from" value="">'
        f'<input type="hidden" name="date_to" value="">'
    )
    for spec in filter_specs:
        if isinstance(spec, ParameterDropdownSpec):
            parts.append(_render_parameter_dropdown(spec))
        elif isinstance(spec, CategoryFilterSpec):
            parts.append(_render_category_filter(spec))
        elif isinstance(spec, NumericRangeSpec):
            parts.append(_render_numeric_range(spec))
        elif isinstance(spec, ParameterNumberSpec):
            parts.append(_render_parameter_number(spec))
        elif isinstance(spec, ParameterMultiSelectSpec):
            parts.append(_render_parameter_multiselect(spec))
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
    docs_url: str | None = None,
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

    ``docs_url`` — when the server has the handbook embedded (X.2.i,
    ``make_app(docs_dir=...)``), pass the mount path (``"/docs/"``) and
    a "Handbook" link renders alongside the title. ``None`` ⇒ no link.
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
    ]
    if docs_url is not None:
        body_parts.append(
            f'  <p class="mx-8 mb-6 text-sm">'
            f'<a href="{html.escape(docs_url)}" '
            f'class="text-accent hover:underline font-semibold">'
            f'📚 Handbook</a> — the same docs site `docs export` produces, '
            f'served right here.</p>'
        )
    body_parts.append(f'  <nav class="{list_class}">')
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
        vendor_css=_VENDOR_CSS,
        vendor_js=_VENDOR_JS,
        bootstrap_js=_BOOTSTRAP_JS,
        dev_log_js=_DEV_LOG_JS,
        dev_log_meta="",
        data_generation_meta="",
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
        vendor_css=_VENDOR_CSS,
        vendor_js=_VENDOR_JS,
        bootstrap_js=_BOOTSTRAP_JS,
        dev_log_js=_DEV_LOG_JS,
        dev_log_meta="",
        data_generation_meta="",
        theme_style=_emit_theme_style(theme),
    )


def _qs_richtext_to_html(content: str) -> str:
    """Project the QS rich-text XML dialect to HTML (X.2.g.1.a).

    The QS format (documented in ``common/rich_text.py``) wraps a
    ``<text-box>`` root around inline runs / line breaks / bullets /
    links. ElementTree parses it; this walker projects each tag to
    a roughly-equivalent HTML node:

      <text-box>...</text-box>            → just the children
      <inline font-size="X" color="Y">…</inline> → <span style="…">…</span>
      <br/>                                → <br>
      <ul><li class="ql-indent-0">…</li></ul> → <ul><li>…</li></ul>
      <a href="…" target="_self">…</a>     → <a href="…" target="_self">…</a>

    Body text is XML-escaped on input + the output stays escaped (we
    only emit element tags, never raw user prose). Unknown tags pass
    through with their text + tail preserved so a future QS-side
    addition degrades to "render the body, drop the styling".
    """
    import xml.etree.ElementTree as ET  # noqa: PLC0415

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        # Malformed XML — escape + render as preformatted so the
        # operator at least sees the raw content.
        return f'<pre class="text-xs text-secondary-fg">{html.escape(content)}</pre>'

    def render(node: ET.Element) -> str:
        tag = node.tag
        text = html.escape(node.text or "")
        children = "".join(render(c) for c in node)
        tail = html.escape(node.tail or "")
        if tag == "text-box":
            return text + children + tail
        if tag == "inline":
            style_parts: list[str] = []
            color = node.get("color")
            if color:
                style_parts.append(f"color: {color}")
            font_size = node.get("font-size")
            if font_size:
                style_parts.append(f"font-size: {font_size}")
            font_weight = node.get("font-weight")
            if font_weight:
                style_parts.append(f"font-weight: {font_weight}")
            style_attr = (
                f' style="{html.escape("; ".join(style_parts))}"'
                if style_parts else ""
            )
            return f"<span{style_attr}>{text}{children}</span>{tail}"
        if tag == "br":
            return f"<br>{tail}"
        if tag == "ul":
            return f"<ul>{text}{children}</ul>{tail}"
        if tag == "ol":
            return f"<ol>{text}{children}</ol>{tail}"
        if tag == "li":
            # Drop the QS-required ``ql-indent-0`` class — HTML <li>
            # doesn't need it.
            return f"<li>{text}{children}</li>{tail}"
        if tag == "a":
            href = node.get("href", "")
            target = node.get("target", "_self")
            return (
                f'<a href="{html.escape(href)}" target="{html.escape(target)}"'
                f' class="text-accent hover:underline">'
                f'{text}{children}</a>{tail}'
            )
        # Unknown tag: degrade to body + tail, drop the wrapper.
        return f"{text}{children}{tail}"

    return render(root)


def _render_text_box(text_box: Any) -> str:
    """Wrap a TextBox's projected HTML in a themed surface card so
    it sits visually consistent with the data-visual sections.
    Pulls the QS rich-text content via ``text_box.content``.

    Pre-grid fallback path — full-width section with mx-8 margin.
    Use ``_render_text_box_in_grid`` when laying out inside the
    sheet's CSS grid.
    """
    section_class = (
        "mx-8 mb-6 p-4 bg-surface rounded-lg shadow-sm "
        "border border-surface-border text-primary-fg"
    )
    raw = getattr(text_box, "content", "") or ""
    inner = _qs_richtext_to_html(raw)
    return f'  <section class="{section_class}">{inner}</section>'


def _render_text_box_in_grid(text_box: Any, *, col_span: int) -> str:
    """Like ``_render_text_box`` but for use inside the CSS grid
    container — drops the outer margin (the grid handles spacing)
    + applies the per-slot column span via inline style."""
    section_class = (
        "p-4 bg-surface rounded-lg shadow-sm "
        "border border-surface-border text-primary-fg"
    )
    raw = getattr(text_box, "content", "") or ""
    inner = _qs_richtext_to_html(raw)
    style = f' style="grid-column: span {col_span};"'
    return f'  <section class="{section_class}"{style}>{inner}</section>'


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
    data_generation_id: int | None = None,
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
    nav_class = "px-8 py-2 border-b border-surface-border text-sm bg-surface"
    nav_link_class = "text-accent hover:underline no-underline font-medium"
    # Back-to-list nav strip — symmetric with the studio's diagram-page
    # chrome (``← landing`` / ``→ dashboards``). Without it, a dashboard
    # sheet is a dead end: tabs walk you between sheets within the
    # current dashboard, but there's no way back to the listing or to
    # the other 3 dashboards. Link target is constant whether the
    # server runs standalone (``recon-gen dashboards``) or under
    # studio — both expose the listing at ``/dashboards``.
    body_parts: list[str] = [
        f'  <nav class="{nav_class}">'
        f'<a href="/dashboards" class="{nav_link_class}">← Dashboards</a>'
        f'</nav>',
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
    # X.2.g.1.a — only emit the filter form when this sheet has data
    # visuals to refresh. Text-box-only sheets (e.g. Executives' Getting
    # Started) used to show a vestigial date picker that did nothing.
    if sheet.visuals:
        body_parts.append(_render_filter_form(visual_fetch_urls, filter_specs))
    # X.2.g.1.d — wrap visuals + text boxes in a CSS grid that
    # respects the tree's GridSlot.col_span. The QS layout is a 36-col
    # grid (see common/tree/structure.py::_GRID_WIDTH_COLS); two
    # _HALF=18-col KPIs sit side-by-side, full-width Tables span 36.
    # Without this wrapper, every visual stacked full-width regardless
    # of declared layout (the screenshot bug).
    grid_slots = list(getattr(sheet, "grid_slots", []) or [])
    if grid_slots:
        body_parts.append(
            '  <div class="mx-8 mb-6" '
            'style="display: grid; grid-template-columns: repeat(36, 1fr); '
            'gap: 1rem;">'
        )
        # Map each LayoutNode (Visual or TextBox) to its slot so the
        # render helpers get col_span. The tree builds slots in
        # placement order; we walk in that order so visuals appear
        # row-by-row left-to-right.
        for slot in grid_slots:
            element = slot.element
            if hasattr(element, "content"):  # TextBox
                body_parts.append(_render_text_box_in_grid(
                    element, col_span=slot.col_span,
                ))
            else:  # Visual
                body_parts.append(_render_visual(
                    element, dashboard_id, sheet_id,
                    col_span=slot.col_span,
                ))
        body_parts.append('  </div>')
    else:
        # Pre-grid fallback: text boxes + visuals as full-width sections.
        # Used when a sheet has no layout (legacy / spike trees).
        for tb in sheet.text_boxes:
            body_parts.append(_render_text_box(tb))
        for visual in sheet.visuals:
            body_parts.append(_render_visual(visual, dashboard_id, sheet_id))
    # X.4.g.12.b — emit data-generation-id meta when set so the
    # bootstrap.js poller has a baseline. Newline-prefixed so the
    # meta lands on its own line in the rendered head, matching the
    # dev-log meta's format.
    data_generation_meta = (
        f'\n  <meta name="data-generation-id" content="{int(data_generation_id)}">'
        if data_generation_id is not None
        else ""
    )
    return _PAGE_SHELL.format(
        title=html.escape(sheet.title),
        body="\n".join(body_parts),
        vendor_css=_VENDOR_CSS,
        vendor_js=_VENDOR_JS,
        bootstrap_js=_BOOTSTRAP_JS,
        dev_log_js=_DEV_LOG_JS,
        dev_log_meta=(
            '  <meta name="dev-log" content="1">' if dev_log else ""
        ),
        data_generation_meta=data_generation_meta,
        theme_style=_emit_theme_style(theme),
    )


def emit_visual_data_fragment(
    visual_id: str,
    data: Any,
    *,
    url_params: Mapping[str, list[str]] | None = None,
) -> str:
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

    ``url_params`` (AA.B.5.followon.diag) — the URL query params
    that drove this fetch. When supplied, the rendered ``param_*``
    and ``filter_*`` / date entries (everything that influences the
    SQL bind) get stamped as ``data-bound-params='<json>'`` on the
    script tag. That makes failure-capture ``dom.html`` self-
    describing: instead of inferring "did the right param reach the
    server?" from the network log (which already showed 200s with
    empty rows looking identical to no-request-fired), the test
    artifact carries the actual params each visual was queried with.
    The same flow that returns 0 rows for "user picked something
    that matches nothing" can be told apart from "the picked value
    never arrived" by reading one attribute on the script tag.

    JSON serialization uses ``json.dumps`` — caller is responsible
    for shaping the payload (d3-sankey wants
    ``{"nodes": [...], "links": [...]}``; a future TimeSeries kind
    would shape differently).
    """
    del visual_id  # reserved for future debug/diagnostic use
    payload = json.dumps(data, default=_json_default)  # typing-smell: ignore[json-indent]: embedded HTML script payload — compact form keeps DOM small + matches d3 hydration expectations
    bound_attr = ""
    if url_params is not None:
        relevant = {
            k: (vs if len(vs) > 1 else vs[0]) if vs else ""
            for k, vs in url_params.items()
            if (
                k.startswith("param_") or k.startswith("filter_")
                or k in ("date_from", "date_to")
            )
        }
        if relevant:
            # AA.A.9.race — ``separators=(',', ':')`` keeps the JSON byte-
            # identical to JS's ``JSON.stringify(...)`` so the bootstrap.js
            # ``requestedParams === renderedParams`` comparison reduces to
            # a string equality check (no canonicalization needed). Python's
            # default (``{"a": 1}`` with space-after-colon) would mismatch
            # JS's compact form on every key, defeating the provenance check.
            bound_json = html.escape(
                json.dumps(relevant, sort_keys=True, separators=(",", ":")),  # typing-smell: ignore[json-indent]: compact embedded attr value — must fit on one line
                quote=True,
            )
            bound_attr = f' data-bound-params="{bound_json}"'
    return (
        f'<script type="application/json" class="chart-data"'
        f'{bound_attr}>{payload}</script>'
    )


def _row_drill_source_column(source: Any) -> str | None:
    """Column name a row-level drill reads its parameter value from.

    Only ``Dim`` / ``Measure`` object refs carry a column the App2 table
    renderer can resolve against the row's cells. ``DrillStaticDateTime``
    / ``DrillResetSentinel`` writes are QuickSight-isms (date-window
    widening, sentinel reset) with no App2 equivalent — App2's date
    filter defaults to "all rows" and there's no calc-field-backed
    sentinel param — so they're dropped; the bare ``DrillSourceField``
    escape hatch carries no column name, so it's dropped too.
    """
    if isinstance(source, (Dim, Measure)):
        return resolve_column(source.column)
    return None


def _serialize_table_row_drills(visual: Any, dashboard_id: str) -> str:
    """Serialize a Table visual's row-level ``Drill`` actions to the
    ``data-row-drills`` attribute JSON (``""`` when there are none).

    Shape — a JSON array, one entry per drill::

        [{"label": "View Transactions for this transfer",
          "trigger": "DATA_POINT_MENU",
          "target_path": "/dashboards/<dash>/sheets/<target-sheet>",
          "params": [{"name": "pL1TxTransfer", "column": "transfer_id"}]}]

    ``bootstrap.js::wireRowDrills`` reads it: a ``DATA_POINT_CLICK`` drill
    makes each ``<tr>`` left-clickable (navigates to ``target_path`` with
    ``?param_<name>=<row cell value>`` for each ``params`` entry); a
    ``DATA_POINT_MENU`` drill adds a trailing "⋯" button per row that
    opens a ``ctxmenu`` popover listing the drill label(s) (and binds the
    same menu on the row's ``contextmenu`` for QS-gesture parity). All
    drills in one table point into the same analysis (= the same App2
    dashboard), so ``target_path`` uses ``dashboard_id``. ``SameSheetFilter``
    actions are skipped — they're a highlight-without-narrowing
    QS-only construct.
    """
    drills = [a for a in getattr(visual, "actions", ()) if isinstance(a, Drill)]
    if not drills:
        return ""
    out: list[dict[str, Any]] = []
    for d in drills:
        target_sheet = d.target_sheet
        if isinstance(target_sheet, _AutoSentinel):
            continue  # not resolved — App.resolve_auto_ids() didn't run
        params: list[dict[str, str]] = []
        for param, source in d.writes:
            col = _row_drill_source_column(source)
            if col is not None:
                params.append({"name": str(param.name), "column": col})
        out.append({
            "label": d.name,
            "trigger": d.trigger,
            "target_path": (
                f"/dashboards/{dashboard_id}/sheets/{target_sheet.sheet_id}"
            ),
            "params": params,
        })
    if not out:
        return ""
    return json.dumps(out, default=_json_default)  # typing-smell: ignore[json-indent]: compact HTML-attribute payload, parsed client-side by wireRowDrills


def _render_visual(
    visual: Any, dashboard_id: str, sheet_id: str,
    *,
    col_span: int | None = None,
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
    # X.2.g.1.d — section sits inside a 36-col CSS grid. The grid
    # container handles the outer margin; per-section margin is just
    # vertical (mb-2) for visual breathing room. Pre-grid sections
    # used mx-8 directly which is now the grid container's job.
    section_class = (
        "p-4 bg-surface rounded-lg shadow-sm border border-surface-border"
    )
    h2_class = "text-xl font-semibold text-primary-fg mb-1"
    subtitle_class = "subtitle text-sm text-secondary-fg mb-4"

    # CSS grid placement — inline style for the per-slot col-span
    # because Tailwind's arbitrary-value support needs a JIT scan
    # over every possible span (1–36) in the CSS, which we'd rather
    # avoid. Inline style is one number per visual; small + clean.
    grid_style = (
        f' style="grid-column: span {col_span};"' if col_span else ""
    )

    # u.4.e.3 — row-level drills (Table only). The serialized drill list
    # rides a ``data-row-drills`` JSON attribute; ``bootstrap.js``'s
    # ``wireRowDrills`` decorates each rendered ``<tr>`` after the table
    # paints (left-click for ``DATA_POINT_CLICK`` drills, a "⋯" ctxmenu
    # button for ``DATA_POINT_MENU`` drills).
    row_drills_attr = ""
    if kind == "Table":
        row_drills = _serialize_table_row_drills(visual, dashboard_id)
        if row_drills:
            row_drills_attr = (
                f' data-row-drills="{html.escape(row_drills, quote=True)}"'
            )

    parts: list[str] = []
    parts.append(
        f'  <section data-visual-kind="{html.escape(kind)}"'
        f' data-visual-id="{esc_id}"'
        f' data-fetch-url="{esc_url}"'
        f'{row_drills_attr}'
        f' class="{section_class}"{grid_style}>'
    )
    parts.append(f'    <h2 class="{h2_class}">{html.escape(title)}</h2>')
    if subtitle:
        parts.append(
            f'    <p class="{subtitle_class}">{html.escape(subtitle)}</p>'
        )
    # X.2.g.1.a — auto-fetch on load. Without ``hx-trigger="load"``
    # the section sits empty until the user clicks Refresh.
    # X.2.g.1.d — also listen for the global ``refresh`` event the
    # single Refresh button broadcasts via htmx.trigger(body, 'refresh').
    # That replaces the per-visual Refresh buttons we used to emit.
    # AA.B.5.followon — ``hx-sync="this:queue last"`` so a ``refresh``
    # event mid-load **queues** the new request and fires it when the
    # in-flight completes. Prior strategies left visuals stale:
    #   - default ``drop``: refresh silently dropped if mid-load
    #     (AA.B.4.followon).
    #   - ``this:replace``: aborts in-flight + issues new — but the new
    #     request observably never fires under chain conditions (3 of
    #     the slowest 6 visuals stay on the initial-load empty result
    #     while the pick value visibly never reaches their URL). The
    #     ``data-bound-params`` diagnostic + per-visual network capture
    #     proved this: Posted Money Records consistently in the
    #     never-refetches group.
    # ``queue last`` keeps the in-flight, waits for it to complete,
    # then issues exactly one queued request with the latest form
    # state. The visual briefly shows the empty-param result then
    # swaps in the filtered one — minor flicker, full correctness.
    parts.append(
        f'    <div id="visual-data-{esc_id}" class="visual-data"'
        f' hx-get="{esc_url}"'
        f' hx-trigger="load, refresh from:body"'
        f' hx-include="#filter-form"'
        f' hx-sync="this:queue last"'
        f' hx-swap="innerHTML">'
        # AA.B.5.followon.skeleton — initial-load placeholder. Sits as
        # a child of the swap target so the first HTMX response wipes
        # it. Refresh requests re-inject via bootstrap.js's
        # htmx:beforeRequest hook so refetches show the skeleton too.
        # Presence/absence of ``.visual-loading`` is the "is this
        # visual still loading?" signal — tests + drivers can poll
        # ``.visual-data:not(:has(.visual-loading))`` for "done".
        # CSS keeps it at opacity 0 with a 300ms transition delay so
        # fast loads (<300ms) never flash the skeleton.
        f'<div class="visual-loading" aria-hidden="true">'
        f'<div class="skeleton-block"></div>'
        f'</div>'
        f'</div>'
    )
    parts.append("  </section>")
    return "\n".join(parts)
