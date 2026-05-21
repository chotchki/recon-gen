"""X.2.f — per-visual data shape adapters.

Each d3 renderer in ``bootstrap.js`` expects a specific JSON shape.
The QS dialect bakes those shapes into AWS QuickSight's rendering
contract; the HTMX dialect needs the same shapes produced here from
SQL rows. This module is the single source of truth for the contract:

- ``shape_kpi``         → ``{values: [{value, label, format, delta}]}``
- ``shape_table``       → ``{columns: [{name}], rows, page_offset, page_size, total_rows, sort_column}``
- ``shape_bar_chart``   → ``{categories, values, x_label, y_label}``
- ``shape_line_chart``  → ``{x_values, series: [{name, values: [num]}], x_label, y_label}``
- ``shape_sankey``      → ``{nodes, links}``  (delegates to existing helper)
- ``shape_force_graph`` → ``{nodes, links}``  (delegates to existing helper)

Inputs: ``rows`` (sequence of row tuples) + ``columns`` (sequence of
column-name strings). Optional kwargs vary per kind (e.g. KPI's
``format``, BarChart's ``x_label`` / ``y_label``). The shapes match
exactly what ``bootstrap.js::renderXxx`` reads — diverging here
silently breaks rendering, so the test suite verifies the d3
expectations end-to-end against the rendered DOM.

X.2.g wires these into ``make_db_fetcher`` per-app; today they're
infrastructure that the smoke fetcher + future per-app fetchers
both consume.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def shape_kpi(
    rows: Sequence[Sequence[Any]],
    columns: Sequence[str],
    *,
    label: str | None = None,
    format: str | None = None,
    delta: float | None = None,
) -> dict[str, Any]:
    """Shape one or more SQL rows as a KPI payload.

    Single-row case → ``{values: [{value, label, format, delta?}]}``.
    Multi-row case  → one entry per row in ``values``.

    First column is the ``value``. ``label`` defaults to the second
    column's value (when present) or the kwarg. ``format`` /
    ``delta`` come from the kwargs (per-Visual config) when not
    obviously columnar — KPI typically carries one number, with the
    metadata supplied at the tree level rather than from SQL.
    """
    del columns  # name preserved for parity with other shape fns
    values: list[dict[str, Any]] = []
    for row in rows:
        entry: dict[str, Any] = {"value": row[0]}
        if label is not None:
            entry["label"] = label
        elif len(row) > 1:
            entry["label"] = row[1]
        if format is not None:
            entry["format"] = format
        if delta is not None:
            entry["delta"] = delta
        values.append(entry)
    return {"values": values}


def shape_table(
    rows: Sequence[Sequence[Any]],
    columns: Sequence[str],
    *,
    page_offset: int = 0,
    page_size: int | None = None,
    total_rows: int | None = None,
    sort_column: str = "",
    column_labels: Mapping[str, str] | None = None,
    column_formats: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Shape SQL rows as a paginated table.

    ``rows`` is the in-page slice (already paginated by the caller).
    ``page_size`` defaults to len(rows) — the renderer treats that
    as "one page of N rows". ``total_rows`` defaults to len(rows)
    when not supplied (no separate count query) — the renderer's
    pagination UI will hide page-N-of-M when total == page size.

    ``columns`` go out as ``[{"name", "label"?, "format"?}]`` objects —
    the shape ``bootstrap.js::renderTable`` reads. ``name`` is the raw
    SQL column; ``label`` is the plain-English header (``col.label ||
    col.name`` in the renderer); ``format`` (``"currency"`` / ``"number"``)
    drives ``formatTableCell`` + right-alignment.

    ``column_labels`` / ``column_formats`` are keyed by raw SQL column
    name (AO.R.1). They carry the SAME per-column presentation QuickSight
    derives from the contract + the visual's field leaves (``human_name``
    header, ``currency`` measure format) — the App2 tree fetcher resolves
    them and passes them here. When BOTH are omitted, columns emit as the
    bare ``[{"name"}]`` shape (renderer falls back to the raw name) so
    callers that don't have the tree (test stubs) are unaffected. A
    per-column key is omitted (not None) when there's no mapping for it,
    keeping the JSON clean.
    """
    labels = column_labels or {}
    formats = column_formats or {}

    def _col(name: str) -> dict[str, Any]:
        out: dict[str, Any] = {"name": name}
        label = labels.get(name)
        if label is not None:
            out["label"] = label
        fmt = formats.get(name)
        if fmt is not None:
            out["format"] = fmt
        return out

    return {
        "columns": [_col(str(c)) for c in columns],
        "rows": [list(r) for r in rows],
        "page_offset": page_offset,
        "page_size": page_size if page_size is not None else len(rows),
        "total_rows": total_rows if total_rows is not None else len(rows),
        "sort_column": sort_column,
    }


