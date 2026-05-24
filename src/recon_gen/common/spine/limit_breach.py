"""Limit-Breach family — deepest L2 coupling of the L1 spine.

`LimitBreachInvariant` fires when per-(account, business_day, rail,
direction) `Σ ABS(amount_money)` exceeds the L2's `LimitSchedule.cap`
for that `(parent_role, rail, direction)` triple. The first spine
invariant whose smart constructor reads BOTH:

1. An L2 entity (the cap from LimitSchedule)
2. The plant's amount AS a function of the L2 value (cap + overshoot)

This is AP.3 finding #4's `from_instance` smart constructor — the
disproof of the "blind generator" hypothesis. The cap value itself is
a load-bearing input to the emission, not just a discovery target.

Per AU.3.b's TZ note: limit_breach's matview is **wall-clock-
agnostic** (groups by `DATE(posting)`, not `CURRENT_TIMESTAMP -
posting`). So the plant uses a static anchor day (2030-01-01 like
drift/overdraft/expected_eod) — no TZ-skew concerns.

Sign convention from the CHECK constraint on `<prefix>_transactions`:
- ``amount_direction='Debit'`` requires ``amount_money <= 0``
- ``amount_direction='Credit'`` requires ``amount_money >= 0``

The matview's `SUM(ABS(amount_money))` makes both contribute positively
to the per-direction total. So:
- Outbound limit (Debit) plant: ``amount_money = -(cap + overshoot)``
- Inbound limit (Credit) plant: ``amount_money = (cap + overshoot)``

Empirical-edge prediction (same as stuck_unbundled): Posted leg with
NO matching balance row doesn't trip drift (no JOIN match in
`_computed_subledger_balance`). Single-edge registry entry expected.
Test verifies.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import ClassVar

from recon_gen.common.l2.primitives import (
    Account, L2Instance, LimitDirection, LimitSchedule,
)
from recon_gen.common.spine._emit_helpers import (
    insert_tx,
    load_spec_example,
    to_date,
    ts,
)
from recon_gen.common.spine.violation import RuleViolation, Violation


@dataclass(frozen=True)
class LimitBreachInvariant:
    """Per-rail per-direction flow-cap detector. The matview gates on
    ``cap IS NOT NULL`` (rail+parent_role+direction has a LimitSchedule)
    AND ``SUM(ABS(amount_money)) > cap``. Identity is
    `(account_id, business_day, rail_name, direction)` — analyst-facing
    diff readability."""

    name: ClassVar[str] = "limit_breach"
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT account_id, business_day, rail_name, direction "
            f"FROM {self.prefix}_limit_breach",
        ).fetchall()
        return {
            RuleViolation.of(
                "limit_breach",
                account_id=str(aid),
                business_day=to_date(bd),
                rail_name=str(rn),
                direction=str(d),
            )
            for aid, bd, rn, d in rows
        }

    def scenario_for(
        self,
        parent_role: str,
        rail_name: str,
        *,
        direction: LimitDirection = "Outbound",
        overshoot: float = 100.0,
        instance: L2Instance | None = None,
        account_id: str | None = None,
    ) -> "LimitBreachGenerator":
        """Resolve `(parent_role, rail_name, direction)` against the L2's
        LimitSchedule; return a generator that plants ONE Posted
        transaction on a child account whose `account_parent_role =
        parent_role`, with amount_money sized to overshoot the cap.

        `overshoot=0.0` ⇒ amount == cap ⇒ matview's strict `>` filter
        excludes ⇒ no fire (AP.2 non-violating convention adapted to
        Money-unit knob). Positive fires.

        Raises `ValueError` if:
        - The L2 has no LimitSchedule matching `(parent_role, rail_name,
          direction)`
        - The L2 has no child account with `account_parent_role =
          parent_role` (the matview filters
          `account_parent_role IS NOT NULL`; without a matching child
          the plant is inert)

        AY.4.c — `account_id` overrides the default synthetic ID. The
        plant adapter (AY.4.c.3) threads OLD
        `LimitBreachPlant.account_id` through this kwarg so N plants on
        the same (parent_role, rail, direction) triple produce N
        distinct generators (the default
        `f"acct-limit-breach-{rail_name}-{direction}"` derivation would
        collide). Existing test callers can pass nothing → preserves
        the synthetic default byte-stable.

        Note: LimitBreachGenerator carries only one `account_id` field
        (the breaching account); there is no `counter_account_id` —
        the matview groups solely on `(account_id, business_day,
        rail_name, direction)`.
        """
        inst = instance if instance is not None else load_spec_example()
        schedule = _find_limit_schedule(
            inst, parent_role, rail_name, direction,
        )
        child = _find_child_with_parent_role(inst, parent_role)
        # _find_child_with_parent_role filters on parent_role IS NOT NULL
        # ⇒ the child is a leaf ⇒ has a role set (validator R-something).
        assert child.role is not None
        return LimitBreachGenerator(
            account_id=(
                account_id
                or f"acct-limit-breach-{rail_name}-{direction}"
            ),
            account_role=child.role,
            account_parent_role=parent_role,
            rail_name=rail_name,
            direction=direction,
            cap=float(schedule.cap),
            overshoot=overshoot,
            anchor_day=date(2030, 1, 1),
        )


@dataclass
class LimitBreachGenerator:
    """Emit a single Posted transaction whose `ABS(amount_money) = cap
    + overshoot` for the given (account, day, rail, direction). The
    matview's GROUP BY collapses to this single row → SUM = cap +
    overshoot > cap → limit_breach fires.

    Sign convention is locked by the transactions CHECK constraint —
    Debit ⇒ money ≤ 0; Credit ⇒ money ≥ 0. The matview's SUM(ABS) makes
    both contribute positively.
    """

    account_id: str
    account_role: str
    account_parent_role: str
    rail_name: str
    direction: LimitDirection
    cap: float
    overshoot: float
    anchor_day: date

    @property
    def intended(self) -> RuleViolation:
        return RuleViolation.of(
            "limit_breach",
            account_id=self.account_id,
            business_day=self.anchor_day,
            rail_name=self.rail_name,
            direction=self.direction,
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        """The single account_id this plant breaches a cap on. AV.5."""
        return frozenset({self.account_id})

    def emit(
        self,
        conn: sqlite3.Connection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        metadata = (
            scenario_metadata(scenario_id, generator="LimitBreachGenerator")
            if scenario_id is not None else None
        )
        amount_magnitude = self.cap + self.overshoot
        if self.direction == "Outbound":
            amount_direction = "Debit"
            amount_money = -amount_magnitude
        else:  # Inbound
            amount_direction = "Credit"
            amount_money = amount_magnitude
        insert_tx(
            conn,
            id=f"tx-limit-breach-{self.rail_name}-{self.direction}-{self.account_id}",
            account_id=self.account_id,
            account_name=f"Limit Breach ({self.rail_name} {self.direction})",
            account_role=self.account_role,
            account_scope="internal",
            account_parent_role=self.account_parent_role,
            amount_money=amount_money,
            amount_direction=amount_direction,
            status="Posted",
            posting=ts(self.anchor_day),
            transfer_id=f"xfer-limit-breach-{self.rail_name}-{self.direction}-{self.account_id}",
            rail_name=self.rail_name,
            origin="etl",
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Limit-breach-specific finders — per-invariant-shape, no duplication
# burden. Shared helpers live in `common/spine/_emit_helpers.py`
# post-AU.3.d.
# ---------------------------------------------------------------------------


def _find_limit_schedule(
    instance: L2Instance,
    parent_role: str,
    rail_name: str,
    direction: LimitDirection,
) -> LimitSchedule:
    """Return the LimitSchedule for the given (parent_role, rail,
    direction) triple. Raises ValueError if none matches — the matview's
    `cap IS NOT NULL` filter would exclude an uncovered (account, rail,
    direction) tuple, so a scenario against an uncovered combo would
    silently inert; we refuse instead."""
    for ls in instance.limit_schedules:
        if (
            ls.parent_role == parent_role
            and ls.rail == rail_name
            and ls.direction == direction
        ):
            return ls
    raise ValueError(
        f"no LimitSchedule matches (parent_role={parent_role!r}, "
        f"rail={rail_name!r}, direction={direction!r}); cannot "
        f"manufacture a limit_breach scenario"
    )


def _find_child_with_parent_role(instance: L2Instance, parent_role: str) -> Account:
    """Return any internal account whose `parent_role` is `parent_role`.
    Raises ValueError if none — the matview filters
    `account_parent_role IS NOT NULL`, and the plant needs to land on a
    real child role from the shape."""
    for a in instance.accounts:
        if a.scope == "internal" and a.parent_role == parent_role:
            return a
    raise ValueError(
        f"no internal child account with parent_role={parent_role!r}; "
        f"cannot manufacture a limit_breach scenario"
    )
