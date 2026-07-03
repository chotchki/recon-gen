"""Balance-cadence-gap (law 14, derivation) — the DS.5.1 canary domain.

Exhaustive engine-vs-residual over the cadence gap's two firing modes.
The comparator is gap-SET equality against the engine's (account_id,
business_day, gap_kind) projection (``cadence_gap_residual`` is exactly
the symmetric difference).

Frame control — a cadence gap depends on the WHOLE feed's day frame
(``[min, max]`` over every internal balance/leg day), so unrelated cells
in one packed DB would extend each other's explicit_daily gaps. A shared
FRAME ANCHOR (a sparse bystander with balance rows on the first + last
window day, no activity → zero gaps of its own) pins the frame to a
fixed window in every run, so each cell's per-account gaps are the same
isolated and packed (the packed-vs-isolated lemma holds by construction,
same reasoning as money_trail's dedicated DB).

Role cadence — stock spec_example declares explicit_daily as a SINGLETON
(``clearing-suspense``) only, so a per-cell prefixed explicit_daily
account is unrepresentable on it. A VARIANT instance adds an
``account_template`` with ``balance_cadence: explicit_daily`` so any
prefixed account carrying that role resolves explicit_daily — the same
config-injection route fan_in's variant used for its orphan branch. Role
accounts pack cleanly (they enter the universe only via observed rows),
which is why the enumeration uses them; the declared SINGLETON gaps
globally (its declaration binds regardless of rows), so it can't slice
per-cell — the anchor satisfies it and its zero-rows all-missing witness
(the DS.3.3c born-red case: a declared account absent from BOTH feeds
must still alarm every frame day) lives in KAT CG2.
"""
from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path
from typing import Final, cast

import yaml

from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.spine.residuals import (
    ResidualState,
    expected_cadence_gaps,
)
from tests.enumeration.domains._base import (
    SPEC_EXAMPLE,
    WINDOW_START,
    BoundaryProfile,
    as_date,
    as_str,
    profile_for,
)
from tests.enumeration.harness import (
    CellBuilder,
    DetectorCheck,
    EnumerationDB,
    PackedCell,
    PackedDomain,
    ViolationMap,
    artifacts_for,
)

_DAILY_ROLE: Final = "CadenceDailyStub"
_DECLARED_ROLE: Final = "ClearingSuspense"
_VARIANT_PREFIX: Final = "cadgap"
_NOON: Final = dt.time(12, 0)

# A four-day window: enough to witness first-day / middle / last-day /
# all-days gaps without the grid exploding.
_DAYS: Final = tuple(WINDOW_START + dt.timedelta(days=i) for i in range(4))
_ANCHOR_ID: Final = "cadframeanchor"

_VARIANT_PATH: list[Path] = []


def variant_l2_path() -> Path:
    """Write (once per process) the spec_example variant declaring an
    account_template with explicit_daily cadence, so prefixed accounts
    carrying its role enumerate the declared_daily_missing branch.
    Generated from the checked-in yaml so the two can't drift."""
    if _VARIANT_PATH:
        return _VARIANT_PATH[0]
    doc: dict[str, object] = yaml.safe_load(SPEC_EXAMPLE.read_text())
    assert isinstance(doc, dict)
    templates = cast("list[object]", doc["account_templates"])
    assert isinstance(templates, list)
    templates.append({
        "role": _DAILY_ROLE,
        "scope": "internal",
        "balance_cadence": "explicit_daily",
        "instance_id_template": "cadence-daily-{n:03d}",
        "instance_name_template": "Cadence Daily {n}",
        "description": (
            "DS.5.1 cadence enumeration variant: a role-declared "
            "explicit_daily template so prefixed accounts reach the "
            "declared_daily_missing branch."
        ),
    })
    path = Path(tempfile.mkdtemp(prefix="ds51-cadgap-")) / "variant.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    _VARIANT_PATH.append(path)
    return path


