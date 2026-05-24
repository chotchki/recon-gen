"""Multi-XOR-violation family — `Invariant` + 2 `ViolationGenerator`s.

AX.4 promotion of the AB.6.6 plants. The matview
`<prefix>_multi_xor_violation` walks every Transfer firing of any
chain that declares ≥2 non-fan_in children (multi-XOR-child chains:
the parent fires once; the chain declares N XOR-sibling children;
exactly ONE child should fire). It LEFT JOINs `_current_transactions`
against the declared XOR-sibling children (matched on
`transfer_parent_id = parent.transfer_id` AND
`rail_name OR template_name = child.name`) and surfaces rows where
`COUNT(matched_child) <> 1`:

  - **missed**: count = 0 → the parent fired but no XOR-sibling child
    did.
  - **overlap**: count ≥ 2 → the parent fired and ≥2 XOR-sibling
    children both fired.

Identity tuple: `(parent_transfer_id, disagreement_kind)`.

Two generators (analogous to AX.2 xor_group) because the emit shapes
differ: missed emits 1 parent only; overlap emits 1 parent + 2
child firings. Single invariant; two generators register as separate
edges in `INVARIANT_GENERATOR_EDGES`.

Single-edge property: transfers-only emit (no daily_balances rows)
→ no drift trip. Note that overlap may trip
`fan_in_disagreement` as a cross-class side effect if the chain has
fan_in entries (the AB.5 coupling) — extras tolerated per AS.5.

The matview uses `concat_agg("fcd.matched_child_name", ",", dialect)`
for the `fired_children` column; AX.0 confirmed SQLite's built-in
`GROUP_CONCAT` routes cleanly.
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
from recon_gen.common.spine.violation import Violation


@dataclass(frozen=True)
class MultiXorViolationInvariant:
    """Detector for the AB.6.5 matview.

    Identity tuple: `(parent_transfer_id, disagreement_kind)`. The
    matview's other columns (`parent_rail_or_template_name`,
    `child_count`, `fired_children`, `business_day`) are diagnostic.
    """

    name: ClassVar[str] = "multi_xor_violation"
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT parent_transfer_id, disagreement_kind "
            f"FROM {self.prefix}_multi_xor_violation",
        ).fetchall()
        return {
            Violation.of(
                "multi_xor_violation",
                parent_transfer_id=str(ptid),
                disagreement_kind=str(kind),
            )
            for ptid, kind in rows
        }

    def scenario_for_missed(
        self,
        *,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "MultiXorMissedGenerator":
        """Pick a chain with ≥2 non-fan_in children + return a
        generator that plants ONE parent firing with NO child
        firings — matview reads count=0, surfaces 'missed' row."""
        inst = instance if instance is not None else load_spec_example()
        chain_parent, _child_a, _child_b = _pick_or_raise(inst, kind="missed")
        return MultiXorMissedGenerator(
            chain_parent_name=str(chain_parent),
            anchor_day=anchor_day,
            instance=inst,
            prefix=self.prefix,
        )

    def scenario_for_overlap(
        self,
        *,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "MultiXorOverlapGenerator":
        """Pick a chain with ≥2 non-fan_in children + return a
        generator that plants ONE parent firing + TWO child firings
        (both XOR-siblings; both linked to the parent via
        transfer_parent_id) — matview reads count=2, surfaces
        'overlap' row."""
        inst = instance if instance is not None else load_spec_example()
        chain_parent, child_a, child_b = _pick_or_raise(inst, kind="overlap")
        return MultiXorOverlapGenerator(
            chain_parent_name=str(chain_parent),
            variant_a_child_name=str(child_a),
            variant_b_child_name=str(child_b),
            anchor_day=anchor_day,
            instance=inst,
            prefix=self.prefix,
        )


def _pick_or_raise(
    instance: L2Instance, *, kind: str,
) -> tuple[str, str, str]:
    """Wrap the AB.6.6 picker + raise a clear error on failure."""
    from recon_gen.common.l2.auto_scenario import (
        _pick_multi_xor_chain_inputs,
    )
    pick = _pick_multi_xor_chain_inputs(instance)
    if pick is None:
        raise ValueError(
            f"shape has no chain with ≥2 non-fan_in children and a "
            f"known Rail or Template parent (AB.6.6 picker rejected); "
            f"cannot manufacture a multi_xor_violation {kind} scenario"
        )
    return (str(pick[0]), str(pick[1]), str(pick[2]))


def _resolve_chain_parent(
    name: str, instance: L2Instance,
) -> tuple[str, str | None]:
    """Resolve a chain.parent identifier to the
    (rail_name_for_emit, template_name_for_emit) pair.

    Rail parent → (name, None); Template parent → (template.leg_rails[0],
    template.name). Mirrors `seed.py::_resolve_plant_chain_parent`
    but local to the spine module — the spine doesn't reach into
    seed.py's helpers.
    """
    for r in instance.rails:
        if str(r.name) == name:
            return (name, None)
    for t in instance.transfer_templates:
        if str(t.name) == name and t.leg_rails:
            return (str(t.leg_rails[0]), name)
    raise ValueError(
        f"chain parent {name!r} resolves to neither a Rail nor a "
        f"Template with leg_rails; picker should have rejected this"
    )


def _classify_child(
    name: str, instance: L2Instance,
) -> tuple[str | None, str | None]:
    """Resolve a chain-child identifier to (rail_name_for_emit,
    template_name_for_emit). Rail child → (name, None); Template
    child → (template.leg_rails[0], name). Raises ``ValueError`` if
    name resolves to neither."""
    rail_names = {str(r.name) for r in instance.rails}
    if name in rail_names:
        return (name, None)
    for t in instance.transfer_templates:
        if str(t.name) == name and t.leg_rails:
            return (str(t.leg_rails[0]), name)
    raise ValueError(
        f"chain child {name!r} resolves to neither a Rail nor a "
        f"Template with leg_rails; picker should have rejected this"
    )


@dataclass
class MultiXorMissedGenerator:
    """Plant a parent Transfer with NO declared XOR-sibling children
    firing. Matview's `child_count = 0` → 'missed' row.

    Emits ONE leg row for the parent: rail_name stamps the
    chain.parent (or the template's first leg_rail when the parent
    is a Template); template_name stamps the parent template name
    when applicable.

    AY.4.c.2 — account_id_override allows the plant adapter
    (AY.4.c.3) to thread OLD plant account_ids through, preventing
    PK collisions when N plants of the same shape compose.
    """

    chain_parent_name: str
    anchor_day: date
    instance: L2Instance | None = None
    prefix: str = "spec_example"
    account_id_override: str | None = None

    @property
    def parent_transfer_id(self) -> str:
        return f"tr-mxor-missed-{self.chain_parent_name}"

    @property
    def account_id(self) -> str:
        """``account_id_override`` wins when set (AY.4.c.2)."""
        if self.account_id_override is not None:
            return self.account_id_override
        return f"acct-mxor-missed-{self.chain_parent_name}"

    @property
    def intended(self) -> Violation:
        return Violation.of(
            "multi_xor_violation",
            parent_transfer_id=self.parent_transfer_id,
            disagreement_kind="missed",
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
                scenario_id, generator="MultiXorMissedGenerator",
            )
            if scenario_id is not None else None
        )
        inst = self.instance if self.instance is not None else load_spec_example()
        rail_name, template_name = _resolve_chain_parent(
            self.chain_parent_name, inst,
        )
        insert_tx(
            conn,
            prefix=self.prefix,
            id=f"tx-mxor-missed-{self.chain_parent_name}",
            account_id=self.account_id,
            account_name=f"Multi-XOR Missed ({self.chain_parent_name})",
            account_role="CustomerSubledger",
            account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=100.0,
            amount_direction="Credit",
            status="Posted",
            posting=ts(self.anchor_day),
            transfer_id=self.parent_transfer_id,
            rail_name=rail_name,
            template_name=template_name,
            origin="InternalInitiated",
            metadata=metadata,
        )


@dataclass
class MultiXorOverlapGenerator:
    """Plant a parent Transfer + TWO child firings (both XOR-siblings;
    both linked to the parent via transfer_parent_id). Matview's
    `child_count = 2` → 'overlap' row.

    Emits 3 leg rows: 1 parent + 2 children. Each child row's
    `rail_name`/`template_name` matches the chain's declared
    XOR-sibling child name (rail vs template kind resolved at emit
    time).

    AY.4.c.2 — account_id_override allows the plant adapter
    (AY.4.c.3) to thread OLD plant account_ids through, preventing
    PK collisions when N plants of the same shape compose.
    """

    chain_parent_name: str
    variant_a_child_name: str
    variant_b_child_name: str
    anchor_day: date
    instance: L2Instance | None = None
    prefix: str = "spec_example"
    account_id_override: str | None = None

    @property
    def parent_transfer_id(self) -> str:
        return f"tr-mxor-overlap-{self.chain_parent_name}"

    @property
    def account_id(self) -> str:
        """``account_id_override`` wins when set (AY.4.c.2)."""
        if self.account_id_override is not None:
            return self.account_id_override
        return f"acct-mxor-overlap-{self.chain_parent_name}"

    @property
    def intended(self) -> Violation:
        return Violation.of(
            "multi_xor_violation",
            parent_transfer_id=self.parent_transfer_id,
            disagreement_kind="overlap",
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
                scenario_id, generator="MultiXorOverlapGenerator",
            )
            if scenario_id is not None else None
        )
        inst = self.instance if self.instance is not None else load_spec_example()
        parent_rail, parent_template = _resolve_chain_parent(
            self.chain_parent_name, inst,
        )
        # Parent firing.
        insert_tx(
            conn,
            prefix=self.prefix,
            id=f"tx-mxor-overlap-{self.chain_parent_name}-p",
            account_id=self.account_id,
            account_name=f"Multi-XOR Overlap parent ({self.chain_parent_name})",
            account_role="CustomerSubledger",
            account_scope="internal",
            account_parent_role="CustomerLedger",
            amount_money=100.0,
            amount_direction="Credit",
            status="Posted",
            posting=ts(self.anchor_day, hour=12),
            transfer_id=self.parent_transfer_id,
            rail_name=parent_rail,
            template_name=parent_template,
            origin="InternalInitiated",
            metadata=metadata,
        )
        # Two child firings; both reference the parent via
        # transfer_parent_id. Each carries its own rail_name (and
        # template_name when the child is a TransferTemplate).
        for suffix, child_name in (
            ("a", self.variant_a_child_name),
            ("b", self.variant_b_child_name),
        ):
            rail_name, template_name = _classify_child(child_name, inst)
            insert_tx(
                conn,
                prefix=self.prefix,
                id=f"tx-mxor-overlap-{self.chain_parent_name}-{suffix}",
                account_id=self.account_id,
                account_name=(
                    f"Multi-XOR Overlap child {suffix} "
                    f"({child_name})"
                ),
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
                amount_money=100.0,
                amount_direction="Credit",
                status="Posted",
                posting=ts(self.anchor_day, hour=13),
                transfer_id=(
                    f"tr-mxor-overlap-{self.chain_parent_name}-{suffix}"
                ),
                transfer_parent_id=self.parent_transfer_id,
                rail_name=rail_name,
                template_name=template_name,
                origin="InternalInitiated",
                metadata=metadata,
            )
