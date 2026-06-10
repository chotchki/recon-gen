"""BV.7 — coverage-matrix surface generator.

Walks :data:`recon_gen.common.l2.plant_registry.PLANT_REGISTRY` plus the
three typed section catalogues (:mod:`common.handbook.invariants`,
:mod:`common.handbook.l2ft_exceptions`, :mod:`common.handbook.l2_triage_gaps`)
and emits the canonical §0.5 violation coverage matrix from BU.0.5 as
a Markdown table.

The hand-maintained §0.5 table lives in
``docs/audits/bu_design_mockups.md`` (and the archived
``docs/audits/_archive/bu_0_replan.md``). Per BU.0 Lock 11 the matrix
moves to "generated artifact" — every PLANT_REGISTRY edit refreshes the
table mechanically so the doc can't drift from the registry. BU.0 Lock
9 ("docs freshness byte-identity") pins the generated output to a
committed reference artifact under ``tests/data/_handbook_artifacts/``;
a registry addition without a refresh fails CI loudly.

Columns:

- **Family** — ``PlantKindEntry.family`` (accordion group on the
  Trainer landing).
- **Kind** — ``PlantKindEntry.kind`` (canonical machine name; matches
  the registry slug).
- **Section** — ``section_kind or kind`` (typed-catalogue key; the
  load-bearing column for Lock 8's bijectivity check).
- **Dashboard** — the operator-facing sheet / route the violation
  lands on. Derived from the three ``*_KIND_TO_SHEET`` mappings for
  L1 + L2FT; static ``/etl/triage`` / ``/etl/run`` for L2 Triage +
  Coverage (no per-kind sheet split — both render on Studio routes).
- **Tour URL** — ``PlantKindEntry.tour_destination.primary_url``
  (verbatim; the Lock 9 tour-URL-liveness test guards shape).
- **Typed-source class** — the Python dataclass that owns the
  display strings for this kind. ``InvariantSection`` /
  ``L2FTExceptionSection`` / ``L2TriageGapSection``. **This column
  proves every kind has a SoT** per BU.0 Lock 11; if you see a blank
  one, the bijectivity test should have caught it first.

Output: Markdown table to stdout (or ``--output PATH`` from the CLI).
The CLI surface lives in :mod:`recon_gen.cli.docs` as
``recon-gen docs export --surface=matrix``.
"""

from __future__ import annotations

from recon_gen.common.handbook.invariants import INVARIANT_KIND_TO_SHEET
from recon_gen.common.handbook.l2ft_exceptions import (
    L2FT_EXCEPTION_KIND_TO_SHEET,
)
from recon_gen.common.l2.plant_registry import (
    PLANT_REGISTRY,
    PlantCategory,
    PlantKindEntry,
)


# -- Category → typed source class label ----------------------------------
#
# Per BU.0 Lock 8 the registry's ``category`` discriminates which typed
# catalogue owns the section. The label is the Python class name (the
# bijectivity test downstream of this can ``hasattr`` it back to the real
# class if needed; the docs reader sees the class name).
_CATEGORY_TO_SOT_CLASS: dict[PlantCategory, str] = {
    PlantCategory.L1_INVARIANT: "InvariantSection",
    PlantCategory.L2_TRIAGE: "L2TriageGapSection",
    PlantCategory.L2_COVERAGE: "L2TriageGapSection",
    PlantCategory.L2FT_HYGIENE: "L2FTExceptionSection",
}


# -- Category → dashboard fallback for sheet-less surfaces ----------------
#
# L2 Triage + L2 Coverage render on Studio routes (``/etl/triage`` and
# ``/etl/run`` respectively), not on QS dashboard sheets. The L1 +
# L2FT_HYGIENE entries look their dashboard sheet up in
# :data:`INVARIANT_KIND_TO_SHEET` / :data:`L2FT_EXCEPTION_KIND_TO_SHEET`
# keyed on ``section_kind`` (or ``kind`` when unset).
_CATEGORY_TO_ROUTE: dict[PlantCategory, str] = {
    PlantCategory.L2_TRIAGE: "/etl/triage",
    PlantCategory.L2_COVERAGE: "/etl/run",
}


