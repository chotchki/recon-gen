"""DS.3.6 helpers — metamorphic transforms over the real engine.

A metamorphic law is a state TRANSFORM plus an exact claim about how
the engine's violation sets respond. The base state here is a modest
hand-built packed state in which EVERY registered detector alarms at
least once (asserted by the suite's non-vacuity test), so an "inert"
transform is proven inert against live sets rather than empty ones.

Everything runs through the DS.3.4/3.5 harness: real ``emit_schema``,
real config populate, real ``refresh_matviews_sql`` order, and the
CellBuilder's one-construction-site guarantee (engine rows + residual
twins from the same call). Every DB in this module loads through
``insert_with_entries`` — entries are PINNED explicitly, so the
insert-order permutation laws permute physical order only (the DS.0
correction: permuting under sequence-assigned entries changes the
supersession winners, which is a semantics change).

The anomaly (probabilistic) laws live here too, scoped exactly as the
DS.0 attack corrected: location invariance is asserted on DENSE
per-pair histories only, the min-n floor and the stddev=0 guard are
asserted as exact integer guards, and bucket-level SCALE claims are
out of scope — band-edge epsilon semantics are DS.4's tolerance
contract, not a metamorphic law.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from recon_gen.common.l2.primitives import (
    POSTED_STATUS,
    SCOPE_EXTERNAL,
)
from recon_gen.common.spine.residuals import ResidualState
from tests.enumeration.domains import (
    chain_parent,
    drift,
    expected_eod,
    fan_in,
    ledger_drift,
    limit_breach,
    money_trail,
    multi_xor,
    overdraft,
    stuck_pending,
    stuck_unbundled,
    xor_group,
)
from tests.enumeration.domains._base import (
    SPEC_EXAMPLE,
    SPEC_PREFIX,
    WINDOW_START,
    as_date,
    as_int,
    as_str,
    spec_profile,
)
from tests.enumeration.harness import (
    ENUM_AS_OF,
    BalRow,
    CellBuilder,
    DetectorCheck,
    EnumerationDB,
    TxRow,
    ViolationMap,
    artifacts_for,
)

# The metamorphic window: fixed two days (not tier-scaled — the laws
# are about set responses, not domain breadth), anchored like the
# packed domains so the LOCF spine covers both days by construction.
WINDOW: Final[tuple[dt.date, ...]] = (
    WINDOW_START, WINDOW_START + dt.timedelta(days=1),
)

_NOON: Final = dt.time(12, 0)

ALL_CHECKS: Final[tuple[DetectorCheck, ...]] = (
    drift.CHECK, ledger_drift.CHECK, overdraft.CHECK, expected_eod.CHECK,
    limit_breach.CHECK, stuck_pending.CHECK, stuck_unbundled.CHECK,
    chain_parent.CHECK, xor_group.CHECK, fan_in.CHECK, multi_xor.CHECK,
    money_trail.CHECK,
)

#: The detectors bound by the money status law (residuals return
#: Cents) — the scope of the failed-leg inertness claim.
MONEY_DETECTORS: Final[tuple[str, ...]] = (
    "drift", "ledger_drift", "overdraft", "expected_eod", "limit_breach",
)

type EntriedTx = tuple[TxRow, int]
type EntriedBal = tuple[BalRow, int]


def entried(builder: CellBuilder) -> tuple[list[EntriedTx], list[EntriedBal]]:
    """Pair a builder's engine rows with its residual twins' entry
    values. Sound because CellBuilder appends both sides in lockstep;
    per-builder entry counters are safe to insert explicitly since
    only the RELATIVE order within one logical key carries meaning
    and every builder here owns its keys."""
    state = builder.state()
    tx_rows, bal_rows = builder.rows()
    tx = [(row, leg.entry) for row, leg in zip(tx_rows, state.legs)]
    bal = [(row, claim.entry) for row, claim in zip(bal_rows, state.balances)]
    return tx, bal


def build_db(tx: list[EntriedTx], bal: list[EntriedBal]) -> EnumerationDB:
    db = EnumerationDB(artifacts_for(SPEC_EXAMPLE, prefix=SPEC_PREFIX))
    db.insert_with_entries(tx, bal)
    db.refresh()
    return db


def read_all(db: EnumerationDB) -> dict[str, ViolationMap]:
    return {check.detector: check.read_engine(db) for check in ALL_CHECKS}


# ---------------------------------------------------------------------------
# The base state — one alarming cell per detector, prefix-owned.


@dataclass(frozen=True, slots=True)
class BaseState:
    """The metamorphic base: entried rows plus the handles the
    transforms target."""

    tx: tuple[EntriedTx, ...]
    bal: tuple[EntriedBal, ...]
    #: Highest entry any base builder assigned — appended transform
    #: rows use values above this so supersedes always win.
    entry_ceiling: int
    #: The drift cell the supersession transforms correct.
    drift_account: str
    drift_leg_id: str
    #: The at-cap limit cell (flow exactly at cap — NOT breached);
    #: the adversarial target for the failed-leg transform: a counted
    #: non-Posted leg here would mint a NEW breach key.
    at_cap_account: str
    at_cap_rail: str
    at_cap_direction: str


def drift_cell(*, corrected_amount: int | None) -> CellBuilder:
    """The drift cell built from one site for both the base state and
    the supersession-correction prediction: stored balance 100, one
    Posted leg (+40). ``corrected_amount`` appends a superseding
    re-emit of the SAME leg id, so the corrected residual state comes
    from exactly the construction the engine rows come from."""
    b = CellBuilder()
    b.balance(account="mmdr0a", day=WINDOW[0], money=100)
    b.leg(
        id="mmdr0aL0", account="mmdr0a", amount=40, status=POSTED_STATUS,
        posting=dt.datetime.combine(WINDOW[0], _NOON), transfer="mmdr0t0",
    )
    if corrected_amount is not None:
        b.leg(
            id="mmdr0aL0", account="mmdr0a", amount=corrected_amount,
            status=POSTED_STATUS,
            posting=dt.datetime.combine(WINDOW[0], _NOON),
            transfer="mmdr0t0",
        )
    return b


def drift_cell_state(*, corrected_amount: int | None) -> ResidualState:
    return drift_cell(corrected_amount=corrected_amount).state()


DRIFT_ACCOUNT: Final = "mmdr0a"


def _anchors() -> CellBuilder:
    """Window anchors, same contract as the packed LOCF domains: pin
    the fleet-wide spine to the full window; violation-inert."""
    b = CellBuilder()
    for day in (WINDOW[0], WINDOW[-1]):
        b.balance(
            account="zzmeta", day=day, money=0, role=None, parent_role=None,
        )
    return b


def _first_limit_schedule() -> tuple[str, str, str, int]:
    profile = spec_profile()
    key = sorted(profile.limit_caps_cents)[0]
    return key[0], key[1], key[2], profile.limit_caps_cents[key]


def _signed(direction: str, magnitude: int) -> int:
    return -magnitude if direction == "Outbound" else magnitude


def build_base() -> BaseState:
    """One alarming cell per detector plus the at-cap control cell and
    one pre-superseded pair (so the permutation laws stress the argmax
    against physical order in the base itself)."""
    profile = spec_profile()
    builders: list[CellBuilder] = [_anchors(), drift_cell(corrected_amount=None)]

    # Supersession pair: balance re-emitted (stale 999 -> current 7),
    # leg re-emitted (stale +999 -> current +5). Current drift: 2.
    b = CellBuilder()
    b.balance(account="mmss0a", day=WINDOW[0], money=999)
    b.balance(account="mmss0a", day=WINDOW[0], money=7)
    b.leg(
        id="mmss0aL0", account="mmss0a", amount=999, status=POSTED_STATUS,
        posting=dt.datetime.combine(WINDOW[0], _NOON), transfer="mmss0t0",
    )
    b.leg(
        id="mmss0aL0", account="mmss0a", amount=5, status=POSTED_STATUS,
        posting=dt.datetime.combine(WINDOW[0], _NOON), transfer="mmss0t0",
    )
    builders.append(b)

    # Overdraft: stored balance below zero.
    b = CellBuilder()
    b.balance(account="mmov0a", day=WINDOW[0], money=-5)
    builders.append(b)

    # Expected-EOD breach: stored 10 against expectation 0.
    b = CellBuilder()
    b.balance(account="mmeo0a", day=WINDOW[0], money=10, expected_eod=0)
    builders.append(b)

    # Ledger drift: parent stores 100, single child stores 30, no
    # direct postings.
    b = CellBuilder()
    b.balance(
        account="mmlg0p", day=WINDOW[0], money=100, role="mmlg0r",
        parent_role=None,
    )
    b.balance(account="mmlg0c", day=WINDOW[0], money=30, parent_role="mmlg0r")
    builders.append(b)

    # Limit breach (one cent over) + the at-cap control cell.
    role, rail, direction, cap = _first_limit_schedule()
    for account, magnitude in (("mmlb0a", cap + 1), ("mmlb1a", cap)):
        b = CellBuilder()
        b.leg(
            id=f"{account}L0", account=account,
            amount=_signed(direction, magnitude), status=POSTED_STATUS,
            posting=dt.datetime.combine(WINDOW[0], _NOON),
            transfer=f"{account}t0", rail=rail, parent_role=role,
        )
        builders.append(b)

    # Stuck pending: Pending leg well over the resolved cap.
    pending_rail, pending_cap = sorted(profile.pending_age_caps.items())[0]
    b = CellBuilder()
    b.leg(
        id="mmsp0aL0", account="mmsp0a", amount=0, status="Pending",
        posting=ENUM_AS_OF - dt.timedelta(seconds=pending_cap + 3600),
        transfer="mmsp0t0", rail=pending_rail,
    )
    builders.append(b)

    # Stuck unbundled: Posted, bundle-less, well over the resolved cap.
    unb_rail, unb_cap = sorted(profile.unbundled_age_caps.items())[0]
    b = CellBuilder()
    b.leg(
        id="mmsu0aL0", account="mmsu0a", amount=0, status=POSTED_STATUS,
        posting=ENUM_AS_OF - dt.timedelta(seconds=unb_cap + 3600),
        transfer="mmsu0t0", rail=unb_rail,
    )
    builders.append(b)

    # XOR overlap: both members fire.
    members = sorted(profile.xor_groups[xor_group.TEMPLATE][0])
    b = CellBuilder()
    for n, member in enumerate(members):
        b.leg(
            id=f"mcxg0L{n}", account="mcxg0a", amount=0,
            status=POSTED_STATUS,
            posting=dt.datetime.combine(WINDOW[0], _NOON),
            transfer="mcxg0", rail=member, template=xor_group.TEMPLATE,
            parent_role=None,
        )
    builders.append(b)

    # Chain-parent disagreement: two firing legs, two distinct claims.
    b = CellBuilder()
    for n, parent in enumerate(("mccp0P1", "mccp0P2")):
        b.leg(
            id=f"mccp0L{n}", account="mccp0a", amount=0,
            status=POSTED_STATUS,
            posting=dt.datetime.combine(WINDOW[0], _NOON),
            transfer="mccp0", parent=parent, rail="RailCP",
            template=chain_parent.CHILD_TEMPLATE, parent_role=None,
        )
    builders.append(b)

    # Fan-in missing: one claim against an expectation of two.
    b = CellBuilder()
    b.leg(
        id="mcfi0L0", account="mcfi0a", amount=0, status=POSTED_STATUS,
        posting=dt.datetime.combine(WINDOW[0], _NOON), transfer="mcfi0",
        parent="mcfi0P1", rail="BatchPayoutClose",
        template=fan_in.SPEC_CHILD_TEMPLATE, parent_role=None,
    )
    builders.append(b)

    # Multi-XOR overlap: rail-parent plus both declared siblings.
    mx_parent = "BulkAccrualSettlement"
    siblings = sorted(profile.multi_xor_children[mx_parent])
    b = CellBuilder()
    b.leg(
        id="mcmx0pL0", account="mcmx0a", amount=0, status=POSTED_STATUS,
        posting=dt.datetime.combine(WINDOW[0], _NOON), transfer="mcmx0",
        rail=mx_parent, parent_role=None,
    )
    for n, sibling in enumerate(siblings):
        b.leg(
            id=f"mcmx0c{n}L0", account="mcmx0a", amount=0,
            status=POSTED_STATUS,
            posting=dt.datetime.combine(WINDOW[0], _NOON),
            transfer=f"mcmx0c{n}", parent="mcmx0", rail=sibling,
            parent_role=None,
        )
    builders.append(b)

    # Money trail: two-hop chain, two edges.
    b = CellBuilder()
    for i, (src, tgt, parent) in enumerate((
        ("mmmt0a0", "mmmt0a1", None), ("mmmt0a1", "mmmt0a2", "mmmt0t0"),
    )):
        transfer = f"mmmt0t{i}"
        b.leg(
            id=f"{transfer}Ls", account=src, amount=-100,
            status=POSTED_STATUS,
            posting=dt.datetime.combine(WINDOW[0], _NOON),
            transfer=transfer, parent=parent, rail="TrailRail",
            parent_role=None,
        )
        b.leg(
            id=f"{transfer}Lt", account=tgt, amount=100,
            status=POSTED_STATUS,
            posting=dt.datetime.combine(WINDOW[0], _NOON),
            transfer=transfer, parent=parent, rail="TrailRail",
            parent_role=None,
        )
    builders.append(b)

    tx: list[EntriedTx] = []
    bal: list[EntriedBal] = []
    ceiling = 0
    for built in builders:
        b_tx, b_bal = entried(built)
        tx.extend(b_tx)
        bal.extend(b_bal)
        ceiling = max(
            [ceiling]
            + [entry for _, entry in b_tx]
            + [entry for _, entry in b_bal],
        )
    return BaseState(
        tx=tuple(tx), bal=tuple(bal), entry_ceiling=ceiling,
        drift_account=DRIFT_ACCOUNT, drift_leg_id="mmdr0aL0",
        at_cap_account="mmlb1a", at_cap_rail=rail, at_cap_direction=direction,
    )


# ---------------------------------------------------------------------------
# Transforms — each returns the rows it APPENDS to the base.


def status_probe_rows(base: BaseState, status: str) -> list[EntriedTx]:
    """The failed-leg / unknown-status inertness probe: one leg with
    ``status`` on the drift account (a counted leg would move the
    computed balance) and one on the at-cap limit account (a counted
    leg would mint a brand-new breach key)."""
    b = CellBuilder()
    b.leg(
        id="mxfl0L0", account=base.drift_account, amount=777, status=status,
        posting=dt.datetime.combine(WINDOW[0], _NOON), transfer="mxfl0t0",
    )
    b.leg(
        id="mxfl0L1", account=base.at_cap_account,
        amount=_signed(base.at_cap_direction, 50), status=status,
        posting=dt.datetime.combine(WINDOW[0], _NOON), transfer="mxfl0t1",
        rail=base.at_cap_rail,
    )
    tx, _ = entried(b)
    return [(row, base.entry_ceiling + i + 1) for i, (row, _) in enumerate(tx)]


EXTERNAL_TRANSFER: Final = "mxext0"


def balanced_external_rows(base: BaseState) -> list[EntriedTx]:
    """A balanced two-leg Posted transfer between two EXTERNAL-scope
    accounts (fresh transfer, undeclared rail, no template)."""
    b = CellBuilder()
    b.leg(
        id=f"{EXTERNAL_TRANSFER}Ls", account=f"{EXTERNAL_TRANSFER}s",
        amount=-250, status=POSTED_STATUS,
        posting=dt.datetime.combine(WINDOW[0], _NOON),
        transfer=EXTERNAL_TRANSFER, rail="MetaExternalRail",
        role=None, parent_role=None, scope=SCOPE_EXTERNAL,
    )
    b.leg(
        id=f"{EXTERNAL_TRANSFER}Lt", account=f"{EXTERNAL_TRANSFER}r",
        amount=250, status=POSTED_STATUS,
        posting=dt.datetime.combine(WINDOW[0], _NOON),
        transfer=EXTERNAL_TRANSFER, rail="MetaExternalRail",
        role=None, parent_role=None, scope=SCOPE_EXTERNAL,
    )
    tx, _ = entried(b)
    return [(row, base.entry_ceiling + i + 1) for i, (row, _) in enumerate(tx)]


def superseding_leg_row(
    base: BaseState, *, amount: int, entry_offset: int = 1,
) -> EntriedTx:
    """A re-emit of the base drift leg at a pinned higher entry."""
    b = CellBuilder()
    b.leg(
        id=base.drift_leg_id, account=base.drift_account, amount=amount,
        status=POSTED_STATUS,
        posting=dt.datetime.combine(WINDOW[0], _NOON), transfer="mmdr0t0",
    )
    tx, _ = entried(b)
    return (tx[0][0], base.entry_ceiling + entry_offset)


def superseding_balance_row(base: BaseState) -> EntriedBal:
    """An identical re-emit of the base drift cell's balance claim at
    a pinned higher entry."""
    b = CellBuilder()
    b.balance(account=base.drift_account, day=WINDOW[0], money=100)
    _, bal = entried(b)
    return (bal[0][0], base.entry_ceiling + 2)


def unrelated_transfer_rows(
    base: BaseState, *, entry_offset: int,
) -> list[EntriedTx]:
    """A fresh XOR-overlap transfer, unrelated to every base key —
    the dedup-commute law's second operation. Alarming (a new
    xor_group key) so the commute test is non-vacuous."""
    profile = spec_profile()
    members = sorted(profile.xor_groups[xor_group.TEMPLATE][0])
    b = CellBuilder()
    for n, member in enumerate(members):
        b.leg(
            id=f"mcxg1L{n}", account="mcxg1a", amount=0,
            status=POSTED_STATUS,
            posting=dt.datetime.combine(WINDOW[0], _NOON),
            transfer="mcxg1", rail=member, template=xor_group.TEMPLATE,
            parent_role=None,
        )
    tx, _ = entried(b)
    return [
        (row, base.entry_ceiling + entry_offset + i)
        for i, (row, _) in enumerate(tx)
    ]


# ---------------------------------------------------------------------------
# Anomaly (probabilistic) fixtures — own DBs, pairs only.


DENSE_SENDER: Final = "anp0s"
DENSE_RECIPIENT: Final = "anp0r"
#: Dense-pair per-day sums; the rolling window straddles consecutive
#: days, so shifting alternating days by LOCATION_SHIFT moves EVERY
#: window by exactly LOCATION_SHIFT (the exact preimage of a uniform
#: window-level location shift — a same-constant-per-day shift would
#: move the first, one-day window by half as much as the rest).
DENSE_DAY_SUMS: Final = (400, 700, 500, 900)
LOCATION_SHIFT: Final = 1000

FLOOR_SENDER: Final = "anp1s"
FLOOR_RECIPIENT: Final = "anp1r"

GUARD_SENDER: Final = "anp2s"
GUARD_RECIPIENT: Final = "anp2r"

LIVE_SENDER: Final = "anp3s"
LIVE_RECIPIENT: Final = "anp3r"


def pair_transfer(
    b: CellBuilder,
    *,
    transfer: str,
    sender: str,
    recipient: str,
    day: dt.date,
    amount: int,
) -> None:
    b.leg(
        id=f"{transfer}Ls", account=sender, amount=-amount,
        status=POSTED_STATUS, posting=dt.datetime.combine(day, _NOON),
        transfer=transfer, rail="AnomRail", parent_role=None,
    )
    b.leg(
        id=f"{transfer}Lt", account=recipient, amount=amount,
        status=POSTED_STATUS, posting=dt.datetime.combine(day, _NOON),
        transfer=transfer, rail="AnomRail",
    )


def anomaly_rows(*, shifted: bool) -> list[EntriedTx]:
    """The anomaly base: a dense pair (every day in its span active),
    a two-window pair for the min-n floor, an equal-window sparse pair
    for the stddev=0 guard, and a three-window live pair proving the
    floor's boundary. ``shifted`` adds the alternating-day location
    shift to the dense pair only."""
    b = CellBuilder()
    day0 = WINDOW_START
    for i, amount in enumerate(DENSE_DAY_SUMS):
        pair_transfer(
            b, transfer=f"anp0t{i}", sender=DENSE_SENDER,
            recipient=DENSE_RECIPIENT, day=day0 + dt.timedelta(days=i),
            amount=amount,
        )
    if shifted:
        for i in range(0, len(DENSE_DAY_SUMS), 2):
            pair_transfer(
                b, transfer=f"anp0x{i}", sender=DENSE_SENDER,
                recipient=DENSE_RECIPIENT, day=day0 + dt.timedelta(days=i),
                amount=LOCATION_SHIFT,
            )
    # Floor pair: two windows, wild spike — pair_n below the floor.
    for i, amount in enumerate((100, 100000)):
        pair_transfer(
            b, transfer=f"anp1t{i}", sender=FLOOR_SENDER,
            recipient=FLOOR_RECIPIENT, day=day0 + dt.timedelta(days=i),
            amount=amount,
        )
    # Guard pair: gap-spaced equal days — every window is one day, all
    # equal, so the sample stddev is exactly zero at pair_n over the
    # floor.
    for i in range(3):
        pair_transfer(
            b, transfer=f"anp2t{i}", sender=GUARD_SENDER,
            recipient=GUARD_RECIPIENT, day=day0 + dt.timedelta(days=2 * i),
            amount=500,
        )
    # Live pair: dense three-window history with real variance — the
    # floor's other side.
    for i, amount in enumerate((100, 300, 200)):
        pair_transfer(
            b, transfer=f"anp3t{i}", sender=LIVE_SENDER,
            recipient=LIVE_RECIPIENT, day=day0 + dt.timedelta(days=i),
            amount=amount,
        )
    tx, _ = entried(b)
    return tx


@dataclass(frozen=True, slots=True)
class AnomalyRow:
    window_sum: int
    transfer_count: int
    pop_mean: Decimal
    pop_stddev: Decimal
    z_score: Decimal
    z_bucket: str


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    raise TypeError(f"not a numeric engine value: {value!r}")


def read_anomalies(
    db: EnumerationDB,
) -> dict[tuple[str, str, dt.date], AnomalyRow]:
    rows = db.fetchall(
        f"SELECT sender_account_id, recipient_account_id, window_end, "
        f"window_sum, transfer_count, pop_mean, pop_stddev, z_score, "
        f"z_bucket "
        f"FROM {db.prefix}_inv_pair_rolling_anomalies",
    )
    return {
        (as_str(row[0]), as_str(row[1]), as_date(row[2])): AnomalyRow(
            window_sum=as_int(row[3]),
            transfer_count=as_int(row[4]),
            pop_mean=_as_decimal(row[5]),
            pop_stddev=_as_decimal(row[6]),
            z_score=_as_decimal(row[7]),
            z_bucket=as_str(row[8]),
        )
        for row in rows
    }
