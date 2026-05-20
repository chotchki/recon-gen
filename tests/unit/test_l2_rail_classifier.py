"""AG.4 (Gap D, minimal scope): the `_classify_rail` inbound over-match
guard routes payroll/batch rails to PAYROLL_BATCH (system-wide) instead
of CUSTOMER_INBOUND (per-customer).

The over-match bug: a rail name containing "inbound"/"deposit" was
always bucketed CUSTOMER_INBOUND (4 firings/customer/day × customer
count). A system-wide batch — one ACH payroll file fanning out to N
customers — got that per-customer scaling wrongly applied, firing
~80×/day where the real cadence is ~1 per pay period.

Scope note: the broader vocabulary expansion (sale/swipe/refund/...
+ INTEREST/CASH/VOUCHER kinds) from the gap doc was deferred — AF's
`firings_typical_per_period` is the universal operator-declared count
override, so the classifier's count role is now backstop-only. This
test covers the one real mis-scaling bug that AF doesn't auto-fix for
un-annotated rails.
"""

from __future__ import annotations

from typing import Literal

from recon_gen.common.l2.primitives import Identifier, SingleLegRail
from recon_gen.common.l2.seed import _RAIL_KIND_PARAMS, _RailKind, _classify_rail


def _leg(name: str, *, direction: Literal["Debit", "Credit"] = "Credit") -> SingleLegRail:
    return SingleLegRail(
        name=Identifier(name),
        origin="InternalInitiated",
        metadata_keys=(Identifier("k"),),
        leg_role=(Identifier("R"),),
        leg_direction=direction,
    )


def test_inbound_plus_batch_routes_to_payroll_batch() -> None:
    """inbound/deposit + payroll|batch → PAYROLL_BATCH (the guard)."""
    for name in (
        "PayrollBatchInbound",
        "BatchACHInbound",
        "PayrollInboundCredit",
        "BatchPayrollDeposit",
    ):
        assert _classify_rail(_leg(name)) is _RailKind.PAYROLL_BATCH, name


def test_plain_inbound_still_customer_inbound() -> None:
    """A plain customer inbound (no payroll/batch) is unchanged — the
    guard is intentionally narrow so it can't reclassify the calibration
    set's CustomerInboundACH / CustomerCashDeposit."""
    assert _classify_rail(_leg("CustomerInboundACH")) is _RailKind.CUSTOMER_INBOUND
    assert _classify_rail(_leg("CustomerCashDeposit")) is _RailKind.CUSTOMER_INBOUND
    assert _classify_rail(_leg("InboundWire")) is _RailKind.CUSTOMER_INBOUND


def test_payroll_batch_scales_system_wide_not_per_customer() -> None:
    """Regression on the over-match: PAYROLL_BATCH must be system-scaled
    with a low per-period cadence, NOT per-customer (which fired the
    single batch ~80×/day)."""
    params = _RAIL_KIND_PARAMS[_RailKind.PAYROLL_BATCH]
    assert params.scaling_kind == "system"
    # ~1 per pay period (bi-weekly ≈ 0.1/business-day) — far below the
    # 4.0/customer/day CUSTOMER_INBOUND rate the over-match applied.
    assert params.daily_target_per_unit <= 0.5
    assert _RAIL_KIND_PARAMS[_RailKind.CUSTOMER_INBOUND].scaling_kind == "customer"


def test_payout_batch_not_reclassified_by_guard() -> None:
    """MerchantWeeklyPayoutBatch contains "batch" but NOT inbound/deposit,
    so the guard doesn't fire — it stays MERCHANT_PAYOUT (matches the
    earlier "payout" pattern). Confirms the guard is scoped to the
    inbound branch only (calibration-set byte-equivalence)."""
    assert _classify_rail(_leg("MerchantWeeklyPayoutBatch")) is _RailKind.MERCHANT_PAYOUT
    assert _classify_rail(_leg("BatchPayoutClose")) is _RailKind.MERCHANT_PAYOUT
