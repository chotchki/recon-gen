"""BV.7 Surface 4 — dynamic plant banner (per BU.0 Lock 11.4).

Renders an operator-facing "currently planted" summary by walking
:data:`recon_gen.common.l2.plant_registry.PLANT_REGISTRY` filtered to
the **L2-side categories** (per BU.0 Lock 8 — :class:`PlantCategory`
``L2_TRIAGE`` / ``L2_COVERAGE`` / ``L2FT_HYGIENE``) and intersecting
against the persisted ``trainer_applied_plants`` KV row in
``<v>_config_kv`` (the same KV
:func:`recon_gen.common.l2.v_overlay.read_applied_state` reads).

The banner is mounted on ``/etl/triage`` so when the operator opens
Triage with plants active in the v overlay they see *which* L2 kinds
are currently planted — rather than the static "some gaps are demo
plants" banner that doesn't tell them anything they can act on.

**Per Lock 11.4 — "full dynamic":** the renderer hits the KV at
request time. No precomputed snapshot, no scenario fallback. When
the KV is unreachable (no v overlay yet, DB down, parse fail) the
banner short-circuits to a neutral "clean baseline" empty state —
matches the L2 staleness banner shape (silent on unknown).

**Per Lock 8 category filter:** the L1 invariant plants (drift,
overdraft, etc.) live above the L2/ETL layer — they don't show up
on ``/etl/triage`` because Triage is the L2-shape gap surface, not
the L1-balance surface. Filtering at the banner saves the operator
from "why is `drift` in my Triage plant list when I'm here for
ETL gaps?" confusion. The L1 plants still show up on
``/training/``'s landing banner (different surface, different scope).

The byte-identity reference table of per-kind copy strings emits
through ``recon-gen docs export --surface=plant-banner-snippets`` —
the BV.7 CLI surface so the operator can grep one artifact to confirm
what each kind would render under the banner.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape

from recon_gen.common.handbook.violations import _slug_for
from recon_gen.common.html._studio_training_v2 import resolve_section
from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY,
    PlantCategory,
    PlantKindEntry,
)


# Per BU.0 Lock 8 — these are the L2-side categories ``/etl/triage`` cares
# about. L1_INVARIANT is excluded (L1 balance plants belong on /training/
# + the L1 dashboards). Tuple so order is stable when the banner lists
# multiple kinds.
_L2_CATEGORIES: tuple[PlantCategory, ...] = (
    PlantCategory.L2_TRIAGE,
    PlantCategory.L2_COVERAGE,
    PlantCategory.L2FT_HYGIENE,
)


def _is_l2_category(entry: PlantKindEntry) -> bool:
    return entry.category in _L2_CATEGORIES


def _l2_entries() -> tuple[PlantKindEntry, ...]:
    """Return ``PLANT_REGISTRY`` filtered to the L2-side categories,
    preserving registry order. Pulled out so tests can introspect the
    same filter the renderer uses without coupling to the registry's
    full ordering."""
    return tuple(e for e in PLANT_REGISTRY if _is_l2_category(e))


def render_plant_banner(
    applied_state: Mapping[str, Mapping[str, str]],
    *,
    deep_link_base: str = "/training/violations.md",
) -> str:
    """Render the ``/etl/triage`` dynamic plant banner.

    Args:
      applied_state: ``{kind: form_values}`` map as returned by
        :func:`recon_gen.common.l2.v_overlay.read_applied_state`.
        The keys are the canonical machine ``kind`` strings.
      deep_link_base: prefix for the per-kind anchor links. Defaults
        to the violations handbook surface (Surface 2) so the operator
        can click a chip and land on the handbook section for that
        kind. Override for tests / alternative docs hosts.

    Returns:
      One ``<aside>`` HTML block. When no L2-category kinds are
      active, renders an empty-state shape ("No active plants…"). When
      one-or-more are active, renders a horizontal chip list with
      slug-anchored deep links per kind.
    """
    l2_kinds_planted: list[PlantKindEntry] = [
        entry for entry in _l2_entries() if entry.kind in applied_state
    ]

    if not l2_kinds_planted:
        return _render_empty_state()
    return _render_active_state(l2_kinds_planted, deep_link_base=deep_link_base)


def _render_empty_state() -> str:
    """No L2 plants active. Operator-friendly "you're at clean
    baseline" copy. Carries the same ``data-test-plant-banner``
    test hook as the active state so tests can pin both branches
    on one attribute + branch on ``data-test-plant-state``."""
    return (
        '<aside class="mx-8 mt-6 mb-2 bg-surface-bg border border-surface-border '
        'rounded-md px-4 py-3 text-sm text-secondary-fg" '
        'data-test-plant-banner data-test-plant-state="empty" role="status">'
        '<strong class="text-primary-fg">No active plants.</strong> '
        'Trainer is at clean baseline — the gaps below are real.'
        '</aside>'
    )


def _render_active_state(
    entries: list[PlantKindEntry], *, deep_link_base: str,
) -> str:
    """One-or-more L2 kinds planted. Render an inline chip list with
    per-kind deep links to the violations handbook anchor."""
    chip_html: list[str] = []
    for entry in entries:
        section = resolve_section(entry)
        slug = _slug_for(entry)
        href = f"{escape(deep_link_base)}#{escape(slug)}"
        chip_html.append(
            f'<a href="{href}" '
            f'class="inline-block px-2 py-0.5 rounded-sm bg-accent/10 '
            f'border border-accent/30 text-accent text-xs hover:opacity-85" '
            f'data-test-plant-chip data-test-plant-kind="{escape(entry.kind)}" '
            f'title="{escape(section.title)}">'
            f'<code class="text-accent">{escape(entry.kind)}</code>'
            f'</a>'
        )
    chips_block = " ".join(chip_html)
    count = len(entries)
    plural = "kind" if count == 1 else "kinds"
    return (
        '<aside class="mx-8 mt-6 mb-2 bg-warning/5 border border-warning/40 '
        'rounded-md px-4 py-3 text-sm" '
        'data-test-plant-banner data-test-plant-state="active" '
        f'data-test-plant-count="{count}" role="status">'
        f'<strong class="text-warning">⚑ Currently planted:</strong> '
        f'<span class="text-secondary-fg">{count} L2 {plural} active.</span> '
        f'<span class="ml-2 inline-flex flex-wrap gap-1 align-middle">'
        f'{chips_block}</span>'
        '</aside>'
    )


# -- CLI surface: per-kind reference snippets -------------------------------


def render_plant_banner_snippets() -> str:
    """BV.7 Surface 4 CLI export — per-kind banner-copy reference
    table.

    The runtime banner (``render_plant_banner``) is dynamic — it reads
    the KV at request time and intersects against the L2 registry slice.
    For the CLI export we emit a Markdown reference table listing every
    L2-category kind with its banner-time identifiers (canonical
    machine ``kind`` + display title + deep-link slug), so an operator
    or docs reader can confirm at a glance what would render for each
    kind without spinning up Studio.

    Output shape: leading H1 + provenance preamble + row-count + a
    Markdown table with columns ``Kind | Title | Slug | Category``.
    """
    entries = _l2_entries()
    lines: list[str] = []
    lines.append("# Plant banner — per-kind reference\n")
    lines.append(
        "Generated by `recon-gen docs export --surface=plant-banner-snippets` — "
        "do not hand-edit. Source of truth is "
        "`src/recon_gen/common/l2/plant_registry.py::PLANT_REGISTRY` "
        "filtered to the L2-side categories per BU.0 Lock 8 "
        "(``L2_TRIAGE`` / ``L2_COVERAGE`` / ``L2FT_HYGIENE``). The "
        "runtime banner at ``/etl/triage`` reads "
        "``<v>_config_kv['trainer_applied_plants']`` and intersects "
        "against the rows below — kinds present in the KV render as "
        "chips, others are dormant.\n"
    )
    lines.append(f"Row count: **{len(entries)}** L2 kinds.\n")
    lines.append("| Kind | Title | Slug | Category |")
    lines.append("|---|---|---|---|")
    for entry in entries:
        section = resolve_section(entry)
        slug = _slug_for(entry)
        lines.append(
            f"| `{entry.kind}` | {section.title} | `{slug}` "
            f"| `{entry.category.value}` |"
        )
    lines.append("")
    return "\n".join(lines)
