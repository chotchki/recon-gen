"""Stuck unbundled (law 7) — the twin threshold domain (DS.0 §3).

Same shape as stuck_pending on (Posted, ``bundle_id IS NULL``), with
the extra ``bundle_id ∈ {NULL, '', 'b1'}`` axis — the nullable-string
witness from DS.0 finding 5. On DuckDB '' is NOT NULL, so an
empty-string bundle_id reads as "bundled" on BOTH sides (the residual
receives the raw value; the feed-boundary '' normalization is
upstream of the law and the Oracle ''≡NULL fork is DS.6's per-dialect
lane, not this gate's).

Age grid derives from the resolved ``max_unbundled_age`` caps via the
BoundaryProfile; strict-``>`` at-cap witness included; an uncapped
declared rail replays the grid and must stay invisible.
"""
from __future__ import annotations

import datetime as dt
from typing import Final

from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.spine.residuals import (
    current_legs,
    stuck_unbundled_residual,
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

_STATUSES: Final = (POSTED_STATUS, "Pending", "Zq9x")
_BUNDLES: Final = (None, "", "b1")
_DELTAS: Final = (-1, 0, 1)

# Fixed-index boundary-smoke pair (same pattern as stuck_pending):
# Posted + unbundled on the first capped rail at cap / cap+1.
AT_CAP_KEY: Final = ("tsu0000L0",)
OVER_CAP_KEY: Final = ("tsu0001L0",)


def covered_ages(profile: BoundaryProfile) -> dict[str, frozenset[int]]:
    return {
        rail: frozenset(cap + delta for delta in _DELTAS)
        for rail, cap in profile.unbundled_age_caps.items()
    }


def _one_cell(
    index: int,
    rail: str,
    cap: int | None,
    reference_cap: int,
    status: str,
    bundle: str | None,
    delta: int,
) -> PackedCell:
    prefix = f"tsu{index:04d}"
    account = f"{prefix}a"
    b = CellBuilder()
    b.leg(
        id=f"{prefix}L0", account=account, amount=0, status=status,
        posting=ENUM_AS_OF - dt.timedelta(seconds=reference_cap + delta),
        transfer=prefix, rail=rail, bundle=bundle,
    )
    state = b.state()
    expected: ViolationMap = {}
    for current in current_legs(state):
        residual = stuck_unbundled_residual(current, cap, ENUM_AS_OF)
        if residual is not None and residual > 0:
            expected[(current.id,)] = residual
    return PackedCell(
        *b.rows(), prefixes=(prefix,), expected={"stuck_unbundled": expected},
    )


def cells(profile: BoundaryProfile) -> list[PackedCell]:
    assert profile.unbundled_age_caps, (
        "BoundaryProfile resolved no max_unbundled_age caps — the "
        "domain would be vacuous"
    )
    assert UNCAPPED_RAIL not in profile.unbundled_age_caps, (
        f"{UNCAPPED_RAIL} unexpectedly declares an unbundled cap; pick "
        f"a different uncapped control rail"
    )
    reference_cap = min(profile.unbundled_age_caps.values())
    first_rail = sorted(profile.unbundled_age_caps)[0]
    first_cap = profile.unbundled_age_caps[first_rail]
    out: list[PackedCell] = [
        _one_cell(0, first_rail, first_cap, first_cap, POSTED_STATUS, None, 0),
        _one_cell(1, first_rail, first_cap, first_cap, POSTED_STATUS, None, 1),
    ]
    index = 2
    for rail, cap in sorted(profile.unbundled_age_caps.items()):
        for status in _STATUSES:
            for bundle in _BUNDLES:
                for delta in _DELTAS:
                    out.append(_one_cell(
                        index, rail, cap, cap, status, bundle, delta,
                    ))
                    index += 1
    for status in _STATUSES:
        for delta in _DELTAS:
            out.append(_one_cell(
                index, UNCAPPED_RAIL, None, reference_cap, status, None,
                delta,
            ))
            index += 1
    return out


def _read_engine(db: EnumerationDB) -> ViolationMap:
    rows = db.fetchall(
        f"SELECT transaction_id, "
        f"CAST(age_seconds AS BIGINT) - max_unbundled_age_seconds "
        f"FROM {db.prefix}_stuck_unbundled",
    )
    return {(as_str(row[0]),): as_int(row[1]) for row in rows}


CHECK: Final = DetectorCheck(
    detector="stuck_unbundled", read_engine=_read_engine,
)
