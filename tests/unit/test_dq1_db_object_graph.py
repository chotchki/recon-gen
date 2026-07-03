"""DQ.1 — the DbObject dependency graph is the single source the four
matview order lists derive from.

Replaces the DQ.0 interim guard (``test_dq0_overlay_order.py``): that
pinned one hand-copy (``_V_OVERLAY_MATVIEW_SUFFIXES``) against another
(``refresh_matviews_sql``). Here there is ONE source — ``SCHEMA_GRAPH`` —
and the tests prove (a) the emitted refresh / drop SQL is a projection of
it (no divergence representable), (b) the graph is topologically valid,
(c) the load-bearing dependency edges hold, and (d) an inverted /
out-of-order / duplicate declaration RAISES at construction.

No hand-listed parallel order lives here — expectations are walked from
the graph, matching the repo's "tree IS the source of truth" discipline.
"""
from __future__ import annotations

import re

import pytest

from recon_gen.common.db_objects import (
    DbObject,
    DbObjectGraph,
    DbObjectKind,
    MatviewGroup,
    SCHEMA_GRAPH,
)
from recon_gen.common.ids import DbObjectId
from recon_gen.common.l2 import L2Instance
from recon_gen.common.l2.schema import refresh_matviews_sql
from recon_gen.common.sql import Dialect

_PREFIX = "dq1test"


def _empty_instance() -> L2Instance:
    """Bare instance — the refresh name list is unconditional, so a
    content-free instance emits every matview's REFRESH."""
    return L2Instance(
        accounts=(),
        account_templates=(),
        rails=(),
        transfer_templates=(),
        chains=(),
        limit_schedules=(),
    )


def _emitted_refresh_names(dialect: Dialect) -> list[str]:
    """The prefixed matview names in the order the emitter's names-list
    drives, parsed from ``refresh_matviews_sql``.

    PG interleaves ``REFRESH MATERIALIZED VIEW`` per matview — parse
    those. DuckDB has no native matview (refresh = DROP + CTAS from the
    templates, DQ.2.2 scope); its names-list drives only the trailing
    ``ANALYZE`` block — parse those. Both statement kinds appear exactly
    once per matview, so either is a faithful read of the names order the
    graph must reproduce.
    """
    sql = refresh_matviews_sql(_empty_instance(), prefix=_PREFIX, dialect=dialect)
    if dialect is Dialect.POSTGRES:
        pattern = rf"REFRESH\s+MATERIALIZED\s+VIEW\s+(?:CONCURRENTLY\s+)?{_PREFIX}_(\w+)"
    else:  # DuckDB — the names-list drives the trailing ANALYZE block.
        pattern = rf"ANALYZE\s+{_PREFIX}_(\w+)"
    order: list[str] = []
    for m in re.finditer(pattern, sql):
        name = f"{_PREFIX}_{m.group(1)}"
        if name not in order:
            order.append(name)
    return order


# -- the graph is the single source the emitter consumes -------------------


def test_dq1_pg_refresh_order_is_the_graph() -> None:
    """The PG refresh SQL emits matviews in exactly the graph's refresh
    order — the emitter is a projection of the graph, not a parallel
    hand-list."""
    emitted = _emitted_refresh_names(Dialect.POSTGRES)
    assert emitted == list(SCHEMA_GRAPH.refresh_names(_PREFIX)), (
        "PG refresh order diverged from SCHEMA_GRAPH.refresh_names — the "
        "emitter must derive its order from the graph (DQ.1.4)."
    )


def test_dq1_duckdb_analyze_order_is_the_graph() -> None:
    """The DuckDB refresh's ANALYZE block walks the graph order. NOTE:
    this covers the ANALYZE list DQ.1 unified — NOT the CTAS re-CREATE
    body order, which is still template-authored (``_L1_INVARIANT_VIEWS_
    TEMPLATE``) until DQ.2.2 routes the CREATE emit through the graph. So
    a DuckDB-only CREATE-order break is out of this test's reach by
    design; DQ.2.2 closes it."""
    emitted = _emitted_refresh_names(Dialect.DUCKDB)
    assert emitted == list(SCHEMA_GRAPH.refresh_names(_PREFIX)), (
        "DuckDB ANALYZE order diverged from the graph — the names-list "
        "driving it must derive from SCHEMA_GRAPH."
    )


