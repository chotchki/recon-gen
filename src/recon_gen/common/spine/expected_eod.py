"""Expected-EOD-balance family — `Invariant` + `ViolationGenerator`.

`ExpectedEodBalanceInvariant` fires when a daily_balances row has a
non-null `expected_eod_balance` AND `money ≠ expected_eod_balance`. The
matview is a one-line variance check on
``<prefix>_current_daily_balances``; like overdraft, no leg arithmetic,
no parent dependency, no role join.

Per AU.0/AU.2 lessons (audit §5 "AU.2 result"):

- **Many-to-many edges are universal.** A plant on a LEAF internal
  account satisfies drift's matview filter
  (``parent_role IS NOT NULL AND stored ≠ Σ legs``: leaf has parent_role;
  emission has zero transactions ⇒ Σ legs = 0; planted ``money`` is
  ``expected + variance``, so drift = stored − 0 = expected + variance ≠
  0). So `(ExpectedEodBalanceInvariant, DriftInvariant)` is the
  registered edge tuple — same shape as overdraft's two-edge entry.
- **Lone parent plants are single-edge.** A plant on a parent role (e.g.
  CustomerLedger) trips ONLY this invariant; `_computed_ledger_balance`
  requires children to exist (the EXISTS gate). ledger_drift fires only
  in COMPOSITION scenarios where another generator supplies the
  children.

What this module deliberately does NOT carry: an `rng` field on the
generator (deterministic single-row plant; same as overdraft). The
helpers stay module-private for now — AU.3 will hoist once the third
balance-only invariant lands and the duplication becomes painful.
"""

from __future__ import annotations

from recon_gen.common.db import SyncConnection
from dataclasses import dataclass
from datetime import date
from typing import ClassVar

from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.money import Cents
from recon_gen.common.spine._db import fetch_all
from recon_gen.common.spine._emit_helpers import (
    day_bounds,
    find_internal_with_role,
    insert_balance,
    load_spec_example,
    to_date,
)
from recon_gen.common.spine.residuals import (
    BalanceRow,
    ResidualState,
    drift_residual,
    expected_eod_residual,
)
from recon_gen.common.spine.violation import RuleViolation, Violation, identity_dollars


@dataclass(frozen=True)
class ExpectedEodBalanceInvariant:
    """Expected-EOD-balance detector. Persona-blind — the matview SQL
    filters only on the per-row `expected_eod_balance` column being set
    and not matching `money`. `scenario_for(role)` accepts ANY internal
    account with the requested role (no leaf/parent filter)."""

    name: ClassVar[str] = "expected_eod_balance_breach"
    prefix: str = "spec_example"

    def detect(self, conn: SyncConnection) -> set[Violation]:
        rows = fetch_all(
            conn,
            f"SELECT account_id, business_day_start, variance "
            f"FROM {self.prefix}_expected_eod_balance_breach",
        )
        # AO.1: variance is BIGINT cents — see overdraft.detect note.
        return {
            RuleViolation.of(
                "expected_eod_balance_breach",
                account_id=aid,
                business_day=to_date(bds),
                variance=round(
                    float(Cents.from_db(int(var)).to_dollars()), 2,
                ),
            )
            for aid, bds, var in rows
        }

    def scenario_for(
        self,
        role: str,
        *,
        expected: float = 100.0,
        variance: float = 5.0,
        instance: L2Instance | None = None,
        account_id: str | None = None,
    ) -> "ExpectedEodBalanceGenerator":
        """Resolve a role; return a generator that plants
        ``money = expected + variance`` with the per-row
        ``expected_eod_balance`` set, so the variance row materializes.

        ``variance=0.0`` is the non-violating shape (stored ==
        expected ⇒ the matview row is filtered out). Same AP.2
        convention as overdraft / drift.

        Raises `ValueError` if the L2 has no internal account with the
        requested role.

        AY.4.c — `account_id` overrides the default synthetic ID. The
        plant adapter (AY.4.c.3) threads OLD
        `ExpectedEodBalancePlant.account_id` through this kwarg so N
        plants on the same role produce N distinct generators (the
        default `f"acct-eod-{role}"` derivation would collide). Existing
        test callers can pass nothing → preserves the synthetic default
        byte-stable.
        """
        inst = instance if instance is not None else load_spec_example()
        acct = find_internal_with_role(inst, role, error_kind="expected-EOD")
        return ExpectedEodBalanceGenerator(
            account_id=account_id or f"acct-eod-{role}",
            account_role=role,
            account_parent_role=acct.parent_role,
            anchor_day=date(2030, 1, 1),
            expected=expected,
            variance=variance,
        )


