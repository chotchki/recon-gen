"""AG.3 (Gap A): the 3 chain-parent pickers in ``auto_scenario`` must
accept Template parents, not just Rail parents.

Pre-fix: ``_pick_two_template_chain_inputs`` / ``_pick_fan_in_chain_inputs``
/ ``_pick_multi_xor_chain_inputs`` silently filter out any Chain whose
``chain.parent`` resolves to a TransferTemplate (via
``c.parent not in rail_names: continue``). Cumulative effect: 7 plant
kinds (TwoTemplateChainPlant, ChainParentDisagreementPlant,
FanInChainPlant + missing + extra, MultiXorMissedPlant,
MultiXorOverlapPlant) never auto-derive for template-heavy L2s.

Tests build minimal L2 instances with Template-parent chains in each of
the three pick-eligible shapes and assert the picker returns a non-None
tuple. Tests for the corresponding plant emit (parent row stamped with
the Template's first leg_rail + template_name = parent template) live
in the plant-emit test files.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Literal

from recon_gen.common.l2.auto_scenario import (
    _pick_fan_in_chain_inputs,
    _pick_multi_xor_chain_inputs,
    _pick_two_template_chain_inputs,
    default_scenario_for,
)
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
from recon_gen.common.l2.seed import emit_full_seed
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


def _template(name: str, leg_rails: tuple[str, ...]) -> TransferTemplate:
    return TransferTemplate(
        name=Identifier(name),
        expected_net=Money(Decimal("0")),
        transfer_key=(Identifier("k"),),
        completion="business_day_end+1d",
        leg_rails=tuple(Identifier(r) for r in leg_rails),
    )


def _l2_with_chain(
    *, chain: Chain, extra_templates: tuple[TransferTemplate, ...] = (),
    extra_rails: tuple[SingleLegRail, ...] = (),
) -> L2Instance:
    """Build a minimal L2 around a given Chain + extra templates/rails."""
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
            AccountTemplate(role=Identifier("R"), scope="internal"),
        ),
        rails=extra_rails,
        transfer_templates=extra_templates,
        chains=(chain,),
        limit_schedules=(),
    )


# ----------------------------------------------------------------------------
# _pick_two_template_chain_inputs — Template parent + Template singleton child
# ----------------------------------------------------------------------------


def test_pick_two_template_chain_inputs_accepts_template_parent() -> None:
    """AG.3 Gap A: when chain.parent is a TransferTemplate AND the
    singleton child is also a TransferTemplate, the picker must return
    the (parent, child) pair so the AB.2.6 plant (TwoTemplateChainPlant
    + ChainParentDisagreementPlant) can land.

    Pre-fix this case silently returned None.
    """
    parent_tmpl = _template("ParentTmpl", ("ParentLeg",))
    child_tmpl = _template("ChildTmpl", ("ChildLeg",))
    inst = _l2_with_chain(
        chain=Chain(
            parent=Identifier("ParentTmpl"),
            children=(ChainChildSpec(name=Identifier("ChildTmpl")),),
        ),
        extra_templates=(parent_tmpl, child_tmpl),
        extra_rails=(
            _single_leg("ParentLeg"),
            _single_leg("ChildLeg", direction="Credit"),
        ),
    )
    pick = _pick_two_template_chain_inputs(inst)
    assert pick is not None, (
        "Template-parent + Template-child chain must be picked by "
        "_pick_two_template_chain_inputs after AG.3"
    )
    parent, child = pick
    assert parent == Identifier("ParentTmpl")
    assert child == Identifier("ChildTmpl")


# ----------------------------------------------------------------------------
# _pick_fan_in_chain_inputs — Template parent + fan_in template child
# ----------------------------------------------------------------------------


def test_pick_fan_in_chain_inputs_accepts_template_parent() -> None:
    """AG.3 Gap A: when chain.parent is a TransferTemplate AND one of
    the children declares fan_in=True with a Template name, the picker
    must return the tuple so FanInChainPlant + its violation siblings
    land.
    """
    parent_tmpl = _template("ParentTmpl", ("ParentLeg",))
    child_tmpl = _template("FanInChild", ("FanInLeg",))
    inst = _l2_with_chain(
        chain=Chain(
            parent=Identifier("ParentTmpl"),
            children=(
                ChainChildSpec(
                    name=Identifier("FanInChild"),
                    fan_in=True,
                    expected_parent_count=3,
                ),
            ),
        ),
        extra_templates=(parent_tmpl, child_tmpl),
        extra_rails=(
            _single_leg("ParentLeg"),
            _single_leg("FanInLeg", direction="Credit"),
        ),
    )
    pick = _pick_fan_in_chain_inputs(inst)
    assert pick is not None, (
        "Template-parent + fan_in Template-child chain must be picked "
        "by _pick_fan_in_chain_inputs after AG.3"
    )
    parent, child, expected = pick
    assert parent == Identifier("ParentTmpl")
    assert child == Identifier("FanInChild")
    assert expected == 3


# ----------------------------------------------------------------------------
# _pick_multi_xor_chain_inputs — Template parent + multi non-fan_in children
# ----------------------------------------------------------------------------


def test_pick_multi_xor_chain_inputs_accepts_template_parent() -> None:
    """AG.3 Gap A: when chain.parent is a TransferTemplate AND the
    children list has ≥2 non-fan_in entries, the picker must return
    the tuple so MultiXorMissedPlant + MultiXorOverlapPlant land.
    """
    parent_tmpl = _template("ParentTmpl", ("ParentLeg",))
    inst = _l2_with_chain(
        chain=Chain(
            parent=Identifier("ParentTmpl"),
            children=(
                ChainChildSpec(name=Identifier("ChildA")),
                ChainChildSpec(name=Identifier("ChildB")),
            ),
        ),
        extra_templates=(parent_tmpl,),
        extra_rails=(
            _single_leg("ParentLeg"),
            _single_leg("ChildA", direction="Credit"),
            _single_leg("ChildB", direction="Credit"),
        ),
    )
    pick = _pick_multi_xor_chain_inputs(inst)
    assert pick is not None, (
        "Template-parent + multi non-fan_in children chain must be "
        "picked by _pick_multi_xor_chain_inputs after AG.3"
    )
    parent, child_a, child_b = pick
    assert parent == Identifier("ParentTmpl")
    assert child_a == Identifier("ChildA")
    assert child_b == Identifier("ChildB")


# ----------------------------------------------------------------------------
# End-to-end: Template-parent chain must NOT be omitted from the scenario AND
# the plant emitter must synthesize a parent row carrying template_name.
# ----------------------------------------------------------------------------


def test_template_parent_multi_xor_plant_emits_with_template_name() -> None:
    """AG.3 Gap A end-to-end: a Template-parent multi-XOR chain (the ONLY
    chain in the L2) must (1) NOT be omitted from the scenario and
    (2) emit plant rows whose parent row carries
    ``template_name='ParentTmpl'`` — synthesized via the parent
    template's first leg_rail by ``_resolve_plant_chain_parent``.

    This exercises the plant-emit half of the AG.3 fix end-to-end (the
    picker tests cover the pick half). spec_example + sasquatch pickers
    still hit a Rail-parent match first in sort order, so this hermetic
    L2 — with NO Rail-parent alternative — is the only thing that drives
    the Template-parent plant-emit path.
    """
    parent_tmpl = _template("ParentTmpl", ("ParentLeg",))
    inst = _l2_with_chain(
        chain=Chain(
            parent=Identifier("ParentTmpl"),
            children=(
                ChainChildSpec(name=Identifier("ChildA")),
                ChainChildSpec(name=Identifier("ChildB")),
            ),
        ),
        extra_templates=(parent_tmpl,),
        extra_rails=(
            _single_leg("ParentLeg"),
            _single_leg("ChildA", direction="Credit"),
            _single_leg("ChildB", direction="Credit"),
        ),
    )
    report = default_scenario_for(inst)
    omitted_kinds = {kind for kind, _ in report.omitted}
    assert "MultiXorMissedPlant" not in omitted_kinds, (
        "Template-parent multi-XOR chain must NOT omit MultiXorMissedPlant "
        f"(AG.3). omitted={sorted(omitted_kinds)}"
    )
    assert "MultiXorOverlapPlant" not in omitted_kinds

    sql = emit_full_seed(
        inst, report.scenario, prefix="t",
        anchor=date(2030, 1, 1), dialect=Dialect.SQLITE,
    )
    # The mxor plant parent rows use transfer_id prefix `tr-mxor-*`.
    # Post-AG.3 they carry template_name='ParentTmpl' (synthesized from
    # the parent template's first leg_rail).
    mxor_parent_rows = [
        ln for ln in sql.splitlines()
        if "'tr-mxor-" in ln and "'ParentTmpl'" in ln
    ]
    assert mxor_parent_rows, (
        "multi_xor plant parent rows must carry template_name='ParentTmpl' "
        "(AG.3 synthesizes the parent row from the parent template's first "
        "leg_rail). Pre-fix the plant emitter returned [] for Template "
        "parents."
    )


def test_template_parent_multi_xor_plant_child_rail_name_is_real_rail() -> None:
    """AJ.2 (Gap G): a MultiXorOverlapPlant must stamp every child row's
    ``rail_name`` with a REAL declared Rail, never the chain-parent's
    name.

    When the chain children are TransferTemplates, the emitter takes the
    ``kind == 'template'`` branch at ``seed.py``'s
    ``_emit_multi_xor_overlap_rows`` and (pre-fix) sets
    ``rail_name = p.chain_parent_rail_name``. For a Template-parent chain
    that's a TransferTemplate name — which matches no declared Rail, so
    the planted child surfaces as a spurious ``unmatched_rail_name``
    exception (the highest-signal L1 invariant: 'a posting matching no
    declared rail is always wrong'). The fix: resolve ``rail_name`` to
    the fired child template's own leg_rail.

    Hermetic L2 with NO Rail-parent alternative so the picker selects
    this chain; both children are Templates so the buggy branch fires.
    """
    parent_tmpl = _template("ParentTmpl", ("ParentLeg",))
    child_a = _template("ChildTmplA", ("LegA",))
    child_b = _template("ChildTmplB", ("LegB",))
    inst = _l2_with_chain(
        chain=Chain(
            parent=Identifier("ParentTmpl"),
            children=(
                ChainChildSpec(name=Identifier("ChildTmplA")),
                ChainChildSpec(name=Identifier("ChildTmplB")),
            ),
        ),
        extra_templates=(parent_tmpl, child_a, child_b),
        extra_rails=(
            _single_leg("ParentLeg"),
            _single_leg("LegA", direction="Credit"),
            _single_leg("LegB", direction="Credit"),
        ),
    )
    report = default_scenario_for(inst)
    sql = emit_full_seed(
        inst, report.scenario, prefix="t",
        anchor=date(2030, 1, 1), dialect=Dialect.SQLITE,
    )
    # Overlap-plant CHILD rows have tx ids tx-mxor-overlap-NNNN-a / -b
    # (the parent row is ...-p). A correct child row references only the
    # child template (LegA/LegB + ChildTmplA/B) — never the chain-parent
    # template name. The leak: rail_name = 'ParentTmpl'.
    child_row_re = re.compile(r"tx-mxor-overlap-\d+-[ab]'")
    leaking_child_rows = [
        ln for ln in sql.splitlines()
        if child_row_re.search(ln) and "'ParentTmpl'" in ln
    ]
    assert not leaking_child_rows, (
        "MultiXorOverlapPlant leaked the chain-parent template name "
        "'ParentTmpl' into a child row's rail_name (seed.py Gap G, the "
        "kind=='template' branch). The child row's rail_name must be the "
        "child template's leg_rail (LegA / LegB), a declared Rail. "
        f"Leaking rows: {len(leaking_child_rows)}"
    )
