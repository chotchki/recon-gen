"""X.2.f — per-visual data-shape adapter tests.

Each ``shape_xxx`` produces the JSON contract its bootstrap.js
``renderXxx`` reads. The browser-side renderers + their unit tests
under ``tests/js/`` are the authoritative shape spec; these tests
just verify the Python-side adapters produce that shape from raw
SQL rows.
"""

from __future__ import annotations

import pytest

from recon_gen.common.html._data_shape import (
    _SANKEY_OTHERS_NAME,
    shape_bar_chart,
    shape_for_kind,
    shape_kpi,
    shape_line_chart,
    shape_sankey,
    shape_table,
)


# ---------------------------------------------------------------------------
# KPI
# ---------------------------------------------------------------------------


def test_shape_kpi_single_value() -> None:
    out = shape_kpi(rows=[(47,)], columns=["count"], label="Open", format="number")
    assert out == {
        "values": [{"value": 47, "label": "Open", "format": "number"}],
    }


def test_shape_kpi_uses_second_column_as_label_when_no_kwarg() -> None:
    out = shape_kpi(rows=[(47, "Open Exceptions")], columns=["v", "lbl"])
    assert out["values"][0]["value"] == 47
    assert out["values"][0]["label"] == "Open Exceptions"


def test_shape_kpi_multi_row() -> None:
    out = shape_kpi(
        rows=[(47, "Open"), (12, "Closed")],
        columns=["v", "lbl"],
    )
    assert len(out["values"]) == 2


def test_shape_kpi_omits_metadata_when_not_supplied() -> None:
    """Renderer treats missing ``format`` / ``delta`` as "default"
    — adapter shouldn't insert them as None."""
    out = shape_kpi(rows=[(47,)], columns=["v"])
    assert "format" not in out["values"][0]
    assert "delta" not in out["values"][0]


def test_shape_kpi_threshold_banding_green_below_amber() -> None:
    """CF.X-infra — value < amber_at renders the GREEN state: ✓ icon
    + success color. Mirrors the QS-side CHECKMARK + green-700
    emit."""
    out = shape_kpi(
        rows=[(0,)], columns=["n"], threshold_banding=(1, 20),
    )
    entry = out["values"][0]
    assert entry["value"] == 0
    assert entry["state_icon"] == "✓"
    assert entry["state_color"] == "success"


def test_shape_kpi_threshold_banding_amber_at_threshold() -> None:
    """CF.X-infra — value == amber_at hits the AMBER band: ⚠ icon +
    warning color. Threshold is inclusive on the lower end."""
    out = shape_kpi(
        rows=[(1,)], columns=["n"], threshold_banding=(1, 20),
    )
    entry = out["values"][0]
    assert entry["state_icon"] == "⚠"
    assert entry["state_color"] == "warning"


def test_shape_kpi_threshold_banding_amber_between_thresholds() -> None:
    """CF.X-infra — amber_at < value < red_at stays in the AMBER
    band."""
    out = shape_kpi(
        rows=[(10,)], columns=["n"], threshold_banding=(1, 20),
    )
    entry = out["values"][0]
    assert entry["state_icon"] == "⚠"
    assert entry["state_color"] == "warning"


def test_shape_kpi_threshold_banding_red_at_threshold() -> None:
    """CF.X-infra — value == red_at hits the RED band: ✗ icon +
    danger color. Threshold is inclusive on the lower end (matches
    the QS expression ``>= red_at``)."""
    out = shape_kpi(
        rows=[(20,)], columns=["n"], threshold_banding=(1, 20),
    )
    entry = out["values"][0]
    assert entry["state_icon"] == "✗"
    assert entry["state_color"] == "danger"


def test_shape_kpi_threshold_banding_null_value_stays_neutral() -> None:
    """CF.X-infra — None values stay neutral (no state_icon /
    state_color). Parity with the BK.2 / BK.9 neutral-on-null
    contract: empty-source / no-data-yet renders as neutral, not as
    an alarming red."""
    out = shape_kpi(
        rows=[(None,)], columns=["n"], threshold_banding=(1, 20),
    )
    entry = out["values"][0]
    assert "state_icon" not in entry
    assert "state_color" not in entry


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


def test_shape_table_basic() -> None:
    out = shape_table(
        rows=[("a", 1), ("b", 2), ("c", 3)],
        columns=["letter", "n"],
    )
    # Column *objects* ({name}) — renderTable reads col.name.
    assert out["columns"] == [{"name": "letter"}, {"name": "n"}]
    assert out["rows"] == [["a", 1], ["b", 2], ["c", 3]]
    assert out["page_offset"] == 0
    assert out["page_size"] == 3
    assert out["total_rows"] == 3