def test_dq1_overlay_suffixes_are_the_graph() -> None:
    """The snapshotter overlay-restore order is the graph's matview ids
    in refresh order."""
    from recon_gen.common.snapshotter import _V_OVERLAY_MATVIEW_SUFFIXES

    assert _V_OVERLAY_MATVIEW_SUFFIXES == tuple(SCHEMA_GRAPH.overlay_suffixes()), (
        "the Oracle overlay-restore order must be derived from SCHEMA_GRAPH, "
        "not hand-copied (the DQ.0 §1 divergence lived here)."
    )


def test_dq1_l1_and_inv_drop_orders_are_the_graph() -> None:
    """The L1 + Investigation drop blocks are reverse-dependency slices
    of the graph."""
    from recon_gen.common.l2.schema import (
        _INV_MATVIEW_DROP_NAMES,
        _L1_INVARIANT_DROP_NAMES,
    )

    assert _L1_INVARIANT_DROP_NAMES == SCHEMA_GRAPH.drop_ids(MatviewGroup.L1)
    assert _INV_MATVIEW_DROP_NAMES == SCHEMA_GRAPH.drop_ids(MatviewGroup.INVESTIGATION)


# -- the graph is topologically valid + the hard edges hold ----------------


def test_dq1_refresh_order_is_topologically_valid() -> None:
    """Every matview's declared dependencies REFRESH before it — the
    property that makes 'a matview reads stale upstream data' (DQ.0 §1)
    unrepresentable."""
    order = SCHEMA_GRAPH.refresh_order()
    position = {o.obj_id: i for i, o in enumerate(order)}
    for obj in order:
        for dep in obj.depends_on:
            if dep.kind is DbObjectKind.MATVIEW:
                assert position[dep.obj_id] < position[obj.obj_id], (
                    f"{obj.obj_id} refreshes before its dependency {dep.obj_id}"
                )


@pytest.mark.parametrize(
    ("earlier", "later"),
    [
        # The DS.3.2 spine edges — the exact ordering the overlay copy
        # got wrong (DQ.0 §1).
        ("effective_balances", "computed_subledger_balance"),
        ("effective_balances", "computed_ledger_balance"),
        ("computed_subledger_balance", "drift"),
        ("computed_ledger_balance", "ledger_drift"),
        # DL.3.5 — drift_summary is UNION ALL of its two parents.
        ("drift", "drift_summary"),
        ("ledger_drift", "drift_summary"),
        # AB.4.7 — fan_in JOINs transfer_parents.
        ("transfer_parents", "fan_in_disagreement"),
        # The 12-way union sink is last of the L1 detectors.
        ("drift", "l1_exceptions"),
        ("multi_xor_violation", "l1_exceptions"),
    ],
)
def test_dq1_hard_dependency_edges_hold(earlier: str, later: str) -> None:
    order = [o.obj_id for o in SCHEMA_GRAPH.refresh_order()]
    assert order.index(DbObjectId(earlier)) < order.index(DbObjectId(later))


# -- construction-time validation: wrong graphs RAISE ----------------------


def test_dq1_inverted_dependency_raises() -> None:
    """A matview declared BEFORE something it reads from is the DQ.0 §1
    footgun — it must raise at construction, not silently emit a
    stale-refresh order."""
    parent = DbObject(DbObjectId("parent"), DbObjectKind.MATVIEW)
    child = DbObject(DbObjectId("child"), DbObjectKind.MATVIEW, depends_on=(parent,))
    with pytest.raises(ValueError, match="not dependency-respecting"):
        DbObjectGraph(objects=(child, parent))  # child declared first — inverted


def test_dq1_duplicate_id_raises() -> None:
    a = DbObject(DbObjectId("dup"), DbObjectKind.TABLE)
    b = DbObject(DbObjectId("dup"), DbObjectKind.MATVIEW)
    with pytest.raises(ValueError, match="duplicate DbObject id"):
        DbObjectGraph(objects=(a, b))


def test_dq1_stray_dependency_ref_raises() -> None:
    """A node depending on an object not registered in THIS graph (a
    stray ref from a different graph build) raises."""
    orphan = DbObject(DbObjectId("orphan"), DbObjectKind.TABLE)
    dependent = DbObject(
        DbObjectId("dependent"), DbObjectKind.MATVIEW, depends_on=(orphan,)
    )
    with pytest.raises(ValueError, match="not registered in this graph"):
        DbObjectGraph(objects=(dependent,))  # orphan not included