def _dashboard_for(entry: PlantKindEntry) -> str:
    """Resolve the operator-facing surface for ``entry``.

    L1 + L2FT_HYGIENE kinds hit the ``*_KIND_TO_SHEET`` maps; L2 Triage
    + L2 Coverage hit the static Studio route. Loud-fails on miss so a
    new family or a kind without a sheet mapping surfaces here, not
    silently blank-celled.
    """
    section_kind = entry.section_kind or entry.kind
    if entry.category == PlantCategory.L1_INVARIANT:
        sheet = INVARIANT_KIND_TO_SHEET.get(section_kind)
        if sheet is None:
            raise KeyError(
                f"coverage_matrix: L1 kind {entry.kind!r} (section "
                f"{section_kind!r}) missing from INVARIANT_KIND_TO_SHEET"
            )
        return sheet
    if entry.category == PlantCategory.L2FT_HYGIENE:
        sheet = L2FT_EXCEPTION_KIND_TO_SHEET.get(section_kind)
        if sheet is None:
            raise KeyError(
                f"coverage_matrix: L2FT kind {entry.kind!r} (section "
                f"{section_kind!r}) missing from "
                f"L2FT_EXCEPTION_KIND_TO_SHEET"
            )
        return sheet
    route = _CATEGORY_TO_ROUTE.get(entry.category)
    if route is None:
        raise KeyError(
            f"coverage_matrix: no dashboard mapping for category "
            f"{entry.category!r} (kind {entry.kind!r})"
        )
    return route


def _typed_source_for(entry: PlantKindEntry) -> str:
    """Render the typed-source-class cell — ``ClassName["section_kind"]``.

    The bracketed key documents the catalogue lookup so a reader of the
    matrix can spot a slug mismatch (e.g. registry ``dead_metadata`` ->
    section ``dead_metadata_declarations``) without cross-referencing.
    Per BU.0 Lock 8 this column is the load-bearing anti-drift signal.
    """
    cls = _CATEGORY_TO_SOT_CLASS.get(entry.category)
    if cls is None:
        raise KeyError(
            f"coverage_matrix: no SoT class for category "
            f"{entry.category!r} (kind {entry.kind!r})"
        )
    section_kind = entry.section_kind or entry.kind
    qualifier = (
        f" [{entry.kind_qualifier}]" if entry.kind_qualifier else ""
    )
    return f"`{cls}[\"{section_kind}\"]`{qualifier}"


# Column order — locked here so a reorder is one source edit, not a
# scattered set of `f"| {a} | {b} | ..."` literal rewrites.
_COLUMNS: tuple[str, ...] = (
    "Family",
    "Kind",
    "Section",
    "Dashboard",
    "Tour URL",
    "Typed-source class",
)


def _row(entry: PlantKindEntry) -> tuple[str, ...]:
    section_kind = entry.section_kind or entry.kind
    return (
        entry.family,
        f"`{entry.kind}`",
        f"`{section_kind}`",
        _dashboard_for(entry),
        f"`{entry.tour_destination.primary_url}`",
        _typed_source_for(entry),
    )


def _format_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    """Render a GitHub-flavored markdown table.

    No column-width padding — markdown renderers don't need it and the
    artifact diff stays line-stable as kinds get added (padding shifts
    every row when the widest cell changes).
    """
    lines: list[str] = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_coverage_matrix() -> str:
    """Walk PLANT_REGISTRY + emit the §0.5 coverage matrix as Markdown.

    Output shape: a leading H1 + a one-paragraph "this is generated"
    preamble + the table + a trailing newline. The preamble pins the
    artifact's provenance so a reader who lands on it without context
    knows it's machine-generated.
    """
    rows = [_row(entry) for entry in PLANT_REGISTRY]
    body = _format_table(_COLUMNS, rows)
    preamble = (
        "# §0.5 Violation coverage matrix\n"
        "\n"
        "Generated by `recon-gen docs export --surface=matrix` — do "
        "not hand-edit. Source of truth is "
        "`src/recon_gen/common/l2/plant_registry.py::PLANT_REGISTRY` "
        "plus the three typed section catalogues under "
        "`src/recon_gen/common/handbook/`. Every row is one "
        "`PlantKindEntry`; the **Typed-source class** column proves "
        "the kind has a typed SoT (BU.0 Lock 11 + Lock 8 bijectivity).\n"
        "\n"
        f"Row count: **{len(rows)}** kinds across "
        f"**{len({e.family for e in PLANT_REGISTRY})}** families.\n"
        "\n"
    )
    return preamble + body + "\n"