def test_shape_table_pagination_metadata() -> None:
    """Caller supplies the in-page slice + the total count — the
    renderer's pagination UI uses both to render "page N of M"."""
    out = shape_table(
        rows=[("a", 1), ("b", 2)],
        columns=["letter", "n"],
        page_offset=20,
        page_size=10,
        total_rows=523,
        sort_column="n",
    )
    assert out["page_offset"] == 20
    assert out["page_size"] == 10
    assert out["total_rows"] == 523
    assert out["sort_column"] == "n"


# ---------------------------------------------------------------------------
# BarChart
# ---------------------------------------------------------------------------


def test_shape_bar_chart_categories_and_values() -> None:
    out = shape_bar_chart(
        rows=[("open", 47), ("closed", 12), ("pending", 5)],
        columns=["status", "count"],
    )
    assert out["categories"] == ["open", "closed", "pending"]
    assert out["values"] == [47, 12, 5]


def test_shape_bar_chart_axis_labels_default_to_column_names() -> None:
    out = shape_bar_chart(
        rows=[("a", 1)],
        columns=["status", "count"],
    )
    assert out["x_label"] == "status"
    assert out["y_label"] == "count"


def test_shape_bar_chart_explicit_axis_labels_override() -> None:
    out = shape_bar_chart(
        rows=[("a", 1)],
        columns=["status", "count"],
        x_label="Status", y_label="Open Count",
    )
    assert out["x_label"] == "Status"
    assert out["y_label"] == "Open Count"


# Phase DB.1.1 — BarChart orientation + color_label parity with QS.


def test_shape_bar_chart_horizontal_emits_orientation_key() -> None:
    """``orientation="HORIZONTAL"`` flows from `_ChartMeta` through
    `shape_bar_chart` to the renderer as ``data.orientation``. Pre-DB.1.1
    the kwarg didn't exist and the renderer always painted vertical."""
    out = shape_bar_chart(
        rows=[("a", 1)],
        columns=["status", "count"],
        orientation="HORIZONTAL",
    )
    assert out["orientation"] == "HORIZONTAL"


def test_shape_bar_chart_vertical_omits_orientation_key() -> None:
    """Default VERTICAL stays out of the payload so test fixtures
    without `orientation=` keep their pre-DB.1.1 JSON shape."""
    out = shape_bar_chart(
        rows=[("a", 1)],
        columns=["status", "count"],
        # orientation defaults to "VERTICAL".
    )
    assert "orientation" not in out


def test_shape_bar_chart_color_label_emits_when_set() -> None:
    """``color_label`` flows through to ``data.color_label`` so the
    renderer can paint the legend header (e.g. "Rail" above the
    rail-name swatches). Parity with QS's ColorLabelOptions.CustomLabel."""
    out = shape_bar_chart(
        rows=[("a", 10)],
        columns=["status", "count"],
        color_label="Rail",
    )
    assert out["color_label"] == "Rail"


def test_shape_bar_chart_color_label_omitted_by_default() -> None:
    out = shape_bar_chart(
        rows=[("a", 10)],
        columns=["status", "count"],
    )
    assert "color_label" not in out


# Phase DB.1.4 — LineChart Type parity with QS.


def test_shape_line_chart_chart_type_default_omits_key() -> None:
    """No ``chart_type=`` → omit from payload so existing fixtures
    stay byte-stable. Renderer falls back to ``LINE`` when absent."""
    out = shape_line_chart(
        rows=[(1, 10.0), (2, 20.0)],
        columns=["x", "y"],
    )
    assert "chart_type" not in out


def test_shape_line_chart_chart_type_line_explicit_omits_key() -> None:
    """Explicit ``chart_type="LINE"`` is the default — omit it too so
    only deviations show up in the payload."""
    out = shape_line_chart(
        rows=[(1, 10.0)],
        columns=["x", "y"],
        chart_type="LINE",
    )
    assert "chart_type" not in out


def test_shape_line_chart_chart_type_area_forwards() -> None:
    out = shape_line_chart(
        rows=[(1, 10.0), (2, 20.0)],
        columns=["x", "y"],
        chart_type="AREA",
    )
    assert out["chart_type"] == "AREA"


def test_shape_line_chart_chart_type_stacked_area_forwards() -> None:
    out = shape_line_chart(
        rows=[(1, 10.0, "A"), (1, 5.0, "B"), (2, 20.0, "A"), (2, 8.0, "B")],
        columns=["x", "y", "series"],
        series_column=2,
        chart_type="STACKED_AREA",
    )
    assert out["chart_type"] == "STACKED_AREA"
    # Verify the multi-series shape stayed intact.
    assert len(out["series"]) == 2


# ---------------------------------------------------------------------------
# LineChart — single + multi series
# ---------------------------------------------------------------------------


