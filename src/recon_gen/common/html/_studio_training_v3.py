"""BV.4.0+4.4 — /training/ landing surface.

The new Trainer landing per BV.5 spike (15 design locks). Single
page, all 25 plant kinds in per-family accordions, checkbox + inline
form fields + Clean / Violation Tour links per card. Session Start
populates a `<base>_v_*` overlay; Apply mutates it to match the
checkbox state. Two Tour links per card route between the base
prefix (Clean) and the v overlay (Violation) via the `?prefix=` URL
param the BV.4.2 work threads through dashboard routes.

BV.4.0 — vertical slice (1 card, phantom_rail).
BV.4.4 — scale to all 25 cards, per-family accordions, bulk-toggle
chips, selection-density badges, top-level filter, collision-safe
form field naming (`form_<kind>_<primitive>`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from html import escape

from recon_gen.common.html._studio_training_v2 import (
    resolve_section,
)
from recon_gen.common.html.render import _render_inline_markdown
from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY, PlantCategory, PlantKindEntry,
    PrimitiveIntField, PrimitiveStringField,
)


# Per-family display order on the landing — matches the §0.5 matrix.
_FAMILY_ORDER: tuple[str, ...] = (
    "L1 Conservation",
    "L1 Cap",
    "L1 Aging",
    "L1 Chain coherence",
    "L1 Audit",
    "L2 Triage gaps",
    "L2 Coverage gaps",
    "L2FT Hygiene",
)


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
    failed_kinds: Mapping[str, str] | None = None,
    l2_stale: bool = False,
    session_start_time: str = "",
) -> str:
    """The /training/ landing.

    Args:
      base_prefix: ``cfg.db_table_prefix`` — the production prefix.
        Clean dashboard link points here.
      v_overlay_exists: ``True`` when ``<base>_v_*`` tables are
        present (Session Start has run). Drives enable/disable of
        Apply + Tour buttons.
      session_status: optional banner text to display.
      enabled_kinds: kinds whose checkboxes were last applied.
      form_values: per-kind form-value snapshot.
    """
    enabled_set = set(enabled_kinds)
    fv = form_values or {}
    failed = failed_kinds or {}

    # Group registry entries by family in display order.
    by_family: dict[str, list[PlantKindEntry]] = {}
    for entry in PLANT_REGISTRY:
        by_family.setdefault(entry.family, []).append(entry)

    families_html: list[str] = []
    for idx, family in enumerate(_FAMILY_ORDER):
        entries = by_family.get(family, [])
        if not entries:
            continue
        families_html.append(_render_family_section(
            family, entries,
            enabled_set=enabled_set,
            form_values=fv,
            failed=failed,
            base_prefix=base_prefix,
            v_overlay_exists=v_overlay_exists,
            open_by_default=(idx == 0),
        ))
    # Any registry families not in _FAMILY_ORDER (defensive — a new
    # family lands without _FAMILY_ORDER update) render at the end.
    for family, entries in by_family.items():
        if family in _FAMILY_ORDER:
            continue
        families_html.append(_render_family_section(
            family, entries,
            enabled_set=enabled_set,
            form_values=fv,
            failed=failed,
            base_prefix=base_prefix,
            v_overlay_exists=v_overlay_exists,
            open_by_default=False,
        ))

    banner_html = ""
    if session_status:
        banner_html += (
            '<div class="bg-success/10 border border-success rounded-md '
            'px-3 py-2 mb-3 text-sm" data-test-training-banner>'
            f'<strong class="text-success">✓</strong> {escape(session_status)}'
            "</div>"
        )
    if l2_stale:
        banner_html += (
            '<div class="bg-warning/10 border border-warning rounded-md '
            'px-3 py-2 mb-3 text-sm" data-test-l2-stale-banner>'
            '<strong class="text-warning">⚠</strong> '
            'Your L2 yaml has changed since this Session Start'
            f'{f" ({escape(session_start_time)})" if session_start_time else ""}. '
            'Click <strong>Session Start (re-fetch)</strong> to pick up the new schema '
            '+ reseed the base + re-clone the v overlay.'
            "</div>"
        )
    if failed:
        failed_summary = ", ".join(sorted(failed.keys())[:5])
        if len(failed) > 5:
            failed_summary += f", … +{len(failed) - 5} more"
        banner_html += (
            '<div class="bg-danger/10 border border-danger rounded-md '
            'px-3 py-2 mb-3 text-sm" data-test-failed-banner>'
            f'<strong class="text-danger">✗</strong> {len(failed)} plant(s) '
            f'failed on the last Apply: {escape(failed_summary)}. '
            "Hover the card's error badge for the underlying message."
            "</div>"
        )

    total_enabled = sum(
        1 for entry in PLANT_REGISTRY if entry.kind in enabled_set
    )
    total_kinds = len(PLANT_REGISTRY)

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
      <section class="bg-white border border-surface-border rounded-md p-4 flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <span class="text-sm font-semibold" data-test-top-density>
            {total_enabled}/{total_kinds} plants enabled
          </span>
          <button type="button" data-test-top-all
                  class="text-xs px-2 py-1 border border-surface-border rounded-sm hover:bg-accent/10 cursor-pointer"
                  onclick="window._bvToggleAll(true)">[Select all]</button>
          <button type="button" data-test-top-none
                  class="text-xs px-2 py-1 border border-surface-border rounded-sm hover:bg-accent/10 cursor-pointer"
                  onclick="window._bvToggleAll(false)">[None]</button>
        </div>
        <div class="flex items-center gap-2">
          <label class="text-xs text-secondary-fg">Show:</label>
          <select id="bv-show-filter"
                  class="text-xs px-2 py-1 border border-surface-border rounded-sm bg-white cursor-pointer"
                  onchange="window._bvApplyFilter(this.value)">
            <option value="all">All</option>
            <option value="enabled">Only enabled</option>
            <option value="errors">Only with errors</option>
          </select>
        </div>
      </section>
      <div id="bv-families" class="flex flex-col gap-2">
        {chr(10).join(families_html)}
      </div>
      <div id="bv-empty-state" data-test-empty-state
           class="hidden bg-surface border border-surface-border rounded-md p-6 text-center text-sm text-secondary-fg">
        <p class="font-semibold mb-1">No plants match this filter.</p>
        <p>Switch the <strong>Show:</strong> selector back to <em>All</em>, or click
        <strong>[Select all]</strong> on a family below to start a teaching session.</p>
      </div>
      <div class="bg-white border border-surface-border rounded-md p-4 sticky bottom-0 flex items-center gap-3 z-10">
        <button type="submit" id="training-apply-btn"
                class="px-4 py-2 bg-accent text-accent-fg rounded-sm border border-accent text-sm font-semibold hover:opacity-85"
                {("" if v_overlay_exists else "disabled")}>
          ⚡ Apply selection
        </button>
        {("" if v_overlay_exists else '<span class="text-xs text-secondary-fg">Click Session Start first to populate the v overlay.</span>')}
      </div>
    </form>
  </main>
  <script>
{_BV_LANDING_JS}
  </script>
</body>
</html>
"""


