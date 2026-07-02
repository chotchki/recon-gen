"""Chain parent disagreement (law 8) — exhaustive parent-claim shapes.

Cell = one child Transfer of a chain-child template; legs enumerate
multisets of size ≤3 over (claimed parent ∈ {P1, P2, P3, none} ×
status ∈ {Posted, Pending, Failed, Zq9x}). The residual counts
distinct parents over FIRING legs (Posted/Pending; Failed and the
unknown tail land on the failed side), cell exists only when some
firing leg claims a parent.

Control cells that must stay invisible in this matview:

- the same multi-parent shapes on the fan_in-excluded child template
  (its cardinality is fan_in_disagreement's job — those probes
  DECLARE their fan_in expectations so the cross-detector packing in
  the shared DB stays exact);
- multi-parent legs with a NULL template (rail-as-child chains carry
  no template-level identity — outside the law's cell universe).
"""
from __future__ import annotations

import datetime as dt
import itertools
from typing import Final

from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.spine.residuals import (
    chain_parent_residual,
    fan_in_residual,
)
from tests.enumeration.domains._base import (
    WINDOW_START,
    BoundaryProfile,
    as_int,
    as_str,
)
from tests.enumeration.harness import (
    CellBuilder,
    DetectorCheck,
    EnumerationDB,
    PackedCell,
    ViolationMap,
)

#: The (non-fan_in) chain-child template the cells fire on. Declared a
#: chain child of ReconciliationLeg on spec_example; the matview
#: itself is template-agnostic beyond the fan_in exclusion list.
CHILD_TEMPLATE: Final = "MerchantSettlementCycle"
#: The fan_in-excluded template (asserted against the profile).
EXCLUDED_TEMPLATE: Final = "BatchedPayoutBatch"

_PARENT_TOKENS: Final = ("P1", "P2", "P3", None)
_STATUSES: Final = (POSTED_STATUS, "Pending", "Failed", "Zq9x")
_POSTING: Final = dt.datetime.combine(WINDOW_START, dt.time(12, 0))

type LegShape = tuple[str | None, str]


def _leg_shapes() -> list[tuple[LegShape, ...]]:
    atoms: list[LegShape] = [
        (parent, status)
        for parent in _PARENT_TOKENS
        for status in _STATUSES
    ]
    out: list[tuple[LegShape, ...]] = [()]
    for n in (1, 2, 3):
        out.extend(itertools.combinations_with_replacement(atoms, n))
    return out


def _build_cell(
    prefix: str,
    legs: tuple[LegShape, ...],
    template: str | None,
) -> tuple[CellBuilder, str]:
    transfer = prefix
    account = f"{prefix}a"
    b = CellBuilder()
    for li, (parent_token, status) in enumerate(legs):
        parent = None if parent_token is None else f"{prefix}{parent_token}"
        b.leg(
            id=f"{prefix}L{li}", account=account, amount=0, status=status,
            posting=_POSTING, transfer=transfer, parent=parent,
            rail="RailCP", template=template, parent_role=None,
        )
    return b, transfer


def cells(profile: BoundaryProfile) -> list[PackedCell]:
    assert EXCLUDED_TEMPLATE in profile.chain_parent_excluded, (
        f"{EXCLUDED_TEMPLATE} is no longer a fan_in child on "
        f"spec_example — the exclusion probes test nothing"
    )
    assert CHILD_TEMPLATE not in profile.chain_parent_excluded
    out: list[PackedCell] = []
    for i, legs in enumerate(_leg_shapes()):
        prefix = f"cp{i:06d}"
        b, transfer = _build_cell(prefix, legs, CHILD_TEMPLATE)
        residual = chain_parent_residual(b.state(), transfer, CHILD_TEMPLATE)
        expected: ViolationMap = {}
        if residual is not None and residual != 0:
            expected[(transfer, CHILD_TEMPLATE)] = residual
        out.append(PackedCell(
            *b.rows(), prefixes=(prefix,),
            expected={"chain_parent": expected},
        ))
    # Exclusion probes on the fan_in child template. Their fan_in
    # expectations are declared through the SAME residual the fan_in
    # domain uses, so the shared packed DB stays exactly accounted.
    fan_in_expected_count = profile.fan_in_expected[
        ("BatchPayoutTrigger", EXCLUDED_TEMPLATE)
    ]
    probe_shapes: tuple[tuple[LegShape, ...], ...] = (
        (("P1", POSTED_STATUS), ("P2", POSTED_STATUS)),
        (("P1", POSTED_STATUS), ("P2", POSTED_STATUS), ("P3", POSTED_STATUS)),
    )
    for j, legs in enumerate(probe_shapes):
        prefix = f"cq{j:04d}"
        b, transfer = _build_cell(prefix, legs, EXCLUDED_TEMPLATE)
        fan_in_map: ViolationMap = {}
        residual = fan_in_residual(b.state(), transfer, fan_in_expected_count)
        if residual is not None and residual != 0:
            fan_in_map[(transfer, "BatchPayoutTrigger")] = residual
        out.append(PackedCell(
            *b.rows(), prefixes=(prefix,),
            expected={"chain_parent": {}, "fan_in": fan_in_map},
        ))
    # NULL-template probe: multi-parent legs outside the cell universe.
    b, _ = _build_cell(
        "cq9999", (("P1", POSTED_STATUS), ("P2", POSTED_STATUS)), None,
    )
    out.append(PackedCell(
        *b.rows(), prefixes=("cq9999",), expected={"chain_parent": {}},
    ))
    return out


def _read_engine(db: EnumerationDB) -> ViolationMap:
    rows = db.fetchall(
        f"SELECT transfer_id, child_template_name, "
        f"distinct_parent_count - 1 "
        f"FROM {db.prefix}_chain_parent_disagreement",
    )
    return {
        (as_str(row[0]), as_str(row[1])): as_int(row[2])
        for row in rows
    }


CHECK: Final = DetectorCheck(
    detector="chain_parent", read_engine=_read_engine,
)
