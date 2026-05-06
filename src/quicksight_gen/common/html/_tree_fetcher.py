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

from quicksight_gen.common.config import Config
from quicksight_gen.common.dataset_contract import get_sql
from quicksight_gen.common.db import AsyncConnectionPool
from quicksight_gen.common.html._data_shape import shape_for_kind
from quicksight_gen.common.html._sql_executor import execute_visual_sql_async
from quicksight_gen.common.html._visual_sql import wrap_for_visual
from quicksight_gen.common.tree.structure import App


# Async fetcher shape — what production callers (the App2 server)
# get from ``make_tree_db_fetcher``. ``Mapping[str, str]`` (not
# ``dict``) so callers signal "I'm not going to mutate the URL params"
# at the type level.
DataFetcher = Callable[[str, Mapping[str, str]], Awaitable[Any]]
# Legacy sync alias, used by stub fetchers in tests + the older
# ``_db_fetcher.py`` code paths. The server route accepts both via
# ``inspect.iscoroutinefunction`` dispatch (X.2.n.5).
SyncDataFetcher = Callable[[str, Mapping[str, str]], Any]


# Visual fields that may carry Dim/Measure references back to a
# Dataset. Order matters — we return the FIRST dataset found, on
# the assumption that a visual's primary dataset is the one its
# values / category fields point at. Walk order matches typical
# visual construction (values are the load-bearing field for KPI /
# Table; category for BarChart; etc.).
_FIELDS_WITH_DATASET_REFS: tuple[str, ...] = (
    "values",
    "category",
    "color",
    "source",
    "destination",
    "weight",
    "group_by",
)


def _find_visual_dataset_identifier(visual: Any) -> str | None:
    """Walk a visual's known fields, return the first dataset
    identifier we find on a Dim or Measure.

    Returns ``None`` for visuals that don't carry a SQL-driven
    dataset (text boxes, non-data primitives). Callers treat that
    as "fetcher returns empty payload".
    """
    for field_name in _FIELDS_WITH_DATASET_REFS:
        field_val: Any = getattr(visual, field_name, None)
        if field_val is None:
            continue
        # Most visual fields are lists of Dim/Measure; a few
        # (Sankey source/target on certain shapes) are scalar refs.
        # Narrowing list[Any] from `isinstance(field_val, list)` keeps
        # the element type as Unknown — explicit annotation collapses
        # it back to ``list[Any]`` so pyright stops complaining about
        # the per-item walk below.
        if isinstance(field_val, list):
            candidates: list[Any] = field_val  # pyright: ignore[reportUnknownVariableType]
        else:
            candidates = [field_val]
        for item in candidates:
            ds: Any = getattr(item, "dataset", None)
            if ds is None:
                continue
            identifier: Any = getattr(ds, "identifier", None)
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
    visual_index: dict[str, tuple[str, str | None]] = {}
    for sheet in tree_app.analysis.sheets:
        for visual in sheet.visuals:
            vid = str(getattr(visual, "visual_id", ""))
            if not vid:
                continue
            kind = type(visual).__name__
            ds_id = _find_visual_dataset_identifier(visual)
            sql: str | None = None
            if ds_id is not None:
                base_sql = get_sql(ds_id)
                sql = wrap_for_visual(base_sql, visual)
            visual_index[vid] = (kind, sql)

    async def fetcher(visual_id: str, params: Mapping[str, str]) -> Any:
        if visual_id not in visual_index:
            # Unknown visual_id — typically a stale URL from a
            # cached page. Return empty so the d3 renderers paint
            # an empty visual instead of throwing.
            return {}
        kind, sql = visual_index[visual_id]
        if sql is None:
            # Visual without a SQL-backed dataset (text box etc.).
            # Empty payload renders as a blank visual — fine for
            # the page-chrome-only case.
            return {}
        rows, columns = await execute_visual_sql_async(
            pool, sql, params, dialect=cfg.dialect,
        )
        # ForceGraph + Sankey have specialized projectors today
        # (_db_fetcher._topology_to_force_graph, etc.); the generic
        # SQL path handles KPI / Table / BarChart / LineChart /
        # Sankey via shape_for_kind. Visual kinds without a SQL
        # adapter raise from shape_for_kind — same loud-failure
        # pattern as the SQL lookup above.
        return shape_for_kind(kind, rows, columns)

    return fetcher
