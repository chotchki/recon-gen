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

from recon_gen.common.db import AsyncConnectionPool
from recon_gen.common.l2.primitives import Identifier, L2Instance
from recon_gen.common.l2.topology import (
    _rail_id,
    _role_id,
    _template_id,
)
from recon_gen.common.sql.dialect import Dialect, column_name


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
        # Z.A: emit one coverage entry per child in the row. The
        # diagram chrome paints one chain edge per child (singleton =
        # 1 edge, multi/XOR = N edges), so the coverage map needs to
        # match 1:1.
        for child_spec in ch.children:
            child_name = child_spec.name
            child_count = (
                rail_counts.get(str(child_name), 0)
                + tmpl_counts.get(str(child_name), 0)
            )
            present = parent_count > 0 and child_count > 0
            # Upper bound on actual chain firings — one parent txn can
            # host at most one matching child slot, so min(parent,
            # child) is the tightest count we can report without
            # joining transfer_parent_id. Approximate but informative
            # on hover.
            count = min(parent_count, child_count) if present else 0
            by_chain_edge_id[chain_edge_id(str(ch.parent), str(child_name))] = (
                CoverageEntry(present=present, count=count)
            )

    return CoverageMap(by_node_id=by_node_id, by_chain_edge_id=by_chain_edge_id)


@dataclass(frozen=True, slots=True)
class TemplateMetadataCoverage:
    """Per-template required-metadata-key landing tally.

    For one TransferTemplate, ``row_count`` is the total rows tagged
    with that template_name; ``per_key`` maps each required key (from
    ``TransferTemplate.transfer_key`` + each leg-rail's ``metadata_keys``)
    to the count of rows whose metadata JSON contains the key. BT.3's
    coverage card reads ``per_key[k] == row_count`` to paint
    ✓-all-rows-have-it, ``per_key[k] == 0`` for ✗-missing, partial
    counts for the "12/14 rows have it" middle.
    """

    template_name: str
    row_count: int
    per_key: Mapping[str, int]


async def metadata_coverage_per_template(
    pool: AsyncConnectionPool,
    prefix: str,
    instance: L2Instance,
    *,
    dialect: Dialect,
) -> Mapping[str, TemplateMetadataCoverage]:
    """For each TransferTemplate, count rows landing its required
    metadata keys.

    The required-keys set unions the template's own ``transfer_key``
    plus each leg-rail's ``metadata_keys``. Counts use the same
    JSON-text quoted-key heuristic ``probe.evaluate_predicate`` uses
    (``"key"`` substring scan) — robust enough for the BT.3 coverage
    card; BT.4's Triage will do proper JSON_VALUE / JSONPath
    extraction when the gap shape needs it.

    Iteration is over the L2's declared templates so an empty-data
    instance still gets a complete result (every template returns a
    ``row_count=0`` entry, ``per_key`` empty for each required key).

    One query per template (per-template + per-key count is folded
    into a single SELECT with N conditional SUMs); studio's audience
    is one user iterating, so the per-call cost is negligible.
    """
    out: dict[str, TemplateMetadataCoverage] = {}
    txns = f"{prefix}_transactions"
    tmpl_col = column_name("template_name", dialect)
    md_col = column_name("metadata", dialect)

    rail_metadata_keys: dict[str, tuple[str, ...]] = {
        str(r.name): tuple(str(k) for k in r.metadata_keys)
        for r in instance.rails
    }

    for template in instance.transfer_templates:
        required_keys: list[str] = list(str(k) for k in template.transfer_key)
        for leg in template.leg_rails:
            required_keys.extend(rail_metadata_keys.get(str(leg), ()))
        # Dedupe + stable order — same key may appear via multiple paths.
        seen: set[str] = set()
        unique_keys: list[str] = []
        for k in required_keys:
            if k not in seen:
                seen.add(k)
                unique_keys.append(k)

        if not unique_keys:
            # Template with no required-metadata expectations — still
            # report row_count so the card can show "no required keys."
            count_sql = (
                f"SELECT COUNT(*) FROM {txns} "
                f"WHERE {tmpl_col} = "
                + _string_literal(str(template.name), dialect)
            )
            count_rows = await _fetch_rows(pool, count_sql, dialect)
            row_count = int(count_rows[0][0]) if count_rows else 0
            out[str(template.name)] = TemplateMetadataCoverage(
                template_name=str(template.name),
                row_count=row_count,
                per_key={},
            )
            continue

        # One query: COUNT(*) + a conditional SUM per required key
        # (1 when the key appears in the metadata JSON, 0 otherwise).
        sum_clauses = ", ".join(
            f"SUM(CASE WHEN {md_col} LIKE "
            + _string_literal(f'%"{k}"%', dialect)
            + " THEN 1 ELSE 0 END) AS k{i}"
            .format(i=i)
            for i, k in enumerate(unique_keys)
        )
        sql = (
            f"SELECT COUNT(*), {sum_clauses} FROM {txns} "
            f"WHERE {tmpl_col} = "
            + _string_literal(str(template.name), dialect)
        )
        rows = await _fetch_rows(pool, sql, dialect)
        if not rows:
            row_count, per_key = 0, {k: 0 for k in unique_keys}
        else:
            row = rows[0]
            row_count = int(row[0])
            per_key = {
                k: int(row[i + 1] or 0) for i, k in enumerate(unique_keys)
            }
        out[str(template.name)] = TemplateMetadataCoverage(
            template_name=str(template.name),
            row_count=row_count,
            per_key=per_key,
        )
    return out


def _string_literal(value: str, dialect: Dialect) -> str:
    """SQL-quote a string literal with single-quote escaping.

    The metadata-coverage helper builds SQL strings (LIKE patterns +
    equality match) by inlining template names + key names. They come
    from the L2-declared identifiers (constrained to
    ``[A-Za-z_][A-Za-z0-9_]*`` per SPEC), so injection isn't an
    attack surface — but escape anyway so a future YAML loader
    relaxation can't sneak a `'` through.
    """
    del dialect  # PG / Oracle / SQLite all use single-quote string literals
    return "'" + value.replace("'", "''") + "'"


async def _fetch_rows(
    pool: AsyncConnectionPool, query: str, dialect: Dialect,
) -> list[tuple[Any, ...]]:  # typing-smell: ignore[explicit-any]: row tuples are heterogeneous; per-call shape lives in the SELECT contract
    """Driver-uniform execute + fetchall. Sibling of ``_fetch_count_map``
    that returns raw row tuples instead of a single-cell coercion."""
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
    return [tuple(r) for r in rows]


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
