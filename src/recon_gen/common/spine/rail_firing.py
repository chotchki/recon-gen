"""Rail firing (broad-mode) family — `ViolationGenerator` only.

AY.2.b promotion of `common/l2/seed.py::RailFiringPlant` (M.4.2
broad-mode plant kind). This is a SEED-COLOR generator: it plants
ONE Posted firing of an L2-declared Rail to populate the L2 Flow
Tracing dashboard's Rails / Chains / Transfer Templates sheets with
visible content. No SHOULD violation — the L1 surface stays clean.

The OLD `_emit_rail_firing_rows` carries a lot of picker-layer
sophistication (chain-completion via `_emit_plant_chain_completion`,
transfer-key metadata cascade from containing TransferTemplates,
per-leg-origin resolution table). The spine generator deliberately
stays minimal — emit a clean firing on a single resolved rail. The
picker layer (post-AY.4) composes multiple generators + handles
chain-completion via a dedicated generator. Convergence path: per
AY.0 "the spine generator can start simpler than the OLD path and
converge over time; AY.5 re-locks byte seeds after the rewrite."

Per the AY.2.b evidence-currency layering:

  - `intended` returns a `CoverageObservation` keyed on `(rail_name,
    transfer_id, firing_seq)` — the seed claims "I planted a firing
    of rail X." No matching `Invariant` (the L2 Flow Tracing surface
    reads rail firings directly off `<prefix>_transactions`; no
    matview surfaces "this rail did fire" as a violation, that
    would be backwards).

Rail kind branches internally (TwoLegRail → 2 legs summing to zero;
SingleLegRail → 1 leg in the rail's declared `leg_direction`); the
factory's `scenario_for_*_rail` smart constructors resolve the rail
kind from the L2 instance and pre-populate the right fields. The
single generator class avoids fragmenting the AY.2.b surface across
4 new modules (one per rail-kind × generator).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import ClassVar, Literal

from recon_gen.common.l2.primitives import (
    L2Instance,
    Rail,
    SingleLegRail,
    TwoLegRail,
)
from recon_gen.common.spine._emit_helpers import (
    insert_tx,
    load_spec_example,
    ts,
)
from recon_gen.common.spine.violation import CoverageObservation


@dataclass(frozen=True)
class RailFiringFactory:
    """Smart constructor namespace for `RailFiringGenerator`.

    Mirrors the `Invariant.scenario_for(...)` pattern from the rest
    of the spine for surface parity. Picks a rail by name from the
    L2 instance + resolves its kind, then builds a generator with
    the right field shape.
    """

    name: ClassVar[str] = "rail_firing"
    prefix: str = "spec_example"

    def scenario_for_rail(
        self,
        rail_name: str,
        *,
        account_id_a: str | None = None,
        account_id_b: str | None = None,
        amount: float = 100.0,
        firing_seq: int = 1,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "RailFiringGenerator":
        """Resolve `rail_name` to a Rail + build a generator that
        plants one firing.

        Account fields default to synthetic per-rail strings when not
        supplied — matches the AY.0 minimal-viable shape. The picker
        layer (post-AY.4) threads real `TemplateInstance` / `Account`
        identifiers when the firing needs to land on a materialized
        account.

        Raises `ValueError` if the rail isn't declared on the L2 OR
        if `account_id_b is None` for a TwoLegRail (second leg
        required for two-leg rails).
        """
        inst = instance if instance is not None else load_spec_example()
        rail = _resolve_rail(inst, rail_name)
        is_two_leg = isinstance(rail, TwoLegRail)
        # Synthetic defaults: deterministic + recognizably "broad-mode
        # firing for rail X" in the seeded data.
        acct_a = account_id_a or f"acct-rf-{rail_name}-a"
        acct_b = account_id_b
        if is_two_leg and acct_b is None:
            acct_b = f"acct-rf-{rail_name}-b"
        if not is_two_leg:
            # SingleLegRail: account_id_b is meaningless; the picker
            # historically set it equal to account_id_a for shape
            # consistency. We just drop it.
            acct_b = None
        # Determine the per-leg direction (single-leg only). Variable
        # → Debit per the OLD path's convention (closing-leg semantics
        # aren't material to seed-color coverage).
        leg_direction: Literal["Debit", "Credit"] = "Debit"
        if isinstance(rail, SingleLegRail) and rail.leg_direction == "Credit":
            leg_direction = "Credit"
        return RailFiringGenerator(
            rail_name=rail_name,
            is_two_leg=is_two_leg,
            account_id_a=acct_a,
            account_id_b=acct_b,
            amount=amount,
            single_leg_direction=leg_direction,
            firing_seq=firing_seq,
            anchor_day=anchor_day,
            prefix=self.prefix,
        )


def _resolve_rail(instance: L2Instance, name: str) -> Rail:
    for r in instance.rails:
        if str(r.name) == name:
            return r
    raise ValueError(
        f"rail {name!r} not declared on the L2 instance; cannot "
        f"manufacture a RailFiringGenerator for an unknown rail"
    )


@dataclass
class RailFiringGenerator:
    """Emit one Posted firing of an L2-declared Rail.

    TwoLegRail: 2 legs (debit on account_id_a for -amount + credit on
    account_id_b for +amount) sharing one transfer_id; net = 0.

    SingleLegRail: 1 leg on account_id_a in the resolved
    single_leg_direction; net = ±amount (the L1 SQL surfaces this as
    'Imbalanced' against the rail's expected_net = 0 — accurate
    representation of a bare single-leg cycle without its sibling
    legs in the broad-mode picker context).

    `intended` returns a CoverageObservation keyed on `(rail_name,
    transfer_id, firing_seq)`. No matching Invariant (rail firings
    aren't violations).

    Account fields are caller-supplied (the factory provides
    deterministic synthetic defaults). The picker layer (post-AY.4)
    threads real `TemplateInstance.account_id` / `Account.id` values
    when the firing needs to land on a materialized account.
    """

    rail_name: str
    is_two_leg: bool
    account_id_a: str
    account_id_b: str | None
    amount: float
    single_leg_direction: Literal["Debit", "Credit"]
    firing_seq: int
    anchor_day: date
    prefix: str = "spec_example"
    # AY.6.b — per-firing metadata field values (e.g. from
    # `Rail.metadata_value_examples`). The OLD path cycled through
    # the rail's declared example lists per firing_seq; the plant
    # adapter (AY.4.c.3) threads each plant's `extra_metadata` tuple
    # through this field. Empty tuple (default) → no extras emitted,
    # only the AV.5 scenario_id stamp lands in the JSON column.
    metadata_extras: tuple[tuple[str, str], ...] = ()

    @property
    def transfer_id(self) -> str:
        return f"tr-rf-{self.rail_name}-{self.firing_seq:04d}"

    @property
    def intended(self) -> CoverageObservation:
        """Presence evidence: rail X fired once on the anchor day.
        Identity carries the natural-key tuple a coverage detector
        would round-trip against if/when one lands."""
        return CoverageObservation.of(
            "rail_firing",
            rail_name=self.rail_name,
            transfer_id=self.transfer_id,
            firing_seq=self.firing_seq,
            leg_count=2 if self.is_two_leg else 1,
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        """AV.5 contract: union of the rail's leg account_ids. Two
        firings of the same rail with different firing_seq target
        the same account_id_a → ScenarioContext rejects parallel
        firings of the same rail unless the picker varies accounts."""
        accounts = {self.account_id_a}
        if self.account_id_b is not None:
            accounts.add(self.account_id_b)
        return frozenset(accounts)

    def emit(
        self,
        conn: sqlite3.Connection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        import json
        from recon_gen.common.spine.scenario_context import scenario_metadata
        # AY.6.b — build the metadata JSON as the union of
        # `metadata_extras` (per-firing field values from the rail's
        # `metadata_value_examples`) + the AV.5 scenario_id stamp.
        # Untagged callers (scenario_id is None) AND no extras → metadata
        # is None → SQL NULL (byte-stable with pre-AY.6.b).
        extras_dict: dict[str, str] = dict(self.metadata_extras)
        if scenario_id is not None:
            tagged = scenario_metadata(
                scenario_id, generator="RailFiringGenerator",
            )
            tagged_dict = json.loads(tagged)
            extras_dict = {**tagged_dict, **extras_dict}  # extras win on overlap (matches OLD path)
        if extras_dict:
            metadata: str | None = json.dumps(
                extras_dict, sort_keys=True,
                separators=(",", ":"),  # typing-smell: ignore[json-indent]: compact deterministic per-row DB metadata, not a human-diffable file
            )
        else:
            metadata = None
        posting = ts(self.anchor_day, hour=11)

        if self.is_two_leg:
            assert self.account_id_b is not None, (
                "is_two_leg=True requires account_id_b set; factory "
                "should have populated it"
            )
            # Source-side debit leg.
            insert_tx(
                conn,
                prefix=self.prefix,
                id=f"tx-rf-{self.rail_name}-{self.firing_seq:04d}-src",
                account_id=self.account_id_a,
                account_name=f"RF {self.rail_name} src ({self.firing_seq})",
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
                amount_money=-self.amount,
                amount_direction="Debit",
                status="Posted",
                posting=posting,
                transfer_id=self.transfer_id,
                rail_name=self.rail_name,
                origin="InternalInitiated",
                metadata=metadata,
            )
            # Destination-side credit leg.
            insert_tx(
                conn,
                prefix=self.prefix,
                id=f"tx-rf-{self.rail_name}-{self.firing_seq:04d}-dst",
                account_id=self.account_id_b,
                account_name=f"RF {self.rail_name} dst ({self.firing_seq})",
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
                amount_money=self.amount,
                amount_direction="Credit",
                status="Posted",
                posting=posting,
                transfer_id=self.transfer_id,
                rail_name=self.rail_name,
                origin="InternalInitiated",
                metadata=metadata,
            )
            return

        # SingleLegRail: 1 leg in the resolved direction.
        signed_amount = (
            self.amount if self.single_leg_direction == "Credit"
            else -self.amount
        )
        insert_tx(
            conn,
            prefix=self.prefix,
            id=f"tx-rf-{self.rail_name}-{self.firing_seq:04d}",
            account_id=self.account_id_a,
            account_name=f"RF {self.rail_name} ({self.firing_seq})",
            account_role="CustomerSubledger",
            account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=signed_amount,
            amount_direction=self.single_leg_direction,
            status="Posted",
            posting=posting,
            transfer_id=self.transfer_id,
            rail_name=self.rail_name,
            origin="InternalInitiated",
            metadata=metadata,
        )
