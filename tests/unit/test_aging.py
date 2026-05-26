"""Unit tests for ``common/aging.py`` — shared aging-bucket bar chart helper.

The helper is one factory function returning a ``Visual`` dataclass.
Tests assert the returned shape carries the inputs the caller passed
(title / subtitle / dataset_id / count_column) and that the orientation
defaults to horizontal — the entire reason this helper exists is to
keep the per-app aging-bucket bar visuals byte-identical across apps,
so a regression in default orientation or label wiring would
quietly fork the visual style.
"""

from __future__ import annotations

from recon_gen.common.aging import aging_bar_visual


def test_returns_horizontal_bar_chart_with_title_subtitle() -> None:
    v = aging_bar_visual(
        visual_id="aging-1",
        title="Stuck Pending Aging",
        subtitle="Count by aging bucket",
        dataset_id="ds-stuck-pending",
        count_column="leg_count",
    )
    bc = v.BarChartVisual
    assert bc is not None
    assert bc.VisualId == "aging-1"
    title = bc.Title
    subtitle = bc.Subtitle
    assert title is not None and subtitle is not None
    assert title.FormatText == {"PlainText": "Stuck Pending Aging"}
    assert subtitle.FormatText == {"PlainText": "Count by aging bucket"}
    cfg = bc.ChartConfiguration
    assert cfg is not None
    assert cfg.Orientation == "HORIZONTAL"


def test_category_field_wires_aging_bucket_column() -> None:
    """The aging-bucket column name is fixed by convention — every app
    feeds an L1 view that exposes a column literally named
    ``aging_bucket``. Hardcoding it here keeps the helper from leaking
    that detail to every caller."""
    v = aging_bar_visual(
        visual_id="aging-2",
        title="t", subtitle="s",
        dataset_id="ds-x",
        count_column="row_count",
    )
    bc = v.BarChartVisual
    assert bc is not None
    cfg = bc.ChartConfiguration
    assert cfg is not None
    fw = cfg.FieldWells
    assert fw is not None
    agg = fw.BarChartAggregatedFieldWells
    assert agg is not None
    category = agg.Category
    assert category is not None
    cat = category[0].CategoricalDimensionField
    assert cat is not None
    col = cat.Column
    assert col is not None
    assert col.DataSetIdentifier == "ds-x"
    assert col.ColumnName == "aging_bucket"


def test_value_field_uses_caller_count_column_with_count_aggregation() -> None:
    v = aging_bar_visual(
        visual_id="aging-3",
        title="t", subtitle="s",
        dataset_id="ds-y",
        count_column="custom_count_col",
    )
    bc = v.BarChartVisual
    assert bc is not None
    cfg = bc.ChartConfiguration
    assert cfg is not None
    fw = cfg.FieldWells
    assert fw is not None
    agg = fw.BarChartAggregatedFieldWells
    assert agg is not None
    values = agg.Values
    assert values is not None
    val = values[0].CategoricalMeasureField
    assert val is not None
    col = val.Column
    assert col is not None
    assert col.DataSetIdentifier == "ds-y"
    assert col.ColumnName == "custom_count_col"
    assert val.AggregationFunction == "COUNT"


def test_axis_labels_say_age_and_count() -> None:
    """The category-axis label is fixed at ``"Age"`` and the value-axis
    at ``"Count"`` — hardcoded so every aging visual reads the same
    even when the underlying column names differ."""
    v = aging_bar_visual(
        visual_id="aging-4",
        title="t", subtitle="s",
        dataset_id="ds-z",
        count_column="c",
    )
    bc = v.BarChartVisual
    assert bc is not None
    cfg = bc.ChartConfiguration
    assert cfg is not None
    cat_label_opts = cfg.CategoryLabelOptions
    val_label_opts = cfg.ValueLabelOptions
    assert cat_label_opts is not None and val_label_opts is not None
    cat_axis = cat_label_opts.AxisLabelOptions
    val_axis = val_label_opts.AxisLabelOptions
    assert cat_axis is not None and val_axis is not None
    cat_label = cat_axis[0]
    val_label = val_axis[0]
    assert cat_label.CustomLabel == "Age"
    assert val_label.CustomLabel == "Count"


def test_field_ids_derive_from_visual_id() -> None:
    """Field IDs prefix with the visual_id so two aging visuals on the
    same sheet don't collide. Encoded in the helper so callers can't
    forget the prefix and produce a duplicate-FieldId validator error."""
    v = aging_bar_visual(
        visual_id="my-special-aging",
        title="t", subtitle="s",
        dataset_id="ds", count_column="c",
    )
    bc = v.BarChartVisual
    assert bc is not None
    cfg = bc.ChartConfiguration
    assert cfg is not None
    fw = cfg.FieldWells
    assert fw is not None
    agg = fw.BarChartAggregatedFieldWells
    assert agg is not None
    category = agg.Category
    values = agg.Values
    assert category is not None and values is not None
    cat = category[0].CategoricalDimensionField
    val = values[0].CategoricalMeasureField
    assert cat is not None and val is not None
    assert cat.FieldId == "my-special-aging-dim"
    assert val.FieldId == "my-special-aging-count"
