"""XOR group (law 9) — the DS.0 §6.2 exhaustive domain, ported.

Cell = one Transfer of the xor-declaring template: a multiset of size
≤2 per member rail over (status {Posted, Pending, Failed, Zq9x} ×
day {0, 1}) plus an extra slot carrying either a non-member
template-stamped leg (SettlementSlow — anchors transfer existence
without firing the group) or a member-rail leg with a NULL template
(inert: the template partition never matches it). The spike measured
this grid at 22,275 cells with EXACT engine==residual agreement; this
port swaps the spike's hand-rolled residual for the canonical
``xor_group_residual`` (DS.1).

Members + group shape derive from the resolved instance via the
BoundaryProfile (config-injection lemma B: the VALUES rowset is
emit-time SQL text, so the theorem is instance-parametric).
"""
from __future__ import annotations

import datetime as dt
import itertools
from typing import Final

from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.spine.residuals import xor_group_residual
from tests.enumeration.domains._base import (
    WINDOW_START,
    BoundaryProfile,
    as_int,
    as_str,
)
from tests.enumeration.harness import (
    CellBuilder,
    DetectorCheck,
    EnumerationDB,
    PackedCell,
    ViolationMap,
)

TEMPLATE: Final = "SettlementTimingCycle"
NON_MEMBER_RAIL: Final = "SettlementSlow"

_STATUSES: Final = (POSTED_STATUS, "Pending", "Failed", "Zq9x")
_DAYS: Final = (WINDOW_START, WINDOW_START + dt.timedelta(days=1))

# Member-leg atom: (status, day index). Extra-slot options: absent, a
# SLOW leg per (status, day), or the two NULL-template member probes.
type MemberAtom = tuple[str, int]
type ExtraOpt = tuple[str, str, int] | None

_MEMBER_ATOMS: Final[tuple[MemberAtom, ...]] = tuple(
    (status, day) for status in _STATUSES for day in (0, 1)
)
_EXTRA_OPTS: Final[tuple[ExtraOpt, ...]] = (
    (None,)
    + tuple(("SLOW", status, day) for status in _STATUSES for day in (0, 1))
    + (("NOTMPL", POSTED_STATUS, 0), ("NOTMPL", "Zq9x", 0))
)


def _member_multisets() -> list[tuple[MemberAtom, ...]]:
    out: list[tuple[MemberAtom, ...]] = [()]
    for n in (1, 2):
        out.extend(itertools.combinations_with_replacement(_MEMBER_ATOMS, n))
    return out


def _posting(day_index: int) -> dt.datetime:
    return dt.datetime.combine(_DAYS[day_index], dt.time(12, 0))


def cells(profile: BoundaryProfile) -> list[PackedCell]:
    groups = profile.xor_groups.get(TEMPLATE)
    assert groups is not None and len(groups) == 1, (
        f"spec_example's xor declaration moved — expected exactly one "
        f"group on {TEMPLATE}, resolved {groups!r}"
    )
    members = sorted(groups[0])
    assert len(members) == 2 and NON_MEMBER_RAIL not in groups[0]
    member_a, member_b = members
    multisets = _member_multisets()
    out: list[PackedCell] = []
    index = 0
    for legs_a in multisets:
        for legs_b in multisets:
            for extra in _EXTRA_OPTS:
                prefix = f"xr{index:06d}"
                account = f"{prefix}a"
                b = CellBuilder()
                n = 0
                for rail, legs in ((member_a, legs_a), (member_b, legs_b)):
                    for status, day in legs:
                        b.leg(
                            id=f"{prefix}L{n}", account=account, amount=0,
                            status=status, posting=_posting(day),
                            transfer=prefix, rail=rail, template=TEMPLATE,
                            parent_role=None,
                        )
                        n += 1
                if extra is not None:
                    tag, status, day = extra
                    b.leg(
                        id=f"{prefix}L{n}", account=account, amount=0,
                        status=status, posting=_posting(day),
                        transfer=prefix,
                        rail=NON_MEMBER_RAIL if tag == "SLOW" else member_a,
                        template=TEMPLATE if tag == "SLOW" else None,
                        parent_role=None,
                    )
                residual = xor_group_residual(
                    b.state(), prefix, TEMPLATE, frozenset(members),
                )
                expected: ViolationMap = {}
                if residual is not None and residual != 0:
                    expected[(prefix, TEMPLATE, 0)] = residual
                out.append(PackedCell(
                    *b.rows(), prefixes=(prefix,),
                    expected={"xor_group": expected},
                ))
                index += 1
    return out


def _read_engine(db: EnumerationDB) -> ViolationMap:
    rows = db.fetchall(
        f"SELECT transfer_id, template_name, xor_group_index, "
        f"firing_count - 1 FROM {db.prefix}_xor_group_violation",
    )
    return {
        (as_str(row[0]), as_str(row[1]), as_int(row[2])): as_int(row[3])
        for row in rows
    }


CHECK: Final = DetectorCheck(detector="xor_group", read_engine=_read_engine)
