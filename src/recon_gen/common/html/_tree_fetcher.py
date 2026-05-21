"""X.2.g.0 — generic per-tree DataFetcher factory.

Given a tree ``App``, walk its visuals and build an async
``DataFetcher`` that dispatches by ``visual_id``. The fetcher
resolves each visual's dataset SQL from the registry populated by
``build_dataset()`` (in ``common/dataset_contract.py``), executes
via ``_sql_executor.execute_visual_sql_async``, and shapes via
``_data_shape.shape_for_kind``.

Per-app wiring (X.2.g.1 onward):

    from recon_gen.apps.executives.app import build_executives_app
    from recon_gen.apps.executives.datasets import build_all_datasets
    from recon_gen.common.db import make_connection_pool
    build_all_datasets(cfg)         # populates the SQL registry
    tree_app = build_executives_app(cfg, l2_instance=instance)
    pool = await make_connection_pool(cfg, max_size=10)
    fetcher = make_tree_db_fetcher(tree_app, cfg, pool=pool)

No per-app fetcher code. The tree is the source of truth; visual
kinds drive the shape; dataset identifiers drive the SQL.

Visuals without a recoverable dataset (e.g. ``SheetTextBox``,
text-only Info panels) get a ``visual_id → None`` mapping at build
time, and the fetcher returns an empty payload for them — the d3
hydrators handle empty payloads gracefully.

X.2.n.4 — the fetcher is now ``async def``. The tree walk +
SQL-registry resolution + ``wrap_for_visual`` + ``shape_for_kind``
remain sync (pure CPU); only the SQL-execute roundtrip is awaited.
``DataFetcher`` is the new ``Awaitable``-returning type alias;
``SyncDataFetcher`` stays available so test stubs and the legacy
``_db_fetcher.py`` code paths continue to work without rewrite.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

# X.2.b URL contract: query params come back as a multi-dict (a key
# can repeat — ``?param_pRail=A&param_pRail=B``). The fetcher carries
# the full ``list[str]`` per key; the SQL executor picks the last
# value for single binds and expands 2+ values into an ``IN``-list
# (Y.2.app2.cde.multivalued). ``list[str]`` (not ``Sequence[str]``)
# on purpose — ``str`` IS a ``Sequence[str]``, so a stray ``{"x": "a"}``
# would type-check against ``Mapping[str, Sequence[str]]`` and then
# silently do ``"a"[-1]``; ``Mapping[str, list[str]]`` rejects it.

from recon_gen.common.config import Config
from recon_gen.common.dataset_contract import (
    get_contract,
    get_dataset_params,
    get_sql,
)
from recon_gen.common.db import AsyncConnectionPool
from recon_gen.common.html._data_shape import shape_for_kind
from recon_gen.common.html._sql_executor import execute_visual_sql_async
from recon_gen.common.html._visual_sql import wrap_for_visual
from recon_gen.common.ids import VisualId
from recon_gen.common.sql.dialect import Dialect, column_name
from recon_gen.common.tree.fields import Dim, Measure
from recon_gen.common.tree.structure import App
# AO.R.1 — reuse the EXACT label QuickSight stamps on a table header so
# App2 headers match QS by construction (single source of truth; the
# AO.R.5 parity gate asserts they stay in lock-step).
from recon_gen.common.tree.visuals import _field_label


# Async fetcher shape — what production callers (the App2 server)
# get from ``make_tree_db_fetcher``. ``VisualId`` (X.2.o.3) ties the
# fetcher to the tree's typed visual identifier — passing a SheetId
# or DashboardId here is a type error at the call site.
# ``Mapping[str, list[str]]`` (not ``dict``) so callers signal "I'm
# not going to mutate the URL params" at the type level; the
# ``list[str]`` value carries the full multi-dict (a query key can
# repeat — ``?param_pRail=A&param_pRail=B``).
DataFetcher = Callable[[VisualId, Mapping[str, list[str]]], Awaitable[Any]]
# Legacy sync alias, used by stub fetchers in tests + the older
# ``_db_fetcher.py`` code paths. The server route accepts both via
# ``inspect.iscoroutinefunction`` dispatch (X.2.n.5).
SyncDataFetcher = Callable[[VisualId, Mapping[str, list[str]]], Any]


# X.2.g.5.followon + X.2.h.5 — server-side pagination + sort for Table
# visuals. The renderer (``bootstrap.js::renderTable``) reads
# ``page_offset`` / ``page_size`` / ``total_rows`` / ``sort_column`` off
# the data fragment and re-fetches ``?page_offset=N&page_size=M&
# sort_column=<col>:<asc|desc>`` on pager / header clicks; without the
# server honoring these the fetcher returned EVERY row — a 68k-row
# L1-transactions table → a ~20 MB JSON fragment → the browser freezes
# building 68k <tr>s before any client-side pagination runs. Default
# page size mirrors the renderer's "0–50 of N" pager; capped so a
# crafted ``page_size`` can't OOM the server.
_TABLE_PAGE_SIZE = 50
_TABLE_PAGE_SIZE_MAX = 10_000
# A bare SQL identifier — the ONLY thing we'll splice into ORDER BY
# (the renderer sends ``<column-name>:<dir>``; the column name comes
# from the shaped result columns). Anything else → fall back to the
# stable ``ORDER BY 1``. (The worst a crafted name can do is name a
# non-existent column → SQL error → the fragment 500s; this guard is
# belt-and-braces, not the only line of defense — there's no untrusted
# input path to App2 in practice.)
_BARE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _page_int(params: Mapping[str, list[str]], key: str, default: int) -> int:
    """Read a non-negative int off the URL multi-dict (last value);
    fall back to ``default`` on missing / blank / non-numeric."""
    vals = params.get(key, [])
    raw = vals[-1].strip() if vals else ""
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        return default
    return n if n >= 0 else default


def _parse_sort(params: Mapping[str, list[str]]) -> tuple[str, bool]:
    """Parse the renderer's ``sort_column=<name>:<asc|desc>`` URL param.

    Returns ``(column_name, descending)`` — ``("", False)`` when absent,
    malformed, or the name isn't a bare identifier (→ the table page is
    ordered by column 1 instead).
    """
    vals = params.get("sort_column", [])
    raw = vals[-1].strip() if vals else ""
    if ":" not in raw:
        return "", False
    name, _, direction = raw.partition(":")
    name = name.strip()
    if not _BARE_IDENT_RE.match(name):
        return "", False
    return name, direction.strip().lower() == "desc"


def _paginate_table_sql(
    base_sql: str, *, offset: int, limit: int,
    sort_col: str, sort_desc: bool, dialect: Dialect,
) -> str:
    """Wrap ``base_sql`` with an ORDER BY + dialect-correct OFFSET/LIMIT
    + a ``COUNT(*) OVER ()`` total column (appended last; the fetcher
    strips it positionally, so the alias name is cosmetic).

    With a ``sort_col`` (a bare identifier — see ``_parse_sort``):
    ``ORDER BY <case-correct ref> [DESC], 1`` (the trailing ``1`` is a
    deterministic tiebreak so equal sort values don't shuffle page
    boundaries). Without one: ``ORDER BY 1`` — so pagination is stable
    across requests regardless of whether the base query's own
    ``ORDER BY`` survives the derived-table wrap (PG/Oracle don't
    promise it does). ``qs_page`` is letter-initial — Oracle rejects a
    leading-underscore identifier unquoted.
    """
    if sort_col:
        ref = column_name(sort_col, dialect)
        order_by = f"ORDER BY {ref}{' DESC' if sort_desc else ''}, 1"
    else:
        order_by = "ORDER BY 1"
    page_clause = (
        f"OFFSET {offset} ROWS FETCH NEXT {limit} ROWS ONLY"
        if dialect is Dialect.ORACLE
        else f"LIMIT {limit} OFFSET {offset}"  # postgres / sqlite
    )
    return (
        f"SELECT qs_page.*, COUNT(*) OVER () AS qs_row_total "
        f"FROM ({base_sql}) qs_page {order_by} {page_clause}"
    )


# Visual fields that may carry Dim/Measure references back to a
# Dataset. Order matters — we return the FIRST dataset found, on
# the assumption that a visual's primary dataset is the one its
# values / category fields point at. Walk order matches typical
# visual construction (values for KPI / group-by-Table; category for
# BarChart; columns for a flat-dump Table — X.2.u.3.fix: a Table
# built with `columns=[...]` and no group_by/values pointed nowhere,
# so its App2 fetch returned `{}` → an empty 0-row 0-col table).
_FIELDS_WITH_DATASET_REFS: tuple[str, ...] = (
    "values",
    "columns",
    "category",
    "color",
    "source",
    "destination",
    "weight",
    "group_by",
)


def _find_visual_dataset_identifier(visual: Any) -> str | None:  # typing-smell: ignore[explicit-any]: walks dynamic visual subtypes via getattr; static union of every Visual subtype would be fragile across tree changes
    """Walk a visual's known fields, return the first dataset
    identifier we find on a Dim or Measure.

    Returns ``None`` for visuals that don't carry a SQL-driven
    dataset (text boxes, non-data primitives). Callers treat that
    as "fetcher returns empty payload".
    """
    for field_name in _FIELDS_WITH_DATASET_REFS:
        field_val: Any = getattr(visual, field_name, None)  # typing-smell: ignore[explicit-any]: getattr returns Any; explicit annotation collapses to a known shape so the iteration below stays typeable
        if field_val is None:
            continue
        # Most visual fields are lists of Dim/Measure; a few
        # (Sankey source/target on certain shapes) are scalar refs.
        # Narrowing list[Any] from `isinstance(field_val, list)` keeps
        # the element type as Unknown — explicit annotation collapses
        # it back to ``list[Any]`` so pyright stops complaining about
        # the per-item walk below.
        if isinstance(field_val, list):
            candidates: list[Any] = field_val  # pyright: ignore[reportUnknownVariableType]  # typing-smell: ignore[explicit-any]: list elements are Dim/Measure unions narrowed by the per-item walk below
        else:
            candidates = [field_val]
        for item in candidates:
            ds: Any = getattr(item, "dataset", None)  # typing-smell: ignore[explicit-any]: dynamic getattr against Dim/Measure refs
            if ds is None:
                continue
            identifier: Any = getattr(ds, "identifier", None)  # typing-smell: ignore[explicit-any]: same dynamic-getattr pattern; coerced to str on return
            if identifier:
                return str(identifier)
    return None


@dataclass(frozen=True)
class _ChartMeta:
    """Per-chart presentation derived from a BarChart / LineChart's field
    wells (AO.R.2), so App2 charts match QuickSight: the series/``colors``
    dim (``series_column_name``, resolved to a column index at fetch
    time), plain-English axis labels, the value ``currency`` / ``number``
    format, and ``stacked`` (``bars_arrangement="STACKED"``)."""

    series_column_name: str | None
    x_label: str
    y_label: str
    value_format: str
    stacked: bool


@dataclass(frozen=True)
class _VisualPlan:
    """Pre-resolved per-visual fetch plan, built once at fetcher-construction
    and reused per request. ``column_labels`` / ``column_formats`` (AO.R.1)
    are keyed by raw SQL column name and carry the SAME per-column
    presentation QuickSight derives (contract ``human_name`` header +
    ``currency`` measure format) so App2 renders identical headers + money.
    ``chart`` (AO.R.2) is set for BarChart / LineChart visuals only."""

    kind: str
    sql: str | None
    ds_id: str | None
    column_labels: Mapping[str, str]
    column_formats: Mapping[str, str]
    chart: _ChartMeta | None


def _leaf_column_name(leaf: Any) -> str | None:  # typing-smell: ignore[explicit-any]: walks dynamic Dim/Measure leaves via getattr
    """The SQL column name a Dim/Measure leaf projects (its ``Column`` /
    ``CalcField`` ``name``), or None when there's no resolvable column."""
    col: Any = getattr(leaf, "column", None)  # typing-smell: ignore[explicit-any]: leaf.column is Column | CalcField | str
    name = getattr(col, "name", None)
    if name is None and isinstance(col, str):
        name = col
    return str(name) if name else None


