"""TransferTemplate firing (broad-mode) family — `ViolationGenerator` only.

AY.2.b promotion of `common/l2/seed.py::TransferTemplatePlant` (M.3.10g
+ broad-mode plant kind). This is a SEED-COLOR generator: it plants
ONE shared Transfer firing of an L2-declared TransferTemplate so the
L2 Flow Tracing dashboard's Transfer Templates sheet has visible
content for each declared template.

The OLD `_emit_transfer_template_rows` carries picker-layer
sophistication (chain_children resolution + per-firing transfer_key
synthesis + per-leg-origin table). The spine generator stays minimal
— emit a clean firing of a single resolved template. The picker
layer (post-AY.4) composes multiple generators + handles chain
children + metadata cascade through helpers reused across the
broad-mode plants.

Per the AY.2.b evidence-currency layering:

  - `intended` returns a `CoverageObservation` keyed on
    `(template_name, transfer_id, firing_seq)` — "I planted a firing
    of template T." No matching `Invariant` (the L2 Flow Tracing
    surface reads template firings directly; "this template fired"
    isn't a violation shape).

Like `RailFiringGenerator`, the rail-kind discriminator (first
leg_rail of the template) lives on the generator as `is_two_leg:
bool`; the factory's `scenario_for_template` resolves the kind from
the L2 instance + pre-populates the right fields. The template's
`transfer_key` metadata fields land at the picker layer (each firing
needs distinct synthetic values per the SPEC's "same transfer_key
joins one shared Transfer" rule — picker-layer concern, not
generator-layer).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Literal

from recon_gen.common.l2.primitives import (
    L2Instance,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
)
from recon_gen.common.spine._emit_helpers import (
    insert_tx,
    load_spec_example,
    ts,
)
from recon_gen.common.spine.rail_firing import _resolve_rail
from recon_gen.common.spine.violation import CoverageObservation


@dataclass(frozen=True)
class TransferTemplateFactory:
    """Smart constructor namespace for `TransferTemplateGenerator`.

    Resolves a TransferTemplate by name + its first leg_rail's kind
    from the L2 instance, then builds a generator that plants one
    firing in the kind-appropriate shape.
    """

    name: ClassVar[str] = "transfer_template_firing"
    prefix: str = "spec_example"

    def scenario_for_template(
        self,
        template_name: str,
        *,
        source_account_id: str | None = None,
        destination_account_id: str | None = None,
        amount: float = 100.0,
        firing_seq: int = 1,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "TransferTemplateGenerator":
        """Resolve `template_name` to a TransferTemplate + the first
        leg_rail's kind; build a generator that plants one firing.

        Synthetic account defaults match `RailFiringFactory`'s shape:
        deterministic per-template strings when not supplied. Picker
        layer threads real materialized-account identifiers post-AY.4.

        Raises `ValueError` when the template / leg_rail isn't
        declared on the L2.
        """
        inst = instance if instance is not None else load_spec_example()
        template = _resolve_template(inst, template_name)
        if not template.leg_rails:
            raise ValueError(
                f"template {template_name!r} has no leg_rails; cannot "
                f"manufacture a TransferTemplateGenerator (every firing "
                f"requires at least one leg_rail)"
            )
        first_rail = _resolve_rail(inst, str(template.leg_rails[0]))
        is_two_leg = isinstance(first_rail, TwoLegRail)
        src = source_account_id or f"acct-tt-{template_name}-src"
        dst = destination_account_id
        if is_two_leg and dst is None:
            dst = f"acct-tt-{template_name}-dst"
        if not is_two_leg:
            dst = None
        leg_direction: Literal["Debit", "Credit"] = "Debit"
        if (
            isinstance(first_rail, SingleLegRail)
            and first_rail.leg_direction == "Credit"
        ):
            leg_direction = "Credit"
        return TransferTemplateGenerator(
            template_name=template_name,
            rail_name=str(first_rail.name),
            is_two_leg=is_two_leg,
            source_account_id=src,
            destination_account_id=dst,
            amount=amount,
            single_leg_direction=leg_direction,
            firing_seq=firing_seq,
            anchor_day=anchor_day,
            prefix=self.prefix,
        )


def _resolve_template(
    instance: L2Instance, name: str,
) -> TransferTemplate:
    for t in instance.transfer_templates:
        if str(t.name) == name:
            return t
    raise ValueError(
        f"transfer_template {name!r} not declared on the L2 instance; "
        f"cannot manufacture a TransferTemplateGenerator for an "
        f"unknown template"
    )


@dataclass
class TransferTemplateGenerator:
    """Emit one Posted firing of an L2-declared TransferTemplate.

    Rail kind branches on `is_two_leg` (set by the factory from the
    template's first leg_rail kind):

      - Two-leg: debit on source + credit on destination, both
        carrying `template_name=template_name`. Net = 0 (matches the
        template's expected_net).
      - Single-leg: one leg on source in the resolved direction,
        carrying `template_name=template_name`. Net = ±amount (the
        L1 SQL surfaces as 'Imbalanced' against expected_net = 0 —
        accurate for a bare single-leg cycle).

    `intended` returns a CoverageObservation keyed on `(template_name,
    transfer_id, firing_seq)`. No matching Invariant.
    """

    template_name: str
    rail_name: str
    is_two_leg: bool
    source_account_id: str
    destination_account_id: str | None
    amount: float
    single_leg_direction: Literal["Debit", "Credit"]
    firing_seq: int
    anchor_day: date
    prefix: str = "spec_example"

    @property
    def transfer_id(self) -> str:
        return f"tr-tt-{self.template_name}-{self.firing_seq:04d}"

    @property
    def intended(self) -> CoverageObservation:
        return CoverageObservation.of(
            "transfer_template_firing",
            template_name=self.template_name,
            transfer_id=self.transfer_id,
            firing_seq=self.firing_seq,
            leg_count=2 if self.is_two_leg else 1,
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        accounts = {self.source_account_id}
        if self.destination_account_id is not None:
            accounts.add(self.destination_account_id)
        return frozenset(accounts)

    def emit(
        self,
        conn: sqlite3.Connection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        metadata = (
            scenario_metadata(
                scenario_id, generator="TransferTemplateGenerator",
            )
            if scenario_id is not None else None
        )
        posting = ts(self.anchor_day, hour=11)

        if self.is_two_leg:
            assert self.destination_account_id is not None, (
                "is_two_leg=True requires destination_account_id set; "
                "factory should have populated it"
            )
            insert_tx(
                conn,
                prefix=self.prefix,
                id=f"tx-tt-{self.template_name}-{self.firing_seq:04d}-src",
                account_id=self.source_account_id,
                account_name=(
                    f"TT {self.template_name} src ({self.firing_seq})"
                ),
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
                amount_money=-self.amount,
                amount_direction="Debit",
                status="Posted",
                posting=posting,
                transfer_id=self.transfer_id,
                rail_name=self.rail_name,
                template_name=self.template_name,
                origin="InternalInitiated",
                metadata=metadata,
            )
            insert_tx(
                conn,
                prefix=self.prefix,
                id=f"tx-tt-{self.template_name}-{self.firing_seq:04d}-dst",
                account_id=self.destination_account_id,
                account_name=(
                    f"TT {self.template_name} dst ({self.firing_seq})"
                ),
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
                amount_money=self.amount,
                amount_direction="Credit",
                status="Posted",
                posting=posting,
                transfer_id=self.transfer_id,
                rail_name=self.rail_name,
                template_name=self.template_name,
                origin="InternalInitiated",
                metadata=metadata,
            )
            return

        signed_amount = (
            self.amount if self.single_leg_direction == "Credit"
            else -self.amount
        )
        insert_tx(
            conn,
            prefix=self.prefix,
            id=f"tx-tt-{self.template_name}-{self.firing_seq:04d}",
            account_id=self.source_account_id,
            account_name=f"TT {self.template_name} ({self.firing_seq})",
            account_role="CustomerSubledger",
            account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=signed_amount,
            amount_direction=self.single_leg_direction,
            status="Posted",
            posting=posting,
            transfer_id=self.transfer_id,
            rail_name=self.rail_name,
            template_name=self.template_name,
            origin="InternalInitiated",
            metadata=metadata,
        )
