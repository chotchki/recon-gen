"""Fan-in-disagreement family — `Invariant` + `ViolationGenerator`.

AX.3 promotion of the AB.4.5 / AB.4.5 family of plants (healthy /
missing-parent / extra-parent). The matview
`<prefix>_fan_in_disagreement` checks every child Transfer of every
chain whose child declares `fan_in=True` against the expected parent
count and surfaces rows where `parent_count != expected`:

  - **orphan**: `expected_parent_count IS NULL` (variable-batch) AND
    `parent_count < 2` — a fan_in child should batch ≥2 parents.
  - **missing**: `expected_parent_count IS NOT NULL` AND
    `parent_count < expected`.
  - **extra**: `expected_parent_count IS NOT NULL` AND
    `parent_count > expected`.

Identity tuple: `(child_transfer_id, disagreement_kind)`.

Single generator (all 3 variants share the emit shape: N parent
legs + 1 child Transfer whose legs each carry a contributing parent's
`transfer_parent_id`; only N differs). Three smart constructors —
`scenario_for_healthy` / `scenario_for_missing_parent` /
`scenario_for_extra_parent` — pick a fan_in chain from the L2 and
set the `parent_count` knob appropriately. The healthy constructor
returns a generator whose emit produces NO matview row (the AP.2
non-violating convention).

The matview reads `expected_parent_count` from the L2 yaml's inline
`fan_in_chains` CTE; the generator queries the L2 to get the expected
value when constructing the missing/extra variants. When expected is
NULL on the L2 chain (variable-batch case), missing falls through
to 'orphan' and extra is undefined (the generator raises).

Single-edge property: transfers-only emit → no daily_balances rows →
no drift trip.
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
class FanInDisagreementInvariant:
    """Detector for the AB.4.7 matview.

    Identity tuple: `(child_transfer_id, disagreement_kind)`. Other
    columns (`chain_parent_name`, `child_template_name`,
    `parent_count`, `expected_parent_count`, `business_day`) are
    diagnostic.
    """

    name: ClassVar[str] = "fan_in_disagreement"
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT child_transfer_id, disagreement_kind "
            f"FROM {self.prefix}_fan_in_disagreement",
        ).fetchall()
        return {
            Violation.of(
                "fan_in_disagreement",
                child_transfer_id=str(ctid),
                disagreement_kind=str(kind),
            )
            for ctid, kind in rows
        }

    def scenario_for_healthy(
        self,
        *,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "FanInChainGenerator":
        """Healthy non-violating shape per AP.2: parent_count ==
        expected (when set; defaults to 2 for variable-batch).
        Emit produces NO matview row — the AP.2 convention's
        positive-control for the test suite."""
        pick, expected = self._pick(instance, kind="healthy")
        chain_parent, child_template, expected_count = pick
        parent_count = expected if expected is not None else 2
        return FanInChainGenerator(
            chain_parent_name=str(chain_parent),
            child_template_name=str(child_template),
            expected_parent_count=expected_count,
            parent_count=parent_count,
            anchor_day=anchor_day,
            expected_kind="healthy",
            prefix=self.prefix,
        )

    def scenario_for_missing_parent(
        self,
        *,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "FanInChainGenerator":
        """Missing-parent shape: parent_count = expected - 1 (when
        expected is set) OR parent_count = 1 (orphan, when expected
        is unset on the L2 chain — variable-batch case where ≥2 is
        the implicit floor).

        Disagreement_kind on the matview row: 'missing' when expected
        is set; 'orphan' when expected is unset and parent_count < 2.
        """
        pick, expected = self._pick(instance, kind="missing")
        chain_parent, child_template, expected_count = pick
        if expected is not None:
            parent_count = max(expected - 1, 1)
            expected_kind = "missing"
        else:
            parent_count = 1  # orphan: variable-batch with <2
            expected_kind = "orphan"
        return FanInChainGenerator(
            chain_parent_name=str(chain_parent),
            child_template_name=str(child_template),
            expected_parent_count=expected_count,
            parent_count=parent_count,
            anchor_day=anchor_day,
            expected_kind=expected_kind,
            prefix=self.prefix,
        )

    def scenario_for_extra_parent(
        self,
        *,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "FanInChainGenerator":
        """Extra-parent shape: parent_count = expected + 1. Only
        defined when expected is set on the L2 chain — the matview's
        'extra' branch gates on `expected IS NOT NULL`.

        Raises `ValueError` if the picked chain has no
        `expected_parent_count` (the variable-batch case has no upper
        bound to exceed)."""
        pick, expected = self._pick(instance, kind="extra")
        chain_parent, child_template, expected_count = pick
        if expected is None:
            raise ValueError(
                f"chain {chain_parent}→{child_template} has no "
                f"expected_parent_count; the matview's 'extra' branch "
                f"requires expected to be set"
            )
        parent_count = expected + 1
        return FanInChainGenerator(
            chain_parent_name=str(chain_parent),
            child_template_name=str(child_template),
            expected_parent_count=expected_count,
            parent_count=parent_count,
            anchor_day=anchor_day,
            expected_kind="extra",
            prefix=self.prefix,
        )

    def _pick(
        self, instance: L2Instance | None, *, kind: str,
    ) -> tuple[tuple[str, str, int | None], int | None]:
        """Wrap the AB.4.5 picker + surface the expected count for the
        smart constructors. ``kind`` is used in the error message so
        the operator knows which scenario flavor failed."""
        inst = instance if instance is not None else load_spec_example()
        from recon_gen.common.l2.auto_scenario import (
            _pick_fan_in_chain_inputs,
        )
        pick = _pick_fan_in_chain_inputs(inst)
        if pick is None:
            raise ValueError(
                f"shape has no chain declaring fan_in=True with a "
                f"known Rail or Template parent (AB.4.5 picker "
                f"rejected); cannot manufacture a fan_in_disagreement "
                f"{kind} scenario"
            )
        return (
            (str(pick[0]), str(pick[1]), pick[2]),
            pick[2],
        )


@dataclass
class FanInChainGenerator:
    """Emit `parent_count` synthetic parent legs + 1 child Transfer
    whose legs each carry a contributing parent's
    `transfer_parent_id`. The matview reads `COUNT(DISTINCT
    parent_transfer_id)` for the child and compares to the L2's
    declared `expected_parent_count` to derive the
    `disagreement_kind` row (or no row, in the healthy case).

    Account fields are synthetic — the matview doesn't filter on
    them. The `parent_count` knob differentiates the 3 variants:
    healthy (== expected), missing (< expected), extra (> expected).

    Single-edge: transfers-only → no balance rows → no drift trip.

    AY.4.c.2 — account_id_override allows the plant adapter
    (AY.4.c.3) to thread OLD plant account_ids through, preventing
    PK collisions when N plants of the same shape compose.
    """

    chain_parent_name: str
    child_template_name: str
    expected_parent_count: int | None
    parent_count: int
    anchor_day: date
    expected_kind: str  # 'healthy' | 'missing' | 'orphan' | 'extra'
    prefix: str = "spec_example"
    account_id_override: str | None = None

    @property
    def child_transfer_id(self) -> str:
        return f"tr-fanin-{self.expected_kind}-{self.child_template_name}"

    @property
    def account_id(self) -> str:
        """Derivation keys off ``expected_kind`` + ``child_template_name``
        (variant kind matters here — healthy/missing/orphan/extra each
        get a distinct account so a compose of multiple variants on
        the same chain doesn't PK-collide). ``account_id_override``
        wins when set (AY.4.c.2)."""
        if self.account_id_override is not None:
            return self.account_id_override
        return f"acct-fanin-{self.expected_kind}-{self.child_template_name}"

    @property
    def intended(self) -> Violation | None:
        """The matview row this plant triggers, or `None` for the
        healthy shape (parent_count == expected → no row)."""
        if self.expected_kind == "healthy":
            return None
        return Violation.of(
            "fan_in_disagreement",
            child_transfer_id=self.child_transfer_id,
            disagreement_kind=self.expected_kind,
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
                scenario_id, generator="FanInChainGenerator",
            )
            if scenario_id is not None else None
        )
        parent_posting = ts(self.anchor_day, hour=10)
        child_posting = ts(self.anchor_day, hour=11)

        # Emit `parent_count` synthetic parent legs.
        parent_ids: list[str] = []
        for k in range(self.parent_count):
            parent_tid = (
                f"tr-fanin-{self.expected_kind}-"
                f"{self.child_template_name}-parent-{k}"
            )
            parent_ids.append(parent_tid)
            insert_tx(
                conn,
                prefix=self.prefix,
                id=(
                    f"tx-fanin-{self.expected_kind}-"
                    f"{self.child_template_name}-parent-{k}"
                ),
                account_id=self.account_id,
                account_name=f"Fan-In {self.expected_kind} parent {k}",
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
                amount_money=-100.0,
                amount_direction="Debit",
                status="Posted",
                posting=parent_posting,
                transfer_id=parent_tid,
                rail_name=self.chain_parent_name,
                template_name=self.chain_parent_name,
                origin="InternalInitiated",
                metadata=metadata,
            )

        # Emit child Transfer's leg rows, each tagged with a different
        # parent's transfer_parent_id so the _transfer_parents matview
        # reads `parent_count` DISTINCT contributors for this child.
        # All legs share the single child_transfer_id (the fan-in shape).
        # The fan_in matview only cares about the count of distinct
        # parent_transfer_id; rail_name choice doesn't matter for
        # detection, so we use the child template name as the rail.
        for k, parent_tid in enumerate(parent_ids):
            insert_tx(
                conn,
                prefix=self.prefix,
                id=(
                    f"tx-fanin-{self.expected_kind}-"
                    f"{self.child_template_name}-child-{k}"
                ),
                account_id=self.account_id,
                account_name=f"Fan-In {self.expected_kind} child leg {k}",
                account_role="CustomerSubledger",
                account_scope="internal",
                account_parent_role="CustomerLedger",
                amount_money=50.0,
                amount_direction="Credit",
                status="Posted",
                posting=child_posting,
                transfer_id=self.child_transfer_id,
                transfer_parent_id=parent_tid,
                rail_name=self.child_template_name,
                template_name=self.child_template_name,
                origin="InternalInitiated",
                metadata=metadata,
            )
