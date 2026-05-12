"""X.2.g.0 — generic per-tree DataFetcher factory.

Given a tree ``App``, walk its visuals and build an async
``DataFetcher`` that dispatches by ``visual_id``. The fetcher
resolves each visual's dataset SQL from the registry populated by
``build_dataset()`` (in ``common/dataset_contract.py``), executes
via ``_sql_executor.execute_visual_sql_async``, and shapes via
``_data_shape.shape_for_kind``.

Per-app wiring (X.2.g.1 onward):

    from quicksight_gen.apps.executives.app import build_executives_app
    from quicksight_gen.apps.executives.datasets import build_all_datasets
    from quicksight_gen.common.db import make_connection_pool
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

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

# X.2.b URL contract: query params come back as a multi-dict (a key
# can repeat — ``?param_pRail=A&param_pRail=B``). The fetcher carries
# the full ``list[str]`` per key; the SQL executor picks the last
# value for single binds and expands 2+ values into an ``IN``-list
# (Y.2.app2.cde.multivalued). ``list[str]`` (not ``Sequence[str]``)
# on purpose — ``str`` IS a ``Sequence[str]``, so a stray ``{"x": "a"}``
# would type-check against ``Mapping[str, Sequence[str]]`` and then
# silently do ``"a"[-1]``; ``Mapping[str, list[str]]`` rejects it.

from quicksight_gen.common.config import Config
from quicksight_gen.common.dataset_contract import get_dataset_params, get_sql
from quicksight_gen.common.db import AsyncConnectionPool
from quicksight_gen.common.html._data_shape import shape_for_kind
from quicksight_gen.common.html._sql_executor import execute_visual_sql_async
from quicksight_gen.common.html._visual_sql import wrap_for_visual
from quicksight_gen.common.ids import VisualId
from quicksight_gen.common.sql.dialect import Dialect, column_name
from quicksight_gen.common.tree.structure import App


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
    # (kind, wrapped_sql | None, dataset_identifier | None) per visual.
    visual_index: dict[VisualId, tuple[str, str | None, str | None]] = {}
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
            visual_index[vid] = (kind, sql, ds_id)

    async def fetcher(visual_id: VisualId, params: Mapping[str, list[str]]) -> Any:  # typing-smell: ignore[explicit-any]: per-visual-kind shape (KPI float, Sankey {nodes,links}, etc.) — JSON-serialized downstream, so a real union here would be every renderer's shape
        if visual_id not in visual_index:
            # Unknown visual_id — typically a stale URL from a
            # cached page. Return empty so the d3 renderers paint
            # an empty visual instead of throwing.
            return {}
        kind, sql, ds_id = visual_index[visual_id]
        if sql is None:
            # Visual without a SQL-backed dataset (text box etc.).
            # Empty payload renders as a blank visual — fine for
            # the page-chrome-only case.
            return {}
        rows, columns = await execute_visual_sql_async(
            pool, sql, params, dialect=cfg.dialect,
            # Y.2.app2.cde — resolve `<<$paramName>>` defaults from
            # the dataset's QS parameters when the URL doesn't supply
            # them (keeps the freshly-loaded page consistent with QS).
            dataset_parameters=get_dataset_params(ds_id) if ds_id else [],
        )
        # ForceGraph + Sankey have specialized projectors today
        # (_db_fetcher._topology_to_force_graph, etc.); the generic
        # SQL path handles KPI / Table / BarChart / LineChart /
        # Sankey via shape_for_kind. Visual kinds without a SQL
        # adapter raise from shape_for_kind — same loud-failure
        # pattern as the SQL lookup above.
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
