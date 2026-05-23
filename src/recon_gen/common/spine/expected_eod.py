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

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import ClassVar

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.spine.violation import Violation


@dataclass(frozen=True)
class ExpectedEodBalanceInvariant:
    """Expected-EOD-balance detector. Persona-blind — the matview SQL
    filters only on the per-row `expected_eod_balance` column being set
    and not matching `money`. `scenario_for(role)` accepts ANY internal
    account with the requested role (no leaf/parent filter)."""

    name: ClassVar[str] = "expected_eod_balance_breach"
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT account_id, business_day_start, variance "
            f"FROM {self.prefix}_expected_eod_balance_breach",
        ).fetchall()
        return {
            Violation.of(
                "expected_eod_balance_breach",
                account_id=aid,
                business_day=_to_date(bds),
                variance=round(float(var), 2),
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
    ) -> "ExpectedEodBalanceGenerator":
        """Resolve a role; return a generator that plants
        ``money = expected + variance`` with the per-row
        ``expected_eod_balance`` set, so the variance row materializes.

        ``variance=0.0`` is the non-violating shape (stored ==
        expected ⇒ the matview row is filtered out). Same AP.2
        convention as overdraft / drift.

        Raises `ValueError` if the L2 has no internal account with the
        requested role.
        """
        inst = instance if instance is not None else _spec_example()
        acct = _find_internal_with_role(inst, role)
        return ExpectedEodBalanceGenerator(
            account_id=f"acct-eod-{role}",
            account_role=role,
            account_parent_role=(
                str(getattr(acct, "parent_role"))
                if getattr(acct, "parent_role", None) is not None
                else None
            ),
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

    @property
    def intended(self) -> Violation:
        # The matview's variance column = money − expected_eod_balance =
        # variance. Identity carries the variance directly (matches the
        # detect projection).
        return Violation.of(
            "expected_eod_balance_breach",
            account_id=self.account_id,
            business_day=self.anchor_day,
            variance=round(self.variance, 2),
        )

    @property
    def also_trips_drift(self) -> Violation | None:
        """The empirical AU.0-style edge: drift fires on the same
        account/day when the planted account is a LEAF (account_parent_
        role is set). Drift magnitude = stored − Σ legs = (expected +
        variance) − 0 = expected + variance.

        Returns `None` when the planted account is NOT a leaf — drift's
        matview filter excludes parent-role rows.
        """
        if self.account_parent_role is None:
            return None
        return Violation.of(
            "drift",
            account_id=self.account_id,
            business_day=self.anchor_day,
            drift=round(self.expected + self.variance, 2),
        )

    def emit(self, conn: sqlite3.Connection) -> None:
        start, end = _day_bounds(self.anchor_day)
        _insert_balance(
            conn,
            account_id=self.account_id,
            account_name=f"EOD Acct ({self.account_role})",
            account_role=self.account_role,
            account_scope="internal",
            account_parent_role=self.account_parent_role,
            expected_eod_balance=self.expected,
            business_day_start=start,
            business_day_end=end,
            money=self.expected + self.variance,
        )


# ---------------------------------------------------------------------------
# Helpers — module-private (same discipline as drift.py / overdraft.py).
# AU.3.d (queued) hoists the shared trio once the third balance-only
# invariant lands and the 4-copy duplication justifies the refactor.
# ---------------------------------------------------------------------------


def _spec_example() -> L2Instance:
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[4]
    return load_instance(repo_root / "tests" / "l2" / "spec_example.yaml")


def _find_internal_with_role(instance: L2Instance, role: str) -> object:
    """Return the first internal account with the requested role —
    accepts both leaf and parent variants (no parent_role filter)."""
    for a in instance.accounts:
        if (
            getattr(a, "role", None) == role
            and getattr(a, "scope", None) == "internal"
        ):
            return a
    raise ValueError(
        f"shape has no expected-EOD-eligible internal account with role "
        f"{role!r}; cannot manufacture a variance scenario"
    )


_DB_COLS = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance", "business_day_start",
    "business_day_end", "money",
)


def _insert_balance(conn: sqlite3.Connection, **vals: object) -> None:
    placeholders = ", ".join("?" for _ in _DB_COLS)
    table = f"{_PREFIX}_daily_balances"
    conn.execute(
        f"INSERT INTO {table} ({', '.join(_DB_COLS)}) "
        f"VALUES ({placeholders})",
        [vals.get(c) for c in _DB_COLS],
    )


_PREFIX = "spec_example"


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        (start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )


def _to_date(bds: object) -> date:
    return datetime.strptime(str(bds)[:10], "%Y-%m-%d").date()
