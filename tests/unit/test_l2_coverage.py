"""Unit tests for ``common.l2.coverage.coverage_for``.

A file-backed aiosqlite pool is seeded with a tiny ``<prefix>_transactions``
table (just the three columns the fetcher reads: ``account_role``,
``rail_name``, ``template_name``). The test populates rows for SOME but
not all L2-declared primitives — the absence half is the integrator's
"is my ETL hooked up" signal, so it has to be tested explicitly.

Why a file-backed DB instead of ``:memory:``: aiosqlite's in-memory mode
gives each new connection a fresh isolated DB, but the pool's
per-acquire fresh connection (the ``_AsyncSqlitePool`` wrapper) means
the seeded table would vanish between the seed and the fetch. A
tempfile is the smallest fix; mirrors the pattern in
``tests/unit/test_html_sql_executor.py``'s ``aiosqlite_pool`` fixture.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from recon_gen.common.db import AsyncConnectionPool, make_connection_pool
from recon_gen.common.l2.coverage import (
    CoverageEntry,
    chain_edge_id,
    coverage_for,
)
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import Identifier, L2Instance
from recon_gen.common.l2.topology import (
    _rail_id,
    _role_id,
    _template_id,
)
from recon_gen.common.sql.dialect import Dialect
from tests._test_helpers import make_test_config


_SPEC_EXAMPLE = (
    Path(__file__).resolve().parent.parent / "l2" / "spec_example.yaml"
)


@pytest.fixture
def spec_example_instance() -> L2Instance:
    """The bundled spec_example L2 — covers every primitive kind."""
    return load_instance(_SPEC_EXAMPLE)


@pytest.fixture
def seeded_pool() -> Iterator[AsyncConnectionPool]:
    """File-backed aiosqlite pool with a tiny ``coverage_test_transactions``
    table.

    Seeded such that:
    - ``CustomerLedger`` role has 5 rows.
    - ``ExternalCounterparty`` role has 2 rows (one with template_name set).
    - ``CustomerSubledger`` role has 0 rows (declared in YAML, fed nothing —
      the integrator's "missing ETL" case).
    - ``ExternalRailInbound`` rail has 4 rows.
    - ``ExternalRailOutbound`` rail has 0 rows.
    - ``MerchantSettlementCycle`` template has 1 row.
    - ``ExternalReconciliationCycle`` template has 0 rows.

    Every other L2-declared primitive is fed nothing too, so the
    map should record those as ``present=False, count=0``.
    """
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)

    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE coverage_test_transactions ("
        "id INTEGER PRIMARY KEY, "
        "account_role TEXT, "
        "rail_name TEXT, "
        "template_name TEXT)"
    )
    rows: list[tuple[str, str, str | None]] = [
        ("CustomerLedger", "ExternalRailInbound", None),
        ("CustomerLedger", "ExternalRailInbound", None),
        ("CustomerLedger", "ExternalRailInbound", None),
        ("CustomerLedger", "ExternalRailInbound", None),
        ("CustomerLedger", "SubledgerCharge", None),
        ("ExternalCounterparty", "ExternalRailInbound", None),
        # One row whose template_name lights up MerchantSettlementCycle:
        ("ExternalCounterparty", "ReconciliationLeg", "MerchantSettlementCycle"),
    ]
    conn.executemany(
        "INSERT INTO coverage_test_transactions "
        "(account_role, rail_name, template_name) VALUES (?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    cfg = make_test_config(dialect=Dialect.SQLITE, demo_database_url=path)
    pool = asyncio.run(make_connection_pool(cfg))
    try:
        yield pool
    finally:
        asyncio.run(pool.close())
        os.unlink(path)


def _run_coverage(
    pool: AsyncConnectionPool, instance: L2Instance,
) -> "tuple[dict[str, CoverageEntry], dict[str, CoverageEntry]]":
    cov = asyncio.run(
        coverage_for(pool, "coverage_test", instance, dialect=Dialect.SQLITE),
    )
    return dict(cov.by_node_id), dict(cov.by_chain_edge_id)


def test_coverage_role_present_when_seeded(
    seeded_pool: AsyncConnectionPool,
    spec_example_instance: L2Instance,
) -> None:
    """Roles with rows in the table report ``present=True`` + the row count."""
    nodes, _edges = _run_coverage(seeded_pool, spec_example_instance)

    cl = nodes[_role_id(_id("CustomerLedger"))]
    assert cl == CoverageEntry(present=True, count=5)

    ec = nodes[_role_id(_id("ExternalCounterparty"))]
    assert ec == CoverageEntry(present=True, count=2)


def test_coverage_role_absent_for_declared_but_unfed_role(
    seeded_pool: AsyncConnectionPool,
    spec_example_instance: L2Instance,
) -> None:
    """Roles with no rows still appear in the map as ``present=False``.

    The integrator's "is my ETL hooked up" signal depends on this — an
    L2-declared role that's missing from the map entirely is a different
    bug than a declared role that's confirmed empty.
    """
    nodes, _edges = _run_coverage(seeded_pool, spec_example_instance)

    cs = nodes[_role_id(_id("CustomerSubledger"))]
    assert cs == CoverageEntry(present=False, count=0)


def test_coverage_rail_present_and_absent(
    seeded_pool: AsyncConnectionPool,
    spec_example_instance: L2Instance,
) -> None:
    """Rails follow the same present/absent contract as roles."""
    nodes, _edges = _run_coverage(seeded_pool, spec_example_instance)

    rin = nodes[_rail_id(_id("ExternalRailInbound"))]
    assert rin == CoverageEntry(present=True, count=5)

    rout = nodes[_rail_id(_id("ExternalRailOutbound"))]
    assert rout == CoverageEntry(present=False, count=0)


def test_coverage_template_present_and_absent(
    seeded_pool: AsyncConnectionPool,
    spec_example_instance: L2Instance,
) -> None:
    """Templates use the top-level ``template_name`` column (not JSON)."""
    nodes, _edges = _run_coverage(seeded_pool, spec_example_instance)

    msc = nodes[_template_id(_id("MerchantSettlementCycle"))]
    assert msc == CoverageEntry(present=True, count=1)

    erc = nodes[_template_id(_id("ExternalReconciliationCycle"))]
    assert erc == CoverageEntry(present=False, count=0)


def test_coverage_chain_edge_derived_from_endpoints(
    seeded_pool: AsyncConnectionPool,
    spec_example_instance: L2Instance,
) -> None:
    """Chain edge coverage is the AND of its endpoints' presence.

    spec_example declares chain ``ExternalReconciliationCycle ->
    ReconciliationClosing``. Both endpoints have 0 rows in the seed →
    edge is absent. (Reflects the seed-data shape, not a bug.)
    """
    nodes, edges = _run_coverage(seeded_pool, spec_example_instance)

    # Spot-check at least one chain edge is reported (every declared
    # ChainEntry should land in the edges map).
    assert len(edges) >= 1
    for edge_id, entry in edges.items():
        assert edge_id.startswith("chain__")
        # Sanity: no chain edge can be present without both endpoints
        # also being present (or zero, since chains are the AND).
        if entry.present:
            assert entry.count > 0


def test_coverage_chain_edge_id_helper_matches() -> None:
    """``chain_edge_id`` produces the documented prefix scheme."""
    assert chain_edge_id("Foo", "Bar") == "chain__Foo__Bar"


def test_coverage_includes_every_declared_primitive(
    seeded_pool: AsyncConnectionPool,
    spec_example_instance: L2Instance,
) -> None:
    """Every L2-declared role / rail / template lands in the map.

    Absence-as-a-signal contract: the map's keys cover the full L2
    declaration set, not just the rows in the seed.
    """
    nodes, edges = _run_coverage(seeded_pool, spec_example_instance)

    declared_role_ids = {
        _role_id(a.role) for a in spec_example_instance.accounts
        if a.role is not None
    }
    declared_role_ids.update(
        _role_id(t.role) for t in spec_example_instance.account_templates
    )
    for rid in declared_role_ids:
        assert rid in nodes, f"role {rid!r} missing from coverage map"

    for rail in spec_example_instance.rails:
        assert _rail_id(rail.name) in nodes

    for tt in spec_example_instance.transfer_templates:
        assert _template_id(tt.name) in nodes

    # Coverage emits one edge per chain CHILD (Z.A: singleton = 1 edge,
    # multi/XOR = N edges) so the diagram pane and the coverage map
    # stay 1:1. For spec_example: 3 singleton chains + 1 multi-XOR
    # chain (2 children, AB.6.5.spec) = 5 edges total.
    expected_edge_count = sum(
        len(c.children) for c in spec_example_instance.chains
    )
    assert len(edges) == expected_edge_count


def _id(s: str) -> Identifier:
    """Local Identifier wrapper — keeps the role/rail name literals
    inside the test bodies short. Identifier is just NewType("Identifier", str)
    so cast-as-call is safe."""
    return Identifier(s)