def _render_session_controls(v_overlay_exists: bool) -> str:
    """Top-of-page Session Start / Re-clone / Cleanup buttons (DL.10).

    Pre-overlay: Session Start only.
    Post-overlay: Re-clone (skip ETL — fast reset) + Session Start
      (full lifecycle — re-runs /etl/run) + Cleanup."""
    session_start_title = (
        "Full lifecycle: runs the /etl/run flow (so base prefix is "
        "current) + drops + creates the v overlay schema + clones "
        "base data + refreshes v matviews. On Oracle this takes ~10 "
        "min for the /etl/run leg; PG ~30s; sqlite ~30s."
    )
    reclone_btn = (
        '<form method="post" action="/training/reclone" class="inline-block">'
        '<button type="submit" id="training-reclone-btn" '
        'class="px-3 py-1.5 bg-accent text-accent-fg rounded-sm border border-accent text-xs font-semibold hover:opacity-85" '
        'title="Skips /etl/run. Just drops + clones from current base '
        '+ refreshes v matviews. For when the operator knows base is '
        'current and wants to reset v overlay plant state.">'
        "↻ Re-clone from base"
        "</button>"
        "</form>"
        if v_overlay_exists else ""
    )
    cleanup_btn = (
        '<form method="post" action="/training/cleanup" class="inline-block">'
        '<button type="submit" id="training-cleanup-btn" '
        'class="px-3 py-1.5 bg-warning text-white rounded-sm border border-warning text-xs font-semibold hover:opacity-85" '
        'title="Drops the &lt;base&gt;_v_* schema. Base prefix untouched.">'
        "🗑 Cleanup"
        "</button>"
        "</form>"
        if v_overlay_exists else ""
    )
    session_start_label = (
        "▶ Session Start (re-fetch)" if v_overlay_exists
        else "▶ Session Start"
    )
    return f"""
    <div class="mt-3 inline-flex items-center gap-2">
      <form method="post" action="/training/session-start" class="inline-block">
        <button type="submit" id="training-session-start-btn"
                class="px-3 py-1.5 bg-accent text-accent-fg rounded-sm border border-accent text-xs font-semibold hover:opacity-85"
                title="{escape(session_start_title)}">
          {session_start_label}
        </button>
      </form>
      {reclone_btn}
      {cleanup_btn}
    </div>
    """


