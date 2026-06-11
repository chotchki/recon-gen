"""BX.9 (2026-06-11) — theme editor reorder: essentials-first + live-preview.

Operator-locked design (Option C, 2026-06-11):
- ``ThemePreset`` schema unchanged — the BX.9 work is rendering-only.
- Most institutions only customize their primary brand colour + a
  secondary surface + the logo URL. The three "essentials" render at
  the top of the form with a live-preview sample card next to them;
  everything else collapses into a ``<details>Advanced</details>``
  block (default-closed unless the L2 already customized one of the
  advanced fields, in which case default-open so the operator sees
  what they had set).
- Auto-save on field blur (no Save button per the BX.9 lock) via
  debounced ``hx-post="/l2_shape/theme/field"``.
- Live-preview re-renders through ``hx-post="/l2_shape/theme/preview"``
  on input events.

This module lives separate from ``_studio_editor_routes.py`` so the BX.9
surface can be reasoned about independently. The route registration +
``_render_theme_form`` callsite still live in ``_studio_editor_routes.py``;
this module exports the helpers + the two new handlers.
"""

from __future__ import annotations

import dataclasses as dc
from collections.abc import Callable, Mapping
from html import escape
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from recon_gen.common.html._studio_assets.tw_classes import (
    field_input_classes,
    field_row_classes,
)
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.editor import singleton_save_l2
from recon_gen.common.l2.validate import L2ValidationError, validate
from recon_gen.common.theme import DEFAULT_PRESET


# Typing-smell: the theme-dict-from-instance fetcher comes from
# _studio_editor_routes (closure-friendly) and operates on an
# L2Instance dataclass — we pass it in instead of importing because
# this module sits below that one in the dependency graph.
_ThemeDictFetcher = Callable[[Any], dict[str, object]]  # typing-smell: ignore[explicit-any]: L2Instance dataclass


# Re-export the private helpers so pyright's strict reportUnusedFunction
# walk sees them as load-bearing (consumers reach in via lazy imports
# inside _studio_editor_routes._render_theme_form, which pyright can't
# trace at module-load time).
__all__ = [
    "_THEME_ESSENTIAL_COLOR_FIELDS",
    "_THEME_ESSENTIAL_FIELDS",
    "_THEME_ESSENTIAL_URL_FIELDS",
    "_render_advanced_details_open",
    "_render_theme_essentials_section",
    "_render_theme_preview_card",
    "_theme_advanced_has_customizations",
    "_theme_default_dict",
    "make_theme_field_save_handler",
    "make_theme_preview_handler",
]


# ---------------------------------------------------------------------------
# Essentials field set (operator-locked)
# ---------------------------------------------------------------------------


# Operator-locked essentials per BX.9 (Option C). Three fields, no more:
# the primary brand colour (accent), a secondary surface for visual
# contrast (secondary_bg), and the logo URL.
_THEME_ESSENTIAL_COLOR_FIELDS: tuple[str, ...] = ("accent", "secondary_bg")
_THEME_ESSENTIAL_URL_FIELDS: tuple[str, ...] = ("logo",)
_THEME_ESSENTIAL_FIELDS: tuple[str, ...] = (
    *_THEME_ESSENTIAL_COLOR_FIELDS,
    *_THEME_ESSENTIAL_URL_FIELDS,
)


# ---------------------------------------------------------------------------
# DEFAULT_PRESET as a dict — for the customization detection
# ---------------------------------------------------------------------------


def _theme_default_dict() -> dict[str, object]:
    """Return DEFAULT_PRESET as the same dict shape ``_theme_dict_from_instance``
    produces. None-valued optionals are dropped so the comparison in
    ``_theme_advanced_has_customizations`` lines up 1:1.
    """
    raw = dc.asdict(DEFAULT_PRESET)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]  # WHY: ThemePreset is a dataclass; asdict returns dict[str, Any]
    return {k: v for k, v in raw.items() if v is not None}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]  # WHY: dataclasses.asdict element type is Any


