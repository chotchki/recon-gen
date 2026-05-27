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


def _measure_sql(measure: Any, *, contract: Any) -> str:
    """Render one Measure as an aggregation expression.

    ``Measure.kind`` is "sum" / "max" / "min" / "average" / "count" /
    "distinct_count". ``Measure.column.name`` is the column to
    aggregate (gets quoted via ``_quote_col`` — see Y.3.f.alt.1).

    BH.24.6 (2026-05-25): contract is now **required** — the BH.24.1
    backwards-compat "no contract → divide on currency=True alone"
    fallback was an escape hatch that hid the exact bug class BH.24
    was created to fix (per ``feedback_no_compat_shims`` + user
    "needs to be gone by the end of BH.24"). Callers MUST register a
    contract for any dataset whose visuals route through this
    function. Production does this via ``build_dataset`` already;
    tests register contracts in their module-level setup. The
    cents → dollars /100 divide fires iff the contract declares
    the column ``storage=CENTS``.

    ``count`` / ``distinct_count`` never divide (they're row counts,
    not cents sums) regardless of contract.
    """
    kind = getattr(measure, "kind", None)
    column = getattr(getattr(measure, "column", None), "name", None)
    if not kind or not column:
        return ""
    if kind == "count":
        # BL.1 — App2 + QS stay symmetric: QS emits
        # NumericalMeasureField(SUM) over a literal-1 CalcField; App2
        # emits ``SUM(1)``. Both compute the same row count without
        # tripping QS's "COUNT on string-dim column renders distinct"
        # quirk. The Measure's column ref is intentionally ignored
        # for row-count semantics (any non-null column would give the
        # same number; SUM(1) makes the intent explicit).
        return "SUM(1)"
    quoted = _quote_col(column)
    fn = _AGG_SQL_FN.get(kind)
    is_currency = bool(getattr(measure, "currency", False))
    counting = kind == "distinct_count"
    if fn is None:
        return f"COUNT({quoted})"  # safe fallback
    if kind == "distinct_count":
        # _AGG_SQL_FN entry is "COUNT(DISTINCT" — needs a closing paren.
        return f"COUNT(DISTINCT {quoted})"
    expr = f"{fn}({quoted})"
    if is_currency and not counting and _wants_cents_divide(column, contract):
        # cents → dollars at the SQL boundary; matches
        # cents_to_dollars_sql's PG/Oracle/SQLite shape (the implicit
        # NUMERIC promotion of `int / 100.0` works on every dialect
        # the App2 path supports).
        expr = f"({expr} / 100.0)"
    return expr


def _wants_cents_divide(column: str, contract: Any) -> bool:
    """BH.24.6 — return True iff ``_measure_sql`` should emit a /100
    divide for this column.

    Decision (contract REQUIRED post-BH.24.6):
    - Contract has the column declared ``storage=CENTS`` → divide.
    - Contract has the column declared ``storage=DOLLARS`` (the
      default) → skip divide.
    - Contract is None → raises (no silent fallback; the BH.24 bug
      class lived in exactly that "fall back to old heuristic" path).
    - Contract declared but missing the column → raises (same
      reason — silent assumption is a bug-hiding fallback).
    """
    if contract is None:
        raise RuntimeError(
            f"_wants_cents_divide({column!r}): contract is None. "
            "Per BH.24.6, every visual-served dataset must register "
            "a DatasetContract via build_dataset (production) or "
            "register_contract (tests / fixtures). The pre-BH.24.6 "
            "no-contract fallback ('divide whenever currency=True') "
            "hid the BG.7 100× systemic bug — removed."
        )
    # Local import keeps this module free of a hard dependency on
    # dataset_contract at import time (avoids circular import shapes).
    from recon_gen.common.dataset_contract import Storage

    for col in getattr(contract, "columns", []):
        if getattr(col, "name", None) == column:
            return getattr(col, "storage", Storage.DOLLARS) is Storage.CENTS
    raise RuntimeError(
        f"_wants_cents_divide({column!r}): contract has columns "
        f"{[getattr(c, 'name', None) for c in getattr(contract, 'columns', [])]!r} "
        "but not this column. Either add a ColumnSpec for it or "
        "verify the visual's field reference matches the dataset's "
        "projected column name."
    )


def _dim_sql(dim: Any) -> str:
    """Return the quoted column reference for a Dim (used in GROUP BY
    + SELECT). See ``_quote_col`` for why the quoting matters on Oracle.

    Dim never carries an aggregation — it's a raw column reference.
    When the Dim is ``currency=True`` (a money column projected as-is
    into a table or chart category), the cents→dollars conversion has
    to land on the row-side (``_apply_cents_to_dollars`` in the fetcher)
    because the Dim reference appears in GROUP BY and projection both —
    dividing here would force GROUP BY (col/100.0) which is fine
    semantically but breaks ORDER BY by ordinal (the fetcher sorts on
    the bare ref).
    """
    name = str(getattr(getattr(dim, "column", None), "name", "") or "")
    return _quote_col(name) if name else ""


def wrap_for_visual(base_sql: str, visual: Any, *, contract: Any = None) -> str:
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

    BH.24.1 (2026-05-25): ``contract`` (a ``DatasetContract``)
    threads through to ``_measure_sql`` so the cents→dollars divide
    is gated on the column's storage shape (not on ``currency=True``
    alone). Production caller (``_tree_fetcher.make_tree_db_fetcher``)
    looks up the contract via ``get_contract(ds_id)`` and passes it;
    ad-hoc callers (tests, Studio preview) can omit it and keep the
    pre-BH.24.1 "divide whenever currency=True" behavior.
    """
    kind = type(visual).__name__
    if kind == "KPI":
        measures = getattr(visual, "values", []) or []
        if not measures:
            return base_sql
        cols = [_measure_sql(m, contract=contract) for m in measures]
        cols = [c for c in cols if c]
        if not cols:
            return base_sql
        return f"SELECT {', '.join(cols)} FROM (\n{base_sql}\n) sub"

    if kind in ("BarChart", "LineChart"):
        cats = getattr(visual, "category", []) or []
        measures = getattr(visual, "values", []) or []
        if not cats or not measures:
            return base_sql
        cat_cols = [c for c in (_dim_sql(d) for d in cats) if c]
        # AO.R.2 — a BarChart's ``colors`` dim is the stacked/grouped
        # series. Project + group it (between category and value) so the
        # per-series breakdown survives to ``shape_bar_chart``; without
        # this the SQL rolled up to one value per category and App2 lost
        # the rail-name composition QuickSight stacks. (LineChart has no
        # ``colors`` field, so ``color_cols`` is empty there — no change.)
        color_dims = getattr(visual, "colors", []) or []
        color_cols = [c for c in (_dim_sql(d) for d in color_dims) if c]
        meas_cols = [c for c in (_measure_sql(m, contract=contract) for m in measures) if c]
        if not cat_cols or not meas_cols:
            return base_sql
        select_clause = ", ".join(cat_cols + color_cols + meas_cols)
        group_clause = ", ".join(cat_cols + color_cols)
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
        wt_expr = _measure_sql(weight, contract=contract)
        if not src_col or not tgt_col or not wt_expr:
            return base_sql
        return (
            f"SELECT {src_col}, {tgt_col}, {wt_expr} "
            f"FROM (\n{base_sql}\n) sub "
            f"GROUP BY {src_col}, {tgt_col}"
        )

    # Table / ForceGraph / unknown → raw rows pass through.
    return base_sql
