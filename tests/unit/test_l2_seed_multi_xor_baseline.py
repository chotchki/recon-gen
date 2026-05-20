"""AG.2 (Gap C): baseline ``_emit_baseline_chains`` must honor multi-
children XOR semantics — exactly one non-fan_in child MUST fire per
parent firing.

Pre-fix: ``_emit_baseline_chains`` rolled a 50% completion threshold
for every parent firing of a multi-children chain — 50% of parent
firings emitted ZERO children → ``<prefix>_multi_xor_violation`` matview
fired ``disagreement_kind='missed'`` on what should be a healthy
baseline. (Singleton-children chains use 95% completion intentionally;
the 5% baseline orphan noise is what ``<prefix>_chain_orphans`` reads
as the required-but-missed shape.)

This test asserts the contract at the seed level: every parent firing
of a multi-children chain emits EXACTLY one chain-emit row whose
``transfer_parent_id`` points back at the parent's transfer_id. The
AB.6.0 ``_multi_xor_violation`` matview can only return zero rows on
healthy baseline if this contract holds.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Literal

import pytest

from recon_gen.common.l2.primitives import (
    Account,
    AccountTemplate,
    Chain,
    ChainChildSpec,
    Identifier,
    L2Instance,
    Money,
    Name,
    SingleLegRail,
    TransferTemplate,
)
from recon_gen.common.l2.seed import emit_baseline_seed
from recon_gen.common.sql.dialect import Dialect


def _single_leg(
    name: str, *, direction: Literal["Debit", "Credit"] = "Debit",
) -> SingleLegRail:
    return SingleLegRail(
        name=Identifier(name),
        origin="InternalInitiated",
        metadata_keys=(Identifier("k"),),
        leg_role=(Identifier("R"),),
        leg_direction=direction,
    )


def _build_multi_children_chain_l2(
    *,
    n_children: int = 2,
    include_fan_in_child: bool = False,
) -> L2Instance:
    """L2 with a Rail-parent chain whose non_fan_in children list has
    ``n_children`` Rail entries. When ``include_fan_in_child`` is True,
    a Template-typed fan_in child is appended — fan_in semantics aren't
    XOR-governed, so it should fire INDEPENDENTLY of the XOR pick.
    """
    rails: list[SingleLegRail] = [_single_leg("ParentRail", direction="Debit")]
    children: list[ChainChildSpec] = []
    templates: list[TransferTemplate] = []
    for i in range(n_children):
        name = f"ChildRail{i+1}"
        rails.append(_single_leg(name, direction="Credit"))
        children.append(ChainChildSpec(name=Identifier(name)))
    if include_fan_in_child:
        rails.append(_single_leg("FanInLeg", direction="Credit"))
        templates.append(
            TransferTemplate(
                name=Identifier("FanInTmpl"),
                expected_net=Money(Decimal("0")),
                transfer_key=(Identifier("k"),),
                completion="business_day_end+1d",
                leg_rails=(Identifier("FanInLeg"),),
            )
        )
        children.append(
            ChainChildSpec(
                name=Identifier("FanInTmpl"),
                fan_in=True,
                expected_parent_count=2,
            )
        )

    return L2Instance(
        accounts=(
            Account(
                id=Identifier("a1"),
                role=Identifier("R"),
                scope="internal",
                name=Name("Acct1"),
            ),
        ),
        account_templates=(
            AccountTemplate(
                role=Identifier("R"),
                scope="internal",
            ),
        ),
        rails=tuple(rails),
        transfer_templates=tuple(templates),
        chains=(
            Chain(
                parent=Identifier("ParentRail"),
                children=tuple(children),
            ),
        ),
        limit_schedules=(),
    )


def _emit_sql(inst: L2Instance) -> str:
    return emit_baseline_seed(
        inst,
        prefix="t",
        window_days=10,
        anchor=date(2030, 1, 1),
        dialect=Dialect.SQLITE,
    )


_TR_ID_RE = re.compile(r"'(tr-[A-Za-z0-9_-]+)'")


def _row_ids(row: str) -> list[str]:
    return _TR_ID_RE.findall(row)


def _parse_parent_and_child_firings(
    sql: str, parent_rail: str, child_rails: set[str],
) -> tuple[set[str], dict[str, int]]:
    """Return (parent_transfer_ids, child_count_by_parent_id).

    A parent firing row has ``rail_name='ParentRail'`` and no
    ``transfer_parent_id`` set (it IS the parent). A chain-emit child
    row has ``rail_name`` in ``child_rails`` AND a ``transfer_parent_id``
    matching one of the parent transfer_ids.
    """
    parent_ids: set[str] = set()
    child_count: dict[str, int] = {}
    for ln in sql.splitlines():
        ids = _row_ids(ln)
        if not ids:
            continue
        if f"'{parent_rail}'" in ln:
            # Two cases: this is a parent leg row OR a chain-emit row
            # whose child happens to be ParentRail. Distinguish by
            # whether transfer_parent_id is present (2 tr-* ids = chain
            # emit; 1 tr-* id = standalone parent firing).
            if len(ids) == 1:
                parent_ids.add(ids[0])
            continue
        for child in child_rails:
            if f"'{child}'" in ln:
                # Chain-emit row: ids[0] = child_transfer_id, ids[1] =
                # transfer_parent_id (when set).
                if len(ids) >= 2:
                    parent_tid = ids[1]
                    child_count[parent_tid] = child_count.get(parent_tid, 0) + 1
                break
    return parent_ids, child_count


@pytest.mark.parametrize("n_children", [2, 3])
def test_multi_children_chain_emits_exactly_one_child_per_parent_firing(
    n_children: int,
) -> None:
    """AG.2 Gap C: every parent firing of a multi-children chain must
    emit EXACTLY one chain-emit child row (chain.md XOR contract).

    Pre-fix: ``completion_threshold=0.50`` for multi-children chains
    meant ~50% of parent firings emitted zero children → the
    ``_multi_xor_violation`` matview reported them as 'missed'. With
    AG.2 the threshold is 1.0 for multi-children; singleton-children
    chains keep 0.95 (intentional 5% baseline orphan noise).
    """
    inst = _build_multi_children_chain_l2(n_children=n_children)
    sql = _emit_sql(inst)
    child_rails = {f"ChildRail{i+1}" for i in range(n_children)}
    parent_ids, child_count = _parse_parent_and_child_firings(
        sql, parent_rail="ParentRail", child_rails=child_rails,
    )

    assert parent_ids, "ParentRail produced no firings — toy L2 too small?"

    # Every parent firing must have EXACTLY 1 chain-emit child row.
    # Count omits parent_ids that never appeared as transfer_parent_id —
    # those are the "missed" violations the AG.2 fix eliminates.
    parents_with_one_child = sum(
        1 for tid in parent_ids if child_count.get(tid, 0) == 1
    )
    parents_with_no_child = sum(
        1 for tid in parent_ids if child_count.get(tid, 0) == 0
    )
    parents_with_overlap = sum(
        1 for tid in parent_ids if child_count.get(tid, 0) >= 2
    )

    assert parents_with_no_child == 0, (
        f"{parents_with_no_child}/{len(parent_ids)} parent firings of a "
        f"{n_children}-children chain emitted ZERO children ('missed' "
        f"multi_xor_violation). chain.md requires exactly one child."
    )
    assert parents_with_overlap == 0, (
        f"{parents_with_overlap}/{len(parent_ids)} parent firings of a "
        f"{n_children}-children chain emitted ≥2 children ('overlap' "
        f"multi_xor_violation)."
    )
    assert parents_with_one_child == len(parent_ids)


def test_multi_children_chain_with_fan_in_child_treats_fan_in_independently() -> None:
    """AG.2 lock + AB.6 contract: fan_in children of a mixed-cardinality
    chain fire INDEPENDENTLY (per the AB.4 batched-payout pattern); they
    do NOT count toward the multi-children XOR pick. The XOR pick still
    selects exactly one non_fan_in child per parent firing.

    Test L2: chain.children = [ChildRail1, ChildRail2, FanInTmpl(fan_in)].
    Expectation per parent firing: exactly 1 of {ChildRail1, ChildRail2}
    fires (XOR pick over the 2 non_fan_in entries). FanInTmpl fires per
    its own batched schedule (AB.4 — every ``expected_parent_count``
    parents share one shared child transfer_id) — orthogonal to XOR.
    """
    inst = _build_multi_children_chain_l2(
        n_children=2, include_fan_in_child=True,
    )
    sql = _emit_sql(inst)
    parent_ids, child_count = _parse_parent_and_child_firings(
        sql,
        parent_rail="ParentRail",
        child_rails={"ChildRail1", "ChildRail2"},
    )
    assert parent_ids
    parents_with_no_child = sum(
        1 for tid in parent_ids if child_count.get(tid, 0) == 0
    )
    parents_with_overlap = sum(
        1 for tid in parent_ids if child_count.get(tid, 0) >= 2
    )
    assert parents_with_no_child == 0, (
        f"{parents_with_no_child} parent firings emitted ZERO non_fan_in "
        f"children. fan_in entries are exempt; XOR pick over the 2 "
        f"non_fan_in entries must still fire exactly one."
    )
    assert parents_with_overlap == 0
