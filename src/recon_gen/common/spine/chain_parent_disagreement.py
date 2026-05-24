"""Chain-parent-disagreement family — `Invariant` + `ViolationGenerator`.

AX.1 promotion of the AB.2.6 / AB.2.3 plant. The matview
`<prefix>_chain_parent_disagreement` GROUPs `<prefix>_current_transactions`
by `(transfer_id, template_name)` and surfaces rows where
`COUNT(DISTINCT transfer_parent_id) > 1` — a single child Transfer's
legs claim multiple parent Transfers, which is an ETL bug (parent
reference drift, cross-cycle contamination, or a chain emitter that
forgot the first-firing-wins rule from SPEC gap doc §3).

Per the AB.0 lock: chain integrity is an L2-SHAPE invariant (the L2
yaml declares the chain; ETL must honor the declared parent
linkage). It lives in `ALL_L2_SHAPE_INVARIANTS` alongside the other
3 chain/XOR/fan-in invariants AX promotes.

Matview semantics:
  - Filter: `transfer_parent_id IS NOT NULL` AND `template_name IS
    NOT NULL` AND `status <> 'Failed'`.
  - GROUP BY `(transfer_id, template_name)`.
  - HAVING `COUNT(DISTINCT transfer_parent_id) > 1`.
  - Account columns are NOT filtered or grouped → the generator's
    synthetic account is fine.

The plant is account-shape-agnostic — the matview only cares that
≥2 distinct `transfer_parent_id` values exist under one
`(transfer_id, template_name)`. The generator emits 2 Posted leg
rows sharing one synthetic `transfer_id` + `template_name`, each
carrying a distinct synthetic `transfer_parent_id`.

Single-edge property (matches AT.3 / anomaly / money_trail):
transfers-only — no daily_balances rows → no drift trip from this
plant.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar

from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.spine._emit_helpers import (
    insert_tx,
    load_spec_example,
    to_date,
    ts,
)
from recon_gen.common.spine.violation import Violation


@dataclass(frozen=True)
class ChainParentDisagreementInvariant:
    """Detector for the AB.2.3 matview.

    Identity tuple: `(transfer_id, child_template_name)`. The matview's
    other columns (`distinct_parent_count`, `parent_transfer_id_min`,
    `parent_transfer_id_max`, `business_day`) are diagnostic — they
    help an analyst eyeball which parents disagreed but they're not
    part of the violation's identity.
    """

    name: ClassVar[str] = "chain_parent_disagreement"
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT transfer_id, child_template_name "
            f"FROM {self.prefix}_chain_parent_disagreement",
        ).fetchall()
        return {
            Violation.of(
                "chain_parent_disagreement",
                transfer_id=str(tid),
                child_template_name=str(tname),
            )
            for tid, tname in rows
        }

    def scenario_for(
        self,
        *,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "ChainParentDisagreementGenerator":
        """Pick a chain whose singleton child is a TransferTemplate from
        the L2; return a generator that plants ONE child Transfer with
        2 legs carrying disagreeing `transfer_parent_id` values.

        Raises `ValueError` if the L2 has no chain whose singleton child
        resolves to a TransferTemplate (the AB.2.6 picker's input
        requirement).
        """
        inst = instance if instance is not None else load_spec_example()
        # The AB.2.6 picker (`_pick_two_template_chain_inputs`) returns
        # any chain-child template, INCLUDING fan_in templates — those
        # are silently excluded by the matview's
        # `_render_chain_parent_disagreement_fan_in_filter` (fan_in
        # children are legitimately multi-parent by design). We need
        # a NON-fan_in template here so the plant actually surfaces.
        child_name = _pick_non_fan_in_chain_child(inst)
        if child_name is None:
            raise ValueError(
                "shape has no chain whose singleton child resolves to "
                "a non-fan_in TransferTemplate; cannot manufacture a "
                "chain_parent_disagreement scenario (fan_in children "
                "are excluded by the matview's fan-in filter and would "
                "yield 0 detected violations even when planted)"
            )
        return ChainParentDisagreementGenerator(
            child_template_name=str(child_name),
            anchor_day=anchor_day,
        )


def _pick_non_fan_in_chain_child(instance: L2Instance) -> str | None:
    """Pick any chain-child TransferTemplate that is NOT marked
    `fan_in=True`. The chain_parent_disagreement matview excludes
    fan_in children via its NOT IN filter
    (`_render_chain_parent_disagreement_fan_in_filter`), so a fan_in
    pick would yield a silently-empty matview.

    Walks chains in deterministic order (sorted by parent name +
    children names — same key the AB.2.6 picker uses) so the same L2
    always yields the same pick. Skips chains with no template-shaped
    children at all.
    """
    template_names = {str(t.name) for t in instance.transfer_templates}
    for chain in sorted(
        instance.chains,
        key=lambda ch: (
            str(ch.parent),
            ",".join(sorted(str(d.name) for d in ch.children)),
        ),
    ):
        for child in chain.children:
            if child.fan_in:
                continue
            name = str(child.name)
            if name in template_names:
                return name
    return None


@dataclass
class ChainParentDisagreementGenerator:
    """Emit 2 Posted transaction legs sharing one `transfer_id` +
    `template_name` but assigning different `transfer_parent_id`
    values — surfaces in the matview's
    `COUNT(DISTINCT transfer_parent_id) > 1` branch.

    Account fields are synthetic + deterministic (the matview's GROUP
    BY ignores them). The transfer_id + template_name combination IS
    the violation's identity; both are derived from
    `child_template_name` so two generators built for the same
    template would collide at compose time — caught by AV.5's
    `claimed_accounts` pairwise-disjoint check.

    Single-edge: transfers-only emit → no balance rows → no drift
    trip (matches the AT.3 anomaly / money_trail shape).
    """

    child_template_name: str
    anchor_day: date
    parent_a_transfer_id: str = field(default="tr-cpd-parent-a")
    parent_b_transfer_id: str = field(default="tr-cpd-parent-b")
    rail_name: str = "ach"
    prefix: str = "spec_example"

    @property
    def transfer_id(self) -> str:
        """Deterministic child transfer_id — used as the matview's
        natural-key tuple value + the row's id discriminator."""
        return f"tr-cpd-{self.child_template_name}"

    @property
    def account_id(self) -> str:
        """Single synthetic account_id the generator's 2 legs land on.
        Matview filters don't depend on account columns; the account
        is here just to satisfy NOT NULL constraints + AV.5's
        claimed_accounts contract."""
        return f"acct-cpd-{self.child_template_name}"

    @property
    def intended(self) -> Violation:
        """The natural-key tuple the matview surfaces post-plant."""
        return Violation.of(
            "chain_parent_disagreement",
            transfer_id=self.transfer_id,
            child_template_name=self.child_template_name,
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        """Single synthetic account — AV.5 contract. Two generators
        targeting the same `child_template_name` collide here (same
        account_id derivation → same string in the claimed set)."""
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
                scenario_id, generator="ChainParentDisagreementGenerator",
            )
            if scenario_id is not None else None
        )
        posting = ts(self.anchor_day)
        # 2 legs, same transfer_id + template_name, different parent.
        # The matview reads `COUNT(DISTINCT transfer_parent_id)` and
        # surfaces the row at 2 distinct parents.
        for i, parent_tid in enumerate((
            self.parent_a_transfer_id, self.parent_b_transfer_id,
        )):
            insert_tx(
                conn,
                prefix=self.prefix,
                id=f"tx-cpd-{self.child_template_name}-{i}",
                account_id=self.account_id,
                account_name=f"CPD plant ({self.child_template_name})",
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
                amount_money=100.0,  # arbitrary; matview only counts parents
                amount_direction="Credit",
                status="Posted",
                posting=posting,
                transfer_id=self.transfer_id,
                transfer_parent_id=parent_tid,
                rail_name=self.rail_name,
                template_name=self.child_template_name,
                origin="ExternalForcePosted",
                metadata=metadata,
            )
