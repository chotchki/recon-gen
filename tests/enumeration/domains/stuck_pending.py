"""Stuck pending (law 6) — ages straddling the L2-RESOLVED cap.

The cap is CONFIG DATA (``v_config_rails.max_pending_age_seconds``),
so the age grid derives from the resolved instance via the
BoundaryProfile — never from SQL text (the DS.0 attack finding). Axes
per capped rail:

- age ∈ {cap-1, cap, cap+1} seconds relative to the owned ``as_of``
  frame (``ENUM_AS_OF``, written to the config kv at populate time
  and passed explicitly to the residual — never wall-clock). The
  at-cap cell is the strict-``>`` boundary witness: exactly-at-cap is
  NOT stuck.
- status ∈ {Posted, Pending, Failed, Zq9x} — only Pending ages; the
  unknown tail must not.
- supersession ∈ {plain, corrected}: ``corrected`` first feeds a
  Pending leg old enough to be stuck, then supersedes it (same id,
  higher entry) with the cell's status + posting — the detector must
  read the CURRENT leg only.

An uncapped declared rail (no ``max_pending_age``) replays the same
grid and must stay invisible (cap None ⇒ never stuck).
"""
from __future__ import annotations

import datetime as dt
from typing import Final

from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.spine.residuals import (
    current_legs,
    stuck_pending_residual,
)
from tests.enumeration.domains._base import (
    BoundaryProfile,
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

_STATUSES: Final = (POSTED_STATUS, "Pending", "Failed", "Zq9x")
_DELTAS: Final = (-1, 0, 1)

# Planted-boundary smoke anchors: cells() builds these two cells FIRST
# (fixed indices 0 + 1, so the leg ids stay inside their cell's id
# prefix for the isolated-lemma restriction). The gate asserts the
# at-cap leg is NOT in the engine set and the over-cap leg IS.
AT_CAP_KEY: Final = ("tsp0000L0",)
OVER_CAP_KEY: Final = ("tsp0001L0",)


def covered_ages(profile: BoundaryProfile) -> dict[str, frozenset[int]]:
    """Ages (in seconds) the domain exercises per capped rail — the
    coverage-lint surface."""
    return {
        rail: frozenset(cap + delta for delta in _DELTAS)
        for rail, cap in profile.pending_age_caps.items()
    }


def _one_cell(
    index: int,
    rail: str,
    cap: int | None,
    reference_cap: int,
    status: str,
    delta: int,
    corrected: bool,
) -> PackedCell:
    prefix = f"tsp{index:04d}"
    account = f"{prefix}a"
    leg = f"{prefix}L0"
    posting = ENUM_AS_OF - dt.timedelta(seconds=reference_cap + delta)
    b = CellBuilder()
    if corrected:
        # The superseded original: stuck-old Pending, then corrected.
        b.leg(
            id=leg, account=account, amount=0, status="Pending",
            posting=ENUM_AS_OF - dt.timedelta(seconds=reference_cap + 3600),
            transfer=prefix, rail=rail,
        )
    b.leg(
        id=leg, account=account, amount=0, status=status, posting=posting,
        transfer=prefix, rail=rail,
    )
    state = b.state()
    expected: ViolationMap = {}
    for current in current_legs(state):
        residual = stuck_pending_residual(current, cap, ENUM_AS_OF)
        if residual is not None and residual > 0:
            expected[(current.id,)] = residual
    return PackedCell(
        *b.rows(), prefixes=(prefix,), expected={"stuck_pending": expected},
    )


def cells(profile: BoundaryProfile) -> list[PackedCell]:
    assert profile.pending_age_caps, (
        "BoundaryProfile resolved no max_pending_age caps — the domain "
        "would be vacuous (the coverage lint's reason to exist)"
    )
    assert UNCAPPED_RAIL not in profile.pending_age_caps, (
        f"{UNCAPPED_RAIL} unexpectedly declares a pending cap; pick a "
        f"different uncapped control rail"
    )
    reference_cap = min(profile.pending_age_caps.values())
    # The named boundary-smoke pair at fixed indices 0 + 1: Pending on
    # the first capped rail, exactly at cap (NOT stuck — strict >) and
    # one second over. AT_CAP_KEY / OVER_CAP_KEY name their leg ids.
    first_rail = sorted(profile.pending_age_caps)[0]
    first_cap = profile.pending_age_caps[first_rail]
    out: list[PackedCell] = [
        _one_cell(0, first_rail, first_cap, first_cap, "Pending", 0, False),
        _one_cell(1, first_rail, first_cap, first_cap, "Pending", 1, False),
    ]
    index = 2
    for rail, cap in sorted(profile.pending_age_caps.items()):
        for status in _STATUSES:
            for delta in _DELTAS:
                for corrected in (False, True):
                    out.append(_one_cell(
                        index, rail, cap, cap, status, delta, corrected,
                    ))
                    index += 1
    for status in _STATUSES:
        for delta in _DELTAS:
            out.append(_one_cell(
                index, UNCAPPED_RAIL, None, reference_cap, status, delta,
                False,
            ))
            index += 1
    return out


def _read_engine(db: EnumerationDB) -> ViolationMap:
    rows = db.fetchall(
        f"SELECT transaction_id, "
        f"CAST(age_seconds AS BIGINT) - max_pending_age_seconds "
        f"FROM {db.prefix}_stuck_pending",
    )
    return {(as_str(row[0]),): as_int(row[1]) for row in rows}


CHECK: Final = DetectorCheck(
    detector="stuck_pending", read_engine=_read_engine,
)