def shape_bar_chart(
    rows: Sequence[Sequence[Any]],
    columns: Sequence[str],
    *,
    x_label: str | None = None,
    y_label: str | None = None,
    series_column: int | None = None,
    format: str | None = None,
    stacked: bool = False,
) -> dict[str, Any]:
    """Shape SQL rows as a bar chart.

    Two modes (mirrors :func:`shape_line_chart`):

    - **Single series** (``series_column=None``, default): column 0 is
      the category, column 1 the value — emits the
      ``{categories, values}`` shorthand ``bootstrap.js`` accepts.
    - **Multi series** (``series_column=N``, AO.R.2): rows split by the
      value of column ``N`` (the BarChart's ``colors`` dim). Column 0 is
      the category; the remaining non-category, non-series column is the
      value. Emits ``{categories, series: [{name, values}]}`` aligned to
      a shared, first-seen-ordered category axis (``None`` where a
      series has no bar for a category).

    ``format`` (``"currency"`` / ``"number"``) drives the y-axis tick
    formatting; ``stacked`` asks the renderer to stack (vs. cluster) the
    series. Axis labels default to the column names; pass ``x_label`` /
    ``y_label`` to override (Q.1.a.3 plain-English labels).
    """
    x_label_out = (
        x_label if x_label is not None else (str(columns[0]) if columns else "")
    )

    def _decorate(out: dict[str, Any]) -> dict[str, Any]:
        if format is not None:
            out["format"] = format
        if stacked:
            out["stacked"] = True
        return out

    if series_column is None:
        cats: list[Any] = []
        vals: list[Any] = []
        for row in rows:
            cats.append(row[0])
            if len(row) > 1:
                vals.append(row[1])
        return _decorate({
            "categories": cats,
            "values": vals,
            "x_label": x_label_out,
            "y_label": (
                y_label if y_label is not None
                else (str(columns[1]) if len(columns) > 1 else "")
            ),
        })

    cat_order: list[Any] = []
    buckets: dict[Any, dict[Any, Any]] = {}
    for row in rows:
        cat = row[0]
        series_key = row[series_column]
        if cat not in cat_order:
            cat_order.append(cat)
        y_idx = next(
            (i for i in range(len(row)) if i != 0 and i != series_column),
            None,
        )
        buckets.setdefault(series_key, {})[cat] = (
            row[y_idx] if y_idx is not None else None
        )
    return _decorate({
        "categories": cat_order,
        "series": [
            {"name": str(name), "values": [pts.get(c) for c in cat_order]}
            for name, pts in buckets.items()
        ],
        "x_label": x_label_out,
        "y_label": y_label if y_label is not None else "",
    })


