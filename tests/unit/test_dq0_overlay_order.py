"""DQ.0 §1 — the Oracle overlay-restore matview order MUST equal the
canonical refresh order.

``_V_OVERLAY_MATVIEW_SUFFIXES`` (snapshotter.py) is a hand-copy of the
dependency-ordered matview list ``refresh_matviews_sql`` emits. It
diverged silently: a DS.3.2 re-key put ``effective_balances`` ahead of
the ``computed_*`` matviews that read FROM it in the canonical order, but
the overlay copy kept the stale ordering — so an Oracle overlay restore
complete-refreshed ``computed_*`` / ``drift`` / ``ledger_drift`` /
``drift_summary`` against stale carry-forward balances. Nothing caught
it: no test referenced the list, and the Oracle-only restore path never
runs in the DuckDB default chain.

This pins the suffix list against the ACTUAL emitted refresh order — the
single source of truth, parsed from ``refresh_matviews_sql``, not a
hand-copied parallel expectation. A re-divergence fails loud. DQ.1
retires this guard by deriving BOTH orders from one ``DbObject`` graph
(the topological sort); until then, this is the guard.
"""
from __future__ import annotations

import re

from recon_gen.common.l2 import L2Instance
from recon_gen.common.l2.schema import refresh_matviews_sql
from recon_gen.common.snapshotter import _V_OVERLAY_MATVIEW_SUFFIXES
from recon_gen.common.sql import Dialect

_PREFIX = "dq0test"


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


def _canonical_refresh_order() -> tuple[str, ...]:
    """The matview leaf-names in refresh order, parsed from the SQL
    ``refresh_matviews_sql`` actually emits on PG (the canonical source)."""
    sql = refresh_matviews_sql(
        _empty_instance(), prefix=_PREFIX, dialect=Dialect.POSTGRES,
    )
    order: list[str] = []
    pattern = rf"REFRESH\s+MATERIALIZED\s+VIEW\s+(?:CONCURRENTLY\s+)?{_PREFIX}_(\w+)"
    for m in re.finditer(pattern, sql):
        name = m.group(1)
        if name not in order:
            order.append(name)
    return tuple(order)


def test_dq0_canonical_refresh_order_is_parseable() -> None:
    """Guard the guard: the regex actually finds the refresh statements
    (if the emit shape changes, fail here explicitly, not silently)."""
    order = _canonical_refresh_order()
    assert len(order) >= 20, order
    assert order[0] == "current_transactions", order
    # effective_balances MUST precede the computed_* it feeds (the exact
    # invariant DS.3.2 established and the overlay copy violated).
    assert order.index("effective_balances") < order.index("computed_subledger_balance")
    assert order.index("effective_balances") < order.index("computed_ledger_balance")


def test_dq0_overlay_suffixes_match_refresh_order() -> None:
    """The Oracle overlay-restore order == the canonical refresh order.

    Born red against the pre-fix ``_V_OVERLAY_MATVIEW_SUFFIXES`` (which
    ordered ``effective_balances`` AFTER ``computed_*``); green once the
    suffix list matches the dependency-ordered refresh SQL.
    """
    canonical = _canonical_refresh_order()
    assert _V_OVERLAY_MATVIEW_SUFFIXES == canonical, (
        "the Oracle overlay-restore matview order has drifted from the "
        "canonical refresh order (refresh_matviews_sql). An overlay "
        "complete-refresh does NOT cascade, so any matview ordered before "
        "one it reads FROM recomputes against stale upstream data (DQ.0 "
        "§1). Re-sync _V_OVERLAY_MATVIEW_SUFFIXES to the refresh order.\n"
        f"  refresh order: {canonical}\n"
        f"  overlay order: {_V_OVERLAY_MATVIEW_SUFFIXES}"
    )
