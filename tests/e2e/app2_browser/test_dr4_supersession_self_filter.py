"""Browser e2e: Supersession Audit interactions (DR.4 + DR.5 + DR.6).

Parametrized over ``[qs, app2]`` via ``l1_dashboard_driver``.

DR.4 — the same-sheet transaction self-filter is a ``DATA_POINT_CLICK``
drill (``drill_from_first_row``) that writes the ``pL1SaTransaction``
pushdown param and re-renders the SAME sheet — narrowing the Transactions
Audit table to one logical transaction's full entry trail. Because it's a
control-write (not a cross-sheet URL nav) it sidesteps the QS
URL-param-no-control-sync quirk, so both renderers narrow identically.
Clearing the focus is the DR.6 Transaction ID dropdown's job (empty it →
back to all), so there's no right-click "Clear" action to exercise here.

DR.5 — the "Reason Provided" dropdown isolates no-reason (policy-violation)
rows from rows that carry a reason. The partition invariant (with the
Supersedes Reason filter at its sentinel): ``Has reason`` count +
``No reason`` count == the full audit count, since every row's flag is 0
or 1. This both proves the pushdown narrows AND that it partitions cleanly.

DR.6 — the visible "Transaction ID" dropdown shares P_L1_SA_TRANSACTION
with the DR.4 drill; picking an id narrows to that one trail. Its option
universe is the audit's own superseded ids, so a table id is always
pickable.

Data-agnostic: assert relative narrowing (no hard-coded ids/counts), skip
cleanly if the seeded DB has no superseded rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._marks import Need, Tier, needs, tier

from recon_gen.apps.l1_dashboard.app import _SUPERSESSION_AUDIT_NAME
from recon_gen.apps.l1_dashboard.datasets import (
    L1_SA_HAS_REASON_LABEL,
    L1_SA_NO_REASON_LABEL,
)

if TYPE_CHECKING:
    from tests.e2e._drivers import DashboardDriver

pytestmark = [
    pytest.mark.e2e,
    tier(Tier.APP2),
    needs(Need.PLAYWRIGHT),
]

_AUDIT_TABLE = "Transactions Audit"

# DR.7.c — narrowing is only OBSERVABLE when the audit holds >1 distinct
# transaction_id: with a single id, every filter result trivially equals
# that id and `focus_count == full_count`, so a dead control-write→dataset
# bridge would pass green (the tautology the adversarial review caught).
# DR.7.a's day-unique supersession id makes CI's densified scenario plant
# 5 day-distinct trails, so this skip never fires on CI; it only guards a
# thin local seed.
_NARROWING_UNOBSERVABLE_SKIP = (
    "Supersession Audit has <2 distinct transaction_ids in the seeded DB — "
    "narrowing is unobservable (any filter result trivially equals the single "
    "id, so a dead bridge would pass). CI's densified scenario plants 5 "
    "day-distinct supersession trails (DR.7.a); a thin local seed may not."
)


def test_supersession_audit_self_filter_narrows_to_one_transaction(
    l1_dashboard_driver: tuple["DashboardDriver", str],
) -> None:
    """Left-clicking a transaction_id cell on the Supersession Audit
    narrows the Transactions Audit table to exactly that transaction's
    trail. (Clearing the focus is the DR.6 dropdown's job — see the
    Transaction ID dropdown test below.)"""
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet=_SUPERSESSION_AUDIT_NAME)
    driver.wait_loaded(_AUDIT_TABLE)

    pre_rows = driver.table_rows(_AUDIT_TABLE, columns=["transaction_id"])
    pre_ids = {r["transaction_id"] for r in pre_rows}
    if len(pre_ids) < 2:
        pytest.skip(_NARROWING_UNOBSERVABLE_SKIP)
    target = pre_rows[0]["transaction_id"]
    full_count = driver.table_row_count(_AUDIT_TABLE)

    # DATA_POINT_CLICK self-filter → narrows the SAME sheet to `target`.
    driver.drill_from_first_row(_AUDIT_TABLE)
    driver.wait_loaded(_AUDIT_TABLE)

    post_rows = driver.table_rows(_AUDIT_TABLE, columns=["transaction_id"])
    assert post_rows, (
        "DR.4 self-filter emptied the audit table — the pushdown param "
        "narrowed to zero rows instead of the clicked transaction's trail. "
        "Check `pL1SaTransaction` bridges into "
        "`build_supersession_transactions_dataset`'s WHERE on both renderers."
    )
    assert all(r["transaction_id"] == target for r in post_rows), (
        f"DR.4 self-filter must narrow to exactly the clicked transaction "
        f"{target!r}; saw "
        f"{sorted({r['transaction_id'] for r in post_rows})}."
    )
    focus_count = driver.table_row_count(_AUDIT_TABLE)
    assert focus_count < full_count, (
        f"DR.4 self-filter must STRICTLY narrow the audit. The clicked "
        f"transaction {target!r} is one of {len(pre_ids)} distinct ids, so its "
        f"trail ({focus_count} rows) must be smaller than the full audit "
        f"({full_count} rows). focus == full means the pushdown param never "
        f"reached the dataset WHERE on this renderer — the control-write→bridge "
        f"is dead and the audit didn't actually filter."
    )


def test_supersession_audit_no_reason_filter_partitions_the_audit(
    l1_dashboard_driver: tuple["DashboardDriver", str],
) -> None:
    """DR.5 — the "Reason Provided" dropdown narrows to no-reason vs
    has-reason rows, and the two narrowings partition the full audit
    (every row's flag is exactly 0 or 1, so the counts sum)."""
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet=_SUPERSESSION_AUDIT_NAME)
    driver.wait_loaded(_AUDIT_TABLE)

    full_count = driver.table_row_count(_AUDIT_TABLE)
    if full_count == 0:
        pytest.skip(
            "Supersession Audit is empty in the seeded DB — no rows to "
            "partition by reason presence."
        )

    driver.pick_filter("Reason Provided", [L1_SA_HAS_REASON_LABEL])
    driver.wait_loaded(_AUDIT_TABLE)
    has_count = driver.table_row_count(_AUDIT_TABLE)

    driver.pick_filter("Reason Provided", [L1_SA_NO_REASON_LABEL])
    driver.wait_loaded(_AUDIT_TABLE)
    no_count = driver.table_row_count(_AUDIT_TABLE)

    assert has_count <= full_count and no_count <= full_count, (
        f"DR.5 narrowing must not exceed the full audit: has={has_count}, "
        f"no={no_count}, full={full_count}."
    )
    assert has_count + no_count == full_count, (
        f"DR.5 'Has reason' ({has_count}) + 'No reason' ({no_count}) must "
        f"partition the full audit ({full_count}) — every row's flag is 0 "
        f"or 1. A mismatch means the parallel string CASE diverged from the "
        f"projected flag, or the sentinel guard leaked/dropped rows."
    )