def test_shape_line_chart_single_series_default() -> None:
    out = shape_line_chart(
        rows=[("2030-01-01", 10), ("2030-01-02", 15), ("2030-01-03", 12)],
        columns=["day", "volume"],
    )
    # Parallel x_values + series[].values — the shape renderLineChart reads.
    assert out["x_values"] == ["2030-01-01", "2030-01-02", "2030-01-03"]
    assert len(out["series"]) == 1
    assert out["series"][0]["name"] == "volume"
    assert out["series"][0]["values"] == [10, 15, 12]
    assert out["x_label"] == "day"
    assert out["y_label"] == "volume"


def test_shape_line_chart_multi_series_buckets_by_series_column() -> None:
    """``series_column`` index splits rows into series. The shared x
    axis is first-seen-ordered; each series' ``values`` is index-aligned
    to ``x_values`` (``None`` where that series has no point)."""
    out = shape_line_chart(
        rows=[
            ("2030-01-01", "open", 10),
            ("2030-01-01", "closed", 5),
            ("2030-01-02", "open", 15),
            ("2030-01-03", "closed", 6),  # 'open' has no 2030-01-03 point
        ],
        columns=["day", "status", "count"],
        series_column=1,
    )
    assert out["x_values"] == ["2030-01-01", "2030-01-02", "2030-01-03"]
    assert len(out["series"]) == 2
    open_series = next(s for s in out["series"] if s["name"] == "open")
    closed_series = next(s for s in out["series"] if s["name"] == "closed")
    assert open_series["values"] == [10, 15, None]
    assert closed_series["values"] == [5, None, 6]


# ---------------------------------------------------------------------------
# Sankey
# ---------------------------------------------------------------------------


def test_shape_sankey_basic() -> None:
    out = shape_sankey(
        rows=[
            ("a", "b", 10.0),
            ("b", "c", 20.0),
        ],
        columns=["src", "dst", "value"],
    )
    assert out["nodes"] == [{"name": "a"}, {"name": "b"}, {"name": "c"}]
    assert {"source": 0, "target": 1, "value": 10.0} in out["links"]
    assert {"source": 1, "target": 2, "value": 20.0} in out["links"]


def test_shape_sankey_aggregates_repeated_pairs() -> None:
    """Multiple (source, target) rows sum into one link."""
    out = shape_sankey(
        rows=[
            ("a", "b", 10.0),
            ("a", "b", 5.0),
            ("a", "b", 7.0),
        ],
        columns=["src", "dst", "value"],
    )
    assert len(out["links"]) == 1
    assert out["links"][0]["value"] == 22.0


def test_shape_sankey_drops_self_loops() -> None:
    """d3-sankey rejects self-loops; adapter pre-strips them."""
    out = shape_sankey(
        rows=[("a", "a", 10.0), ("a", "b", 5.0)],
        columns=["src", "dst", "value"],
    )
    assert len(out["links"]) == 1
    # 'a' still gets a node (it appears as source in the surviving row).
    assert any(n["name"] == "a" for n in out["nodes"])


# Phase DB.1.2 — items_limit parity with QS SourceItemsLimit /
# DestinationItemsLimit + OtherCategories: INCLUDE rollup.


def test_shape_sankey_items_limit_none_keeps_all_nodes() -> None:
    """No cap → all distinct sources + destinations land on the diagram
    (pre-DB.1.2 behavior). Smoke check against a 5-node universe."""
    out = shape_sankey(
        rows=[
            ("s1", "d1", 10.0),
            ("s2", "d1", 9.0),
            ("s3", "d2", 8.0),
            ("s4", "d2", 7.0),
            ("s5", "d3", 6.0),
        ],
        columns=["src", "dst", "value"],
    )
    names = {n["name"] for n in out["nodes"]}
    assert names == {"s1", "s2", "s3", "s4", "s5", "d1", "d2", "d3"}
    assert _SANKEY_OTHERS_NAME not in names


def test_shape_sankey_items_limit_caps_sources_into_others() -> None:
    """items_limit=2 → top-2 sources by aggregate weight keep their
    names; the rest collapse into a single ``(others)`` node. Mirrors
    QS's SourceItemsLimit.OtherCategories=INCLUDE shape."""
    out = shape_sankey(
        rows=[
            # s1 = 100 (top), s2 = 50 (top), s3 = 5, s4 = 3 → (others) = 8
            ("s1", "d1", 100.0),
            ("s2", "d1", 50.0),
            ("s3", "d1", 5.0),
            ("s4", "d1", 3.0),
        ],
        columns=["src", "dst", "value"],
        items_limit=2,
    )
    node_names = {n["name"] for n in out["nodes"]}
    assert node_names == {"s1", "s2", _SANKEY_OTHERS_NAME, "d1"}
    # The (others) → d1 link aggregates s3+s4 = 8.0
    nodes_by_idx = [n["name"] for n in out["nodes"]]
    others_idx = nodes_by_idx.index(_SANKEY_OTHERS_NAME)
    d1_idx = nodes_by_idx.index("d1")
    others_link = next(
        link for link in out["links"]
        if link["source"] == others_idx and link["target"] == d1_idx
    )
    assert others_link["value"] == 8.0


