"""AY.4.g — `ChainCompletionGenerator` (chain-completion shim).

A plant that fires a chain-parent rail/template (e.g., an XOR-missed
plant on a TransferTemplate that is ALSO a chain parent) without
emitting matching child legs gets false-positive flagged by the
`multi_xor_violation` matview as a "missed-child" violation — even
though the plant's INTENT was the XOR violation, not the chain
violation.

The OLD seed.py addressed this via `_emit_plant_chain_completion`,
called inline by each affected plant emitter (XOR / limit_breach /
broad rail / broad transfer_template). The spine analog is this
generator: a standalone emitter the adapter composes alongside the
violating plant. Same intent — for each chain the just-emitted plant
parents, emit one synthetic child leg keyed to the plant's
`transfer_id` so the matview sees a matched child + drops the
false positive.

The completion is a CoverageObservation (it's seed scaffolding, not
a rule violation). Registers with empty edges; AU.5 widens for the
coverage bucket.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.spine._emit_helpers import insert_tx, ts
from recon_gen.common.spine.violation import CoverageObservation


@dataclass
class ChainCompletionGenerator:
    """Emit one synthetic child leg per chain whose parent matches
    `parent_name`, with `transfer_parent_id = parent_transfer_id` so
    the chain matview sees a matched child.

    Picks the FIRST non-fan_in child of each matching chain
    (deterministic — matches the OLD `_baseline_xor_child_pick`'s
    first-pick behavior for the AY.4.g minimum-viable scope; the
    fan_in / multi-pick variants land if AY.5 surfaces matview rows
    that need them).

    Account fields denormalize onto the child leg from the parent
    plant's account context — the matview keys on
    `(transfer_parent_id, child_name)`, NOT account columns, so any
    account is fine. The adapter threads the parent plant's account
    triple through.

    `intended` returns a CoverageObservation: "I planted a synthetic
    child leg satisfying chain X." No matching Invariant (the
    completion is a non-violating shape; coverage detector deferred).

    No-op when `parent_name` parents no chain (the common case —
    most parent plants don't sit on a chain-parent rail). The
    `emit` returns silently in that case.
    """

    parent_transfer_id: str
    parent_name: str
    account_id: str
    account_role: str
    account_scope: str
    account_parent_role: str | None
    anchor_day: date
    instance: L2Instance
    prefix: str = "spec_example"

    @property
    def intended(self) -> CoverageObservation:
        return CoverageObservation.of(
            "chain_completion",
            parent_transfer_id=self.parent_transfer_id,
            parent_name=self.parent_name,
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
                scenario_id, generator="ChainCompletionGenerator",
            )
            if scenario_id is not None else None
        )
        posting = ts(self.anchor_day, hour=12)
        rail_names = {str(r.name) for r in self.instance.rails}
        template_by_name = {
            str(t.name): t for t in self.instance.transfer_templates
        }

        emitted = 0
        for chain in self.instance.chains:
            if str(chain.parent) != self.parent_name:
                continue
            # Pick the first non-fan_in child deterministically.
            picked = None
            for child in chain.children:
                if child.fan_in:
                    continue
                picked = child
                break
            if picked is None:
                continue
            child_name = str(picked.name)
            # Resolve child to (rail_name_for_row, template_name_for_row).
            if child_name in rail_names:
                rail_for_row = child_name
                template_for_row: str | None = None
            else:
                template = template_by_name.get(child_name)
                if template is None or not template.leg_rails:
                    continue
                rail_for_row = str(template.leg_rails[0])
                template_for_row = child_name
            # Include the parent's transfer_id in the PK so two
            # different parent plants of the SAME chain don't collide
            # on the chainfill row's id (e.g., xor-missed + xor-overlap
            # both targeting MerchantSettlementCycle would otherwise
            # derive the same tx-chainfill-* string).
            insert_tx(
                conn,
                prefix=self.prefix,
                id=(
                    f"tx-chainfill-{self.parent_transfer_id}-"
                    f"{child_name}-{emitted}"
                ),
                account_id=self.account_id,
                account_name=(
                    f"Chain Completion ({self.parent_name})"
                ),
                account_role=self.account_role,
                account_scope=self.account_scope,
                account_parent_role=self.account_parent_role,
                amount_money=100.0,
                amount_direction="Credit",
                status="Posted",
                posting=posting,
                transfer_id=(
                    f"tr-chainfill-{self.parent_transfer_id}-"
                    f"{child_name}-{emitted}"
                ),
                transfer_parent_id=self.parent_transfer_id,
                rail_name=rail_for_row,
                template_name=template_for_row,
                origin="InternalInitiated",
                metadata=metadata,
            )
            emitted += 1


# Note: the emit path inlines L2 template resolution rather than
# breaking it out — keeps the generator self-contained + the only
# call site is `emit()`, so a helper would add no value.