def test_supersession_audit_transaction_id_dropdown_narrows_to_one_trail(
    l1_dashboard_driver: tuple["DashboardDriver", str],
) -> None:
    """DR.6 — the visible Transaction ID dropdown narrows the audit to one
    transaction's trail (the picker shares P_L1_SA_TRANSACTION with the
    DR.4 drill). Picking an id the audit shows must narrow to exactly that
    id; the companion's option universe IS the audit's superseded ids, so a
    table id is always pickable."""
    driver, dashboard_arg = l1_dashboard_driver
    driver.open(dashboard_arg, sheet=_SUPERSESSION_AUDIT_NAME)
    driver.wait_loaded(_AUDIT_TABLE)

    pre_rows = driver.table_rows(_AUDIT_TABLE, columns=["transaction_id"])
    pre_ids = {r["transaction_id"] for r in pre_rows}
    if len(pre_ids) < 2:
        pytest.skip(_NARROWING_UNOBSERVABLE_SKIP)
    target = pre_rows[0]["transaction_id"]
    full_count = driver.table_row_count(_AUDIT_TABLE)

    driver.pick_filter("Transaction ID", [target])
    driver.wait_loaded(_AUDIT_TABLE)
    post_rows = driver.table_rows(_AUDIT_TABLE, columns=["transaction_id"])
    assert post_rows, (
        f"DR.6 Transaction ID dropdown narrowed to zero rows for {target!r} "
        f"— the picker's id universe diverged from the audit's, or the "
        f"P_L1_SA_TRANSACTION bridge didn't reach the dataset WHERE."
    )
    assert all(r["transaction_id"] == target for r in post_rows), (
        f"DR.6 dropdown must narrow to exactly {target!r}; saw "
        f"{sorted({r['transaction_id'] for r in post_rows})}."
    )
    focus_count = driver.table_row_count(_AUDIT_TABLE)
    assert focus_count < full_count, (
        f"DR.6 dropdown must STRICTLY narrow the audit. {target!r} is one of "
        f"{len(pre_ids)} distinct ids, so its trail ({focus_count} rows) must "
        f"be smaller than the full audit ({full_count}). focus == full means "
        f"the P_L1_SA_TRANSACTION bridge never reached the dataset WHERE on "
        f"this renderer."
    )
