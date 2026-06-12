"""Phase DB.2 — App2-consumed-attribute registry + construction-time gate.

The DA-shape parity gap that this gate exists to catch:

1. An author adds a field to a Visual dataclass.
2. The field flows into `Visual.emit()` and lands in the QS JSON.
3. App2's `_VisualPlan` extraction and renderer never read it.
4. QS renders the new feature; App2 silently drops it.
5. Months later an operator dogfoods and notices the divergence.

This gate fires at `App.resolve_auto_ids()` time. It walks every Visual
on the analysis, lists each settable dataclass field, and looks each
one up in `APP2_ATTRIBUTE_REGISTRY`. A missing entry raises
`App2ParityGap` at the wiring site with a diagnostic explaining how to
add the missing entry.

The registry is the typed source of truth: adding a field forces the
author to declare its parity disposition — App2 consumes it (and how),
tree-only / construction-only (no QS render either), or operator-locked
by-design divergence (with a `PARITY_BREAKS` cross-reference).

Hardcoded-emit inventory: a separate small section captures `emit()`
literal hardcodes that don't trace to a dataclass field but still
affect QS rendering (e.g. `KPI.emit()` hardcodes `KPIOptions.Comparison.
ComparisonMethod="PERCENT_DIFFERENCE"`). Operator-locked at the DB.0
audit: capture these one-time, doesn't grow per-Visual.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import TYPE_CHECKING, Final, Union

if TYPE_CHECKING:
    from recon_gen.common.tree.structure import App


@dataclass(frozen=True)
class App2Consumed:
    """The field flows through ``_VisualPlan`` (or a sibling extraction)
    and is honored by App2's renderer. ``consumer`` names the consuming
    code site (``_VisualPlan`` field name, ``shape_*`` kwarg, or
    ``render*`` data key) so future readers can trace the wire."""
    consumer: str


@dataclass(frozen=True)
class TreeOnly:
    """The field affects tree construction or QS-only validation, with
    no corresponding visual representation that App2 would render.
    Examples: ``visual_id`` (App2 derives section ID from URL params,
    not the tree field), validation-only ``__post_init__`` flags."""
    reason: str


@dataclass(frozen=True)
class ByDesign:
    """Operator-locked divergence — App2 deliberately doesn't honor.
    Cross-references the ``PARITY_BREAKS`` registry entry name so the
    full justification lives in one place."""
    parity_break: str


_Entry = Union[App2Consumed, TreeOnly, ByDesign]


# Per-Visual-kind, per-attribute parity entries. Keyed by
# ``type(visual).__name__``. Adding a new Visual dataclass field
# without a registry entry here raises ``App2ParityGap`` at
# ``App.resolve_auto_ids()`` time.
APP2_ATTRIBUTE_REGISTRY: Final[dict[str, dict[str, _Entry]]] = {
    "KPI": {
        "title": App2Consumed(consumer="render.py emit_html title block"),
        "subtitle": App2Consumed(consumer="render.py emit_html subtitle block"),
        "visual_id": TreeOnly(
            reason="App2 derives section ID from URL routing, not the field",
        ),
        "values": App2Consumed(consumer="shape_kpi rows -> values"),
        "value_zero_indicator": App2Consumed(
            consumer="_VisualPlan.kpi_zero_is_healthy",
        ),
        "value_sign_indicator": App2Consumed(
            consumer="_VisualPlan.kpi_inflow_is_healthy",
        ),
        "value_threshold_banding": App2Consumed(
            consumer="_VisualPlan.kpi_threshold_banding",
        ),
    },
    "Table": {
        "title": App2Consumed(consumer="render.py emit_html title block"),
        "subtitle": App2Consumed(consumer="render.py emit_html subtitle block"),
        "visual_id": TreeOnly(reason="section ID derives from URL routing"),
        "group_by": App2Consumed(consumer="SQL projection via wrap_for_visual"),
        "values": App2Consumed(consumer="SQL projection via wrap_for_visual"),
        "columns": App2Consumed(consumer="SQL projection via wrap_for_visual"),
        "sort_by": App2Consumed(
            consumer="render.py::_extract_table_sort_default -> URL sort_column",
        ),
        "actions": App2Consumed(
            consumer="render.py::_serialize_table_row_drills -> data-row-drills",
        ),
        "conditional_formatting": App2Consumed(
            consumer="_VisualPlan.column_decoration (Phase DA)",
        ),
        "metadata_popup": App2Consumed(
            consumer="render.py data-metadata-popup attr + ctxmenu entry (CY.4)",
        ),
    },
    "BarChart": {
        "title": App2Consumed(consumer="render.py emit_html title block"),
        "subtitle": App2Consumed(consumer="render.py emit_html subtitle block"),
        "visual_id": TreeOnly(reason="section ID derives from URL routing"),
        "category": App2Consumed(consumer="SQL projection col 0"),
        "values": App2Consumed(consumer="SQL projection col 1"),
        "colors": App2Consumed(consumer="_ChartMeta.series_column_name"),
        "orientation": App2Consumed(
            consumer="_ChartMeta.orientation (Phase DB.1.1)",
        ),
        "bars_arrangement": App2Consumed(consumer="_ChartMeta.stacked"),
        "category_label": App2Consumed(consumer="_ChartMeta.x_label"),
        "value_label": App2Consumed(consumer="_ChartMeta.y_label"),
        "color_label": App2Consumed(
            consumer="_ChartMeta.color_label (Phase DB.1.1)",
        ),
        "sort_by": App2Consumed(
            consumer="_extract_table_sort_default -> URL sort_column",
        ),
        "actions": ByDesign(
            parity_break="chart_visual_drill_clicks_unsupported_app2_only_table_drill",
        ),
        "log_scale": App2Consumed(consumer="_ChartMeta.log_scale (BQ.5)"),
    },
    "LineChart": {
        "title": App2Consumed(consumer="render.py emit_html title block"),
        "subtitle": App2Consumed(consumer="render.py emit_html subtitle block"),
        "visual_id": TreeOnly(reason="section ID derives from URL routing"),
        "category": App2Consumed(consumer="SQL projection col 0"),
        "values": App2Consumed(consumer="SQL projection col 1"),
        "colors": App2Consumed(consumer="_ChartMeta.series_column_name"),
        "chart_type": App2Consumed(
            consumer="_ChartMeta.chart_type (Phase DB.1.4)",
        ),
        "category_label": App2Consumed(consumer="_ChartMeta.x_label"),
        "value_label": App2Consumed(consumer="_ChartMeta.y_label"),
        "sort_by": App2Consumed(
            consumer="_extract_table_sort_default -> URL sort_column",
        ),
        "actions": ByDesign(
            parity_break="chart_visual_drill_clicks_unsupported_app2_only_table_drill",
        ),
    },
    "Sankey": {
        "title": App2Consumed(consumer="render.py emit_html title block"),
        "subtitle": App2Consumed(consumer="render.py emit_html subtitle block"),
        "visual_id": TreeOnly(reason="section ID derives from URL routing"),
        "source": App2Consumed(consumer="SQL projection col 0"),
        "target": App2Consumed(consumer="SQL projection col 1"),
        "weight": App2Consumed(consumer="SQL projection col 2"),
        "items_limit": App2Consumed(
            consumer="_VisualPlan.sankey_items_limit (Phase DB.1.2)",
        ),
        "actions": ByDesign(
            parity_break="chart_visual_drill_clicks_unsupported_app2_only_table_drill",
        ),
    },
    "ForceGraph": {
        # ForceGraph is App2-only by design (HARD_DIVERGENT in
        # PARITY_BREAKS) — emit() raises so QS divergence is N/A.
        "title": App2Consumed(consumer="render.py emit_html title block"),
        "subtitle": App2Consumed(consumer="render.py emit_html subtitle block"),
        "visual_id": TreeOnly(reason="section ID derives from URL routing"),
        "actions": TreeOnly(
            reason="ForceGraph is App2-only; QS never sees the actions",
        ),
    },
}


@dataclass(frozen=True)
class _HardcodedEmit:
    """One emit() literal hardcode — operator-locked inventory of
    JSON paths the tree fixes at emit time without an authoring
    dataclass field. Each entry names the visual, the JSON path the
    emit writes, the literal value, and whether App2 needs to consume
    or stay deliberately divergent.
    """
    visual: str
    emit_path: str
    value: str
    disposition: _Entry


# Hardcoded-emit inventory — emit() literals captured one-time per the
# DB.0 operator lock. NOT walked by the construction-time gate (the
# gate covers dataclass fields). Future-proofs by giving a single
# searchable surface for "what does QS render that we hardcode?".
HARDCODED_EMIT_INVENTORY: Final[tuple[_HardcodedEmit, ...]] = (
    _HardcodedEmit(
        visual="KPI",
        emit_path="KPIOptions.Comparison.ComparisonMethod",
        value="PERCENT_DIFFERENCE",
        disposition=ByDesign(
            parity_break="kpi_no_target_no_comparison_rendered",
        ),
    ),
    _HardcodedEmit(
        visual="KPI",
        emit_path="KPIOptions.PrimaryValueDisplayType",
        value="ACTUAL",
        disposition=App2Consumed(consumer="App2 always renders ACTUAL (no toggle)"),
    ),
    _HardcodedEmit(
        visual="KPI",
        emit_path="KPIOptions.Sparkline.Visibility",
        value="HIDDEN",
        disposition=ByDesign(
            parity_break="kpi_sparkline_no_trend_data_source_yet",
        ),
    ),
    _HardcodedEmit(
        visual="KPI",
        emit_path="KPIOptions.VisualLayoutOptions.StandardLayout.Type",
        value="VERTICAL",
        disposition=App2Consumed(consumer="App2 always renders vertical KPI"),
    ),
    _HardcodedEmit(
        visual="KPI",
        emit_path="KPIFieldWells.TargetValues",
        value="[]",
        disposition=ByDesign(
            parity_break="kpi_no_target_no_comparison_rendered",
        ),
    ),
    _HardcodedEmit(
        visual="KPI",
        emit_path="KPIFieldWells.TrendGroups",
        value="[]",
        disposition=ByDesign(
            parity_break="kpi_sparkline_no_trend_data_source_yet",
        ),
    ),
    _HardcodedEmit(
        visual="Sankey",
        emit_path="SankeyDiagramSortConfiguration.WeightSort.Direction",
        value="DESC",
        disposition=App2Consumed(
            consumer="shape_sankey aggregates DESC by construction",
        ),
    ),
    _HardcodedEmit(
        visual="Sankey",
        emit_path="SankeyDiagramSortConfiguration.*ItemsLimit.OtherCategories",
        value="INCLUDE",
        disposition=App2Consumed(
            consumer="shape_sankey (others) rollup (Phase DB.1.2)",
        ),
    ),
)


# Visual kinds we expect to find on the analysis. Anything else
# (sub-dataclasses without ``_AUTO_KIND``, helper types) is skipped
# silently. Adding a new top-level Visual class must add an entry
# here AND in APP2_ATTRIBUTE_REGISTRY.
_KNOWN_VISUAL_KINDS: Final[frozenset[str]] = frozenset(APP2_ATTRIBUTE_REGISTRY.keys())


class App2ParityGap(ValueError):
    """Raised at ``App.resolve_auto_ids()`` when a Visual carries a
    dataclass field with no corresponding entry in
    ``APP2_ATTRIBUTE_REGISTRY``. The fix is to add an entry with the
    right disposition (``App2Consumed`` / ``TreeOnly`` / ``ByDesign``)."""


def _is_private_attr(name: str) -> bool:
    return name.startswith("_")


def check_app2_parity(app: "App") -> None:
    """Phase DB.2 gate — walk every Visual on the App's analysis and
    assert each dataclass field has a parity entry in the registry.

    Idempotent: re-runs are safe. Skips Visuals whose class name isn't
    in ``_KNOWN_VISUAL_KINDS`` (helpers, sub-dataclasses); skips
    private (``_``-prefixed) and ``ClassVar`` fields.
    """
    if app.analysis is None:
        return
    for sheet_idx, sheet in enumerate(app.analysis.sheets):
        for visual_idx, visual in enumerate(sheet.visuals):
            kind = type(visual).__name__
            if kind not in _KNOWN_VISUAL_KINDS:
                continue
            if not is_dataclass(visual):
                continue
            entries = APP2_ATTRIBUTE_REGISTRY[kind]
            for f in fields(visual):
                if _is_private_attr(f.name):
                    continue
                if f.name not in entries:
                    raise App2ParityGap(
                        f"App2 parity gate (DB.2): Visual kind {kind!r} "
                        f"on sheet {sheet_idx} (visual {visual_idx}) has "
                        f"dataclass field {f.name!r} with no entry in "
                        f"APP2_ATTRIBUTE_REGISTRY. Add an entry to "
                        f"src/recon_gen/common/tree/app2_parity_registry.py "
                        f"declaring whether App2 consumes it "
                        f"(App2Consumed(consumer=...)), it's tree-only "
                        f"(TreeOnly(reason=...)), or it's an operator-locked "
                        f"divergence (ByDesign(parity_break=...))."
                    )
