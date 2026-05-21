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