def _render_family_section(
    family: str, entries: list[PlantKindEntry],
    *,
    enabled_set: set[str],
    form_values: Mapping[str, Mapping[str, str]],
    failed: Mapping[str, str],
    base_prefix: str,
    v_overlay_exists: bool,
    open_by_default: bool,
) -> str:
    """One `<details>` accordion per family, rendering all member
    cards. Summary line carries the family pretty-label + selection-
    density badge."""
    enabled_in_family = sum(
        1 for entry in entries if entry.kind in enabled_set
    )
    total_in_family = len(entries)
    cards_html = "\n".join(
        _render_card(
            entry,
            enabled=(entry.kind in enabled_set),
            form_values=form_values.get(entry.kind, {}),
            failed_message=failed.get(entry.kind),
            base_prefix=base_prefix,
            v_overlay_exists=v_overlay_exists,
        )
        for entry in entries
    )
    open_attr = " open" if open_by_default else ""
    # JS-safe family-id (drop spaces) for the bulk-toggle target.
    family_id = family.replace(" ", "_")
    return (
        '<details class="bg-white border border-surface-border rounded-md overflow-hidden" '
        f'data-test-training-family="{escape(family)}"{open_attr}>'
        '<summary class="cursor-pointer px-4 py-3 font-semibold hover:bg-surface-bg flex items-center gap-3 flex-wrap">'
        f'<span>{escape(family)}</span>'
        f'<span class="text-xs font-normal text-secondary-fg" data-test-family-badge data-family="{escape(family_id)}">'
        f'({enabled_in_family}/{total_in_family} enabled)</span>'
        '<button type="button" '
        f'data-test-family-all="{escape(family_id)}" '
        'class="text-xs px-2 py-1 border border-surface-border rounded-sm hover:bg-accent/10 cursor-pointer font-normal" '
        f'onclick="event.preventDefault(); event.stopPropagation(); window._bvToggleFamily(\'{escape(family_id)}\', true)">[all]</button>'
        '<button type="button" '
        f'data-test-family-none="{escape(family_id)}" '
        'class="text-xs px-2 py-1 border border-surface-border rounded-sm hover:bg-accent/10 cursor-pointer font-normal" '
        f'onclick="event.preventDefault(); event.stopPropagation(); window._bvToggleFamily(\'{escape(family_id)}\', false)">[none]</button>'
        '</summary>'
        f'<div class="px-4 pb-4 flex flex-col gap-3" data-family-body="{escape(family_id)}">'
        f'{cards_html}'
        '</div>'
        '</details>'
    )


