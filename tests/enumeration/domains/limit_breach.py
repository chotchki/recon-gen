"""Limit breach (law 5) — per-direction flows straddling resolved caps.

Caps are CONFIG DATA (``v_config_limit_schedules`` joined on
(parent_role, rail, direction), ×100 dollars→cents at the join) — the
flow-sum grid derives from the L2-RESOLVED caps via the
BoundaryProfile. Per (rail, direction) schedule:

- day flow-magnitude target ∈ {cap-1, cap, cap+1} (strict-``>`` at
  the cap: exactly-at-cap is NOT a breach);
- split ∈ {one leg, two legs} (the SUM is per (account, day, rail,
  direction), not per leg);
- status ∈ {all Posted, first Pending, first Zq9x} — non-Posted legs
  must not move flow (the money status law).

Control cells that must stay invisible on BOTH sides:

- the same targets on a declared rail with NO schedule;
- credit legs on the Outbound-capped rail (no Inbound schedule for
  that rail on spec_example ⇒ cap resolves None);
- a NULL-parent_role account (cap resolution is keyed on the
  account's parent_role);
- a cross-day split (cap on day 0 + 1¢ on day 1 — per-day sums never
  cross midnight).
"""
from __future__ import annotations

import datetime as dt
from typing import Final

from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.money import Cents
from recon_gen.common.spine.residuals import (
    ZERO,
    current_legs,
    limit_breach_residual,
    stuck_pending_residual,
    stuck_unbundled_residual,
)
from tests.enumeration.domains._base import (
    WINDOW_START,
    BoundaryProfile,
    as_date,
    as_int,
    as_str,
)
from tests.enumeration.harness import (
    ENUM_AS_OF,
    CellBuilder,
    DetectorCheck,
    EnumerationDB,
    PackedCell,
    ViolationMap,
)

UNCAPPED_RAIL: Final = "SettlementSlow"
_PARENT_ROLE: Final = "CustomerLedger"

_DIRECTIONS: Final = ("Outbound", "Inbound")
_DELTAS: Final = (-1, 0, 1)
_STATUS_PATTERNS: Final = ("all_posted", "first_pending", "first_zq9x")

_DAY0: Final = WINDOW_START
_DAY1: Final = WINDOW_START + dt.timedelta(days=1)

# Fixed-index boundary-smoke pair (single Posted leg on the first
# schedule): flow exactly AT cap (no breach) / cap+1 (breach). The
# gate reads these keys via at_cap_key()/over_cap_key() because the
# key embeds the schedule's rail + direction.
_SMOKE_AT_PREFIX: Final = "tlb0000"
_SMOKE_OVER_PREFIX: Final = "tlb0001"


def covered_flows(
    profile: BoundaryProfile,
) -> dict[tuple[str, str, str], frozenset[int]]:
    """Flow magnitudes (cents) the domain exercises per resolved
    schedule — the coverage-lint surface."""
    return {
        key: frozenset(cap + delta for delta in _DELTAS)
        for key, cap in profile.limit_caps_cents.items()
    }


def _first_schedule(profile: BoundaryProfile) -> tuple[str, str, str]:
    return sorted(profile.limit_caps_cents)[0]


def at_cap_key(profile: BoundaryProfile) -> tuple[str, dt.date, str, str]:
    _, rail, direction = _first_schedule(profile)
    return (f"{_SMOKE_AT_PREFIX}a", _DAY0, rail, direction)


def over_cap_key(profile: BoundaryProfile) -> tuple[str, dt.date, str, str]:
    _, rail, direction = _first_schedule(profile)
    return (f"{_SMOKE_OVER_PREFIX}a", _DAY0, rail, direction)


def _signed(direction: str, magnitude: int) -> int:
    return -magnitude if direction == "Outbound" else magnitude


def _expected(
    b: CellBuilder,
    account: str,
    rails: tuple[str, ...],
    profile: BoundaryProfile,
    *,
    parent_role: str | None = _PARENT_ROLE,
) -> ViolationMap:
    state = b.state()
    expected: ViolationMap = {}
    for day in (_DAY0, _DAY1):
        for rail in rails:
            for direction in _DIRECTIONS:
                cap_cents = (
                    None if parent_role is None
                    else profile.limit_caps_cents.get(
                        (parent_role, rail, direction),
                    )
                )
                residual = limit_breach_residual(
                    state, account, day, rail, direction,
                    None if cap_cents is None else Cents(cap_cents),
                )
                if residual is not None and residual > ZERO:
                    expected[(account, day, rail, direction)] = residual.value
    return expected


def _threshold_spillover(
    b: CellBuilder, profile: BoundaryProfile,
) -> dict[str, ViolationMap]:
    """Cross-detector accounting for the shared packed DB: a limit
    cell's legs post on the window days (> 24h before the as_of
    frame), so a Pending leg on a pending-capped rail is GENUINELY
    stuck — the cell must declare it through the same residuals the
    threshold domains use, or the packed comparator reads a phantom
    engine-only row."""
    pending: ViolationMap = {}
    unbundled: ViolationMap = {}
    for leg in current_legs(b.state()):
        rail = leg.rail_name
        assert rail is not None
        stuck = stuck_pending_residual(
            leg, profile.pending_age_caps.get(rail), ENUM_AS_OF,
        )
        if stuck is not None and stuck > 0:
            pending[(leg.id,)] = stuck
        idle = stuck_unbundled_residual(
            leg, profile.unbundled_age_caps.get(rail), ENUM_AS_OF,
        )
        if idle is not None and idle > 0:
            unbundled[(leg.id,)] = idle
    return {"stuck_pending": pending, "stuck_unbundled": unbundled}