@dataclass
class ExpectedEodBalanceGenerator:
    """Emit a daily_balances row whose ``money`` is the configured
    ``expected`` ± ``variance``, with ``expected_eod_balance = expected``.
    NO transactions — the variance matview reads daily_balances directly.

    ``variance=0.0`` ⇒ money == expected ⇒ no variance row materializes.
    Non-violating shape per the AP.2 convention.

    AU.0 finding: on a LEAF internal account (account_parent_role !=
    None), this emission ALSO trips `DriftInvariant`, because drift's
    matview filter ``parent_role IS NOT NULL AND stored ≠ Σ legs`` is
    satisfied (no transactions ⇒ Σ legs = 0; planted stored = expected +
    variance ≠ 0). Registry records the two-edge entry.
    """

    account_id: str
    account_role: str
    account_parent_role: str | None
    anchor_day: date
    expected: float
    variance: float
    # AY.4.d — production callers thread cfg.db.table_prefix here.
    prefix: str = "spec_example"

    def _planned_balance(self) -> BalanceRow:
        """The one row this plant emits, projected into the residual
        domain — the SINGLE plan both ``emit()`` and ``intended`` read
        (DS.2: a separately-written expectation is the calibration-drift
        disease with a new name). Cents conversion mirrors the insert
        boundary's ``Cents.from_dollars(str(value))`` exactly."""
        return BalanceRow(
            account_id=self.account_id,
            entry=0,
            day=self.anchor_day,
            money=Cents.from_dollars(str(self.expected + self.variance)),
            expected_eod=Cents.from_dollars(str(self.expected)),
            account_role=self.account_role,
            account_parent_role=self.account_parent_role,
        )

    @property
    def intended(self) -> RuleViolation:
        # DS.2 — the magnitude comes from the RESIDUAL evaluated over
        # the planned row, not hand-inlined arithmetic: if the law
        # moves, this expectation moves with it.
        state = ResidualState(balances=(self._planned_balance(),))
        residual = expected_eod_residual(state, self.account_id, self.anchor_day)
        assert residual is not None  # the plant always sets an expectation
        return RuleViolation.of(
            "expected_eod_balance_breach",
            account_id=self.account_id,
            business_day=self.anchor_day,
            variance=identity_dollars(residual),
        )

    @property
    def also_trips_drift(self) -> RuleViolation | None:
        """The empirical AU.0-style edge: drift fires on the same
        account/day when the planted account is a LEAF (account_parent_
        role is set) — zero transactions means the drift law's computed
        side is 0 and the planted stored balance IS the drift. Derived
        from ``drift_residual`` over the same planned row, so the edge's
        magnitude tracks the drift law (including the DS.3.2 carried-day
        change) automatically.

        Returns `None` when the planted account is NOT a leaf — the
        drift law scopes to internal leaf accounts.
        """
        if self.account_parent_role is None:
            return None
        state = ResidualState(balances=(self._planned_balance(),))
        residual = drift_residual(state, self.account_id, self.anchor_day)
        assert residual is not None  # leaf + emitted day ⇒ the cell exists
        return RuleViolation.of(
            "drift",
            account_id=self.account_id,
            business_day=self.anchor_day,
            drift=identity_dollars(residual),
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        """The single account_id this plant carries an EOD target for. AV.5."""
        return frozenset({self.account_id})

    def emit(
        self,
        conn: SyncConnection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        # CZ.2: unconditional source='training' stamp.
        metadata = scenario_metadata(
            scenario_id, generator="ExpectedEodBalanceGenerator",
        )
        start, end = day_bounds(self.anchor_day)
        plan = self._planned_balance()
        expected_eod = plan.expected_eod
        assert expected_eod is not None  # the plant always sets an expectation
        insert_balance(
            conn,
            prefix=self.prefix,
            account_id=plan.account_id,
            account_name=f"EOD Acct ({self.account_role})",
            account_role=self.account_role,
            account_scope="internal",
            account_parent_role=plan.account_parent_role,
            expected_eod_balance=float(expected_eod.to_dollars()),
            business_day_start=start,
            business_day_end=end,
            money=float(plan.money.to_dollars()),
            metadata=metadata,
        )


# Phase AU.3.d (2026-05-23): local helpers hoisted to
# `common/spine/_emit_helpers.py`. No per-invariant-shape helpers stay
# here — expected_eod's plant is a balance-only single-row INSERT.
