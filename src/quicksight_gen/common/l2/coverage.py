"""``coverage_for()`` — per-L2-primitive presence in ``<prefix>_transactions``.

The Studio diagram's coverage-tint chrome (X.4.c.5) needs a per-entity
"do you have data" answer keyed off the same node IDs the topology
emit produces. This module is the data fetcher: three GROUP BY queries
on ``<prefix>_transactions`` plus a derived chain-edge map, all keyed
to the topology IDs ``role__X`` / ``rail__X`` / ``tmpl__X`` /
``chain__<parent>__<child>``.

Why a separate module:

- The query shape is the same across all four apps (it doesn't ride on
  any one app's dataset SQL); coupling it to either ``apps/`` or
  ``common/html/`` would force one consumer to import the other.
- Studio's diagram chrome (``common/html/_studio_routes.py``) is the
  initial consumer, but the same fetcher is what the eventual
  ETL-engineer ``uncovered_rails`` scope (X.4.h) will reuse to decide
  what to fill — keeping it in ``common/l2/`` matches that future
  symmetry.

Severability: this module imports only ``common/db.py`` (async pool
protocol), ``common/l2/primitives.py`` (the L2 model), and
``common/l2/topology.py`` (the node-id helpers). It does NOT import
``common/html/`` — the coverage data flows the other way.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from quicksight_gen.common.db import AsyncConnectionPool
from quicksight_gen.common.l2.primitives import Identifier, L2Instance
from quicksight_gen.common.l2.topology import (
    _rail_id,
    _role_id,
    _template_id,
)
from quicksight_gen.common.sql.dialect import Dialect, column_name


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    """One L2 primitive's data presence + row count.

    ``count`` is the absolute row count behind ``present`` so the
    chrome's hover tooltip can say "12,304 rows" instead of just "yes".
    """

    present: bool
    count: int


@dataclass(frozen=True, slots=True)
class CoverageMap:
    """Per-topology-element coverage from a single demo-DB snapshot.

    Two maps:

    - ``by_node_id`` — keyed by the topology node IDs that
      ``topology.py``'s ``_role_id`` / ``_rail_id`` / ``_template_id``
      helpers emit. One entry per L2-declared role / rail / template —
      even an empty entity gets a ``present=False, count=0`` record so
      the chrome can render absence as well as presence.
    - ``by_chain_edge_id`` — keyed by ``chain__<parent>__<child>``
      (the discriminator scheme matches the node-id prefix convention).
      A chain edge is "covered" when both endpoints (rail or template)
      have at least one transaction. Derived; no extra DB query.
    """

    by_node_id: Mapping[str, CoverageEntry]
    by_chain_edge_id: Mapping[str, CoverageEntry]


def chain_edge_id(parent: str, child: str) -> str:
    """Stable ID for a chain edge in the topology coverage map.

    Matches the ``role__`` / ``rail__`` / ``tmpl__`` discriminator scheme
    so the SVG post-processor can key off the prefix in the chrome's
    coverage paint.
    """
    return f"chain__{parent}__{child}"


async def coverage_for(
    pool: AsyncConnectionPool,
    prefix: str,
    instance: L2Instance,
    *,
    dialect: Dialect,
) -> CoverageMap:
    """Compute per-L2-primitive row presence in ``<prefix>_transactions``.

    Three GROUP BY queries hit the DB once per call:

    - ``account_role`` → role coverage
    - ``rail_name`` → rail coverage
    - ``template_name`` → template coverage (NULL filtered, since most
      transfers are standalone-rail and carry NULL ``template_name``)

    Chain edge coverage is derived: an edge is "covered" iff both
    parent + child names appear in either rail or template counts.

    Iteration is over the L2-declared primitives (not the DB result set)
    so an L2-declared-but-unfed entity returns ``present=False, count=0``
    instead of being absent from the map entirely. The chrome surface
    needs to render absence too — that's the integrator's "is my ETL
    hooked up" signal.

    Computed on-demand (no caching). Studio's audience is one user
    iterating, not a hot path; a 50-rail L2 against the demo DB
    returns in single-digit ms.

    Args:
      pool: AsyncConnectionPool against the demo DB.
      prefix: L2 instance prefix (e.g. ``"sasquatch_pr"``); used as the
        ``<prefix>_transactions`` schema prefix.
      instance: The L2 model whose declared primitives drive the
        iteration set (so absence = ``present=False, count=0``).
      dialect: SQL dialect; drives column-name case-folding via
        ``column_name(...)``.

    Returns:
      A ``CoverageMap`` with ``by_node_id`` covering every declared
      role / rail / template + ``by_chain_edge_id`` covering every
      declared chain entry.
    """
    txns = f"{prefix}_transactions"
    role_col = column_name("account_role", dialect)
    rail_col = column_name("rail_name", dialect)
    tmpl_col = column_name("template_name", dialect)

    role_counts = await _fetch_count_map(
        pool,
        f"SELECT {role_col}, COUNT(*) FROM {txns} GROUP BY {role_col}",
        dialect,
    )
    rail_counts = await _fetch_count_map(
        pool,
        f"SELECT {rail_col}, COUNT(*) FROM {txns} GROUP BY {rail_col}",
        dialect,
    )
    tmpl_counts = await _fetch_count_map(
        pool,
        f"SELECT {tmpl_col}, COUNT(*) FROM {txns} "
        f"WHERE {tmpl_col} IS NOT NULL GROUP BY {tmpl_col}",
        dialect,
    )

    by_node_id: dict[str, CoverageEntry] = {}

    declared_roles: set[Identifier] = set()
    for acct in instance.accounts:
        if acct.role is not None:
            declared_roles.add(acct.role)
    for tmpl in instance.account_templates:
        declared_roles.add(tmpl.role)
    for role in declared_roles:
        n = role_counts.get(str(role), 0)
        by_node_id[_role_id(role)] = CoverageEntry(present=n > 0, count=n)

    for rail in instance.rails:
        n = rail_counts.get(str(rail.name), 0)
        by_node_id[_rail_id(rail.name)] = CoverageEntry(present=n > 0, count=n)

    for tt in instance.transfer_templates:
        n = tmpl_counts.get(str(tt.name), 0)
        by_node_id[_template_id(tt.name)] = CoverageEntry(present=n > 0, count=n)

    by_chain_edge_id: dict[str, CoverageEntry] = {}
    for ch in instance.chains:
        parent_count = (
            rail_counts.get(str(ch.parent), 0)
            + tmpl_counts.get(str(ch.parent), 0)
        )
        child_count = (
            rail_counts.get(str(ch.child), 0)
            + tmpl_counts.get(str(ch.child), 0)
        )
        present = parent_count > 0 and child_count > 0
        # Upper bound on actual chain firings — one parent txn can host
        # at most one matching child slot, so min(parent, child) is the
        # tightest count we can report without joining transfer_parent_id.
        # Approximate but informative on hover.
        count = min(parent_count, child_count) if present else 0
        by_chain_edge_id[chain_edge_id(str(ch.parent), str(ch.child))] = (
            CoverageEntry(present=present, count=count)
        )

    return CoverageMap(by_node_id=by_node_id, by_chain_edge_id=by_chain_edge_id)


async def _fetch_count_map(
    pool: AsyncConnectionPool, query: str, dialect: Dialect,
) -> dict[str, int]:
    """Run a ``SELECT key, COUNT(*) ...`` query, return ``{key: count}``.

    Mirrors the cursor-handling shape from
    ``common/html/_sql_executor.py`` — Oracle needs an explicit
    ``cursor()`` step, while psycopg / aiosqlite return cursors
    directly from ``await conn.execute(...)``.
    """
    async with pool.acquire() as conn:
        if dialect is Dialect.ORACLE:
            cur: Any = cast(Any, conn).cursor()  # typing-smell: ignore[explicit-any]: per-driver cursor union has no shared Protocol
            await cur.execute(query)
        else:
            cur = cast(Any, await conn.execute(query))  # typing-smell: ignore[explicit-any]: psycopg / aiosqlite cursor types not unified by a single Protocol
        try:
            rows: list[Any] = await cur.fetchall()  # typing-smell: ignore[explicit-any]: driver-typed row union widens to Any after Any cursor
        finally:
            close = getattr(cur, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
    return {str(row[0]): int(row[1]) for row in rows if row[0] is not None}
