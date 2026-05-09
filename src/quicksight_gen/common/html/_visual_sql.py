"""X.2.g.1.c — wrap a Visual's dataset SQL with its declared
visual-level aggregation.

QS aggregates at render time inside the QuickSight engine — the
dataset SQL returns row-grain data and QS computes ``COUNT(...)``
per visual based on field-well declarations. App2 doesn't have a
QuickSight engine, so the same aggregation has to happen at SQL
execution time. This module wraps the dataset SQL in a SELECT that
applies the visual's aggregation:

  KPI(values=[count(account_id)])    →  SELECT COUNT(account_id) FROM (sql)
  BarChart(category=[type],
           values=[count(account_id)]) →  SELECT type, COUNT(account_id)
                                           FROM (sql) GROUP BY type
  LineChart(category=[posted_date],
            values=[sum(amount)])       →  SELECT posted_date, SUM(amount)
                                           FROM (sql) GROUP BY posted_date
                                           ORDER BY posted_date
  Sankey(source=src,
         target=tgt,
         weight=sum(amount))            →  SELECT src, tgt, SUM(amount)
                                           FROM (sql) GROUP BY src, tgt
  Table(...)                            →  unwrapped (raw rows pass through)
  ForceGraph(...)                       →  N/A (non-SQL)

For Visuals with multiple measures (KPI with two count metrics, etc.),
each measure becomes a separate column in the SELECT — the per-kind
shape adapter projects the resulting rows into the JSON shape the
d3 renderer expects.

Dialect-portable: uses ``COUNT(col)`` / ``COUNT(DISTINCT col)`` /
``SUM(col)`` etc. — the SQL-92 aggregations every dialect supports.
The wrapping is plain string composition (no parser); the dataset
SQL goes verbatim inside ``FROM (...) sub`` so any dialect-specific
constructs in the dataset SQL come through unchanged.
"""

from __future__ import annotations

from typing import Any


# Map Measure.kind → SQL aggregation function. Mirrors what QS would
# compute at render time.
_AGG_SQL_FN = {
    "sum": "SUM",
    "max": "MAX",
    "min": "MIN",
    "average": "AVG",
    "count": "COUNT",
    "distinct_count": "COUNT(DISTINCT",
}


def _quote_col(name: str) -> str:
    """Quote a column identifier for the wrapper SELECT.

    Y.3.f.alt.1: every column reference in App2's wrapper SELECT must
    be double-quoted so the dialect-natural case-folding rules don't
    rewrite it. The dataset-side ``_oracle_lowercase_alias_wrapper``
    already produces case-preserved lowercase aliases (e.g.
    ``"account_id"``); quoting the App2-side ref preserves the same
    case and matches.

    On Postgres + SQLite the quoted-lowercase ref matches the lowercase
    DDL columns. On Oracle without quoting, an unquoted ``account_id``
    case-folds to ``ACCOUNT_ID`` and fails to find the wrapper's
    quoted-lowercase ``"account_id"`` (the m.5.d ORA-00904).
    """
    return f'"{name}"'


def _measure_sql(measure: Any) -> str:
    """Render one Measure as an aggregation expression.

    ``Measure.kind`` is "sum" / "max" / "min" / "average" / "count" /
    "distinct_count". ``Measure.column.name`` is the column to
    aggregate (gets quoted via ``_quote_col`` — see Y.3.f.alt.1).
    """
    kind = getattr(measure, "kind", None)
    column = getattr(getattr(measure, "column", None), "name", None)
    if not kind or not column:
        return ""
    quoted = _quote_col(column)
    fn = _AGG_SQL_FN.get(kind)
    if fn is None:
        return f"COUNT({quoted})"  # safe fallback
    if kind == "distinct_count":
        # _AGG_SQL_FN entry is "COUNT(DISTINCT" — needs a closing paren.
        return f"COUNT(DISTINCT {quoted})"
    return f"{fn}({quoted})"


def _dim_sql(dim: Any) -> str:
    """Return the quoted column reference for a Dim (used in GROUP BY
    + SELECT). See ``_quote_col`` for why the quoting matters on Oracle.
    """
    name = str(getattr(getattr(dim, "column", None), "name", "") or "")
    return _quote_col(name) if name else ""


def wrap_for_visual(base_sql: str, visual: Any) -> str:
    """Wrap ``base_sql`` with the aggregation declared on ``visual``.

    The wrap shape depends on the visual kind:

    - KPI: ``SELECT <agg(col)> [, <agg(col)>...] FROM (base_sql) sub``
    - BarChart: ``SELECT <cat>, <agg(val)> FROM (base_sql) sub
                 GROUP BY <cat>``
    - LineChart: same as BarChart plus ``ORDER BY <cat>``
    - Table: returned unwrapped (rows pass through, the renderer
      paginates client-side)
    - Sankey / ForceGraph: returned unwrapped (specialized
      projectors handle their own shape)
    - Unknown visual: returned unwrapped (best-effort)

    The dataset SQL is wrapped as ``FROM (<base_sql>) sub`` so any
    parameter binds, CTEs, or dialect quirks in the inner SQL pass
    through unchanged.
    """
    kind = type(visual).__name__
    if kind == "KPI":
        measures = getattr(visual, "values", []) or []
        if not measures:
            return base_sql
        cols = [_measure_sql(m) for m in measures]
        cols = [c for c in cols if c]
        if not cols:
            return base_sql
        return f"SELECT {', '.join(cols)} FROM (\n{base_sql}\n) sub"

    if kind in ("BarChart", "LineChart"):
        cats = getattr(visual, "category", []) or []
        measures = getattr(visual, "values", []) or []
        if not cats or not measures:
            return base_sql
        cat_cols = [_dim_sql(d) for d in cats]
        cat_cols = [c for c in cat_cols if c]
        meas_cols = [_measure_sql(m) for m in measures]
        meas_cols = [c for c in meas_cols if c]
        if not cat_cols or not meas_cols:
            return base_sql
        select_clause = ", ".join(cat_cols + meas_cols)
        group_clause = ", ".join(cat_cols)
        wrapped = (
            f"SELECT {select_clause} FROM (\n{base_sql}\n) sub "
            f"GROUP BY {group_clause}"
        )
        if kind == "LineChart":
            wrapped += f" ORDER BY {group_clause}"
        return wrapped

    if kind == "Sankey":
        # X.2.g.2.b — Sankey field-wells: scalar source + target Dims +
        # one weight Measure. shape_sankey reads cols 0/1/2 as
        # (source, target, value), so the wrap projects them in that
        # order and rolls up by (source, target). Without this, the
        # raw matview rows would land in shape_sankey with the wrong
        # column order and the d3 ribbons would render against the
        # wrong nodes (or fail to render at all).
        source = getattr(visual, "source", None)
        target = getattr(visual, "target", None)
        weight = getattr(visual, "weight", None)
        if source is None or target is None or weight is None:
            return base_sql
        src_col = _dim_sql(source)
        tgt_col = _dim_sql(target)
        wt_expr = _measure_sql(weight)
        if not src_col or not tgt_col or not wt_expr:
            return base_sql
        return (
            f"SELECT {src_col}, {tgt_col}, {wt_expr} "
            f"FROM (\n{base_sql}\n) sub "
            f"GROUP BY {src_col}, {tgt_col}"
        )

    # Table / ForceGraph / unknown → raw rows pass through.
    return base_sql
