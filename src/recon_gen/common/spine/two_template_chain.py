"""Two-template chain (healthy) family — `ViolationGenerator` only.

AY.2.b promotion of `common/l2/seed.py::TwoTemplateChainPlant` (AB.2.6
healthy plant). This is a SEED-COLOR generator: it plants a healthy
2-template chain firing (one parent leg + N child template legs all
sharing one child Transfer + agreeing on `transfer_parent_id`) so the
L1 dashboard's PostedRequirements panel + the audit PDF have a
clearly-labeled healthy two-template chain row to display, separate
from the probabilistic baseline.

Crucially this plant produces NO matview row — `chain_parent_disagreement`
reads `COUNT(DISTINCT transfer_parent_id) > 1` grouped by
`(transfer_id, template_name)`; this generator emits N child legs all
carrying the SAME `parent_transfer_id`, so the count is 1 and the
matview branch never fires.

Per the AY.2.b evidence-currency layering:

  - `intended` returns a `CoverageObservation` (NOT `RuleViolation`,
    NOT `None`). The seed claims "I planted a healthy two-template
    chain firing"; the audit-PDF "PostedRequirements" coverage section
    reads the row(s) directly. A coverage detector (deferred) could
    eventually walk
    `<prefix>_transactions WHERE transfer_id=? AND template_name=?
    GROUP BY transfer_id HAVING COUNT(DISTINCT transfer_parent_id) = 1`
    and assert ≥1 healthy chain firing per chain-child template; for
    now `intended` is presence-only evidence.

No matching `Invariant` (the AY.0 design's "audit-fixture without
detector" shape). Registers with empty edges in
`INVARIANT_GENERATOR_EDGES`.

Single-edge property (matches the other 4 L2-shape spine generators):
transfers-only → no daily_balances rows → no drift trip.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import ClassVar

from recon_gen.common.l2.primitives import L2Instance, TransferTemplate
from recon_gen.common.spine._emit_helpers import (
    insert_tx,
    load_spec_example,
    ts,
)
from recon_gen.common.spine.violation import CoverageObservation


@dataclass(frozen=True)
class TwoTemplateChainFactory:
    """Smart constructor namespace for `TwoTemplateChainGenerator`.

    AY.2.b deliberately omits an `Invariant` here — there's no matview
    to detect this plant's evidence (the plant is non-violating). The
    factory mirrors the `Invariant.scenario_for(...)` pattern from the
    rest of the spine for surface parity, so seed-color generators
    feel like the violation-detecting ones at the call site.
    """

    name: ClassVar[str] = "two_template_chain_healthy"
    prefix: str = "spec_example"

    def scenario_for_healthy(
        self,
        *,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "TwoTemplateChainGenerator":
        """Pick any chain whose singleton child is a TransferTemplate
        (the AB.2.6 picker) and build a generator that plants a
        healthy 2-template chain firing.

        Parent can resolve to either a Rail (rail-parent) or a
        TransferTemplate (template-parent, AG.3 Gap A); the generator
        carries enough fields to emit the parent row correctly in
        both cases.

        Raises `ValueError` when the L2 has no eligible chain
        (singleton-child + parent ∈ rails ∪ templates + child ∈
        templates with ≥1 leg_rail).
        """
        inst = instance if instance is not None else load_spec_example()
        from recon_gen.common.l2.auto_scenario import (
            _pick_two_template_chain_inputs,
        )
        pick = _pick_two_template_chain_inputs(inst)
        if pick is None:
            raise ValueError(
                "shape has no chain whose singleton child resolves to a "
                "TransferTemplate (AB.2.6 picker rejected); cannot "
                "manufacture a healthy two-template chain plant"
            )
        chain_parent, child_name = pick
        child_template = _find_template(inst, str(child_name))
        if child_template is None or not child_template.leg_rails:
            raise ValueError(
                f"chain child {child_name} resolved by picker but the "
                f"template has no leg_rails — defensive guard, the "
                f"picker should have rejected this shape"
            )
        # AG.3 (Gap A): parent may be Rail OR Template. Match the OLD
        # path's `_resolve_plant_chain_parent` shape: rail-parent →
        # (rail, None); template-parent → (template.leg_rails[0],
        # template.name).
        rail_names = {str(r.name) for r in inst.rails}
        if str(chain_parent) in rail_names:
            parent_rail_name = str(chain_parent)
            parent_template_name: str | None = None
        else:
            parent_template = _find_template(inst, str(chain_parent))
            if parent_template is None or not parent_template.leg_rails:
                raise ValueError(
                    f"chain parent {chain_parent} resolves to neither a "
                    f"Rail nor a Template-with-leg_rails — picker should "
                    f"have rejected this shape"
                )
            parent_rail_name = str(parent_template.leg_rails[0])
            parent_template_name = str(parent_template.name)
        return TwoTemplateChainGenerator(
            chain_parent_name=str(chain_parent),
            parent_rail_name=parent_rail_name,
            parent_template_name=parent_template_name,
            child_template_name=str(child_template.name),
            child_leg_rails=tuple(str(r) for r in child_template.leg_rails),
            anchor_day=anchor_day,
            prefix=self.prefix,
        )


def _find_template(
    instance: L2Instance, name: str,
) -> TransferTemplate | None:
    for t in instance.transfer_templates:
        if str(t.name) == name:
            return t
    return None


@dataclass
class TwoTemplateChainGenerator:
    """Emit one parent leg + N child template legs (one per
    `child_leg_rails` entry) all sharing one child `transfer_id` and
    all carrying the same `parent_transfer_id` — the healthy 2-template
    chain shape.

    Account fields are synthetic + deterministic (matches the
    `ChainParentDisagreementGenerator` pattern; the matview ignores
    account columns when grouping by transfer_id + template_name).

    `intended` returns a `CoverageObservation` keyed on `(transfer_id,
    chain_parent_name, child_template_name, child_leg_count)` — the
    presence-claim tuple a coverage detector could round-trip against.
    """

    chain_parent_name: str
    parent_rail_name: str
    parent_template_name: str | None
    child_template_name: str
    child_leg_rails: tuple[str, ...]
    anchor_day: date
    prefix: str = "spec_example"
    # AY.4.c.2 — account_id_override allows the plant adapter
    # (AY.4.c.3) to thread OLD plant account_ids through, preventing
    # PK collisions when N plants of the same shape compose. Defaults
    # to None → preserves the synthetic `f"acct-ttc-{child_template
    # _name}"` derivation byte-stable for every existing test caller.
    account_id_override: str | None = None

    @property
    def parent_transfer_id(self) -> str:
        return f"tr-ttc-parent-{self.child_template_name}"

    @property
    def child_transfer_id(self) -> str:
        return f"tr-ttc-child-{self.child_template_name}"

    @property
    def account_id(self) -> str:
        if self.account_id_override is not None:
            return self.account_id_override
        return f"acct-ttc-{self.child_template_name}"

    @property
    def intended(self) -> CoverageObservation:
        """Presence evidence: a healthy 2-template chain firing landed
        with N child legs all agreeing on parent_transfer_id. Identity
        carries the natural-key tuple a coverage detector would
        round-trip against if/when one lands."""
        return CoverageObservation.of(
            "two_template_chain_healthy",
            child_transfer_id=self.child_transfer_id,
            chain_parent_name=self.chain_parent_name,
            child_template_name=self.child_template_name,
            child_leg_count=len(self.child_leg_rails),
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        return frozenset({self.account_id})

    def emit(
        self,
        conn: sqlite3.Connection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        metadata = (
            scenario_metadata(
                scenario_id, generator="TwoTemplateChainGenerator",
            )
            if scenario_id is not None else None
        )
        parent_posting = ts(self.anchor_day, hour=10)
        child_posting = ts(self.anchor_day, hour=11)

        # Parent firing: one Debit leg keyed to parent_rail_name. When
        # the chain parent resolved to a Template (AG.3 Gap A), stamp
        # template_name=parent_template_name so the row reads as that
        # template's firing via its first leg_rail.
        insert_tx(
            conn,
            prefix=self.prefix,
            id=f"tx-ttc-parent-{self.child_template_name}",
            account_id=self.account_id,
            account_name=f"TTC parent ({self.child_template_name})",
            account_role="CustomerSubledger",
            account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=-100.0,
            amount_direction="Debit",
            status="Posted",
            posting=parent_posting,
            transfer_id=self.parent_transfer_id,
            rail_name=self.parent_rail_name,
            template_name=self.parent_template_name,
            origin="InternalInitiated",
            metadata=metadata,
        )

        # Child template legs — one per leg_rail of the child template.
        # All share child_transfer_id, all carry the same
        # transfer_parent_id=parent_transfer_id, all stamp
        # template_name=child_template_name. The matview groups these
        # into one row with distinct_parent_count=1 → no violation.
        for k, leg_rail in enumerate(self.child_leg_rails):
            insert_tx(
                conn,
                prefix=self.prefix,
                id=f"tx-ttc-child-{self.child_template_name}-{k}",
                account_id=self.account_id,
                account_name=(
                    f"TTC child ({self.child_template_name}) leg {k}"
                ),
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
                amount_money=50.0,  # arbitrary; matview ignores amount
                amount_direction="Credit",
                status="Posted",
                posting=child_posting,
                transfer_id=self.child_transfer_id,
                transfer_parent_id=self.parent_transfer_id,
                rail_name=leg_rail,
                template_name=self.child_template_name,
                origin="InternalInitiated",
                metadata=metadata,
            )
