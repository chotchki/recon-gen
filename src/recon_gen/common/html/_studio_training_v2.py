"""BU.1 — Trainer surface rendering (registry-driven).

Shared shells per BU.0 Lock 7:

- ``render_training_landing(registry, ...)`` — accordion grouped by
  ``PlantKindEntry.family``.
- ``render_training_plant_page(entry, ...)`` — single canonical
  template; primitives data-drive the form.
- ``render_training_tour_page(entry, ...)`` — Before/After tour
  embedding ``entry.tour_destination`` in an iframe.

ALL display text is resolved at render time via
``resolve_section(entry)`` (the typed-violation-class lookup per
Lock 8). No display strings live on the registry.

Per BU.1 the vertical-slice ships with ONE registry entry
(``phantom_rail``); these shells already iterate the full registry
so BU.2b's populate step lights up the additional 20+ entries
without re-touching this file.

Lives in the new ``_studio_training_v2.py`` rather than overwriting
the BT-era ``_studio_training.py`` (which feeds the legacy
right-column trainer pane on ``/data``) so BU.4 can subsume the
right-column pane in one explicit move rather than wading through
the V2 build with the V1 pane half-functional.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING

from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY,
    PlantCategory,
    PlantKindEntry,
    PrimitiveField,
    PrimitiveIntField,
    entries_by_family,
    get_entry,
)

if TYPE_CHECKING:
    from recon_gen.common.handbook.invariants import InvariantSection
    from recon_gen.common.handbook.l2ft_exceptions import L2FTExceptionSection


# -- Typed-section resolution (Lock 8) -------------------------------------


class _SectionAdHoc:
    """The resolved typed-section's render protocol — every category's
    resolver maps its source dataclass into this shape so the
    renderer reads uniform fields.

    Real sections come from the handbook parsers (`InvariantSection`
    / `L2FTExceptionSection` / forthcoming `L2TriageGapSection`); the
    resolvers here adapt their fields into this shared shape.

    BU.1 — L2 Triage typed catalogue (Lock 10) doesn't exist yet;
    until BU.2a builds it, the resolver falls back to a small
    hand-coded shim so the slice ships without blocking on the
    typed catalogue. BU.2a swaps the shim for the real parser.
    """

    def __init__(
        self, *, title: str, short_statement: str, what_to_do: str,
    ) -> None:
        self.title = title
        self.short_statement = short_statement
        self.what_to_do = what_to_do


def resolve_section(entry: PlantKindEntry) -> _SectionAdHoc:
    """Dispatch to the right typed-section catalogue per category.

    BU.1 vertical-slice scope: only L2_TRIAGE is wired (via the
    ad-hoc shim below). BU.2a builds ``L2TriageGapSection`` + the
    real lookup; BU.2b adds L1 + L2FT_HYGIENE branches.
    """
    if entry.category in (PlantCategory.L2_TRIAGE, PlantCategory.L2_COVERAGE):
        return _l2_triage_shim_section(entry)
    if entry.category == PlantCategory.L1_INVARIANT:
        return _resolve_l1_section(entry)
    if entry.category == PlantCategory.L2FT_HYGIENE:
        return _resolve_l2ft_section(entry)
    raise KeyError(f"no section resolver for category {entry.category}")


def _resolve_l1_section(entry: PlantKindEntry) -> _SectionAdHoc:
    """BU.2b will wire ``load_bundled_invariants()`` here."""
    from recon_gen.common.handbook.invariants import (  # noqa: PLC0415
        load_bundled_invariants,
    )
    section_kind = entry.section_kind or entry.kind
    section = load_bundled_invariants().get(section_kind)
    if section is None:
        raise KeyError(
            f"L1 invariant kind {section_kind!r} not in handbook"
        )
    return _adapter_invariant(section)


def _resolve_l2ft_section(entry: PlantKindEntry) -> _SectionAdHoc:
    """BU.2b will wire ``load_bundled_l2ft_exceptions()`` here."""
    from recon_gen.common.handbook.l2ft_exceptions import (  # noqa: PLC0415
        load_bundled_l2ft_exceptions,
    )
    section_kind = entry.section_kind or entry.kind
    section = load_bundled_l2ft_exceptions().get(section_kind)
    if section is None:
        raise KeyError(
            f"L2FT hygiene kind {section_kind!r} not in handbook"
        )
    return _adapter_l2ft(section)


def _adapter_invariant(section: "InvariantSection") -> _SectionAdHoc:
    """Map ``InvariantSection`` fields to the renderer protocol."""
    out = _SectionAdHoc(
        title=section.title,
        short_statement=getattr(section, "should", "") or "",
        what_to_do=getattr(section, "action", "") or "",
    )
    return out


def _adapter_l2ft(section: "L2FTExceptionSection") -> _SectionAdHoc:
    out = _SectionAdHoc(
        title=section.title,
        short_statement=getattr(section, "should", "")
        or getattr(section, "short_statement", "") or "",
        what_to_do=getattr(section, "action", "")
        or getattr(section, "what_to_do", "") or "",
    )
    return out


# Lock 10 build (BU.2a) replaces this hard-coded shim with the
# typed L2TriageGapSection catalogue. Until then, the slice ships
# with ad-hoc copy so the page renders something sensible.
_L2_TRIAGE_SHIM: Mapping[str, _SectionAdHoc] = {
    "phantom_rail": _SectionAdHoc(
        title="Phantom rail",
        short_statement=(
            "Transactions whose rail_name doesn't resolve to any "
            "rail declared in your L2 yaml. Usually means a legacy "
            "ETL feed produced rail names that nobody updated the "
            "L2 to declare (or vice versa — somebody renamed a rail "
            "in the L2 and the ETL still emits the old name)."
        ),
        what_to_do=(
            "Either declare the rail in your L2 (if it's a real "
            "rail your institution actually operates) OR fix your "
            "ETL hook to translate the old name into the L2-declared "
            "canonical name. Triage's CTA on the gap card deep-links "
            "to the L2 editor's create-new form with the offending "
            "rail name pre-filled."
        ),
    ),
}


def _l2_triage_shim_section(entry: PlantKindEntry) -> _SectionAdHoc:
    section_kind = entry.section_kind or entry.kind
    section = _L2_TRIAGE_SHIM.get(section_kind)
    if section is None:
        # Defensive — surfaces during the BU.2a wiring step if a
        # new entry lands before the typed source is built.
        return _SectionAdHoc(
            title=section_kind.replace("_", " ").title(),
            short_statement="(no typed-source section yet; BU.2a build pending)",
            what_to_do="",
        )
    return section


# -- Landing page ----------------------------------------------------------


def render_training_landing(
    *,
    top_nav_html: str = "",
    devlog_meta: str = "",
    devlog_script: str = "",
    theme_head: str = "",
    asset_url: str = "/static/output.css",
    reset_done: bool = False,
) -> str:
    """The ``/training/`` landing page. Accordion grouped by family
    per BU.0 Lock 1. Iterates the registry; adding a kind just adds
    a row.

    BU.1.6 — landing now ships a "Reset to clean baseline" form
    (POST /training/reset). The Trainer's pedagogical premise is
    "plant ONE thing, see ONLY it" — colliding with /etl/run's
    BTa.8 bundled-demo overlay which auto-plants ~6 gaps. The
    Reset button wipes + reseeds WITHOUT the overlay so the
    dashboard starts noise-free before the operator plants.
    """
    groups = entries_by_family()
    family_sections: list[str] = []
    for family_name, entries in groups.items():
        family_sections.append(_render_family_section(family_name, entries))
    families_html = "\n".join(family_sections)
    reset_banner = ""
    if reset_done:
        reset_banner = (
            '<div class="bg-success/10 border border-success rounded-md '
            'px-3 py-2 mb-3 text-sm" data-test-training-reset-banner>'
            '<strong class="text-success">✓ Clean baseline.</strong> '
            'Demo DB wiped + reseeded without the bundled-demo gap '
            "overlay. Pick a kind below to plant exactly one scenario; "
            "the dashboard tour will show ONLY your plant."
            "</div>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio · Training</title>
  {devlog_meta}{theme_head}
  <link rel="stylesheet" href="{escape(asset_url)}">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  {devlog_script}
</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  <header class="px-8 py-4 border-b border-surface-border bg-white">
    <h1 class="text-xl font-semibold m-0">Training</h1>
    <p class="text-sm text-secondary-fg max-w-3xl m-0 mt-1">
      Plant controlled violations into the demo DB + walk an end-user
      through how each one surfaces on the dashboards. Pick a kind
      below; each plant page lets you tune the scenario then jump to
      the dashboard sheet that should light up.
    </p>
    {_render_reset_button()}
  </header>
  <main class="px-8 py-6 flex flex-col gap-3">
    {reset_banner}
    {families_html}
  </main>
</body>
</html>
"""


