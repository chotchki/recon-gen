"""Frozen dataclass + initial population for the QS-parity-break registry.

Each entry records a deliberate divergence between App2 (the self-hosted
HTMX renderer) and QuickSight (the AWS embed surface). The registry is
the single declaration: doc generation walks it, lints assert reference
resolution, and the handbook's per-sheet QS-parity-notes section reads
from it. Per CN.0 Lock CN-5 (2026-06-08), there is no site-comment lint
— the registry IS the source.

The three severity classes:

- ``ENHANCEMENT`` — App2 has a richer affordance (e.g. the ``?`` help
  panel). QS still renders the underlying data fine; the App2 surface
  just teaches more.
- ``WORKAROUND`` — App2 routes around a QS bug or limit (e.g. the
  count-distinct quirk). QS would render incorrectly without the
  workaround; App2's code path keeps both sides correct.
- ``HARD_DIVERGENCE`` — QS literally cannot host this surface (e.g. the
  Studio L2 editor — no QS edit-form vocabulary exists). App2-only by
  necessity.

Adding a new entry: append to :data:`PARITY_BREAKS`. Run
``pytest tests/unit/test_parity_breaks.py`` to confirm references resolve.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class ParitySeverity(Enum):
    """How much the App2 surface exceeds QS for this break."""

    ENHANCEMENT = "enhancement"
    WORKAROUND = "workaround"
    HARD_DIVERGENCE = "hard_divergence"


@dataclass(frozen=True, slots=True)
class QSParityBreak:
    """A registered deliberate divergence between the App2 + QS renderers.

    Surfaced in :file:`docs/reference/quicksight-quirks.md` via the doc
    generator (CN.6). Lint asserts:

    1. ``references`` paths resolve (files exist; ``module:qualname``
       references import cleanly).
    2. ``surface`` / ``qs_limitation`` are non-empty + ``discovered`` is
       a valid ISO date.
    3. ``name`` is unique across the registry.

    ``references`` may be empty for convention-only breaks (e.g.
    sheet-ID kebab shape) where no single code site anchors the
    divergence — the lint flags empty-references entries for
    sanity-check visibility but allows them.
    """

    name: str
    severity: ParitySeverity
    surface: str
    qs_limitation: str
    discovered: str  # YYYY-MM-DD
    references: tuple[str, ...] = ()


PARITY_BREAKS: Final[tuple[QSParityBreak, ...]] = (
    # ---- WORKAROUND class — App2 routes around a QS bug/limit ----
    QSParityBreak(
        name="count_distinct_quirk_bl1",
        severity=ParitySeverity.WORKAROUND,
        surface=(
            "Measure.kind=='count' emits NumericalMeasureField(SUM) "
            "over a literal-1 CalcField, sidestepping QS's "
            "CategoricalMeasureField(COUNT) distinct-rendering quirk."
        ),
        qs_limitation=(
            "QS renders CategoricalMeasureField(COUNT) as distinct-count "
            "instead of row-count whenever the same column is also used "
            "as a Dim elsewhere on the visual/sheet."
        ),
        discovered="2026-05-27",
        references=("src/recon_gen/common/models.py",),
    ),
    QSParityBreak(
        name="dependent_dropdown_no_refresh_x223",
        severity=ParitySeverity.WORKAROUND,
        surface=(
            "App2's URL parameters narrow data AND update the dependent "
            "dropdown's selected value on initial load."
        ),
        qs_limitation=(
            "QS's MappedDataSetParameters bridge fires URL → dataset, "
            "but NOT URL → dependent ParameterControl widget; data "
            "filters but the dropdown shows the old / All value."
        ),
        discovered="2026-05-07",
        references=(
            "src/recon_gen/common/tree/controls.py",
            "src/recon_gen/common/models.py",
        ),
    ),
    QSParityBreak(
        name="static_values_32_cap",
        severity=ParitySeverity.WORKAROUND,
        surface=(
            "DataSetParameter.DefaultValues.StaticValues raises at "
            "construction time when len > 32; unbounded universes use "
            "a sentinel-match-all clause instead."
        ),
        qs_limitation=(
            "QS silently truncates DataSetParameter default-values lists "
            "longer than 32 elements, producing dashboards that show "
            "wrong data on parameter widgets bound to large enums."
        ),
        discovered="2026-04-30",
        references=("src/recon_gen/common/models.py",),
    ),
    QSParityBreak(
        name="recursive_cte_in_custom_sql",
        severity=ParitySeverity.WORKAROUND,
        surface=(
            "Money-trail + similar walks live in a precomputed matview; "
            "the dataset's custom SQL reads the materialized result."
        ),
        qs_limitation=(
            "QS Direct Query rejects WITH RECURSIVE inside a custom-SQL "
            "dataset's query body."
        ),
        discovered="2026-05-10",
        references=(
            "src/recon_gen/common/l2/schema.py",
        ),
    ),
    QSParityBreak(
        name="embed_url_must_open_qs_paths",
        severity=ParitySeverity.WORKAROUND,
        surface=(
            "App2 hosts arbitrary Starlette routes; helpers that mint "
            "QS embed URLs validate that the path belongs to the QS "
            "embed family before navigation."
        ),
        qs_limitation=(
            "QS embeds reject arbitrary URLs with 'We can\\'t open "
            "that page' if the path isn't in the embed family."
        ),
        discovered="2026-04-21",
        references=("src/recon_gen/common/browser/helpers.py",),
    ),
    # ---- ENHANCEMENT class — App2 richer, QS still works ----
    QSParityBreak(
        name="handbook_help_panel",
        severity=ParitySeverity.ENHANCEMENT,
        surface=(
            "Per-sheet ? button on every dashboard sheet opens the "
            "handbook page in a side panel via /handbook/<app>/<sheet>."
        ),
        qs_limitation=(
            "QS embeds have no portable extension point for arbitrary "
            "static-content side panels; QS operators get the per-sheet "
            "description text and a link out to the handbook URL."
        ),
        discovered="2026-06-08",
        references=("src/recon_gen/common/tree/structure.py",),
    ),
    QSParityBreak(
        name="studio_inline_help",
        severity=ParitySeverity.ENHANCEMENT,
        surface=(
            "Studio L2 editor field labels mount the same ? button as "
            "the dashboard sheets via FieldSpec.handbook_path; opens "
            "the linked handbook anchor in the same side panel. Wired "
            "(CN.5a) on chain.children, transfer_template.{transfer_key, "
            "completion, leg_rail_xor_groups}, rail.{bundles_activity, "
            "max_unbundled_age, metadata_keys}."
        ),
        qs_limitation=(
            "QS has no edit-form surface — HARD-divergent adjacent. "
            "The field-level ? is the ENHANCEMENT extension; the Studio "
            "L2 editor itself is HARD-divergent (see studio_l2_editor)."
        ),
        discovered="2026-06-08",
        references=(
            "src/recon_gen/common/html/_studio_editor_routes.py",
            "src/recon_gen/docs/_handbook_per_sheet/l2-editor/",
        ),
    ),
    QSParityBreak(
        name="xlsx_export_button",
        severity=ParitySeverity.ENHANCEMENT,
        surface=(
            "↓ XLSX download link above every renderTable visual on "
            "App2 dashboards; preserves the active sort / page / filter "
            "params via ?format=xlsx on the visual-data endpoint."
        ),
        qs_limitation=(
            "QS has its own export-to-CSV / export-to-PDF on a separate "
            "code path; the App2 affordance is per-table, instant, and "
            "respects the operator's current visual state."
        ),
        discovered="2026-06-08",
        references=(
            "src/recon_gen/common/html/server.py",
            "src/recon_gen/common/html/assets/js/bootstrap.js",
        ),
    ),
    QSParityBreak(
        name="markdown_prose_richer_than_qs_text",
        severity=ParitySeverity.ENHANCEMENT,
        surface=(
            "App2 renders full markdown in sheet/visual descriptions "
            "(headings, lists, links, inline code) via common/rich_text.py."
        ),
        qs_limitation=(
            "QS SheetTextBox supports only its constrained XML "
            "vocabulary (inline color/size/bg/font, b/i/s/u, block "
            "align, ql-indent-N nesting) — rich operator-help content "
            "must be flattened or linked out."
        ),
        discovered="2026-04-15",
        references=("src/recon_gen/common/rich_text.py",),
    ),
    QSParityBreak(
        name="dropdown_click_target_quirk",
        severity=ParitySeverity.ENHANCEMENT,
        surface=(
            "App2 dropdowns open on a click anywhere within the widget."
        ),
        qs_limitation=(
            "QS ParameterDropDownControl opens only when the operator "
            "clicks the inner grey bar; clicking the visible edge does "
            "nothing — a usability footgun App2 sidesteps by design."
        ),
        discovered="2026-04-08",
    ),
    # ---- HARD_DIVERGENCE class — QS literally cannot host ----
    QSParityBreak(
        name="studio_l2_editor",
        severity=ParitySeverity.HARD_DIVERGENCE,
        surface=(
            "Studio's /l2_shape/* editor — typed form pages for every "
            "L2 entity kind (Account, Rail, TransferTemplate, Chain, "
            "LimitSchedule, AccountTemplate) plus singletons (Instance, "
            "Theme, Persona)."
        ),
        qs_limitation=(
            "QS has no edit-form vocabulary; dashboard surfaces are "
            "read-side only. The entire Studio editor surface is "
            "App2-only by construction."
        ),
        discovered="2026-05-26",
        references=("src/recon_gen/common/html/_studio_editor_routes.py",),
    ),
    QSParityBreak(
        name="studio_etl_support",
        severity=ParitySeverity.HARD_DIVERGENCE,
        surface=(
            "Studio's /etl/* surface — probe configuration, run "
            "execution, triage, and live tail of refresh runs."
        ),
        qs_limitation=(
            "QS has no notion of source-data probes or per-table ETL "
            "runs; the entire surface is App2-only."
        ),
        discovered="2026-06-01",
    ),
    QSParityBreak(
        name="studio_training",
        severity=ParitySeverity.HARD_DIVERGENCE,
        surface=(
            "Studio's /training/* surface — plants known invariant "
            "violations into the demo DB for cold-read / coverage / "
            "trainer-dogfood verification."
        ),
        qs_limitation=(
            "QS dashboards are read-side only; plants mutate the demo "
            "DB. App2-only by construction."
        ),
        discovered="2026-06-02",
    ),
    QSParityBreak(
        name="sheet_id_kebab_not_uuid",
        severity=ParitySeverity.HARD_DIVERGENCE,
        surface=(
            "App2 sheet URLs use analyst-readable kebab-case sheet ids "
            "(l1-sheet-drift, exec-sheet-money-moved) so operators can "
            "bookmark and share specific views."
        ),
        qs_limitation=(
            "QS internally maps sheet ids to a different shape; the "
            "kebab-case convention is App2-only — QS dashboards expose "
            "their own opaque id space."
        ),
        discovered="2026-05-13",
        references=("src/recon_gen/common/tree/structure.py",),
    ),
)
