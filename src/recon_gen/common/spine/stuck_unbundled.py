"""Stuck-Unbundled family — twin of stuck_pending with disjoint conditions.

Same shape as `stuck_pending`:

- Transaction-based plant (single tx, no balance row)
- L2-coupled via `rail.max_unbundled_age` (vs `max_pending_age`)
- Wall-clock matview using `CURRENT_TIMESTAMP - posting`
- Single-edge registry entry (no overlap with drift / ledger_drift /
  overdraft / expected_eod — Posted but `bundle_id IS NULL` is exactly
  the matview's filter, and Posted legs don't trip stuck_pending)

The disjoint conditions vs stuck_pending (per schema.py:2091-2094):

- `status='Posted'` (vs 'Pending') — AggregatingRails only bundle Posted
  legs; a Pending leg isn't "stuck unbundled," it's just "stuck pending"
- `bundle_id IS NULL` — the row has been Posted but no AggregatingRail
  has picked it up
- Per validator R8, `max_unbundled_age` is only meaningful on rails
  whose `rail_name` appears in some AggregatingRail's `bundles_activity`
- `posting + max_unbundled_age` overshot

Same TZ convention as stuck_pending: `datetime.now()` LOCAL per
`[[project-local-tz-convention]]`; SQLite test absorbs UTC skew via
±12h overshoot windows.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance, SingleLegRail, TwoLegRail
from recon_gen.common.spine.violation import Violation

_RailWithUnbundledAge = TwoLegRail | SingleLegRail


@dataclass(frozen=True)
class StuckUnbundledInvariant:
    """Stuck-Unbundled detector. The matview gates on
    ``status = 'Posted'`` AND ``bundle_id IS NULL`` AND ``age_seconds >
    max_unbundled_age_seconds`` (per-rail cap from L2). Identity is
    `(transaction_id, rail_name)`."""

    name: ClassVar[str] = "stuck_unbundled"
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT transaction_id, rail_name "
            f"FROM {self.prefix}_stuck_unbundled",
        ).fetchall()
        return {
            Violation.of(
                "stuck_unbundled",
                transaction_id=str(tid),
                rail_name=str(rn),
            )
            for tid, rn in rows
        }

    def scenario_for(
        self,
        rail_name: str,
        *,
        overshoot_seconds: int = 60,
        account_role: str = "CustomerSubledger",
        instance: L2Instance | None = None,
    ) -> "StuckUnbundledGenerator":
        """Resolve `rail_name` against the shape; plant a Posted-but-
        unbundled transaction with `posting = now() − (rail.
        max_unbundled_age + overshoot)`.

        Raises `ValueError` if rail doesn't exist OR doesn't have a
        `max_unbundled_age` (matview excludes those — uncovered scenario
        would silently inert).
        """
        inst = instance if instance is not None else _spec_example()
        rail = _find_rail_with_max_unbundled_age(inst, rail_name)
        assert rail.max_unbundled_age is not None  # narrowing for pyright
        acct = _find_internal_with_role(inst, account_role)
        return StuckUnbundledGenerator(
            transaction_id=f"tx-stuck-unbundled-{rail_name}",
            transfer_id=f"xfer-stuck-unbundled-{rail_name}",
            rail_name=rail_name,
            account_id=f"acct-stuck-unbundled-{rail_name}",
            account_role=account_role,
            account_parent_role=(
                str(getattr(acct, "parent_role"))
                if getattr(acct, "parent_role", None) is not None
                else None
            ),
            max_unbundled_age_seconds=int(
                rail.max_unbundled_age.total_seconds(),
            ),
            overshoot_seconds=overshoot_seconds,
        )


@dataclass
class StuckUnbundledGenerator:
    """Emit a single Posted transaction with `bundle_id IS NULL` whose
    `posting` is in the past by `max_unbundled_age_seconds + overshoot_
    seconds`. NO balance row, NO related rows."""

    transaction_id: str
    transfer_id: str
    rail_name: str
    account_id: str
    account_role: str
    account_parent_role: str | None
    max_unbundled_age_seconds: int
    overshoot_seconds: int

    @property
    def intended(self) -> Violation:
        return Violation.of(
            "stuck_unbundled",
            transaction_id=self.transaction_id,
            rail_name=self.rail_name,
        )

    def emit(self, conn: sqlite3.Connection) -> None:
        # Same wall-clock + LOCAL-TZ convention as stuck_pending — see
        # `stuck_pending.py` for the rationale + the
        # [[project-local-tz-convention]] memory.
        age_back = self.max_unbundled_age_seconds + self.overshoot_seconds
        posting_dt = datetime.now() - timedelta(seconds=age_back)
        # status='Posted' (not Pending — disjoint from stuck_pending).
        # bundle_id stays NULL by default (`_TX_COLS` doesn't include it,
        # so the INSERT leaves it NULL — exactly what the matview filter
        # wants).
        _insert_tx(
            conn,
            id=self.transaction_id,
            account_id=self.account_id,
            account_name=f"Stuck Unbundled ({self.rail_name})",
            account_role=self.account_role,
            account_scope="internal",
            account_parent_role=self.account_parent_role,
            amount_money=100.0,
            amount_direction="Credit",
            status="Posted",
            posting=posting_dt.strftime("%Y-%m-%d %H:%M:%S"),
            transfer_id=self.transfer_id,
            rail_name=self.rail_name,
            origin="etl",
        )


# ---------------------------------------------------------------------------
# Helpers — module-private (AU.3.d hoists once the duplication burden across
# drift / overdraft / expected_eod / stuck_pending / stuck_unbundled is
# clear enough to refactor with shared semantics).
# ---------------------------------------------------------------------------


def _spec_example() -> L2Instance:
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[4]
    return load_instance(repo_root / "tests" / "l2" / "spec_example.yaml")


def _find_rail_with_max_unbundled_age(
    instance: L2Instance, rail_name: str,
) -> _RailWithUnbundledAge:
    for r in instance.rails:
        if r.name == rail_name:
            if r.max_unbundled_age is None:
                raise ValueError(
                    f"rail {rail_name!r} has no max_unbundled_age set; "
                    f"stuck_unbundled's matview excludes it. Cannot "
                    f"manufacture a stuck_unbundled scenario against this "
                    f"rail."
                )
            return r
    raise ValueError(
        f"shape has no rail named {rail_name!r}; cannot manufacture "
        f"a stuck_unbundled scenario"
    )


def _find_internal_with_role(instance: L2Instance, role: str) -> object:
    for a in instance.accounts:
        if (
            getattr(a, "role", None) == role
            and getattr(a, "scope", None) == "internal"
        ):
            return a
    raise ValueError(
        f"shape has no internal account with role {role!r}"
    )


_TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name", "origin",
)


def _insert_tx(conn: sqlite3.Connection, **vals: object) -> None:
    placeholders = ", ".join("?" for _ in _TX_COLS)
    table = f"{_PREFIX}_transactions"
    conn.execute(
        f"INSERT INTO {table} ({', '.join(_TX_COLS)}) "
        f"VALUES ({placeholders})",
        [vals.get(c) for c in _TX_COLS],
    )


_PREFIX = "spec_example"