def _render_reset_button() -> str:
    """The clean-baseline Reset form. Posts to /training/reset which
    runs the wipe + regenerate pipeline WITHOUT the BTa.8 overlay
    + 303s back to /training/?reset=1 for the success banner."""
    return (
        '<form method="post" action="/training/reset" class="mt-3 inline-flex items-center gap-3">'
        '<button type="submit" id="training-reset-btn" '
        'class="px-3 py-1.5 bg-warning text-white rounded-sm border border-warning text-sm font-semibold hover:opacity-85" '
        'title="Wipes + reseeds the demo DB without the bundled-demo '
        'gap overlay. Use this before planting so the tour shows '
        'only your plant — not the ~6 plants the /etl/run demo path '
        'lays down.">'
        "↻ Reset to clean baseline"
        "</button>"
        '<span class="text-xs text-secondary-fg max-w-md">'
        "Wipes the demo DB + reseeds without the BTa.8 demo-gap "
        "overlay. Plant after reset so the dashboard tour shows "
        "ONLY your scenario."
        "</span>"
        "</form>"
    )


def _render_family_section(
    family: str, entries: Iterable[PlantKindEntry],
) -> str:
    """One <details> per family on the landing accordion."""
    items: list[str] = []
    for entry in entries:
        section = resolve_section(entry)
        items.append(
            '<li class="flex items-baseline gap-3 py-1">'
            f'<a class="text-accent hover:underline font-mono text-sm" '
            f'href="/training/plant/{escape(entry.kind)}" '
            f'data-test-training-kind="{escape(entry.kind)}">'
            f'{escape(entry.kind)}</a>'
            f'<span class="text-sm">{escape(section.title)}</span>'
            '</li>'
        )
    return (
        '<details class="bg-white border border-surface-border rounded-md overflow-hidden" '
        f'data-test-training-family="{escape(family)}" open>'
        '<summary class="cursor-pointer px-4 py-3 font-semibold hover:bg-surface-bg">'
        f'{escape(family)}'
        '</summary>'
        f'<ul class="list-none m-0 p-4 pt-0">{"".join(items)}</ul>'
        '</details>'
    )


