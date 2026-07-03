"""DQ.1 — the typed database-object dependency graph.

One ``DbObject`` per schema object (base table / config view / matview).
Each node OWNS its two facts: what it READS FROM (``depends_on``, as
object refs) and what COLUMNS it emits (``columns`` — populated in DQ.4).
The four hand-kept order lists (``schema.refresh_matviews_sql`` names,
the DuckDB refresh names, ``_L1_INVARIANT_DROP_NAMES`` +
``_INV_MATVIEW_DROP_NAMES``, and ``snapshotter._V_OVERLAY_MATVIEW_SUFFIXES``)
were four INDEPENDENT serializations of this one graph that could silently
diverge — DQ.0 §1 caught exactly that (the DS.3.2 ``effective_balances``
mis-order in the overlay copy, Oracle-restore-only, untested). Here the
graph is the single source and each of those FOUR lists DERIVES from it,
so that divergence class stops being representable. (The DuckDB CTAS
re-CREATE *body* order is still template-authored — DQ.2.2 folds the
CREATE emit onto this same graph; DQ.1 covers the refresh / drop /
ANALYZE / overlay ORDER lists.)

``depends_on`` holds DbObject INSTANCES, not names — so a cycle is
unconstructable (a frozen node cannot reference one that does not exist
yet) and a missing dependency is a ``NameError`` at authoring time. The
graph is a DAG BY CONSTRUCTION; the helpers here only linearize the
declaration order and validate it is dependency-respecting.

The graph STRUCTURE is identical for every L2 instance (the 24 matviews
are persona-blind L1 invariants + Investigation); only the physical
``<cfg.db.table_prefix>_`` envelope varies, applied at name-emission time
via ``DbObject.physical_name``. So there is ONE canonical graph
(``schema_graph()``), not one per instance.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from recon_gen.common.dataset_contract import ColumnSpec
from recon_gen.common.ids import DbObjectId, MatviewName, prefixed_db_object


class DbObjectKind(Enum):
    """What kind of schema object a node is — governs which emit + order
    lists it participates in."""

    #: Root base table (customer-ETL / seed target). No refresh; dropped
    #: in the base-table block, not the matview block.
    TABLE = "table"
    #: Live (non-materialized) view over ``config_kv`` or the Current*
    #: matviews. No refresh — always current.
    VIEW = "view"
    #: Materialized. Refreshed / dropped / ANALYZE-d in dependency order —
    #: the nodes the four order lists are built from.
    MATVIEW = "matview"


class MatviewGroup(Enum):
    """Which drop-block a matview belongs to — the emitter drops matviews
    in three separate blocks (Current* / L1-invariant / Investigation),
    each a reverse-dependency slice of the one graph. A per-object FACT
    (like ``kind``), declared once; the ORDER within a group derives from
    the topo, never hand-listed. ``None`` for non-matview nodes."""

    CURRENT = "current"
    L1 = "l1"
    INVESTIGATION = "investigation"


@dataclass(frozen=True)
class DbObject:
    """One schema object owning its dependencies + emitted columns.

    Frozen + hashable on ``(obj_id, kind)`` (``depends_on`` / ``columns``
    / ``group`` are excluded from eq/hash so the recursive / mutable
    fields don't fight hashability; ``obj_id`` is unique in a valid graph
    so the key degenerates to ``obj_id``), so a node can key a set during
    the graph walk, mirroring the tree ``Dataset`` node one layer up.

    ``columns`` is empty for DQ.1 (deps-only); DQ.4 populates it and the
    ``__getitem__`` KeyError-at-the-wiring-site check goes fully live.
    """

    obj_id: DbObjectId
    kind: DbObjectKind
    depends_on: tuple["DbObject", ...] = field(default=(), compare=False, hash=False)
    columns: tuple[ColumnSpec, ...] = field(default=(), compare=False, hash=False)
    #: Only meaningful for MATVIEW nodes; which drop block they belong to.
    group: MatviewGroup | None = field(default=None, compare=False, hash=False)

    def physical_name(self, prefix: str) -> MatviewName:
        """The prefixed, dialect-facing name — ``<prefix>_<obj_id>``."""
        return prefixed_db_object(prefix, self.obj_id)

    def __getitem__(self, name: str) -> ColumnSpec:
        """Typed column ref — ``KeyError`` at the wiring site on a typo.

        DQ.4 populates ``columns``; until then an empty column set means
        the node can't resolve any ref (mirrors ``Dataset.__getitem__``
        skipping validation when no contract is registered, but louder —
        a DB object with declared columns is the DQ.4 end-state).
        """
        if not self.columns:
            raise KeyError(
                f"DB object {self.obj_id!r} has no declared columns yet "
                f"(DQ.4 populates them); cannot resolve column {name!r}."
            )
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(
            f"Column {name!r} not declared on DB object {self.obj_id!r}. "
            f"Known: {[c.name for c in self.columns]}"
        )


@dataclass(frozen=True)
class DbObjectGraph:
    """The full object graph, declared in dependency (== canonical
    refresh) order. The four order lists derive from this one source:
    refresh = matviews in declaration order; drop = reverse, sliced by
    ``MatviewGroup``; overlay-suffix = refresh.
    """

    objects: tuple[DbObject, ...]

    def __post_init__(self) -> None:
        ids: set[str] = set()
        for obj in self.objects:
            if obj.obj_id in ids:
                raise ValueError(f"duplicate DbObject id {obj.obj_id!r}")
            ids.add(obj.obj_id)
        # Declaration order MUST be dependency-respecting: every node's
        # deps appear BEFORE it. This is the "inverted dep raises at
        # construction" invariant — a matview placed before one it reads
        # FROM is the DQ.0 §1 footgun, now loud at import time.
        emitted: set[str] = set()
        for obj in self.objects:
            for dep in obj.depends_on:
                if dep.obj_id not in ids:
                    raise ValueError(
                        f"DB object {obj.obj_id!r} depends on {dep.obj_id!r}, "
                        f"which is not registered in this graph (a stray "
                        f"object ref from a different graph instance)."
                    )
                if dep.obj_id not in emitted:
                    raise ValueError(
                        f"DB object {obj.obj_id!r} is declared before its "
                        f"dependency {dep.obj_id!r} — the declaration order "
                        f"is not dependency-respecting (an inverted / "
                        f"out-of-order edge). Reorder so every object "
                        f"follows what it reads FROM."
                    )
            emitted.add(obj.obj_id)

    # -- lookups --------------------------------------------------------

    def by_id(self, obj_id: DbObjectId) -> DbObject:
        for obj in self.objects:
            if obj.obj_id == obj_id:
                return obj
        raise KeyError(f"no DB object {obj_id!r} in the graph")

    def matviews(self) -> tuple[DbObject, ...]:
        """Matview nodes in dependency (refresh) order."""
        return tuple(o for o in self.objects if o.kind is DbObjectKind.MATVIEW)

    # -- derived order lists (the four hand copies, single-sourced) -----

    def refresh_order(self) -> tuple[DbObject, ...]:
        """The canonical matview refresh order — the single source the
        PG/Oracle REFRESH, the DuckDB ANALYZE block, and the overlay
        restore all derive from. (The DuckDB CTAS re-CREATE body order is
        template-authored until DQ.2.2 routes it through this graph.)"""
        return self.matviews()

    def refresh_names(self, prefix: str) -> list[MatviewName]:
        """Prefixed matview names in refresh order (for
        ``refresh_matviews_sql`` / the DuckDB refresh)."""
        return [o.physical_name(prefix) for o in self.refresh_order()]

    def overlay_suffixes(self) -> tuple[DbObjectId, ...]:
        """Unprefixed matview ids in refresh order — the snapshotter
        overlay-restore order (``_V_OVERLAY_MATVIEW_SUFFIXES``)."""
        return tuple(o.obj_id for o in self.refresh_order())

    def drop_order(self, group: MatviewGroup) -> tuple[DbObject, ...]:
        """Matviews of one group in REVERSE dependency order (drop
        dependents before the dependencies they read FROM). A leaf with
        no in-group dependency floats to a valid but arbitrary slot — the
        drop is idempotent + order-immaterial for such a node."""
        return tuple(
            o for o in reversed(self.matviews()) if o.group is group
        )

    def drop_ids(self, group: MatviewGroup) -> tuple[DbObjectId, ...]:
        """Unprefixed ids for a group's drop block."""
        return tuple(o.obj_id for o in self.drop_order(group))