def _flow_cell(
    index: int,
    rail: str,
    direction: str,
    target: int,
    split: int,
    status_pattern: str,
    profile: BoundaryProfile,
    *,
    parent_role: str | None = _PARENT_ROLE,
    flip_direction: bool = False,
) -> PackedCell:
    """One (account, day-0) cell whose leg magnitudes sum to
    ``target`` on ``rail``. ``flip_direction`` plants the legs with
    the OPPOSITE sign of the schedule's direction (the no-cap
    cross-direction control)."""
    prefix = f"tlb{index:04d}"
    account = f"{prefix}a"
    effective_direction = (
        ("Inbound" if direction == "Outbound" else "Outbound")
        if flip_direction else direction
    )
    magnitudes = [target] if split == 1 else [1, target - 1]
    b = CellBuilder()
    for li, magnitude in enumerate(magnitudes):
        status = POSTED_STATUS
        if li == 0 and status_pattern == "first_pending":
            status = "Pending"
        if li == 0 and status_pattern == "first_zq9x":
            status = "Zq9x"
        leg_id = f"{prefix}L{li}"
        b.leg(
            id=leg_id, account=account,
            amount=_signed(effective_direction, magnitude), status=status,
            posting=dt.datetime.combine(_DAY0, dt.time(12, 0)),
            transfer=leg_id, rail=rail, parent_role=parent_role,
        )
    return PackedCell(
        *b.rows(), prefixes=(prefix,),
        expected={
            "limit_breach": _expected(
                b, account, (rail,), profile, parent_role=parent_role,
            ),
            **_threshold_spillover(b, profile),
        },
    )


def _cross_day_cell(index: int, profile: BoundaryProfile) -> PackedCell:
    role, rail, direction = _first_schedule(profile)
    cap = profile.limit_caps_cents[(role, rail, direction)]
    prefix = f"tlb{index:04d}"
    account = f"{prefix}a"
    b = CellBuilder()
    for li, (day, magnitude) in enumerate(((_DAY0, cap), (_DAY1, 1))):
        leg_id = f"{prefix}L{li}"
        b.leg(
            id=leg_id, account=account,
            amount=_signed(direction, magnitude), status=POSTED_STATUS,
            posting=dt.datetime.combine(day, dt.time(12, 0)),
            transfer=leg_id, rail=rail,
        )
    return PackedCell(
        *b.rows(), prefixes=(prefix,),
        expected={
            "limit_breach": _expected(b, account, (rail,), profile),
            **_threshold_spillover(b, profile),
        },
    )


def cells(profile: BoundaryProfile) -> list[PackedCell]:
    assert profile.limit_caps_cents, (
        "BoundaryProfile resolved no limit schedules — the domain "
        "would be vacuous"
    )
    role0, rail0, dir0 = _first_schedule(profile)
    cap0 = profile.limit_caps_cents[(role0, rail0, dir0)]
    out: list[PackedCell] = [
        _flow_cell(0, rail0, dir0, cap0, 1, "all_posted", profile),
        _flow_cell(1, rail0, dir0, cap0 + 1, 1, "all_posted", profile),
    ]
    index = 2
    for (_, rail, direction), cap in sorted(profile.limit_caps_cents.items()):
        for delta in _DELTAS:
            for split in (1, 2):
                for status_pattern in _STATUS_PATTERNS:
                    out.append(_flow_cell(
                        index, rail, direction, cap + delta, split,
                        status_pattern, profile,
                    ))
                    index += 1
    # Controls (all must stay invisible on both sides).
    for delta in _DELTAS:
        out.append(_flow_cell(
            index, UNCAPPED_RAIL, dir0, cap0 + delta, 1, "all_posted",
            profile,
        ))
        index += 1
    out.append(_flow_cell(
        index, rail0, dir0, cap0 + 1, 1, "all_posted", profile,
        flip_direction=True,
    ))
    index += 1
    out.append(_flow_cell(
        index, rail0, dir0, cap0 + 1, 1, "all_posted", profile,
        parent_role=None,
    ))
    index += 1
    out.append(_cross_day_cell(index, profile))
    return out


def _read_engine(db: EnumerationDB) -> ViolationMap:
    rows = db.fetchall(
        f"SELECT account_id, business_day, rail_name, direction, "
        f"outbound_total - cap FROM {db.prefix}_limit_breach",
    )
    return {
        (as_str(row[0]), as_date(row[1]), as_str(row[2]), as_str(row[3])):
            as_int(row[4])
        for row in rows
    }


CHECK: Final = DetectorCheck(
    detector="limit_breach", read_engine=_read_engine,
)
