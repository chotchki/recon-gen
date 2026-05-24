"""XOR-group-violation family — `Invariant` + 2 `ViolationGenerator`s.

AX.2 promotion of the AB.3.5 / AB.3.5b plants. The matview
`<prefix>_xor_group_violation` walks every Transfer instance of every
template that declares `leg_rail_xor_groups`, for each (Transfer,
group) pair LEFT JOINs `_current_transactions` against the group's
member rails, and surfaces rows where `COUNT <> 1`:

  - **firing_count = 0** → "missed firing": the Transfer should have
    fired exactly one member of the XOR group; it fired none.
  - **firing_count ≥ 2** → "overlap": the Transfer fired ≥2 members
    of the XOR group; SPEC C1 says they're mutually exclusive.

Identity tuple: `(transfer_id, template_name, xor_group_index)`.

Two generators because the emit shape is genuinely different per
failure mode (missed: 1 witness leg outside the group; overlap: 2
member legs both inside the group). Single invariant; two generators
register as separate edges in `INVARIANT_GENERATOR_EDGES`.

Single-edge property: transfers-only emit (no daily_balances rows) →
no drift trip. Both generators do NOT emit chain-completion rows —
extras the SOLO emission might trip on other matviews (e.g., a
template that's also a multi_xor chain parent could trip
`multi_xor_violation` from the same plant) are tolerated per AS.5's
"intended ⊆ detected" contract. Composition with the multi_xor
generator clears those extras at the wiring site.

The matview uses `concat_agg("tx.rail_name", ",", dialect)` for the
`fired_rails` column; AX.0 confirmed SQLite's built-in
`GROUP_CONCAT` routes cleanly through `common/sql/dialect.py`'s
helper.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import ClassVar

from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.spine._emit_helpers import (
    insert_tx,
    load_spec_example,
    ts,
)
from recon_gen.common.spine.violation import RuleViolation, Violation


@dataclass(frozen=True)
class XorGroupViolationInvariant:
    """Detector for the AB.3.3 matview.

    Identity tuple: `(transfer_id, template_name, xor_group_index)`.
    The matview's other columns (`firing_count`, `fired_rails`,
    `business_day`) are diagnostic — they distinguish missed (0)
    from overlap (≥2) but the identity-level "did this XOR group
    misfire" is what the spine carries.
    """

    name: ClassVar[str] = "xor_group_violation"
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT transfer_id, template_name, xor_group_index "
            f"FROM {self.prefix}_xor_group_violation",
        ).fetchall()
        return {
            RuleViolation.of(
                "xor_group_violation",
                transfer_id=str(tid),
                template_name=str(tname),
                xor_group_index=int(gidx),
            )
            for tid, tname, gidx in rows
        }

    def scenario_for_missed(
        self,
        *,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "XorGroupMissedFiringGenerator":
        """Pick an XOR-grouped template with ≥1 leg_rail outside the
        target group (the witness rail). Returns a generator that
        plants ONE Transfer firing the witness — firing_count=0 for
        the target group, matview surfaces a row.

        Raises `ValueError` if no template has both an XOR group AND
        a witness leg_rail outside it (the AB.3.5 picker's input
        requirement)."""
        inst = instance if instance is not None else load_spec_example()
        from recon_gen.common.l2.auto_scenario import (
            _pick_xor_missed_firing_inputs,
        )
        pick = _pick_xor_missed_firing_inputs(inst)
        if pick is None:
            raise ValueError(
                "shape has no template declaring `leg_rail_xor_groups` "
                "AND a non-XOR-group leg_rail to use as witness "
                "(AB.3.5 picker rejected); cannot manufacture an "
                "xor_group_violation missed-firing scenario"
            )
        template_name, group_index, witness_rail = pick
        return XorGroupMissedFiringGenerator(
            template_name=str(template_name),
            xor_group_index=int(group_index),
            witness_rail_name=str(witness_rail),
            anchor_day=anchor_day,
        )

    def scenario_for_overlap(
        self,
        *,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "XorGroupOverlapGenerator":
        """Pick an XOR group with ≥2 members (validator C1d enforces
        the ≥2 condition at load time). Returns a generator that
        plants ONE Transfer firing TWO members of the same XOR group
        — firing_count=2, matview surfaces a row.

        Raises `ValueError` if no template declares any
        `leg_rail_xor_groups`."""
        inst = instance if instance is not None else load_spec_example()
        from recon_gen.common.l2.auto_scenario import (
            _pick_xor_overlap_inputs,
        )
        pick = _pick_xor_overlap_inputs(inst)
        if pick is None:
            raise ValueError(
                "shape has no template declaring `leg_rail_xor_groups` "
                "(AB.3.5b picker rejected); cannot manufacture an "
                "xor_group_violation overlap scenario"
            )
        template_name, group_index, variant_a, variant_b = pick
        return XorGroupOverlapGenerator(
            template_name=str(template_name),
            xor_group_index=int(group_index),
            variant_a_rail_name=str(variant_a),
            variant_b_rail_name=str(variant_b),
            anchor_day=anchor_day,
        )


@dataclass
class XorGroupMissedFiringGenerator:
    """Plant a Transfer tagged with `template_name` whose target XOR
    group fires NO members.

    Emits ONE leg row carrying the template_name + a witness rail_name
    that is a leg_rail of the template but NOT in the target XOR group.
    The matview's `template_transfers` CTE picks up the Transfer
    (template_name matches an XOR-grouped template); the LEFT JOIN
    against `(transfer_id, template, member_rail)` for the target
    group finds zero rows; `firing_count = 0`; the `HAVING <> 1` gate
    surfaces the row with `fired_rails = ''`.
    """

    template_name: str
    xor_group_index: int
    witness_rail_name: str
    anchor_day: date
    prefix: str = "spec_example"

    @property
    def transfer_id(self) -> str:
        return f"tr-xor-missed-{self.template_name}-{self.xor_group_index}"

    @property
    def account_id(self) -> str:
        return f"acct-xor-missed-{self.template_name}"

    @property
    def intended(self) -> RuleViolation:
        return RuleViolation.of(
            "xor_group_violation",
            transfer_id=self.transfer_id,
            template_name=self.template_name,
            xor_group_index=self.xor_group_index,
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
                scenario_id, generator="XorGroupMissedFiringGenerator",
            )
            if scenario_id is not None else None
        )
        insert_tx(
            conn,
            prefix=self.prefix,
            id=f"tx-xor-missed-{self.template_name}-{self.xor_group_index}",
            account_id=self.account_id,
            account_name=f"XOR Missed ({self.template_name})",
            account_role="CustomerSubledger",
            account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=100.0,
            amount_direction="Credit",
            status="Posted",
            posting=ts(self.anchor_day),
            transfer_id=self.transfer_id,
            rail_name=self.witness_rail_name,
            template_name=self.template_name,
            origin="InternalInitiated",
            metadata=metadata,
        )


@dataclass
class XorGroupOverlapGenerator:
    """Plant a Transfer tagged with `template_name` whose target XOR
    group fires TWO distinct members.

    Emits TWO leg rows sharing one `transfer_id` + `template_name`,
    each carrying a different member rail_name from the target XOR
    group. The matview's LEFT JOIN finds two member-rail firings for
    `(transfer_id, template, target_group)` → `COUNT = 2` → `HAVING
    <> 1` → row surfaces with `fired_rails = '<a>,<b>'`.
    """

    template_name: str
    xor_group_index: int
    variant_a_rail_name: str
    variant_b_rail_name: str
    anchor_day: date
    prefix: str = "spec_example"

    @property
    def transfer_id(self) -> str:
        return f"tr-xor-overlap-{self.template_name}-{self.xor_group_index}"

    @property
    def account_id(self) -> str:
        return f"acct-xor-overlap-{self.template_name}"

    @property
    def intended(self) -> RuleViolation:
        return RuleViolation.of(
            "xor_group_violation",
            transfer_id=self.transfer_id,
            template_name=self.template_name,
            xor_group_index=self.xor_group_index,
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
                scenario_id, generator="XorGroupOverlapGenerator",
            )
            if scenario_id is not None else None
        )
        posting = ts(self.anchor_day)
        for i, variant in enumerate((
            self.variant_a_rail_name, self.variant_b_rail_name,
        )):
            insert_tx(
                conn,
                prefix=self.prefix,
                id=(
                    f"tx-xor-overlap-{self.template_name}-"
                    f"{self.xor_group_index}-{i}"
                ),
                account_id=self.account_id,
                account_name=f"XOR Overlap ({self.template_name})",
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
                amount_money=100.0,
                amount_direction="Credit",
                status="Posted",
                posting=posting,
                transfer_id=self.transfer_id,
                rail_name=variant,
                template_name=self.template_name,
                origin="InternalInitiated",
                metadata=metadata,
            )