# ---------------------------------------------------------------------------
# The canonical graph. Declared in dependency (== refresh) order so the
# declaration reads top-to-bottom as the refresh order AND the
# ``DbObjectGraph`` validator proves that order is dependency-respecting.
# Edges come straight from the SELECT-body reads documented in
# ``schema.py``'s refresh-list + DDL comments (DQ.0 §2 audited them).
# ---------------------------------------------------------------------------


def _obj(
    obj_id: str,
    kind: DbObjectKind,
    *deps: DbObject,
    group: MatviewGroup | None = None,
) -> DbObject:
    return DbObject(
        obj_id=DbObjectId(obj_id),
        kind=kind,
        depends_on=tuple(deps),
        group=group,
    )


def _build_schema_graph() -> DbObjectGraph:
    T, V, M = DbObjectKind.TABLE, DbObjectKind.VIEW, DbObjectKind.MATVIEW
    CUR, L1, INV = MatviewGroup.CURRENT, MatviewGroup.L1, MatviewGroup.INVESTIGATION

    # Roots — customer-ETL / seed targets.
    transactions = _obj("transactions", T)
    daily_balances = _obj("daily_balances", T)
    config_kv = _obj("config_kv", T)

    # Config typed views — the second dependency ARM off config_kv
    # (DQ.0 §2: trace only transactions/balances and you miss half the
    # graph). Live views, never refreshed.
    v_config_rails = _obj("v_config_rails", V, config_kv)
    v_config_limit_schedules = _obj("v_config_limit_schedules", V, config_kv)
    v_config_chain_children = _obj("v_config_chain_children", V, config_kv)
    v_config_transfer_templates = _obj("v_config_transfer_templates", V, config_kv)
    v_config_account_roles = _obj("v_config_account_roles", V, config_kv)
    v_config_rail_metadata_keys = _obj("v_config_rail_metadata_keys", V, config_kv)

    # Current* supersession matviews (highest-``entry``-per-key wins).
    current_transactions = _obj("current_transactions", M, transactions, group=CUR)
    current_daily_balances = _obj("current_daily_balances", M, daily_balances, group=CUR)

    # CL.5 carry-forward spine — the root of the drift/overdraft chain.
    # Reads current_daily_balances ONLY (a pure balance carry-forward; the
    # transactions-side signal enters the chain one layer down, in the
    # computed_* helpers — verified against schema.py:2720-2905).
    effective_balances = _obj(
        "effective_balances", M, current_daily_balances, group=L1
    )
    # DS.3.2 re-keyed the computed_* onto effective_balances.
    computed_subledger_balance = _obj(
        "computed_subledger_balance", M, effective_balances, current_transactions, group=L1
    )
    computed_ledger_balance = _obj(
        "computed_ledger_balance", M,
        effective_balances, current_transactions, current_daily_balances, group=L1,
    )
    # DK.1 data_anchor — leaf (reads only from current_*).
    data_anchor = _obj(
        "data_anchor", M, current_transactions, current_daily_balances, group=L1
    )
    drift = _obj("drift", M, effective_balances, computed_subledger_balance, group=L1)
    ledger_drift = _obj("ledger_drift", M, effective_balances, computed_ledger_balance, group=L1)
    drift_summary = _obj("drift_summary", M, drift, ledger_drift, group=L1)
    overdraft = _obj("overdraft", M, effective_balances, group=L1)
    expected_eod_balance_breach = _obj(
        "expected_eod_balance_breach", M, current_daily_balances, group=L1
    )
    balance_cadence_gap = _obj(
        "balance_cadence_gap", M, current_daily_balances, current_transactions, group=L1
    )
    limit_breach = _obj(
        "limit_breach", M, current_transactions, v_config_limit_schedules, group=L1
    )
    stuck_pending = _obj("stuck_pending", M, current_transactions, v_config_rails, group=L1)
    stuck_unbundled = _obj("stuck_unbundled", M, current_transactions, v_config_rails, group=L1)
    chain_parent_disagreement = _obj(
        "chain_parent_disagreement", M, current_transactions, group=L1
    )
    xor_group_violation = _obj("xor_group_violation", M, current_transactions, group=L1)
    transfer_parents = _obj("transfer_parents", M, current_transactions, group=L1)
    # AB.4.7 — JOINs against transfer_parents; MUST follow it.
    fan_in_disagreement = _obj(
        "fan_in_disagreement", M,
        current_transactions, transfer_parents, v_config_chain_children, group=L1,
    )
    multi_xor_violation = _obj(
        "multi_xor_violation", M, current_transactions, v_config_chain_children, group=L1
    )
    daily_statement_summary = _obj(
        "daily_statement_summary", M, effective_balances, current_transactions, group=L1
    )
    # The 12-way union sink — reads every L1 detector.
    l1_exceptions = _obj(
        "l1_exceptions", M,
        drift, ledger_drift, overdraft, limit_breach, expected_eod_balance_breach,
        balance_cadence_gap, stuck_pending, stuck_unbundled, chain_parent_disagreement,
        xor_group_violation, fan_in_disagreement, multi_xor_violation, group=L1,
    )

    # Investigation matviews — read from current_transactions (the
    # supersession view), NOT base transactions: DS.3.3b routes
    # Investigation through supersession so a corrected leg's stale
    # version can't flow ("the audit PDF alone keeps raw-row access" —
    # schema.py:4096-4099, :4281-4282). Independent of every L1 matview.
    inv_pair_rolling_anomalies = _obj("inv_pair_rolling_anomalies", M, current_transactions, group=INV)
    inv_money_trail_edges = _obj("inv_money_trail_edges", M, current_transactions, group=INV)

    return DbObjectGraph(
        objects=(
            transactions, daily_balances, config_kv,
            v_config_rails, v_config_limit_schedules, v_config_chain_children,
            v_config_transfer_templates, v_config_account_roles, v_config_rail_metadata_keys,
            current_transactions, current_daily_balances,
            effective_balances, computed_subledger_balance, computed_ledger_balance,
            data_anchor, drift, ledger_drift, drift_summary, overdraft,
            expected_eod_balance_breach, balance_cadence_gap, limit_breach,
            stuck_pending, stuck_unbundled, chain_parent_disagreement,
            xor_group_violation, transfer_parents, fan_in_disagreement,
            multi_xor_violation, daily_statement_summary, l1_exceptions,
            inv_pair_rolling_anomalies, inv_money_trail_edges,
        )
    )


#: The one canonical graph — built once at import (module constant). The
#: physical ``<prefix>_`` envelope is applied per call via
#: ``DbObject.physical_name`` / ``DbObjectGraph.refresh_names(prefix)``.
SCHEMA_GRAPH: DbObjectGraph = _build_schema_graph()