def _theme_advanced_has_customizations(
    theme_dict: Mapping[str, object],
    *,
    advanced_color_fields: frozenset[str],
    advanced_url_fields: frozenset[str],
    text_field_names: frozenset[str],
) -> bool:
    """Does any Advanced-section field differ from DEFAULT_PRESET? When
    yes, the ``<details>`` block renders with the ``open`` attribute so
    the operator sees what they had customized. Essentials are excluded.

    The advanced-field name sets are passed in because they're built off
    the field-spec tuples in ``_studio_editor_routes.py`` — keeping the
    caller in charge of the source-of-truth means this module doesn't
    have to mirror those tuples.
    """
    defaults = _theme_default_dict()
    advanced_keys = (
        advanced_color_fields
        | text_field_names
        | advanced_url_fields
        | {"data_colors", "empty_fill_color", "gradient"}
    )
    for key in advanced_keys:
        current = theme_dict.get(key)
        default = defaults.get(key)
        if current is None and default is None:
            continue
        if isinstance(current, list) and isinstance(default, list):
            if list(current) != list(default):  # pyright: ignore[reportUnknownArgumentType]: form-derived list[Any]
                return True
            continue
        if current != default:
            if isinstance(current, str) and not current.strip() and default is None:
                continue
            return True
    return False


# ---------------------------------------------------------------------------
# Live-preview card
# ---------------------------------------------------------------------------