def shape_line_chart(
    rows: Sequence[Sequence[Any]],
    columns: Sequence[str],
    *,
    x_label: str | None = None,
    y_label: str | None = None,
    series_column: int | None = None,
    format: str | None = None,
) -> dict[str, Any]:
    """Shape SQL rows as one or more line series.

    Two modes:

    - **Single series** (``series_column=None``, default): all rows
      are one series, column 0 = x, column 1 = y. Series name is
      column 1's name.
    - **Multi-series** (``series_column=N``): rows split by the
      value of column ``N``. Column 0 = x, the column named
      ``y`` (or whichever non-x non-series column there is) = y.
      One ``series`` entry per distinct series value, in
      first-seen order.

    Single-series is the common case (timeseries → daily volume
    line); multi-series covers the "compare A vs B" overlay
    pattern.

    Output shape matches ``bootstrap.js::renderLineChart``:
    ``{"x_values": [...], "series": [{"name", "values": [num | None]}],
    "x_label", "y_label"}`` — ``values`` is index-aligned to
    ``x_values`` (``None`` where a series has no point at that x).
    """
    x_label_out = (
        x_label if x_label is not None
        else (str(columns[0]) if columns else "")
    )
    if series_column is None:
        x_values = [r[0] for r in rows]
        values: list[Any] = [r[1] if len(r) > 1 else 0 for r in rows]
        series_name = str(columns[1]) if len(columns) > 1 else "value"
        return {
            "x_values": x_values,
            "series": [{"name": series_name, "values": values}],
            "x_label": x_label_out,
            "y_label": (
                y_label if y_label is not None
                else (str(columns[1]) if len(columns) > 1 else "")
            ),
            **({"format": format} if format is not None else {}),
        }
    # Multi-series — bucket each series' (x → y) and align every
    # series to the shared, first-seen-ordered x axis.
    x_order: list[Any] = []
    buckets: dict[Any, dict[Any, Any]] = {}
    for row in rows:
        series_key = row[series_column]
        x_val = row[0]
        if x_val not in x_order:
            x_order.append(x_val)
        # y is the first column that's neither 0 nor series_column.
        y_idx = next(
            (i for i in range(len(row)) if i != 0 and i != series_column),
            None,
        )
        buckets.setdefault(series_key, {})[x_val] = (
            row[y_idx] if y_idx is not None else 0
        )
    return {
        "x_values": x_order,
        "series": [
            {"name": str(name), "values": [pts.get(x) for x in x_order]}
            for name, pts in buckets.items()
        ],
        "x_label": x_label_out,
        "y_label": y_label if y_label is not None else "",
        **({"format": format} if format is not None else {}),
    }


def shape_sankey(
    rows: Sequence[Sequence[Any]],
    columns: Sequence[str],
) -> dict[str, Any]:
    """Shape SQL rows as a d3-sankey ``{nodes, links}`` payload.

    Convention: column 0 = source name, column 1 = target name,
    column 2 = numeric flow value. Node ordering is first-seen
    (stable across calls with the same row set). Self-loops
    dropped (d3-sankey rejects them).

    Mirrors the existing ``_db_fetcher._money_trail_to_sankey``
    behavior — they could share an implementation in a follow-up,
    but for X.2.f the contract lives here and the existing helper
    stays in place to avoid a sweep.
    """
    del columns
    name_to_idx: dict[Any, int] = {}
    aggregated: dict[tuple[int, int], float] = {}
    for row in rows:
        source, target, amount = row[0], row[1], row[2]
        if source == target:
            continue
        if source not in name_to_idx:
            name_to_idx[source] = len(name_to_idx)
        if target not in name_to_idx:
            name_to_idx[target] = len(name_to_idx)
        key = (name_to_idx[source], name_to_idx[target])
        aggregated[key] = aggregated.get(key, 0.0) + float(amount)
    return {
        "nodes": [{"name": str(n)} for n in name_to_idx],
        "links": [
            {"source": s, "target": t, "value": v}
            for (s, t), v in aggregated.items()
        ],
    }


# Visual kind → shape function dispatcher. Used by callers that
# have a Visual object and need to pick the right shape fn — e.g.
# X.2.g's per-app fetcher walking the tree.
_SHAPE_DISPATCH = {
    "KPI": shape_kpi,
    "Table": shape_table,
    "BarChart": shape_bar_chart,
    "LineChart": shape_line_chart,
    "Sankey": shape_sankey,
}


def shape_for_kind(
    kind: str,
    rows: Sequence[Sequence[Any]],
    columns: Sequence[str],
    **kwargs: Any,
) -> dict[str, Any]:
    """Dispatch by visual kind name (matches ``type(visual).__name__``).

    Raises ``ValueError`` for an unknown kind so a typo in the tree
    fails loudly rather than rendering silently broken JSON. Callers
    that have a tree ``Visual`` object pass ``type(visual).__name__``
    as ``kind`` and any per-visual config as kwargs.

    ForceGraph is intentionally NOT dispatched here — its shape
    comes from L2 topology projection (no SQL), already handled by
    ``_db_fetcher._topology_to_force_graph``. Keeping it out of the
    SQL-shape dispatch makes the contract crisp: this dispatcher is
    for "rows came back from a query, shape them."
    """
    fn = _SHAPE_DISPATCH.get(kind)
    if fn is None:
        raise ValueError(
            f"No SQL-shape adapter for visual kind {kind!r}. "
            f"Add an entry to _SHAPE_DISPATCH or render it via a "
            f"separate (non-SQL) projector like ForceGraph uses."
        )
    return fn(rows, columns, **kwargs)