# -- Per-kind plant page --------------------------------------------------


def render_training_plant_page(
    entry: PlantKindEntry,
    *,
    top_nav_html: str = "",
    devlog_meta: str = "",
    devlog_script: str = "",
    theme_head: str = "",
    asset_url: str = "/static/output.css",
    plant_status: str | None = None,
    form_values: Mapping[str, str] | None = None,
) -> str:
    """The ``/training/plant/<kind>`` per-kind plant page.

    Data-driven from the entry. ``plant_status`` is a banner
    rendered when the operator just submitted the plant form +
    we're showing the result of that submission. ``form_values``
    (BU.1.10) preserves the operator's submitted values across the
    POST re-render so the form mirrors the banner text instead of
    snapping back to defaults.
    """
    section = resolve_section(entry)
    primitives_html = "\n".join(
        _render_primitive_field(
            p,
            form_value=(form_values or {}).get(p.name),
        )
        for p in entry.primitives
    )
    status_html = ""
    if plant_status:
        status_html = (
            '<div class="bg-success/10 border border-success rounded-md '
            'px-3 py-2 mb-4 text-sm" data-test-plant-status>'
            f'<strong class="text-success">✓ Planted.</strong> '
            f'{escape(plant_status)}</div>'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio · Training · {escape(section.title)}</title>
  {devlog_meta}{theme_head}
  <link rel="stylesheet" href="{escape(asset_url)}">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  {devlog_script}
</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  <header class="flex items-center gap-4 px-4 py-2 border-b border-surface-border bg-white">
    <a class="text-accent no-underline text-sm hover:underline" href="/training/">← back to Training</a>
    <span class="text-xs text-secondary-fg font-mono">{escape(entry.category.value)} · {escape(entry.family)}</span>
  </header>
  <main class="max-w-3xl mx-auto pt-6 px-4 pb-12 flex flex-col gap-4">
    <h1 class="text-2xl font-semibold m-0">{escape(section.title)}</h1>
    <p class="text-sm m-0">{escape(section.short_statement)}</p>
    {status_html}
    <section class="bg-white border border-surface-border rounded-md p-5">
      <h2 class="text-base font-semibold m-0 mb-3">Plant scenario</h2>
      <form method="post" action="/training/plant/{escape(entry.kind)}" class="flex flex-col gap-3">
        {primitives_html}
        <div class="flex items-center gap-3 mt-3">
          <button type="submit" id="training-plant-btn"
                  class="px-4 py-2 bg-accent text-accent-fg rounded-sm border border-accent text-sm font-semibold hover:opacity-85">
            ⊕ Plant this scenario
          </button>
          <a class="text-accent no-underline text-sm hover:underline"
             href="/training/tour/{escape(entry.kind)}">→ Tour the dashboard</a>
        </div>
      </form>
    </section>
    <section class="bg-white border border-surface-border rounded-md p-5">
      <h2 class="text-base font-semibold m-0 mb-2">What to do about it</h2>
      <p class="text-sm m-0">{escape(section.what_to_do)}</p>
    </section>
    <section class="bg-surface-bg border border-surface-border rounded-md p-4">
      <h2 class="text-sm font-semibold m-0 mb-2">Re-baseline</h2>
      <p class="text-xs text-secondary-fg max-w-md m-0 mb-2">
        Going to plant a different scenario? Reset the demo DB to
        a clean baseline first so the tour shows only your new
        plant, not the previous one stacked on top.
      </p>
      {_render_reset_button()}
    </section>
  </main>
</body>
</html>
"""


def _render_primitive_field(
    primitive: PrimitiveField,
    *,
    form_value: str | None = None,
) -> str:
    """Per-primitive-type renderer. Adding a new primitive shape
    here costs one branch + one HTML template — no per-kind hand
    coding. ``form_value`` (BU.1.10) overrides the primitive's
    default when the operator just submitted a value — keeps the
    form's reported state in sync with the banner."""
    if isinstance(primitive, PrimitiveIntField):
        attrs: list[str] = []
        if primitive.min_value is not None:
            attrs.append(f'min="{primitive.min_value}"')
        if primitive.max_value is not None:
            attrs.append(f'max="{primitive.max_value}"')
        value = form_value if form_value is not None else str(primitive.default)
        return (
            '<label class="block">'
            f'<span class="block text-xs uppercase tracking-wide text-secondary-fg mb-1">'
            f'{escape(primitive.label)}</span>'
            f'<input type="number" name="{escape(primitive.name)}" '
            f'value="{escape(value)}" {" ".join(attrs)} '
            'class="px-2 py-1 border border-surface-border rounded-sm text-sm bg-white w-32">'
            f'<span class="block text-xs text-secondary-fg mt-1">'
            f'{escape(primitive.help_text)}</span>'
            '</label>'
        )
    # Type is PrimitiveStringField by narrowing — drop redundant isinstance.
    value = form_value if form_value is not None else primitive.default
    return (
        '<label class="block">'
        f'<span class="block text-xs uppercase tracking-wide text-secondary-fg mb-1">'
        f'{escape(primitive.label)}</span>'
        f'<input type="text" name="{escape(primitive.name)}" '
        f'value="{escape(value)}" '
        'class="px-2 py-1 border border-surface-border rounded-sm text-sm bg-white w-full max-w-md">'
        f'<span class="block text-xs text-secondary-fg mt-1">'
        f'{escape(primitive.help_text)}</span>'
        '</label>'
    )


# -- Tour page -------------------------------------------------------------


def render_training_tour_page(
    entry: PlantKindEntry,
    *,
    top_nav_html: str = "",
    devlog_meta: str = "",
    devlog_script: str = "",
    theme_head: str = "",
    asset_url: str = "/static/output.css",
) -> str:
    """The ``/training/tour/<kind>`` tour page. Embeds
    ``entry.tour_destination.primary_url`` in an iframe per BU.0 Lock 3.
    Before/After toggle ships in BU.4 polish; vertical slice has the
    iframe + a static caption explaining what to look for."""
    section = resolve_section(entry)
    iframe_url = entry.tour_destination.primary_url
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio · Training · {escape(section.title)} · Tour</title>
  {devlog_meta}{theme_head}
  <link rel="stylesheet" href="{escape(asset_url)}">
  {devlog_script}
</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  <header class="flex items-center gap-4 px-4 py-2 border-b border-surface-border bg-white">
    <a class="text-accent no-underline text-sm hover:underline" href="/training/plant/{escape(entry.kind)}">← back to plant form</a>
    <h1 class="text-base m-0 font-semibold">Tour · {escape(section.title)}</h1>
  </header>
  <main class="px-4 pt-3 pb-12 flex flex-col gap-3">
    <p class="text-sm text-secondary-fg max-w-3xl m-0">
      The dashboard surface below is where this violation kind
      lands. If you plant a scenario then return here, the row
      should be visible. <strong>Empty = plant didn't fire OR the
      matview refresh hasn't run yet</strong> (click Refresh Data
      on the destination if it has its own).
    </p>
    <div class="bg-white border border-surface-border rounded-md overflow-hidden">
      <div class="px-4 py-2 border-b border-surface-border bg-surface-bg text-xs text-secondary-fg font-mono">
        iframe: <a class="text-accent hover:underline" href="{escape(iframe_url)}" target="_blank" rel="noopener">{escape(iframe_url)}</a>
      </div>
      <iframe src="{escape(iframe_url)}"
              class="block w-full"
              style="height: 70vh; border: none;"
              data-test-tour-iframe="{escape(entry.kind)}"
              title="Tour destination for {escape(section.title)}"></iframe>
    </div>
  </main>