def _table_column_meta(
    visual: Any,  # typing-smell: ignore[explicit-any]: dynamic visual subtype walked via getattr
    ds_id: str | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """AO.R.1 — per-column ``(label, format)`` for a visual, derived from the
    SAME sources QuickSight uses so App2 renders identical headers + money.

    - ``label`` ← the dataset contract's ``ColumnSpec.human_name`` (the
      ``display_name`` override or smart-titled snake_case) for every
      contract column — exactly what QS's ``_field_label`` resolves to.
    - ``format`` ← the visual's field leaves: a ``Measure`` formats as
      ``"currency"`` (when ``currency=True``) else ``"number"``; a ``Dim``
      formats as ``"currency"`` only when it carries ``currency=True``.
      Dimension ids stay unformatted (no thousands-separator on an id) —
      mirrors QS's measure-vs-dimension number formatting.

    Empty maps when the visual has no resolvable contract (text boxes etc.):
    the renderer then falls back to the raw column name, unformatted.
    """
    labels: dict[str, str] = {}
    formats: dict[str, str] = {}
    if ds_id is not None:
        try:
            contract = get_contract(ds_id)
        except KeyError:
            contract = None
        if contract is not None:
            for spec in contract.columns:
                labels[spec.name] = spec.human_name
    for field_name in _FIELDS_WITH_DATASET_REFS:
        field_val: Any = getattr(visual, field_name, None)  # typing-smell: ignore[explicit-any]: getattr returns Any; collapsed to a known shape below
        if field_val is None:
            continue
        if isinstance(field_val, list):
            items: list[Any] = field_val  # pyright: ignore[reportUnknownVariableType]  # typing-smell: ignore[explicit-any]: list of Dim/Measure unions narrowed by the isinstance walk below
        else:
            items = [field_val]
        for item in items:
            if not isinstance(item, (Dim, Measure)):
                continue
            name = _leaf_column_name(item)
            if name is None:
                continue
            # Authoritative header — the same _field_label QS emits as the
            # column's CustomLabel (overrides the contract entry for calc
            # fields, which aren't in the contract).
            labels[name] = _field_label(item)
            if isinstance(item, Measure):
                formats[name] = "currency" if getattr(item, "currency", False) else "number"
            elif getattr(item, "currency", False):
                formats[name] = "currency"
    return labels, formats


def _chart_meta(visual: Any) -> _ChartMeta | None:  # typing-smell: ignore[explicit-any]: dynamic visual subtype walked via getattr
    """AO.R.2 — per-chart presentation for a BarChart / LineChart, from
    the SAME field wells QuickSight reads. ``None`` for any other kind.

    - ``series_column_name`` ← the BarChart's first ``colors`` dim (the
      stacked/grouped series); ``None`` when there's no series dim.
    - ``x_label`` / ``y_label`` ← the author's ``category_label`` /
      ``value_label`` override, else ``_field_label`` of the first
      category / value leaf (the same human label QS axis-labels with).
    - ``value_format`` ← ``"currency"`` when the first value measure is
      ``currency=True``, else ``"number"``.
    - ``stacked`` ← ``bars_arrangement`` is ``STACKED`` / ``STACKED_PERCENT``.
    """
    kind = type(visual).__name__
    if kind not in ("BarChart", "LineChart"):
        return None
    cats = getattr(visual, "category", []) or []
    vals = getattr(visual, "values", []) or []
    if not cats or not vals:
        return None
    colors = getattr(visual, "colors", []) or []
    series_name = _leaf_column_name(colors[0]) if colors else None
    x_label = getattr(visual, "category_label", None) or _field_label(cats[0])
    y_label = getattr(visual, "value_label", None) or _field_label(vals[0])
    value_format = "currency" if getattr(vals[0], "currency", False) else "number"
    stacked = getattr(visual, "bars_arrangement", None) in (
        "STACKED", "STACKED_PERCENT",
    )
    return _ChartMeta(
        series_column_name=series_name,
        x_label=str(x_label),
        y_label=str(y_label),
        value_format=value_format,
        stacked=stacked,
    )


def make_tree_db_fetcher(
    tree_app: App,
    cfg: Config,
    *,
    pool: AsyncConnectionPool,
) -> DataFetcher:
    """Return an async ``DataFetcher`` that resolves any visual in
    ``tree_app`` to its dataset SQL → executes via the pool → shapes
    per kind.

    Construction-time invariants:

    1. ``tree_app.resolve_auto_ids()`` runs once so visual IDs are
       stable strings (not the AUTO sentinel) by the time we walk.
    2. The SQL registry must already be populated for every dataset
       the tree references — typically by calling the per-app
       ``build_all_datasets(cfg)`` BEFORE this factory. The factory
       eagerly resolves every visual's SQL so a missing entry
       fails loudly here, not silently inside a hot HTMX swap.
    3. ``pool`` is required — the App2 server's startup hook opens
       it via ``make_connection_pool(cfg)``; tests build a pool
       against in-memory SQLite via the same factory.

    Args:
        tree_app: The App whose visuals need data. Must have its
            analysis attached (validated implicitly via the walk).
        cfg: Loaded config; supplies dialect for SQL placeholder
            rewriting.
        pool: An open ``AsyncConnectionPool`` (PG / Oracle / SQLite).
            Lifecycle (open + close) belongs to the caller — usually
            the server's startup / shutdown hooks.

    Returns:
        An async ``DataFetcher`` matching the ``server.make_app``
        contract: ``await fetcher(visual_id, params) -> Any``.
    """
    tree_app.resolve_auto_ids()
    if tree_app.analysis is None:
        raise ValueError(
            f"App {tree_app.name!r} has no analysis attached — "
            f"can't build a fetcher with no visuals."
        )

    # Pre-resolve every visual's (kind, sql) at build time. Failures
    # surface here, not at request time. Visuals without datasets
    # land with sql=None and return empty payloads at fetch time.
    # X.2.g.1.c — wrap the dataset SQL with the visual's declared
    # aggregation (KPI count → SELECT COUNT, BarChart → GROUP BY
    # category, etc.). Without this, KPI visuals would render one
    # card per dataset row instead of the aggregated value QS shows.
    visual_index: dict[VisualId, _VisualPlan] = {}
    for sheet in tree_app.analysis.sheets:
        for visual in sheet.visuals:
            # ``visual.visual_id`` is ``VisualId | AutoResolved`` per
            # the tree types; ``resolve_auto_ids()`` above guarantees
            # we land on the str-shaped VisualId branch. Re-wrap for
            # the type checker without changing runtime behavior
            # (NewType is identity at runtime).
            vid_raw = getattr(visual, "visual_id", None)
            if not isinstance(vid_raw, str) or not vid_raw:
                continue
            vid = VisualId(vid_raw)
            kind = type(visual).__name__
            ds_id = _find_visual_dataset_identifier(visual)
            sql: str | None = None
            if ds_id is not None:
                base_sql = get_sql(ds_id)
                sql = wrap_for_visual(base_sql, visual)
            col_labels, col_formats = _table_column_meta(visual, ds_id)
            visual_index[vid] = _VisualPlan(
                kind=kind, sql=sql, ds_id=ds_id,
                column_labels=col_labels, column_formats=col_formats,
                chart=_chart_meta(visual),
            )

    async def fetcher(visual_id: VisualId, params: Mapping[str, list[str]]) -> Any:  # typing-smell: ignore[explicit-any]: per-visual-kind shape (KPI float, Sankey {nodes,links}, etc.) — JSON-serialized downstream, so a real union here would be every renderer's shape
        if visual_id not in visual_index:
            # Unknown visual_id — typically a stale URL from a
            # cached page. Return empty so the d3 renderers paint
            # an empty visual instead of throwing.
            return {}
        plan = visual_index[visual_id]
        kind, sql, ds_id = plan.kind, plan.sql, plan.ds_id
        if sql is None:
            # Visual without a SQL-backed dataset (text box etc.).
            # Empty payload renders as a blank visual — fine for
            # the page-chrome-only case.
            return {}
        # Y.2.app2.cde — resolve `<<$paramName>>` defaults from the
        # dataset's QS parameters when the URL doesn't supply them
        # (keeps the freshly-loaded page consistent with QS).
        dataset_params = get_dataset_params(ds_id) if ds_id else []
        if kind == "Table":
            # X.2.g.5.followon + X.2.h.5 — page (and sort) the table
            # SERVER-side. Without this a 68k-row dataset shipped 68k
            # rows in one ~20 MB JSON fragment and the browser froze
            # building the DOM. The renderer sends ``page_offset`` /
            # ``page_size`` / ``sort_column`` on pager / header clicks.
            offset = _page_int(params, "page_offset", 0)
            limit = max(1, min(
                _page_int(params, "page_size", _TABLE_PAGE_SIZE),
                _TABLE_PAGE_SIZE_MAX,
            ))
            sort_col, sort_desc = _parse_sort(params)
            paginated_sql = _paginate_table_sql(
                sql, offset=offset, limit=limit,
                sort_col=sort_col, sort_desc=sort_desc, dialect=cfg.dialect,
            )
            rows, columns = await execute_visual_sql_async(
                pool, paginated_sql, params, dialect=cfg.dialect,
                dataset_parameters=dataset_params,
            )
            # Last column is COUNT(*) OVER () — strip it positionally
            # (the alias name varies by dialect / driver case-folding).
            total = int(rows[0][-1]) if rows and rows[0] else 0
            page_rows = [list(r[:-1]) for r in rows]
            page_cols = list(columns[:-1])
            # Echo the *resolved* sort back (not the raw URL value) so
            # the renderer's sort badge + next-direction logic stays
            # consistent — ``""`` when it didn't parse / wasn't given.
            echo_sort = (
                f"{sort_col}:{'desc' if sort_desc else 'asc'}"
                if sort_col else ""
            )
            return shape_for_kind(
                "Table", page_rows, page_cols,
                page_offset=offset, page_size=limit, total_rows=total,
                sort_column=echo_sort,
                column_labels=plan.column_labels,
                column_formats=plan.column_formats,
            )
        rows, columns = await execute_visual_sql_async(
            pool, sql, params, dialect=cfg.dialect,
            dataset_parameters=dataset_params,
        )
        # AO.R.2 — BarChart / LineChart carry per-chart presentation
        # (series/colors dim → multi-series, axis labels, currency
        # format, stacked) derived from the tree at build time. Resolve
        # the series column to a positional index against the live
        # result columns (case-insensitive — Oracle upper-cases) and
        # pass the chart kwargs the shaper + d3 renderer read.
        if plan.chart is not None:
            series_column: int | None = None
            name = plan.chart.series_column_name
            if name:
                lowered = [str(c).lower() for c in columns]
                if name.lower() in lowered:
                    series_column = lowered.index(name.lower())
            chart_kwargs: dict[str, Any] = {  # typing-smell: ignore[explicit-any]: heterogeneous shape-fn kwargs (int|str|bool), splatted into shape_for_kind
                "series_column": series_column,
                "x_label": plan.chart.x_label,
                "y_label": plan.chart.y_label,
                "format": plan.chart.value_format,
            }
            if kind == "BarChart":
                chart_kwargs["stacked"] = plan.chart.stacked
            return shape_for_kind(kind, rows, columns, **chart_kwargs)
        # ForceGraph + Sankey have specialized projectors today
        # (_db_fetcher._topology_to_force_graph, etc.); the generic
        # SQL path handles KPI / Table / Sankey via shape_for_kind.
        # Visual kinds without a SQL adapter raise from shape_for_kind —
        # same loud-failure pattern as the SQL lookup above.
        return shape_for_kind(kind, rows, columns)

    return fetcher


# X.2.u.4.b — resolve a dataset-sourced dropdown's option universe.
# ``(dataset_identifier, column) -> sorted distinct values as strings``.
# App2's filter bar carries dataset-sourced dropdowns (a tree
# ``ParameterDropdown`` with a ``LinkedValues`` source) as ``<select>``
# widgets with an empty ``<option>`` list (``make_filter_specs_for_sheet``);
# the server calls this before rendering a sheet to fill them.
OptionsFetcher = Callable[[str, str], Awaitable[tuple[str, ...]]]


# Hard cap on a dataset-sourced dropdown's option count — a ``<select>``
# with more than this is a UX problem, not a feature (typeahead /
# server-side search for very large universes is a follow-on).
_OPTIONS_CAP = 2000


def make_options_fetcher(
    cfg: Config,
    *,
    pool: AsyncConnectionPool,
) -> OptionsFetcher:
    """Return an async ``OptionsFetcher`` over ``pool`` (X.2.u.4.b).

    Runs ``SELECT DISTINCT <col> FROM (<dataset SQL>) WHERE <col> IS NOT
    NULL ORDER BY 1 <limit>`` via the same async executor the visual
    fetches use, so placeholder substitution (with the source dataset's
    QS-parameter defaults) is included — a parameterized source dataset
    still resolves. ``<col>`` is the dialect-correct quoted ref
    (``column_name``); ``<limit>`` is ``LIMIT n`` (PG / SQLite) or
    ``FETCH FIRST n ROWS ONLY`` (Oracle).
    """
    async def fetch(dataset_identifier: str, column: str) -> tuple[str, ...]:
        base_sql = get_sql(dataset_identifier)
        col_ref = column_name(column, cfg.dialect)
        limit_clause = (
            f" FETCH FIRST {_OPTIONS_CAP} ROWS ONLY"
            if cfg.dialect == Dialect.ORACLE
            else f" LIMIT {_OPTIONS_CAP}"
        )
        options_sql = (
            f"SELECT DISTINCT {col_ref} AS opt FROM ({base_sql}) opt_src "
            f"WHERE {col_ref} IS NOT NULL ORDER BY 1{limit_clause}"
        )
        rows, _columns = await execute_visual_sql_async(
            pool, options_sql, {}, dialect=cfg.dialect,
            dataset_parameters=get_dataset_params(dataset_identifier),
        )
        return tuple(str(r[0]) for r in rows if r[0] is not None)

    return fetch