def _render_card(
    entry: PlantKindEntry,
    *,
    enabled: bool,
    form_values: Mapping[str, str],
    failed_message: str | None,
    base_prefix: str,
    v_overlay_exists: bool,
) -> str:
    """One kind's card. Carries title + description + checkbox + per-
    kind inline form fields + Clean/Violation Tour links + What-to-do
    copy. Per the BV.5 spike's Card-as-Anchor lock (DL.8)."""
    section = resolve_section(entry)
    primitives_html = "\n".join(
        _render_primitive_field(entry.kind, p, form_values.get(p.name))
        for p in entry.primitives
    )
    if not entry.primitives:
        primitives_html = (
            '<p class="text-xs text-secondary-fg m-0">'
            "(No operator-tunable parameters — the L2 declaration "
            "determines the planted scenario.)</p>"
        )
    v_prefix = f"{base_prefix}_v"
    tour_url = entry.tour_destination.primary_url
    clean_link = (
        f'<a class="text-accent hover:underline text-sm font-semibold" '
        f'href="{escape(tour_url)}?prefix={escape(base_prefix)}" '
        f'data-test-tour-clean-{escape(entry.kind)}>Clean dashboard →</a>'
    )
    violation_link = (
        f'<a class="text-accent hover:underline text-sm font-semibold" '
        f'href="{escape(tour_url)}?prefix={escape(v_prefix)}" '
        f'data-test-tour-violation-{escape(entry.kind)}>Violation dashboard →</a>'
    ) if v_overlay_exists else (
        '<span class="text-xs text-secondary-fg">'
        '(Violation dashboard available after Session Start + Apply)'
        '</span>'
    )
    checked_attr = " checked" if enabled else ""
    qualifier_html = (
        f'<span class="text-xs text-secondary-fg">— {escape(entry.kind_qualifier)}</span>'
        if entry.kind_qualifier else ""
    )
    error_badge_html = ""
    error_attr = ""
    card_bg = ""
    if failed_message:
        error_attr = ' data-error="1"'
        card_bg = ' bg-danger/5'
        error_badge_html = (
            '<span class="text-xs px-2 py-0.5 bg-danger text-white rounded-sm" '
            f'title="{escape(failed_message)}" '
            f'data-test-error-badge-{escape(entry.kind)}>error planting</span>'
        )
    return f"""
    <article class="border border-surface-border rounded-md p-4 flex flex-col gap-2{card_bg}"
             data-test-training-kind="{escape(entry.kind)}"{error_attr}>
      <header class="flex items-baseline gap-3 flex-wrap">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" name="enabled_kinds" value="{escape(entry.kind)}"{checked_attr}
                 data-test-training-enable-{escape(entry.kind)}>
          <span class="text-sm font-semibold">{escape(section.title)}</span>
        </label>
        {qualifier_html}
        <span class="text-xs text-secondary-fg font-mono">{escape(entry.kind)}</span>
        {error_badge_html}
      </header>
      <p class="text-xs text-secondary-fg max-w-3xl m-0">
        {_render_inline_markdown(_first_sentence(section.short_statement))}
      </p>
      <div class="mt-2 flex flex-wrap gap-3 items-end">
        {primitives_html}
      </div>
      <details class="mt-2 text-xs">
        <summary class="cursor-pointer text-secondary-fg">What to do about it</summary>
        <div class="mt-1 prose prose-xs max-w-none">{_render_inline_markdown(section.what_to_do)}</div>
      </details>
      <div class="mt-2 flex items-center gap-4">
        {clean_link}
        {violation_link}
      </div>
    </article>
    """


