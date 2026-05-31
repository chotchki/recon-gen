"""BV.4.0 vertical slice — new /training/ landing.

Single-page rebuild per the BV.5 spike (15 design locks). For the
vertical slice this ships:

- Session Start button (orchestrates /etl/run-equivalent → v overlay
  schema + clone + matview refresh).
- Cleanup button (drops v overlay).
- One card for `phantom_rail`: checkbox + inline form (count +
  rail_name primitives) + Clean / Violation dashboard links + the
  section's "What to do about it" copy.
- Apply button — naive clone-and-replay shape per `v_overlay.apply_plants`.

BV.4.4 expands the card set to all 25 kinds + lands DL.9 diff-only
Apply + per-family/top bulk-toggle chips. Vertical slice keeps the
surface minimal to prove the orchestration architecture.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape

from recon_gen.common.html._studio_training_v2 import (
    resolve_section,
)
from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY, PlantKindEntry, PrimitiveIntField,
)


_VERTICAL_SLICE_KIND = "phantom_rail"


def render_training_v3_landing(
    *,
    top_nav_html: str = "",
    devlog_meta: str = "",
    devlog_script: str = "",
    theme_head: str = "",
    asset_url: str = "/static/output.css",
    base_prefix: str,
    v_overlay_exists: bool,
    session_status: str | None = None,
    enabled_kinds: tuple[str, ...] = (),
    form_values: Mapping[str, Mapping[str, str]] | None = None,
) -> str:
    """The new /training/ page — vertical slice rendering.

    Args:
      base_prefix: ``cfg.db_table_prefix`` — the production prefix.
        Clean dashboard link points here.
      v_overlay_exists: ``True`` when ``<base>_v_*`` tables are
        present (Session Start has run). Drives enable/disable of
        Apply + Tour buttons.
      session_status: optional banner text to display (Session Start
        / Apply / Cleanup just completed).
      enabled_kinds: tuple of kind names whose checkboxes were last
        applied (sourced from the v overlay's tracking state).
      form_values: per-kind form-value snapshot, key=kind →
        {field_name: value}.
    """
    cards_html = _render_phantom_rail_card(
        base_prefix=base_prefix,
        v_overlay_exists=v_overlay_exists,
        enabled=(_VERTICAL_SLICE_KIND in enabled_kinds),
        form_values=(form_values or {}).get(_VERTICAL_SLICE_KIND, {}),
    )

    banner_html = ""
    if session_status:
        banner_html = (
            '<div class="bg-success/10 border border-success rounded-md '
            'px-3 py-2 mb-3 text-sm" data-test-training-banner>'
            f'<strong class="text-success">✓</strong> {escape(session_status)}'
            "</div>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio · Training · Dual-prefix v</title>
  {devlog_meta}{theme_head}
  <link rel="stylesheet" href="{escape(asset_url)}">
  {devlog_script}
</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  <header class="px-8 py-4 border-b border-surface-border bg-white">
    <h1 class="text-xl font-semibold m-0">Training</h1>
    <p class="text-sm text-secondary-fg max-w-3xl m-0 mt-1">
      Pick the violation plants you want to study in this session,
      click <strong>Apply</strong>, then use each card's
      <strong>Clean dashboard</strong> / <strong>Violation dashboard</strong>
      links to see the before/after.
      Session Start populates a <code>{escape(base_prefix)}_v</code>
      overlay that all the Violation views read from; your production
      <code>{escape(base_prefix)}</code> prefix is untouched.
    </p>
    {_render_session_controls(v_overlay_exists)}
  </header>
  <main class="px-8 py-6 flex flex-col gap-4">
    {banner_html}
    <form method="post" action="/training/apply" id="training-apply-form" class="flex flex-col gap-4">
      <section class="bg-white border border-surface-border rounded-md p-4">
        <h2 class="text-base font-semibold m-0 mb-3">Plants (vertical slice — 1 of 25)</h2>
        {cards_html}
        <div class="flex items-center gap-3 mt-4">
          <button type="submit" id="training-apply-btn"
                  class="px-4 py-2 bg-accent text-accent-fg rounded-sm border border-accent text-sm font-semibold hover:opacity-85"
                  {("" if v_overlay_exists else "disabled")}>
            ⚡ Apply selection
          </button>
          {("" if v_overlay_exists else '<span class="text-xs text-secondary-fg">Click Session Start first to populate the v overlay.</span>')}
        </div>
      </section>
    </form>
  </main>
</body>
</html>
"""


def _render_session_controls(v_overlay_exists: bool) -> str:
    """Top-of-page button bar: Session Start / Re-clone / Cleanup.

    Buttons enable/disable based on whether the v overlay schema is
    currently present in the DB."""
    start_label = "↻ Re-clone from base" if v_overlay_exists else "▶ Session Start"
    start_title = (
        "Drops the existing v overlay + clones fresh from the base "
        "prefix + refreshes v matviews. Plant state resets."
        if v_overlay_exists else
        "Creates the <base>_v_* schema + clones data from the base "
        "prefix + refreshes v matviews. (For the vertical slice this "
        "skips the /etl/run leg; BV.4.1 final wires it in.)"
    )
    cleanup_btn = (
        '<form method="post" action="/training/cleanup" class="inline-block ml-2">'
        '<button type="submit" id="training-cleanup-btn" '
        'class="px-3 py-1.5 bg-warning text-white rounded-sm border border-warning text-xs font-semibold hover:opacity-85" '
        'title="Drops the &lt;base&gt;_v_* schema. Base prefix untouched.">'
        "🗑 Cleanup"
        "</button>"
        "</form>"
        if v_overlay_exists else ""
    )
    return f"""
    <div class="mt-3 inline-flex items-center gap-2">
      <form method="post" action="/training/session-start" class="inline-block">
        <button type="submit" id="training-session-start-btn"
                class="px-3 py-1.5 bg-accent text-accent-fg rounded-sm border border-accent text-xs font-semibold hover:opacity-85"
                title="{escape(start_title)}">
          {start_label}
        </button>
      </form>
      {cleanup_btn}
    </div>
    """


def _render_phantom_rail_card(
    *,
    base_prefix: str,
    v_overlay_exists: bool,
    enabled: bool,
    form_values: Mapping[str, str],
) -> str:
    """The single-kind card for the vertical slice."""
    entry = _get_entry(_VERTICAL_SLICE_KIND)
    section = resolve_section(entry)
    primitives_html = "\n".join(
        _render_primitive_field(p, form_values.get(p.name))
        for p in entry.primitives
    )
    v_prefix = f"{base_prefix}_v"
    tour_url = entry.tour_destination.primary_url
    clean_link = (
        f'<a class="text-accent hover:underline text-sm font-semibold" '
        f'href="{escape(tour_url)}?prefix={escape(base_prefix)}" '
        'data-test-tour-clean>Clean dashboard →</a>'
    )
    violation_link = (
        f'<a class="text-accent hover:underline text-sm font-semibold" '
        f'href="{escape(tour_url)}?prefix={escape(v_prefix)}" '
        'data-test-tour-violation>Violation dashboard →</a>'
    ) if v_overlay_exists else (
        '<span class="text-xs text-secondary-fg">(Violation dashboard '
        'available after Session Start + Apply)</span>'
    )
    checked_attr = " checked" if enabled else ""
    return f"""
    <article class="border border-surface-border rounded-md p-4 flex flex-col gap-2"
             data-test-training-kind="{escape(entry.kind)}">
      <header class="flex items-baseline gap-3">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" name="enabled_kinds" value="{escape(entry.kind)}"{checked_attr}
                 data-test-training-enable-{escape(entry.kind)}>
          <span class="text-sm font-semibold">{escape(section.title)}</span>
        </label>
        <span class="text-xs text-secondary-fg font-mono">{escape(entry.kind)}</span>
      </header>
      <p class="text-xs text-secondary-fg max-w-3xl m-0">
        {escape(_first_sentence(section.short_statement))}
      </p>
      <div class="mt-2 flex flex-wrap gap-3 items-end">
        {primitives_html}
      </div>
      <p class="text-xs m-0 mt-2"><strong>What to do about it:</strong>
        {escape(section.what_to_do)}</p>
      <div class="mt-2 flex items-center gap-4">
        {clean_link}
        {violation_link}
      </div>
    </article>
    """


def _render_primitive_field(
    primitive: object, form_value: str | None,
) -> str:
    """Inline-on-card rendering of a primitive (count int / rail_name
    string for phantom_rail). Mirrors BU.1's v2 shape but smaller."""
    from recon_gen.common.l2.plant_registry import (  # noqa: PLC0415
        PrimitiveStringField,
    )

    if isinstance(primitive, PrimitiveIntField):
        value = form_value if form_value is not None else str(primitive.default)
        attrs: list[str] = []
        if primitive.min_value is not None:
            attrs.append(f'min="{primitive.min_value}"')
        if primitive.max_value is not None:
            attrs.append(f'max="{primitive.max_value}"')
        return (
            '<label class="flex flex-col gap-1">'
            f'<span class="text-xs text-secondary-fg">{escape(primitive.label)}</span>'
            f'<input type="number" name="form_{escape(primitive.name)}" '
            f'value="{escape(value)}" {" ".join(attrs)} '
            'class="px-2 py-1 border border-surface-border rounded-sm text-sm bg-white w-24">'
            '</label>'
        )
    assert isinstance(primitive, PrimitiveStringField)
    value = form_value if form_value is not None else primitive.default
    return (
        '<label class="flex flex-col gap-1">'
        f'<span class="text-xs text-secondary-fg">{escape(primitive.label)}</span>'
        f'<input type="text" name="form_{escape(primitive.name)}" '
        f'value="{escape(value)}" '
        'class="px-2 py-1 border border-surface-border rounded-sm text-sm bg-white w-48">'
        '</label>'
    )


def _first_sentence(text: str) -> str:
    """Trim a short-statement paragraph to its first sentence for
    card-density."""
    if not text:
        return ""
    first = text.split(". ", 1)[0]
    if first and not first.endswith("."):
        first += "."
    return first


def _get_entry(kind: str) -> PlantKindEntry:
    """Vertical-slice helper — assert lookup returns the kind."""
    for entry in PLANT_REGISTRY:
        if entry.kind == kind:
            return entry
    raise KeyError(f"Registry missing vertical-slice kind {kind!r}")