def _render_theme_preview_card(theme_dict: Mapping[str, object]) -> str:
    """Sample card showing how the essentials apply.

    Renders a small mock dashboard card using the operator's current
    ``accent`` + ``secondary_bg`` + ``logo`` (with fallbacks to
    DEFAULT_PRESET when blank). Lives inside ``#theme-preview-card`` so
    the HTMX hx-target swap can replace it in place.
    """
    defaults = _theme_default_dict()

    def _color(name: str) -> str:
        v = str(theme_dict.get(name, "") or "").strip()
        if v:
            return v
        return str(defaults.get(name, "#cccccc"))

    accent = _color("accent")
    accent_fg = _color("accent_fg")
    secondary_bg = _color("secondary_bg")
    primary_bg = _color("primary_bg")
    primary_fg = _color("primary_fg")
    logo_raw = str(theme_dict.get("logo", "") or "").strip()
    logo_html = ""
    if logo_raw and logo_raw.startswith(("http://", "https://", "//")):
        logo_html = (
            f'<img src="{escape(logo_raw)}" alt="logo" '
            f'class="h-8 w-auto" style="max-height:32px;">'
        )
    elif logo_raw:
        logo_html = (
            f'<span class="text-xs italic" style="color:{escape(primary_fg)};">'
            f'(logo path: {escape(logo_raw)})</span>'
        )
    return (
        f'<div id="theme-preview-card" data-role="theme-preview" '
        f'class="border border-surface-border rounded-md overflow-hidden '
        f'shadow-sm w-full max-w-sm">'
        f'<div class="flex items-center gap-2 px-3 py-2" '
        f'style="background:{escape(accent)};color:{escape(accent_fg)};">'
        f'{logo_html}'
        f'<span class="text-sm font-semibold">Your Institution</span>'
        f'</div>'
        f'<div class="px-3 py-3 flex flex-col gap-2" '
        f'style="background:{escape(primary_bg)};color:{escape(primary_fg)};">'
        f'<div class="text-xs">Net Position</div>'
        f'<div class="text-lg font-semibold">$1,234,567.89</div>'
        f'<div class="rounded-sm px-2 py-1 text-xs" '
        f'style="background:{escape(secondary_bg)};color:{escape(primary_fg)};">'
        f'Secondary surface — sub-cards / striped rows.</div>'
        f'<button type="button" class="rounded-sm px-3 py-1 text-xs '
        f'self-start cursor-default" '
        f'style="background:{escape(accent)};color:{escape(accent_fg)};">'
        f'Primary action</button>'
        f'</div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Form-row rendering — HTMX wiring for auto-save + live-preview
# ---------------------------------------------------------------------------


def _theme_field_save_attrs(name: str) -> str:
    """HTMX attributes for per-field auto-save on blur (debounced 300ms)."""
    return (
        ' hx-post="/l2_shape/theme/field" '
        f'hx-trigger="blur changed delay:300ms" '
        f'hx-vals=\'{{"_field": "{name}"}}\' '
        f'hx-swap="none"'
    )


def _theme_field_preview_attrs() -> str:
    """HTMX attributes for live-preview re-render on input (debounced 200ms)."""
    return (
        ' hx-post="/l2_shape/theme/preview" '
        'hx-trigger="input changed delay:200ms" '
        'hx-include="[data-theme-essential] [name]" '
        'hx-target="#theme-preview-card" '
        'hx-swap="outerHTML"'
    )


def _render_essentials_color_row(
    name: str, label: str, helper: str, value: str,
) -> str:
    """Color picker row for the essentials section. Auto-save on blur
    + live-preview on input event."""
    row_cls = field_row_classes()
    label_cls = "font-semibold text-xs text-primary-fg"
    helper_cls = "text-xs text-secondary-fg"
    input_cls = field_input_classes()
    hex_value = value if value else "#cccccc"
    save_attrs = _theme_field_save_attrs(name)
    preview_attrs = _theme_field_preview_attrs()
    return (
        f'<div class="{row_cls}">'
        f'<label for="field-{name}-hex" class="{label_cls}">'
        f'{escape(label)}</label>'
        f'<div class="flex items-center gap-3">'
        f'<input type="color" id="field-{name}-color" '
        f'value="{escape(hex_value)}" '
        f'class="w-10 h-9 p-0 border border-surface-border rounded-sm cursor-pointer" '
        f'{preview_attrs} '
        f'oninput="var v=this.value;document.getElementById(&quot;field-{name}-hex&quot;).value=v;">'
        f'<input type="text" id="field-{name}-hex" name="{name}" '
        f'value="{escape(value)}" '
        f'placeholder="#aabbcc" '
        f'class="{input_cls} font-mono tabular-nums w-32" '
        f'{save_attrs} '
        f'{preview_attrs} '
        f'oninput="if (/^#[0-9a-fA-F]{{6}}$/.test(this.value)){{'
        f'document.getElementById(&quot;field-{name}-color&quot;).value=this.value;}}">'
        f'</div>'
        f'<small class="{helper_cls}">{escape(helper)}</small>'
        f'</div>'
    )


def _render_essentials_url_row(
    name: str, label: str, helper: str, value: str,
) -> str:
    """URL input row for the essentials section (logo)."""
    row_cls = field_row_classes()
    label_cls = "font-semibold text-xs text-primary-fg"
    helper_cls = "text-xs text-secondary-fg"
    input_cls = field_input_classes()
    save_attrs = _theme_field_save_attrs(name)
    preview_attrs = _theme_field_preview_attrs()
    return (
        f'<div class="{row_cls}">'
        f'<label for="field-{name}" class="{label_cls}">{escape(label)}</label>'
        f'<input type="text" id="field-{name}" name="{name}" '
        f'value="{escape(value)}" placeholder="https://… or /abs/path.png" '
        f'class="{input_cls}" '
        f'{save_attrs} '
        f'{preview_attrs}>'
        f'<small class="{helper_cls}">{escape(helper)}</small>'
        f'</div>'
    )


def _render_theme_essentials_section(
    theme_dict: Mapping[str, object],
    *,
    color_field_lookup: Mapping[str, tuple[str, str]],
    url_field_lookup: Mapping[str, tuple[str, str]],
) -> str:
    """Top-of-form essentials block + live-preview card.

    ``color_field_lookup`` / ``url_field_lookup`` map field-name →
    (label, helper) — caller supplies them from the source-of-truth
    tuples in ``_studio_editor_routes.py`` so labels stay in sync.
    """
    helper_cls = "text-xs text-secondary-fg"
    section_cls = (
        "border border-surface-border rounded-md p-4 mb-4 "
        "bg-white flex flex-col gap-3"
    )
    section_label_cls = (
        "font-semibold text-sm text-primary-fg flex items-center gap-2"
    )
    rows: list[str] = []
    for fname in _THEME_ESSENTIAL_COLOR_FIELDS:
        label, helper = color_field_lookup[fname]
        value = str(theme_dict.get(fname, "") or "")
        rows.append(
            _render_essentials_color_row(fname, label, helper, value),
        )
    for fname in _THEME_ESSENTIAL_URL_FIELDS:
        label, helper = url_field_lookup[fname]
        value = str(theme_dict.get(fname, "") or "")
        rows.append(
            _render_essentials_url_row(fname, label, helper, value),
        )
    preview_card = _render_theme_preview_card(theme_dict)
    intro_html = (
        f'<p class="{helper_cls} m-0">'
        f'Most institutions only need the essentials below. '
        f'Advanced controls available for fine-tuning.'
        f'</p>'
    )
    return (
        f'<section data-section-name="essentials" '
        f'data-role="theme-essentials" '
        f'data-theme-essential="true" class="{section_cls}">'
        f'<h2 class="{section_label_cls} m-0">Essentials</h2>'
        f'{intro_html}'
        f'<div class="flex flex-col md:flex-row gap-4">'
        f'<div class="flex-1 flex flex-col gap-2">'
        f'{"".join(rows)}'
        f'</div>'
        f'<div class="flex-1 flex items-start justify-center pt-2">'
        f'{preview_card}'
        f'</div>'
        f'</div>'
        f'</section>'
    )


def _render_advanced_details_open(theme_dict: Mapping[str, object]) -> str:
    """Return the ``<details ...>`` opening tag for the Advanced wrapper.

    Reads ``_THEME_COLOR_FIELDS`` / ``_THEME_TEXT_FIELDS`` /
    ``_THEME_OPTIONAL_URL_FIELDS`` from ``_studio_editor_routes`` (lazy
    import to avoid a cycle) and computes whether to add the ``open``
    attribute based on whether any Advanced-bucket field is non-default.
    """
    from recon_gen.common.html._studio_editor_routes import (  # noqa: PLC0415 — cycle break
        _THEME_COLOR_FIELDS,
        _THEME_OPTIONAL_URL_FIELDS,
        _THEME_TEXT_FIELDS,
    )

    all_color_field_names = {fname for fname, _, _, _ in _THEME_COLOR_FIELDS}
    advanced_color_fields = frozenset(
        all_color_field_names - set(_THEME_ESSENTIAL_COLOR_FIELDS),
    )
    text_field_names = frozenset(
        fname for fname, _, _, _, _ in _THEME_TEXT_FIELDS
    )
    url_field_names = {fname for fname, _, _ in _THEME_OPTIONAL_URL_FIELDS}
    advanced_url_fields = frozenset(
        url_field_names - set(_THEME_ESSENTIAL_URL_FIELDS),
    )
    advanced_open = ' open' if _theme_advanced_has_customizations(
        theme_dict,
        advanced_color_fields=advanced_color_fields,
        advanced_url_fields=advanced_url_fields,
        text_field_names=text_field_names,
    ) else ''
    return (
        f'<details data-section-name="advanced" '
        f'data-role="theme-advanced" class="mb-4"{advanced_open}>'
        f'<summary class="cursor-pointer font-semibold text-sm '
        f'text-primary-fg py-2">Advanced</summary>'
        f'<div class="pt-3">'
    )


# ---------------------------------------------------------------------------
# Route handlers — auto-save + live-preview endpoints
# ---------------------------------------------------------------------------


def make_theme_field_save_handler(
    cache: L2InstanceCache,
    theme_dict_from_instance: _ThemeDictFetcher,
) -> Callable[[Request], Any]:
    """Build the per-field auto-save handler bound to the L2 cache.

    POST /l2_shape/theme/field expects:
        _field=<field-name>
        <field-name>=<value>

    Merges into the current theme dict, runs the full ``singleton_save_l2``
    validation path, persists on success. Returns 204 on success
    (HTMX ``hx-swap="none"`` ignores the body), 400 with the validator's
    message on failure.
    """
    import yaml as _yaml  # noqa: PLC0415

    async def theme_field_save(request: Request) -> Response:
        form = await request.form()
        field_name = str(form.get("_field", "")).strip()
        if not field_name:
            return HTMLResponse("missing _field", status_code=400)
        current: dict[str, object] = theme_dict_from_instance(cache.get())
        new_value = str(form.get(field_name, "")).strip()
        if new_value:
            current[field_name] = new_value
        else:
            current.pop(field_name, None)
        yaml_text = _yaml.safe_dump(
            current, default_flow_style=False, sort_keys=False,
        ) if current else ""
        try:
            new_inst = singleton_save_l2(cache.get(), "theme", yaml_text)
        except ValueError as exc:
            return HTMLResponse(
                f'<div role="alert" data-role="theme-field-error" '
                f'class="text-xs text-danger">{escape(str(exc))}</div>',
                status_code=400,
            )
        try:
            validate(new_inst)
        except L2ValidationError as exc:
            return HTMLResponse(
                f'<div role="alert" data-role="theme-field-error" '
                f'class="text-xs text-danger">{escape(str(exc))}</div>',
                status_code=400,
            )
        cache.save(new_inst)
        return Response(status_code=204)

    return theme_field_save


def make_theme_preview_handler(
    cache: L2InstanceCache,
    theme_dict_from_instance: _ThemeDictFetcher,
) -> Callable[[Request], Any]:
    """Build the live-preview handler bound to the L2 cache.

    POST /l2_shape/theme/preview expects the essentials field values
    (form-encoded). Returns the preview card HTML — NO persistence.
    The card composes the operator's typed essentials over the
    persisted theme's other fields so colours like ``accent_fg`` /
    ``primary_bg`` continue to read from the cached values.
    """
    async def theme_preview(request: Request) -> HTMLResponse:
        form = await request.form()
        cached: dict[str, object] = theme_dict_from_instance(cache.get())
        for key in _THEME_ESSENTIAL_FIELDS:
            raw = form.get(key)
            if raw is not None:
                v = str(raw).strip()
                if v:
                    cached[key] = v
                else:
                    cached.pop(key, None)
        return HTMLResponse(_render_theme_preview_card(cached))

    return theme_preview