def _anchor(profile: BoundaryProfile) -> CellBuilder:
    """The frame anchor — two jobs:

    1. Pin the frame to the full window (a sparse bystander with balance
       rows on the first + last window day, no activity → zero gaps).
    2. Satisfy every DECLARED explicit_daily SINGLETON with a balance row
       on every frame day. A declared singleton gaps GLOBALLY (its
       declaration binds independent of any cell's rows), so left
       unsatisfied it would pollute every cell's per-account slice and
       break the packed-vs-isolated lemma. Satisfying it here keeps it
       silent everywhere; its own zero-rows all-missing witness lives in
       KAT CG2 (the born-red canary) — this domain enumerates the
       role-declared branch, which packs cleanly (role accounts enter
       the universe only via observed rows)."""
    b = CellBuilder()
    for day in (_DAYS[0], _DAYS[-1]):
        b.balance(account=_ANCHOR_ID, day=day, money=0)
    for account, cadence in sorted(profile.singleton_cadences.items()):
        if cadence == "explicit_daily":
            for day in _DAYS:
                b.balance(
                    account=account, day=day, money=0,
                    role=_DECLARED_ROLE, parent_role=None,
                )
    return b


def _explicit_cell(
    prefix: str, present_offsets: tuple[int, ...],
    *, start_time: dt.time = dt.time(0, 0),
) -> CellBuilder:
    """An explicit_daily account (role-resolved) with balance rows on
    the given window offsets; every other window day is a gap.
    ``start_time`` stamps a non-midnight business_day_start (an EOD
    cutover) — the residual still keys on the calendar day, so a
    compliant offset account gaps nothing (DS.5.4 witness)."""
    b = CellBuilder()
    account = f"{prefix}a"
    for offset in present_offsets:
        b.balance(
            account=account, day=_DAYS[offset], money=0,
            role=_DAILY_ROLE, parent_role=None, start_time=start_time,
        )
    return b


def _sparse_cell(
    prefix: str,
    *,
    balance_offsets: tuple[int, ...] = (),
    activities: tuple[tuple[int, str], ...] = (),
    supersede: tuple[int, str, str] | None = None,
) -> CellBuilder:
    """A sparse (default-cadence) account: balance rows on
    ``balance_offsets``, one leg per ``activities`` entry (offset,
    status). A gap fires on any activity day with no balance row when
    the leg's status is not Failed."""
    b = CellBuilder()
    account = f"{prefix}a"
    for offset in balance_offsets:
        b.balance(account=account, day=_DAYS[offset], money=0)
    for i, (offset, status) in enumerate(activities):
        b.leg(
            id=f"{prefix}L{i}", account=account, amount=100, status=status,
            posting=dt.datetime.combine(_DAYS[offset], _NOON),
            transfer=f"{prefix}t{i}",
        )
    if supersede is not None:
        offset, first_status, second_status = supersede
        posting = dt.datetime.combine(_DAYS[offset], _NOON)
        b.leg(
            id=f"{prefix}Sx", account=account, amount=100,
            status=first_status, posting=posting,
            transfer=f"{prefix}st",
        )
        b.leg(
            id=f"{prefix}Sx", account=account, amount=100,
            status=second_status, posting=posting,
            transfer=f"{prefix}st",
        )
    return b


def _combined_state(
    anchor: CellBuilder, cell: CellBuilder,
) -> ResidualState:
    a, c = anchor.state(), cell.state()
    return ResidualState(
        legs=a.legs + c.legs, balances=a.balances + c.balances,
    )


def _cell(
    profile: BoundaryProfile,
    anchor: CellBuilder,
    builder: CellBuilder,
    prefix: str,
) -> PackedCell:
    """Compute the cell's expected gap set as the residual over
    (anchor + cell), restricted to the cell's own prefix — anchor
    produces no gaps, so the restriction only drops keys the packing
    contract already excludes."""
    gaps = expected_cadence_gaps(
        _combined_state(anchor, builder),
        profile.singleton_cadences, profile.role_cadences,
    )
    expected: ViolationMap = {
        (g.account_id, g.day.isoformat(), g.gap_kind): None
        for g in gaps
        if g.account_id.startswith(prefix)
    }
    return PackedCell(
        *builder.rows(), prefixes=(prefix,),
        expected={"balance_cadence_gap": expected},
    )


