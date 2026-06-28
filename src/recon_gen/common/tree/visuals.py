"""Typed ``Visual`` subtypes — one per visual kind in active use.

L.1.1 catalog: KPI ×29, Table ×22, BarChart ×13, Sankey ×2 across
the three apps. Each subtype owns its field-well shape and emits the
corresponding ``models.py`` ``Visual`` instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal, Protocol, runtime_checkable

from recon_gen.common.ids import VisualId

from recon_gen.common.tree._helpers import (
    AUTO,
    AutoResolved,
    GridLayoutElementType,
    _AutoSentinel,
)
from recon_gen.common.tree.actions import Action, Drill
from recon_gen.common.tree.formatting import Drillable
from recon_gen.common.tree.calc_fields import CalcField
from recon_gen.common.tree.datasets import Dataset
from recon_gen.common.tree.fields import Dim, FieldRef, Measure


@runtime_checkable
class VisualLike(Protocol):
    """Structural type for tree-level visual nodes.

    Typed subtypes (``KPI`` / ``Table`` / ``BarChart`` / ``Sankey``)
    satisfy this Protocol — duck-typed so subtypes don't have to
    inherit from a base class. Subtypes contribute to the L.1.7
    dependency-graph walk via ``datasets()`` / ``calc_fields()``.

    All visual nodes also satisfy ``LayoutNode`` (in ``structure.py``)
    via ``element_id`` + ``element_type`` so they can be placed in a
    sheet's grid layout (``sheet.layout.row(...).add_<kind>(...)``).

    ``visual_id`` is ``VisualId | AutoResolved`` — typed subtypes default
    to ``AUTO`` and ``App.resolve_auto_ids`` replaces it with the
    derived id before emit. The walker / emit assert via ``isinstance``
    narrowing.
    """
    visual_id: VisualId | AutoResolved

    def datasets(self) -> set[Dataset]: ...

    def calc_fields(self) -> set[CalcField]: ...


def _visual_element_id(node: VisualLike) -> str:
    """LayoutNode.element_id implementation shared by every visual subtype.
    Resolves to ``visual_id`` (the visual's element id is the same id
    QuickSight uses for the visual itself); asserts auto-IDs are
    resolved before access."""
    assert not isinstance(node.visual_id, _AutoSentinel), (
        "visual_id wasn't resolved — App.resolve_auto_ids() must run "
        "before LayoutNode.element_id access."
    )
    return node.visual_id


def _require_non_blank_subtitle(visual: object) -> None:
    """Raise ValueError if the visual's subtitle is missing or blank.

    Enforces the project rule (CLAUDE.md): every visual carries a
    non-blank ``subtitle``. The constructor catches the bug at the
    call site instead of letting a blank subtitle through to a
    silently-mis-rendered dashboard.
    """
    subtitle = getattr(visual, "subtitle", None)
    if not isinstance(subtitle, str) or not subtitle.strip():
        title = getattr(visual, "title", "<unnamed>")
        raise ValueError(
            f"{type(visual).__name__}(title={title!r}): subtitle is "
            f"required and must be non-blank — every visual carries "
            f"a one-line plain-language subtitle (CLAUDE.md tree "
            f"convention). Got: {subtitle!r}"
        )


def field_label(leaf: Dim | Measure) -> str:
    """Plain-English header label for a Dim / Measure leaf (v8.5.0).

    Looks up the underlying ``Column``'s human_name from the
    contract registry. Falls back to a title-cased ``CalcField`` name
    when the leaf references a calc field instead of a real column.

    App2's ``_tree_fetcher`` reads this for chart axis / table column
    headers (it survived the DW emit strip — it's a renderer-agnostic
    label resolver, not QS serialization).
    """
    from recon_gen.common.dataset_contract import _smart_title
    from recon_gen.common.tree.calc_fields import CalcField as _CF
    from recon_gen.common.tree.datasets import Column

    col = leaf.column
    if isinstance(col, Column):
        return col.human_name
    if isinstance(col, _CF):
        # CalcField.name is auto-resolved at emit time, so by the
        # time field_label runs it's a real string. Belt-check via
        # ``str()`` so pyright doesn't complain about the
        # auto-sentinel union.
        return _smart_title(str(col.name))
    # Bare-string fallback (allow_bare_strings escape hatch).
    return _smart_title(str(col))


# BK.2 — QS-side icon enum + WCAG-AA hex colors for the KPI zero
# indicator. The App2-side glyph (``"✓"`` / ``"✗"``) and Tailwind
# color class (``"success"`` / ``"danger"``) live in
# ``common/html/_data_shape.py::shape_kpi`` to avoid a html→tree
# import that would invert the layering.
_KPI_HEALTHY_ICON_QS = "CHECKMARK"
_KPI_BROKEN_ICON_QS = "X"
# QS validation rejects lowercase hex with regex
# ``^#[A-F0-9]{6}$`` (caught via run_tests up_to=deploy probe
# 2026-05-29 on the v11.24.x BK.2 spike — lowercase ``#15803d`` /
# ``#b91c1c`` failed `CreateAnalysis`). Uppercase form.
_KPI_HEALTHY_COLOR_HEX = "#15803D"  # tailwind green-700
_KPI_BROKEN_COLOR_HEX = "#B91C1C"   # tailwind red-700


# Aggregation function names QS accepts inside the KPI conditional-
# formatting expression grammar (lowercase). Mirrors
# ``_NUMERICAL_AGG`` but lowercased for the expression DSL.
_KPI_INDICATOR_AGG_FN = {
    "sum": "sum",
    "max": "max",
    "min": "min",
    "average": "avg",
}


_KPI_INFLOW_ICON_QS = "ARROW_UP"
_KPI_OUTFLOW_ICON_QS = "ARROW_DOWN"
# Same WCAG-AA hex palette as the zero indicator — green-700 for the
# inflow/healthy side, red-700 for the outflow/broken side.
_KPI_INFLOW_COLOR_HEX = "#15803D"
_KPI_OUTFLOW_COLOR_HEX = "#B91C1C"


# CF.X-infra (2026-06-05) — 3-band threshold indicator. Green icon
# below `amber_at`, amber between `amber_at` and `red_at`, red at or
# above `red_at`. Icon is the load-bearing channel for accessibility
# (per BK.2 convention: distinct glyphs for colorblind operators);
# color rides along as parallel signal. Same UPPERCASE-hex + column-
# name expression + aggregation-wrap gotchas as the BK.2/BK.9
# emitters above.
# CF.X-infra deploy-probe (2026-06-05) — AWS run 26991328435 rejected
# EXCLAMATION_CIRCLE with the FULL valid set inline:
#   [TWO_BAR, THUMBS_DOWN, FACE_DOWN, ARROW_RIGHT, TRIANGLE, FLAG,
#    ARROW_UP_RIGHT, ARROW_DOWN_LEFT, CIRCLE, MINUS, ARROW_UP,
#    THREE_BAR, CHECKMARK, ARROW_DOWN_RIGHT, ARROW_UP_LEFT, CARET_UP,
#    X, ARROW_DOWN, ONE_BAR, PLUS, FACE_FLAT, THUMBS_UP, SQUARE,
#    FACE_UP, ARROW_LEFT, CARET_DOWN]
# TRIANGLE is the universal warning glyph (matches the ⚠ App2-side
# emit) and is the pre-locked fallback. FLAG was the secondary
# fallback but reads as "marked for review" rather than "warning
# state." See docs/reference/quicksight-quirks.md KPI icon enum entry.
_KPI_AMBER_ICON_QS = "TRIANGLE"
_KPI_AMBER_COLOR_HEX = "#B45309"  # tailwind amber-700 — WCAG-AA on white


@dataclass(frozen=True)
class KPIValueThresholdBanding:
    """CF.X-infra — 3-band threshold indicator on a KPI's primary
    value. Renders ``CHECKMARK`` (green) when the aggregated value
    is below ``amber_at``, ``EXCLAMATION_CIRCLE`` (amber) between
    ``amber_at`` and ``red_at``, and ``X`` (red) at or above
    ``red_at``.

    Accessibility (per BK.2 convention): ICON is the load-bearing
    channel — colorblind users see distinct ✓ / ⚠ / ✗ glyphs.
    Color rides along as parallel signal.

    Use this on count-style KPIs where the operator wants a glance-
    readable "is anything wrong, anywhere?" tripwire. Bound Measure
    kind must be sum / max / min / average — count / distinct_count
    are blocked at the emit boundary (use a sum-of-1s shape in the
    dataset SQL and ``kind='sum'`` instead).

    Wire shape:
    - **QS**: emits ``KPIVisual.ConditionalFormatting`` with three
      ``ConditionalFormattingOptions`` entries ordered RED → AMBER →
      GREEN (most-restrictive-first); QS picks the first matching
      expression at render time.
    - **App2**: the data-fetcher reads the primary value and emits
      ``state_icon`` (Unicode glyph) + ``state_color`` (semantic
      keyword — ``success`` / ``warning`` / ``danger``) on each
      ``values`` entry. ``bootstrap.js::renderKPI`` prepends the
      glyph + applies the color class.

    First consumer: CF.2 Exec program-health rollup
    (amber_at=1, red_at=20).
    """
    amber_at: int
    red_at: int

    def __post_init__(self) -> None:
        if self.red_at <= self.amber_at:
            raise ValueError(
                f"KPIValueThresholdBanding(amber_at={self.amber_at}, "
                f"red_at={self.red_at}): red_at must be strictly greater "
                f"than amber_at — otherwise the amber band has zero "
                f"width and the indicator collapses to binary."
            )


@dataclass(frozen=True)
class KPIValueSignIndicator:
    """BK.9 — sign-aware (▲ inflow / ▼ outflow) state indicator for a
    KPI's primary value. Renders ARROW_UP in green when the aggregated
    value is non-negative, ARROW_DOWN in red when it's negative.

    Accessibility: icon is the load-bearing channel (▲ vs ▼ is
    distinguishable to all viewers regardless of color perception);
    color rides along as a parallel signal.

    Same QS wire shape as ``KPIValueZeroIndicator`` (see for the
    three layered gotchas around hex case + expression DSL); the
    semantic differs — sign of the aggregated value, not zero-vs-
    not-zero. Currently used on Exec "Net Money Moved" (signed flow).
    """
    inflow_is_healthy: bool = True


@dataclass(frozen=True)
class KPIValueZeroIndicator:
    """BK.2 — binary healthy-when-zero state indicator for a KPI's
    primary value. Renders a CHECKMARK in green when the aggregated
    value equals zero, an X in red otherwise.

    Accessibility (per user 2026-05-29 — colorblind users in the loop):
    the ICON is the load-bearing channel. Color is a parallel signal
    for users who can read it, but the icon alone fully communicates
    healthy/broken state to red/green-colorblind viewers.

    Wire shape:
    - **QS**: emits ``KPIVisual.ConditionalFormatting`` with two
      ``ConditionalFormattingOptions`` entries, each carrying a
      ``PrimaryValue.Icon.CustomCondition`` block — the first matches
      ``zero``, the second matches ``non-zero``. QS evaluates the
      expression at render time against the displayed primary value.
    - **App2**: the data-fetcher reads the primary value and emits
      ``state_icon`` (Unicode glyph) + ``state_color`` (semantic
      keyword) on each ``values`` entry. ``bootstrap.js::renderKPI``
      prepends the glyph + applies the color class.

    The TWO renderers see different payloads but render the
    semantically-equivalent shape — same icon glyph, same color
    intent — so the operator gets the same signal on either surface.
    """
    healthy_when_zero: bool = True


@dataclass(eq=False)
class KPI:
    """KPI visual — single number per ``values`` entry, no grouping.

    Field-well shape: ``Values=[Measure, ...]``. Most KPIs use one
    measure; multiple are allowed and render as side-by-side numbers.

    ``value_zero_indicator`` (BK.2) — optional binary check/X+color
    state on the primary value. See ``KPIValueZeroIndicator``. Only
    fires on single-value KPIs (multi-value KPIs would need per-value
    indicators; not currently supported).

    ``visual_id`` is optional (L.1.8.5 auto-ID). When omitted, the
    App's tree walker assigns ``v-kpi-s{sheet_idx}-{visual_idx}`` at
    emit time. Pass an explicit ``VisualId(...)`` to override.
    """
    title: str
    subtitle: str
    values: list[Measure] = field(default_factory=list[Measure])
    value_zero_indicator: KPIValueZeroIndicator | None = None
    value_sign_indicator: KPIValueSignIndicator | None = None
    value_threshold_banding: KPIValueThresholdBanding | None = None
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "kpi"

    def __post_init__(self) -> None:
        _require_non_blank_subtitle(self)
        indicators = (
            self.value_zero_indicator,
            self.value_sign_indicator,
            self.value_threshold_banding,
        )
        n_indicators = sum(1 for i in indicators if i is not None)
        if n_indicators > 1:
            raise ValueError(
                f"KPI on {self.title!r}: pick ONE of "
                f"value_zero_indicator (healthy = $0), "
                f"value_sign_indicator (healthy = inflow), or "
                f"value_threshold_banding (3-band amber/red). They "
                f"emit competing ConditionalFormattingOptions and QS "
                f"picks the first match — mixing produces an undefined "
                f"render."
            )
        if n_indicators > 0 and len(self.values) != 1:
            raise ValueError(
                f"KPI(value_*_indicator=...) only supports single-"
                f"value KPIs; got {len(self.values)} values on "
                f"{self.title!r}. Drop the indicator or split the "
                f"KPI into one-value tiles."
            )

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        return {m.dataset for m in self.values}

    def calc_fields(self) -> set[CalcField]:
        """CalcFields this visual references via its field-well leaves."""
        return {cf for m in self.values if (cf := m.calc_field()) is not None}

@dataclass(eq=False)
class Table:
    """Table visual — two field-well shapes:

    - **Aggregated** (default): ``group_by=[Dim, ...]`` +
      ``values=[Measure, ...]``. One row per distinct ``group_by``
      combination, aggregated by ``values``. Emits
      ``TableAggregatedFieldWells``.
    - **Unaggregated**: pass ``columns=[Dim, ...]`` (and leave
      ``group_by`` / ``values`` empty). Each cell shows the raw column
      value — no aggregation, one row per source row. Emits
      ``TableUnaggregatedFieldWells``. Use this for detail/drill-source
      tables (AR Balances, AR Daily Statement transaction list).

    Optional ``sort_by`` is a ``(field_ref, direction)`` tuple —
    direction is ``"ASC"`` or ``"DESC"``.

    Optional ``conditional_formatting`` is a list of ``Drillable(on,
    color)`` markers — one per column the analyst should be able to drill
    from. The visual cue (accent text vs. accent + tint background)
    auto-derives from the column's drill triggers at emit time. The
    Phase DA type gate (``__post_init__``) raises if any ``Drillable.on``
    column has no drill writing from it.

    ``visual_id`` is optional (L.1.8.5 auto-ID).
    """
    title: str
    subtitle: str
    group_by: list[Dim] = field(default_factory=list[Dim])
    values: list[Measure] = field(default_factory=list[Measure])
    columns: list[Dim] = field(default_factory=list[Dim])
    sort_by: (
        tuple[FieldRef, Literal["ASC", "DESC"]]
        | list[tuple[FieldRef, Literal["ASC", "DESC"]]]
        | None
    ) = None
    actions: list[Action] = field(default_factory=list[Action])
    conditional_formatting: list[Drillable] | None = None
    #: CY.4 — when True, every row of this table carries a per-row
    #: ``metadata`` column that the App2 renderer surfaces as a popup
    #: (and the renderer stamps ``data-metadata-popup="1"`` on the
    #: ``<section>``). The bound dataset's contract MUST include a
    #: ``"metadata"`` column — enforced by ``__post_init__`` at the
    #: wiring site so the mistake fails at construction, not at fetch
    #: time. Default False = no popup, no extra column expectation.
    metadata_popup: bool = False
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "table"

    def __post_init__(self) -> None:
        _require_non_blank_subtitle(self)
        # Unaggregated and aggregated modes are mutually exclusive: if
        # `columns` is set, `group_by` and `values` must be empty (and
        # vice versa). This is the same pattern as the model's
        # `TableFieldWells` — exactly one of `TableAggregatedFieldWells`
        # / `TableUnaggregatedFieldWells` is set.
        if self.columns and (self.group_by or self.values):
            raise ValueError(
                "Table: `columns` (unaggregated mode) cannot be combined "
                "with `group_by` / `values` (aggregated mode). Pick one."
            )
        # CY.4 — fail at the wiring site when ``metadata_popup=True`` is
        # set on a Table whose bound dataset's contract doesn't declare
        # a ``metadata`` column. Without this, the renderer would emit
        # ``data-metadata-popup="1"`` and the row payload would silently
        # lack the column the JS popup expects — a fetch-time empty-
        # popup bug class. The check resolves the contract via the
        # module-level registry (populated by ``build_dataset()`` for
        # every visual-bound dataset); when no contract is registered
        # (early test fixtures, ad-hoc Datasets), the check is skipped
        # for the same reason ``Dataset.__getitem__`` skips column
        # validation in that case.
        if self.metadata_popup:
            from recon_gen.common.dataset_contract import get_contract

            datasets = self.datasets()
            if not datasets:
                raise ValueError(
                    f"Table {self.title!r}: metadata_popup=True requires "
                    f"the Table to be bound to a dataset (via columns / "
                    f"group_by / values), but found none."
                )
            for ds in datasets:
                try:
                    contract = get_contract(ds.identifier)
                except KeyError:
                    # No contract registered (test fixture / kitchen-sink) —
                    # same skip pattern as Dataset.__getitem__.
                    continue
                contract_columns = {c.name for c in contract.columns}
                if "metadata" not in contract_columns:
                    raise ValueError(
                        f"Table {self.title!r}: metadata_popup=True "
                        f"requires the bound dataset contract to "
                        f"include a 'metadata' column. Got columns: "
                        f"{sorted(contract_columns)}. Either drop "
                        f"metadata_popup or add metadata to the "
                        f"contract."
                    )
        # AA.A.8.bug — duplicate ``(dataset, column)`` entries in a Table's
        # field-well list make QuickSight reject the visual at render
        # time with: "your tabular report contains duplicate columns. To
        # proceed, remove all duplicates." The bug class is silent at
        # JSON emit (the model accepts the duplicate) and only surfaces
        # at render — operator sees a blank Stuck Pending Detail / etc.
        # Found 4 instances on 2026-05-17 (L1 Pending/Unbundled Aging,
        # Supersession Audit's Transactions Audit, Transactions sheet's
        # Posting Ledger — all ``ds["rail_name"].dim()`` listed twice).
        # Make the buggy line fail at construction instead: the
        # mistake is now a typed invariant violation at the wiring site,
        # not a runtime QS error 30 min into a deploy.
        from recon_gen.common.tree.calc_fields import resolve_column

        seen: dict[tuple[str, str], str] = {}
        for well_name, entries in (
            ("columns", self.columns),
            ("group_by", self.group_by),
            ("values", self.values),
        ):
            for entry in entries:
                ds_id = entry.dataset.identifier
                col_name = resolve_column(entry.column)
                key = (ds_id, col_name)
                if key in seen:
                    raise ValueError(
                        f"Table {self.title!r}: duplicate field-well entry "
                        f"({ds_id}, {col_name}) in {well_name} (also "
                        f"appears in {seen[key]}). QuickSight rejects "
                        f"this at render with 'your tabular report "
                        f"contains duplicate columns. To proceed, remove "
                        f"all duplicates.' — drop the duplicate from the "
                        f"field-well list."
                    )
                seen[key] = well_name
        # Phase DA — Drillable type-system gate. Every Drillable in
        # `conditional_formatting` declares a column as drillable; that
        # column MUST have at least one Drill in `actions` writing from
        # it (matched by column name on the Drill's writes list). Raises
        # at the wiring site with the offending Table + column + the
        # actual drill set so the author can fix the row at apps/<app>/
        # app.py without going through emit / deploy / dogfood loops.
        if self.conditional_formatting:
            drill_actions = [a for a in self.actions if isinstance(a, Drill)]
            for cf in self.conditional_formatting:
                target_col = resolve_column(cf.on.column)
                matching_drills: list[Drill] = []
                from recon_gen.common.tree.fields import Dim as _Dim
                for d in drill_actions:
                    for _param, src in d.writes:
                        if (
                            isinstance(src, _Dim)
                            and resolve_column(src.column) == target_col
                        ):
                            matching_drills.append(d)
                            break
                if not matching_drills:
                    # Build a helpful diagnostic — what drills DO exist
                    # on this Table, and which columns do they write
                    # from? Operator can spot the off-by-one column name
                    # at a glance.
                    drill_summary: list[str] = []
                    for d in drill_actions:
                        sources: list[str] = []
                        for _param, src in d.writes:
                            if isinstance(src, _Dim):
                                sources.append(resolve_column(src.column))
                        sources_str = (
                            ", ".join(sources) if sources else "(no Dim sources)"
                        )
                        drill_summary.append(
                            f"  {d.name!r} ({d.trigger}) writes from: {sources_str}"
                        )
                    summary = "\n".join(drill_summary) if drill_summary else (
                        "  (no Drill actions on this Table)"
                    )
                    raise ValueError(
                        f"Table {self.title!r}: Drillable(on={target_col!r}) "
                        f"is in conditional_formatting but no Drill in "
                        f"actions writes from that column. Either remove "
                        f"the Drillable, add a Drill that writes from "
                        f"{target_col!r}, or move the Drillable to the "
                        f"column the existing drill writes from.\nDrills "
                        f"currently on this Table:\n{summary}"
                    )

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        return (
            {d.dataset for d in self.group_by}
            | {m.dataset for m in self.values}
            | {d.dataset for d in self.columns}
        )

    def calc_fields(self) -> set[CalcField]:
        deps: set[CalcField] = set()
        for d in self.group_by:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        for m in self.values:
            if (cf := m.calc_field()) is not None:
                deps.add(cf)
        for d in self.columns:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        return deps

    def _all_leaves(self) -> list[Dim | Measure]:
        """All Dim/Measure leaves on this Table in field-well order.

        Order matters for QuickSight: the SelectedFieldOptions list
        determines the column order in the rendered table when the
        underlying field-well order is the default. Match the same
        order we emit field wells in (``columns`` for unaggregated,
        ``group_by`` then ``values`` for aggregated)."""
        if self.columns:
            return list(self.columns)
        leaves: list[Dim | Measure] = list(self.group_by)
        leaves.extend(self.values)
        return leaves


@dataclass(eq=False)
class BarChart:
    """Bar chart visual — one bar per distinct ``category``, height by
    ``values``.

    Field-well shape: ``Category=[Dim, ...]`` + ``Values=[Measure, ...]``.

    ``orientation`` (``"VERTICAL"`` or ``"HORIZONTAL"``) and
    ``bars_arrangement`` (``"CLUSTERED"`` / ``"STACKED"`` /
    ``"STACKED_PERCENT"``) pass through to the underlying
    ``BarChartConfiguration``. ``sort_by`` is a ``(field_id, direction)``
    tuple — direction ``"ASC"`` or ``"DESC"`` — and emits a
    ``CategorySort`` entry. All three default to ``None`` so the
    QuickSight defaults apply when not specified.

    ``visual_id`` is optional (L.1.8.5 auto-ID).
    """
    title: str
    subtitle: str
    category: list[Dim] = field(default_factory=list[Dim])
    values: list[Measure] = field(default_factory=list[Measure])
    colors: list[Dim] = field(default_factory=list[Dim])
    orientation: Literal["HORIZONTAL", "VERTICAL"] | None = None
    bars_arrangement: Literal[
        "CLUSTERED", "STACKED", "STACKED_PERCENT",
    ] | None = None
    category_label: str | None = None
    value_label: str | None = None
    color_label: str | None = None
    sort_by: tuple[FieldRef, Literal["ASC", "DESC"]] | None = None
    actions: list[Action] = field(default_factory=list[Action])
    # BQ.5 — value-axis log scale for one-bar-dominance presentation
    # (cold-read F6 + F7). When True, emits the
    # ``BarChartConfiguration.ValueAxis`` path with a base-10
    # ``AxisLogarithmicScale``. App2 mirrors via d3's ``scaleLog``.
    # Default False keeps QS's linear default behavior on every existing
    # chart. Documented in the value_label subtitle when set so trainees
    # know the y-axis isn't linear.
    log_scale: bool = False
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "bar"

    def __post_init__(self) -> None:
        _require_non_blank_subtitle(self)

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        return ({d.dataset for d in self.category}
                | {m.dataset for m in self.values}
                | {d.dataset for d in self.colors})

    def calc_fields(self) -> set[CalcField]:
        deps: set[CalcField] = set()
        for d in self.category:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        for m in self.values:
            if (cf := m.calc_field()) is not None:
                deps.add(cf)
        for d in self.colors:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        return deps

@dataclass(eq=False)
class LineChart:
    """Line chart visual — one line per distinct ``colors`` value,
    plotted across ``category`` (x-axis) with height by ``values``
    (y-axis).

    Field-well shape: ``Category=[Dim, ...]`` + ``Values=[Measure, ...]``
    + ``Colors=[Dim, ...]``.

    ``chart_type`` selects ``LINE`` (default), ``AREA``, or
    ``STACKED_AREA``. ``sort_by`` is a ``(field_id, direction)`` tuple
    — direction ``"ASC"`` or ``"DESC"`` — and emits a ``CategorySort``
    entry. All optional fields default to ``None`` so the QuickSight
    defaults apply when not specified.

    ``visual_id`` is optional (L.1.8.5 auto-ID).
    """
    title: str
    subtitle: str
    category: list[Dim] = field(default_factory=list[Dim])
    values: list[Measure] = field(default_factory=list[Measure])
    colors: list[Dim] = field(default_factory=list[Dim])
    chart_type: Literal["LINE", "AREA", "STACKED_AREA"] | None = None
    category_label: str | None = None
    value_label: str | None = None
    sort_by: tuple[FieldRef, Literal["ASC", "DESC"]] | None = None
    actions: list[Action] = field(default_factory=list[Action])
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "line"

    def __post_init__(self) -> None:
        _require_non_blank_subtitle(self)

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        return ({d.dataset for d in self.category}
                | {m.dataset for m in self.values}
                | {d.dataset for d in self.colors})

    def calc_fields(self) -> set[CalcField]:
        deps: set[CalcField] = set()
        for d in self.category:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        for m in self.values:
            if (cf := m.calc_field()) is not None:
                deps.add(cf)
        for d in self.colors:
            if (cf := d.calc_field()) is not None:
                deps.add(cf)
        return deps

@dataclass(eq=False)
class Sankey:
    """Sankey diagram visual — flows from ``source`` nodes to
    ``target`` nodes, ribbon thickness by ``weight``.

    Field-well shape: each of ``source`` / ``target`` / ``weight`` is
    a single ``Dim`` / ``Measure`` (the underlying model expects
    lists, but every usage today has exactly one entry; emit wraps).

    ``items_limit`` caps the number of source / destination nodes
    rendered (matches the ``ItemsLimit`` shape on the underlying
    sort configuration). ``OtherCategories`` defaults to ``"INCLUDE"``
    so capped flows roll into a "(others)" bucket rather than being
    dropped silently.

    ``visual_id`` is optional (L.1.8.5 auto-ID).
    """
    title: str
    subtitle: str
    source: Dim | None = None
    target: Dim | None = None
    weight: Measure | None = None
    items_limit: int | None = None
    actions: list[Action] = field(default_factory=list[Action])
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "sankey"

    def __post_init__(self) -> None:
        _require_non_blank_subtitle(self)

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        deps: set[Dataset] = set()
        if self.source is not None:
            deps.add(self.source.dataset)
        if self.target is not None:
            deps.add(self.target.dataset)
        if self.weight is not None:
            deps.add(self.weight.dataset)
        return deps

    def calc_fields(self) -> set[CalcField]:
        deps: set[CalcField] = set()
        for leaf in (self.source, self.target, self.weight):
            if leaf is None:
                continue
            if (cf := leaf.calc_field()) is not None:
                deps.add(cf)
        return deps

@dataclass(eq=False)
class ForceGraph:
    """Force-directed network visual — HTMX-dialect only (X.2 spike
    capability test for X.4).

    QuickSight's standard visual library doesn't include a force
    layout (only hierarchical ``SankeyDiagramVisual``), so this
    primitive exists to prove the L1 tree primitives can host a
    visual kind that no QS dialect emit knows how to render. The
    HTMX renderer's bootstrap dispatches to ``renderForceGraph``
    via d3-force; ``emit()`` raises because the QS pipeline
    intentionally has no path for this kind.

    Phase.1 design call: either keep this HTMX-only or wire a
    custom-visual emitter for QS. The capability test is the
    artifact; the layering decision is downstream.

    No field-well slots — the visual's data shape (nodes + links)
    flows through the data fetcher directly, not through QS field
    wells. ``visual_id`` is optional (L.1.8.5 auto-ID).
    """
    title: str
    subtitle: str
    actions: list[Action] = field(default_factory=list[Action])
    visual_id: VisualId | AutoResolved = AUTO

    _AUTO_KIND: ClassVar[str] = "force-graph"

    def __post_init__(self) -> None:
        _require_non_blank_subtitle(self)

    @property
    def element_id(self) -> str:
        return _visual_element_id(self)

    @property
    def element_type(self) -> GridLayoutElementType:
        return "VISUAL"

    def datasets(self) -> set[Dataset]:
        return set()

    def calc_fields(self) -> set[CalcField]:
        return set()