def test_shape_sankey_items_limit_caps_destinations_into_others() -> None:
    """Same cap shape applied to the destination side. Top-2 destinations
    keep their names; rest land in a destination ``(others)`` bucket."""
    out = shape_sankey(
        rows=[
            # d1 = 100, d2 = 50, d3 = 3, d4 = 2 → (others) = 5
            ("s1", "d1", 100.0),
            ("s1", "d2", 50.0),
            ("s1", "d3", 3.0),
            ("s1", "d4", 2.0),
        ],
        columns=["src", "dst", "value"],
        items_limit=2,
    )
    node_names = {n["name"] for n in out["nodes"]}
    assert node_names == {"s1", "d1", "d2", _SANKEY_OTHERS_NAME}
    nodes_by_idx = [n["name"] for n in out["nodes"]]
    s1_idx = nodes_by_idx.index("s1")
    others_idx = nodes_by_idx.index(_SANKEY_OTHERS_NAME)
    others_link = next(
        link for link in out["links"]
        if link["source"] == s1_idx and link["target"] == others_idx
    )
    assert others_link["value"] == 5.0


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def test_shape_for_kind_dispatches_by_class_name() -> None:
    """``type(visual).__name__`` is the contract — KPI/Table/etc.
    map to the right shape function."""
    out = shape_for_kind("KPI", rows=[(47,)], columns=["v"], label="X")
    assert out == {"values": [{"value": 47, "label": "X"}]}

    out = shape_for_kind(
        "Table", rows=[("a", 1)], columns=["c", "n"],
    )
    assert out["columns"] == [{"name": "c"}, {"name": "n"}]


def test_shape_for_kind_raises_on_unknown() -> None:
    with pytest.raises(ValueError, match="No SQL-shape adapter"):
        shape_for_kind("HoloDeck", rows=[], columns=[])


def test_shape_for_kind_threads_kwargs() -> None:
    """Per-visual config (axis labels, sort column, etc.) flows
    through the dispatcher to the underlying shape fn."""
    out = shape_for_kind(
        "BarChart",
        rows=[("a", 1)],
        columns=["s", "n"],
        x_label="Status",
        y_label="Count",
    )
    assert out["x_label"] == "Status"
    assert out["y_label"] == "Count"


class TestBarChartChartMeta:
    """AO.R.2 — currency format, multi-series (colors dim), and stacked."""

    def test_single_series_keeps_values_shorthand(self) -> None:
        # Backward-compat: no series_column → {categories, values}.
        out = shape_bar_chart(rows=[("a", 1), ("b", 2)], columns=["s", "n"])
        assert out["categories"] == ["a", "b"]
        assert out["values"] == [1, 2]
        assert "series" not in out
        assert "stacked" not in out

    def test_format_threads_through(self) -> None:
        out = shape_bar_chart(
            rows=[("a", 100)], columns=["s", "amount"], format="currency",
        )
        assert out["format"] == "currency"

    def test_multi_series_buckets_by_series_column(self) -> None:
        # rows: (category, series, value). series_column=1 → one series
        # per distinct series value, aligned to the shared category axis.
        out = shape_bar_chart(
            rows=[
                ("2026-01-01", "ach", 5),
                ("2026-01-01", "wire", 2),
                ("2026-01-02", "ach", 7),
            ],
            columns=["day", "rail", "n"],
            series_column=1,
            stacked=True,
        )
        assert out["categories"] == ["2026-01-01", "2026-01-02"]
        names = {s["name"] for s in out["series"]}
        assert names == {"ach", "wire"}
        ach = next(s for s in out["series"] if s["name"] == "ach")
        assert ach["values"] == [5, 7]
        wire = next(s for s in out["series"] if s["name"] == "wire")
        assert wire["values"] == [2, None]  # no wire bar on day 2
        assert out["stacked"] is True

    def test_stacked_omitted_when_false(self) -> None:
        out = shape_bar_chart(
            rows=[("a", "x", 1)], columns=["c", "s", "n"], series_column=1,
        )
        assert "stacked" not in out


def test_shape_line_chart_format_threads_through() -> None:
    out = shape_line_chart(
        rows=[("2026-01-01", 10)], columns=["day", "amount"], format="currency",
    )
    assert out["format"] == "currency"
