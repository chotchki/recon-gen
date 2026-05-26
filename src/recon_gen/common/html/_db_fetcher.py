"""X.2.a.4 — DB-backed DataFetcher factory for App2.

The smoke runner's stub fetcher (``_smoke_app.stub_money_trail_fetcher``)
returns deterministic fake data so the JS / swap pipeline can be
exercised without infrastructure. This module is the production
counterpart: query the per-instance Investigation matview for the
Money Trail Sankey, project the L2 instance's accounts + rails to
the d3-force topology shape.

The two stages are separated so each is independently testable:

- ``_money_trail_to_sankey(rows, ...)`` — pure shape converter.
  Takes prefetched ``(source_display, target_display, hop_amount)``
  rows + returns the d3-sankey ``{nodes, links}`` shape. No DB.
- ``_topology_to_force_graph(instance)`` — pure projector. Reads
  ``L2Instance.accounts`` + ``rails`` + returns the d3-force
  ``{nodes, links}`` shape. No DB.
- ``make_db_fetcher(cfg, instance, ...)`` — wires both into a
  ``DataFetcher`` callable that the App2 server invokes per HTMX
  POST. Branches by ``visual_id``.

The connection-opening path is injected (``connection_factory``)
so unit tests can swap in a fake DB-API 2.0 connection without
needing a live Postgres / Oracle.

X.3 (SQLite) is the third dialect this fetcher gains for free —
``connect_demo_db`` already branches on ``cfg.dialect``; the SQL
here uses portable subset (CAST, ``||`` concat, no JSONB / window
functions / db-specific casts) so the same query body runs against
all three.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from recon_gen.common.config import Config
from recon_gen.common.l2.primitives import (
    L2Instance,
    SingleLegRail,
    TwoLegRail,
)
from recon_gen.common.money import Cents


# URL params arrive as a multi-dict (a key can repeat). This legacy
# fetcher only consumes scalar filters (date_from / date_to), so it
# collapses each list to its last value at the top of ``fetcher``.
DataFetcher = Callable[[str, Mapping[str, list[str]]], Any]


def make_db_fetcher(
    cfg: Config,
    instance: L2Instance,
    *,
    connection_factory: Callable[[], Any] | None = None,
) -> DataFetcher:
    """Return a ``DataFetcher`` closing over (cfg, instance).

    Per-visual dispatch:

    - ``smoke-sankey`` — runs the Money Trail aggregation against the
      ``<prefix>_inv_money_trail_edges`` matview, shapes to d3-sankey.
    - ``smoke-force`` — projects the L2 instance's accounts + rails
      to d3-force shape (no DB).

    Args:
        cfg: loaded config; supplies dialect + connection url +
          ``db_table_prefix`` (the per-deploy DB schema prefix).
        instance: L2 instance the App2 is rendering (for topology
          projection — accounts + rails).
        connection_factory: callable returning a fresh DB-API 2.0
          connection. Defaults to ``connect_demo_db(cfg)``. Tests
          inject a fake; production opens the real DB.

    Returns:
        A DataFetcher matching the ``server.make_app`` contract.
        Raises ``ValueError`` for any visual_id outside the
        currently-supported pair (callers add cases as new visuals
        land — keeps the dispatch table the single source of truth).
    """
    prefix = cfg.db_table_prefix
    if connection_factory is None:
        # Lazy import — connect_demo_db pulls psycopg2 / oracledb
        # which are optional extras. Tests that pass a stub
        # connection_factory must NOT trigger this import.
        from recon_gen.common.db import connect_demo_db  # noqa: PLC0415

        def _default_factory() -> Any:
            return connect_demo_db(cfg)

        connection_factory = _default_factory

    def fetcher(visual_id: str, params: Mapping[str, list[str]]) -> Any:
        if visual_id == "smoke-force":
            return _topology_to_force_graph(instance)
        if visual_id == "smoke-sankey":
            # Collapse the multi-dict to scalar last-values — this
            # fetcher only reads single-valued filters.
            scalar = {k: v[-1] for k, v in params.items() if v}
            rows = _query_money_trail_edges(connection_factory, prefix, scalar)
            return _money_trail_to_sankey(rows)
        raise ValueError(
            f"App2 DB fetcher has no case for visual_id={visual_id!r}. "
            f"Add an arm to make_db_fetcher when introducing new visuals."
        )

    return fetcher


# ---------------------------------------------------------------------------
# Shape converters (pure — no DB, fully unit-testable).
# ---------------------------------------------------------------------------


def _money_trail_to_sankey(
    rows: Sequence[tuple[str, str, float]],
) -> dict[str, Any]:
    """Aggregate Money Trail edge rows into d3-sankey shape.

    Input rows are ``(source_display, target_display, hop_amount)``
    triples — multiple rows per pair are summed. Output:

        {
          "nodes": [{"name": "..."}, ...],
          "links": [{"source": <idx>, "target": <idx>, "value": <sum>}, ...]
        }

    Node ordering is insertion order (first-seen wins) so the
    Sankey lays out left-to-right matching the source→target
    traversal. d3-sankey reorders internally for layout but the
    indices stay stable across calls with the same row set.

    Self-loops (source == target) are dropped — d3-sankey rejects
    them as invalid graph edges.
    """
    name_to_idx: dict[str, int] = {}
    aggregated: dict[tuple[int, int], float] = {}
    for source, target, amount in rows:
        if source == target:
            continue
        if source not in name_to_idx:
            name_to_idx[source] = len(name_to_idx)
        if target not in name_to_idx:
            name_to_idx[target] = len(name_to_idx)
        key = (name_to_idx[source], name_to_idx[target])
        aggregated[key] = aggregated.get(key, 0.0) + float(amount)

    nodes = [{"name": name} for name in name_to_idx]
    links = [
        {"source": s, "target": t, "value": v}
        for (s, t), v in aggregated.items()
    ]
    return {"nodes": nodes, "links": links}


def _topology_to_force_graph(instance: L2Instance) -> dict[str, Any]:
    """Project an L2 instance's accounts + rails to d3-force shape.

    Output:

        {
          "nodes": [{"id": "...", "label": "...", "group": "<scope>"}, ...],
          "links": [{"source": "<id>", "target": "<id>"}, ...]
        }

    Node ids are role identifiers (``Account.role`` when set, else
    ``Account.id``). Links come from rails:

    - ``TwoLegRail`` — one link per (source_role × destination_role)
      pair across the leg-role expressions. Multi-role expressions
      fan out (mirrors how the topology graphviz builder treats them).
    - ``SingleLegRail`` — one self-loop link per role in the leg-role
      expression. d3-force renders these as small loops; not a full
      circle but enough visual cue that the rail terminates on
      itself.

    Account templates are skipped — they're a class, not a singleton
    node. The X.4 editor will surface them as a separate visual.
    """
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for account in instance.accounts:
        node_id = str(account.role) if account.role else str(account.id)
        if node_id in seen:
            continue
        seen.add(node_id)
        nodes.append({
            "id": node_id,
            "label": str(account.name) if account.name else node_id,
            "group": str(account.scope),
        })

    links: list[dict[str, str]] = []
    for rail in instance.rails:
        if isinstance(rail, TwoLegRail):
            for source in rail.source_role:
                for target in rail.destination_role:
                    links.append({
                        "source": str(source), "target": str(target),
                    })
        elif isinstance(rail, SingleLegRail):  # pyright: ignore[reportUnnecessaryIsInstance]  # BF.1.S2: defensive; Rail is a 2-member union today but the elif documents the contract
            for role in rail.leg_role:
                links.append({"source": str(role), "target": str(role)})
    return {"nodes": nodes, "links": links}


# ---------------------------------------------------------------------------
# DB query (the dialect-portable Money Trail aggregation).
# ---------------------------------------------------------------------------


def _query_money_trail_edges(
    connection_factory: Callable[[], Any],
    prefix: str,
    params: dict[str, str],
) -> list[tuple[str, str, float]]:
    """Run the Money Trail aggregation query, return rows.

    Connection is opened + closed per call. Production deploys can
    swap the factory for a pooled connection; the spike intentionally
    keeps it simple — one short query per HTMX swap, no caching.

    SQL uses the dialect-portable subset:
    - ``CAST`` instead of ``::float``
    - ``||`` for string concat (already in source_display materialized
      in the matview)
    - Date filters as ``WHERE posted_at BETWEEN`` — both PG and Oracle
      bind a string parameter implicitly to TIMESTAMP.

    Rejects no-data gracefully: empty rowset returns ``[]``, the
    Sankey hydrator then renders an empty SVG (the d3 spike
    handles this without crashing).
    """
    where_clauses: list[str] = []
    bound: list[Any] = []
    date_from = params.get("date_from", "").strip()
    date_to = params.get("date_to", "").strip()
    if date_from:
        where_clauses.append("posted_at >= %s")
        bound.append(date_from)
    if date_to:
        where_clauses.append("posted_at <= %s")
        bound.append(date_to)
    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses) + "\n"

    sql = (
        f"SELECT source_display, target_display, "
        f"CAST(SUM(hop_amount) AS DOUBLE PRECISION) AS total_amount\n"
        f"FROM (\n"
        f"  SELECT\n"
        f"    source_account_name || ' (' || source_account_id || ')' "
        f"AS source_display,\n"
        f"    target_account_name || ' (' || target_account_id || ')' "
        f"AS target_display,\n"
        f"    hop_amount,\n"
        f"    posted_at\n"
        f"  FROM {prefix}_inv_money_trail_edges\n"
        f") edges\n"
        f"{where_sql}"
        f"GROUP BY source_display, target_display"
    )

    conn = connection_factory()
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql, bound)
            rows = cur.fetchall()
        finally:
            cur.close()
    finally:
        conn.close()

    # AO.1.impl (Studio slice) — ``hop_amount`` is the Investigation
    # matview's BIGINT cents projection of ``amount_money``. The smoke
    # Sankey renders the value as currency; convert to dollars at the
    # read boundary so ``$1,250.00`` ships through, not ``$125,000.00``
    # (100× off). Mirrors the row-side conversion in
    # ``_tree_fetcher._apply_cents_to_dollars`` — same boundary, two
    # consumers.
    return [
        (
            str(r[0]),
            str(r[1]),
            float(Cents.from_db(int(r[2])).to_dollars()) if r[2] is not None else 0.0,
        )
        for r in rows
    ]