def read_engine(db: EnumerationDB) -> ViolationMap:
    rows = db.fetchall(
        f"SELECT account_id, business_day_start, gap_kind "
        f"FROM {db.prefix}_balance_cadence_gap",
    )
    return {
        (as_str(row[0]), as_date(row[1]).isoformat(), as_str(row[2])): None
        for row in rows
    }


CHECK: Final = DetectorCheck(
    detector="balance_cadence_gap", read_engine=read_engine,
)


def _cells(profile: BoundaryProfile, anchor: CellBuilder) -> list[PackedCell]:
    out: list[PackedCell] = []

    def explicit(
        prefix: str, present: tuple[int, ...],
        *, start_time: dt.time = dt.time(0, 0),
    ) -> None:
        out.append(_cell(
            profile, anchor,
            _explicit_cell(prefix, present, start_time=start_time), prefix,
        ))

    # explicit_daily neighborhood: gaps at every position + healthy.
    explicit("cg00", (0, 1, 2, 3))          # healthy — no gap
    explicit("cg01", (0, 3))                # both middle days gap
    explicit("cg02", (1, 2, 3))             # first day gaps
    explicit("cg03", (0, 1, 2))             # last day gaps
    explicit("cg04", (0, 2))                # d1 + d3 gap
    explicit("cg05", ())                    # role-account, zero rows:
    #   NOT a singleton, so out of the universe — no gap either side.
    # DS.5.4 born-red witnesses: an EOD-cutover account (17:00
    # business_day_start) reporting EVERY day gaps NOTHING (the residual
    # keys on the calendar day); a partial one gaps only the day it
    # actually misses. Pre-fix, the timestamp-vs-DATE join reported
    # phantom gaps on every compliant day.
    explicit("cg06", (0, 1, 2, 3), start_time=dt.time(17, 0))  # compliant offset — silent
    explicit("cg07", (0, 1, 3), start_time=dt.time(17, 0))     # offset, misses d2 only

    def sparse(prefix: str, builder: CellBuilder) -> None:
        out.append(_cell(profile, anchor, builder, prefix))

    # sparse_with_activity witnesses.
    sparse("cg10", _sparse_cell(
        "cg10", balance_offsets=(0,), activities=((1, POSTED_STATUS),)))
    # row present on the activity day — no gap.
    sparse("cg11", _sparse_cell(
        "cg11", balance_offsets=(1,), activities=((1, POSTED_STATUS),)))
    # no balance row anywhere — leg-only frame entry still gaps.
    sparse("cg12", _sparse_cell("cg12", activities=((2, POSTED_STATUS),)))
    # Failed is not activity (existence predicate).
    sparse("cg13", _sparse_cell(
        "cg13", balance_offsets=(0,), activities=((1, "Failed"),)))
    # Pending IS activity.
    sparse("cg14", _sparse_cell(
        "cg14", balance_offsets=(0,), activities=((2, "Pending"),)))
    # superseded Posted -> Failed: current status Failed, not activity.
    sparse("cg15", _sparse_cell(
        "cg15", balance_offsets=(0,), supersede=(1, POSTED_STATUS, "Failed")))
    return out


def build_cadence_gap_domain() -> PackedDomain:
    profile = profile_for(variant_l2_path())
    anchor = _anchor(profile)
    anchor_tx, anchor_bal = anchor.rows()
    return PackedDomain(
        name="cadence_gap",
        artifacts=artifacts_for(variant_l2_path(), prefix=_VARIANT_PREFIX),
        cells=tuple(_cells(profile, anchor)),
        checks=(CHECK,),
        anchor_tx=anchor_tx,
        anchor_bal=anchor_bal,
    )