</body>
</html>
"""


# Re-export the registry module's accessors for callers that want a
# single import. The renderer is intentionally a thin wrapper.
__all__ = [
    "PLANT_REGISTRY",
    "get_entry",
    "render_training_landing",
    "render_training_plant_page",
    "render_training_tour_page",
    "resolve_section",
]


# -- For BU.1's plant POST flow: the route handler will call this -----------


def coerce_form_to_kwargs(
    entry: PlantKindEntry,
    form: Mapping[str, str],
) -> dict[str, object]:
    """Coerce a FormData mapping to the kwargs the plant_function
    expects. Per BU.0 Lock 7, primitives are typed so coercion is
    data-driven from the primitive's type tag."""
    out: dict[str, object] = {}
    for primitive in entry.primitives:
        raw = form.get(primitive.name)
        if raw is None:
            # Caller-supplied form may omit the field; fall back to
            # the primitive's default rather than failing. Union of
            # PrimitiveIntField | PrimitiveStringField both have
            # .default by definition.
            out[primitive.name] = primitive.default
            continue
        if isinstance(primitive, PrimitiveIntField):
            try:
                out[primitive.name] = int(raw)
            except ValueError:
                out[primitive.name] = primitive.default
        else:
            # Type is PrimitiveStringField by narrowing.
            out[primitive.name] = raw
    return out


def now_anchor() -> datetime:
    """Return the wall-clock anchor for the operator's plant.

    Vertical-slice plant invocation uses now() so the planted rows
    fall in the default date window; BU.4 may swap in an explicit
    cfg.test_generator.end_date anchor.
    """
    return datetime.now()  # typing-smell: ignore[no-datetime-now]: vertical-slice plant uses wall clock so planted rows fall in default date window; BU.4 may swap in cfg.test_generator.end_date