def _render_primitive_field(
    kind: str,
    primitive: PrimitiveIntField | PrimitiveStringField,
    form_value: str | None,
) -> str:
    """Inline-on-card rendering of a primitive.

    Form-field naming is `form_<kind>_<primitive_name>` to avoid
    collision across kinds when many cards co-exist on the same page
    (BV.4.4 scale-to-25 requirement)."""
    field_name = f"form_{kind}_{primitive.name}"

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
            f'<input type="number" name="{escape(field_name)}" '
            f'value="{escape(value)}" {" ".join(attrs)} '
            'class="px-2 py-1 border border-surface-border rounded-sm text-sm bg-white w-24">'
            '</label>'
        )
    assert isinstance(primitive, PrimitiveStringField)
    value = form_value if form_value is not None else primitive.default
    return (
        '<label class="flex flex-col gap-1">'
        f'<span class="text-xs text-secondary-fg">{escape(primitive.label)}</span>'
        f'<input type="text" name="{escape(field_name)}" '
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


# Unused import suppressor — PlantCategory is reserved for BV.4.5
# error-card grouping (per-category visual band).
_ = PlantCategory
_ = Iterable


# -- Landing-page JS (bulk-toggle chips + Show filter) ----------------------
#
# Small enough to inline. Adds three window-scoped helpers the buttons
# call via inline onclick: _bvToggleAll, _bvToggleFamily, _bvApplyFilter.
# Also recomputes the per-family + top-level density badges on any
# checkbox change.

_BV_LANDING_JS = """
(function () {
  const root = document;
  function checkboxes(scope) {
    return scope.querySelectorAll('input[type="checkbox"][name="enabled_kinds"]');
  }
  function updateDensity() {
    // Top-level badge.
    const all = checkboxes(root);
    const totalEnabled = Array.from(all).filter(c => c.checked).length;
    const topBadge = root.querySelector('[data-test-top-density]');
    if (topBadge) topBadge.textContent = totalEnabled + '/' + all.length + ' plants enabled';
    // Per-family badges.
    root.querySelectorAll('[data-family-body]').forEach(body => {
      const fid = body.getAttribute('data-family-body');
      const inFamily = checkboxes(body);
      const en = Array.from(inFamily).filter(c => c.checked).length;
      const badge = root.querySelector(`[data-test-family-badge][data-family="${fid}"]`);
      if (badge) badge.textContent = '(' + en + '/' + inFamily.length + ' enabled)';
    });
  }
  window._bvToggleAll = function (enable) {
    checkboxes(root).forEach(c => { c.checked = !!enable; });
    updateDensity();
  };
  window._bvToggleFamily = function (familyId, enable) {
    const body = root.querySelector(`[data-family-body="${familyId}"]`);
    if (!body) return;
    checkboxes(body).forEach(c => { c.checked = !!enable; });
    updateDensity();
  };
  window._bvApplyFilter = function (mode) {
    let anyFamilyShown = false;
    root.querySelectorAll('[data-test-training-family]').forEach(fam => {
      const cards = fam.querySelectorAll('[data-test-training-kind]');
      let anyShown = false;
      cards.forEach(card => {
        const cb = card.querySelector('input[type="checkbox"][name="enabled_kinds"]');
        const enabled = cb && cb.checked;
        // BV.4.5 — per-card error state via the data-error attr.
        const hasError = card.dataset.error === '1';
        let show = true;
        if (mode === 'enabled') show = !!enabled;
        else if (mode === 'errors') show = !!hasError;
        card.style.display = show ? '' : 'none';
        if (show) anyShown = true;
      });
      fam.style.display = anyShown ? '' : 'none';
      if (anyShown) anyFamilyShown = true;
    });
    // BV.4.8.P1.3 — surface the empty-state hint when the filter
    // hides every family (first-time-operator hits this on "Only
    // enabled" before enabling anything; without copy the page
    // reads as broken).
    const empty = root.querySelector('#bv-empty-state');
    if (empty) {
      if (anyFamilyShown) empty.classList.add('hidden');
      else empty.classList.remove('hidden');
    }
  };
  // Live density updates on any checkbox toggle.
  root.addEventListener('change', e => {
    if (e.target && e.target.matches && e.target.matches('input[type="checkbox"][name="enabled_kinds"]')) {
      updateDensity();
    }
  });
})();
"""
