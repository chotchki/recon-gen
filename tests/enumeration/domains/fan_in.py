"""Fan-in disagreement (law 10) — parent counts around the resolved
expectation, plus the unset-expectation (orphan) variant instance.

Spec_example declares one fan_in child (BatchPayoutTrigger →
BatchedPayoutBatch, expected_parent_count = 2). Cells enumerate
parent counts {1, 2, 3} (the {e-1, e, e+1} neighborhood) × leg-status
patterns × an optional NULL-parent anchor leg. The orphan branch
(expected UNSET ⇒ require ≥2) is unrepresentable on stock
spec_example, so a VARIANT instance (spec_example + one chain:
InternalBalanceMaintenance → CardLoadCycle fan_in with no
expected_parent_count) carries it in its own packed DB — enumeration
is instance-parametric by design (DS.0 §5, config-injection lemma).
``expected = 1`` from the DS.0 sketch's {None, 1, 2} axis is
deliberately not declared: it exercises no SQL branch that
{None, 2} doesn't already cover (future-axis candidate, noted here).

KNOWN DIVERGENCE — the zero-parent blind spot (DS.3.5 FINDING,
2026-07-02). The signed law (``fan_in_residual``) gives a cell to any
child transfer with firing legs: zero parent claims + expected 2 ⇒
residual −2 ('missing' — the WORST corruption shape, no contribution
landed at all). The engine structurally cannot represent it:
``transfer_parents`` only materializes rows for legs with a non-NULL
``transfer_parent_id``, and ``child_parent_counts`` builds FROM it,
so a zero-parent child never reaches the CASE. One-of-two-missing
alarms; all-missing is INVISIBLE. Fix requires production SQL (seed
child_parent_counts from the child template's firings, not from the
parent-claim table) — out of this task's write scope, so the packed
domain EXCLUDES parent_count 0 and the gate pins the divergence
executable-and-loud in ``test_fan_in_zero_parent_engine_blind_spot``
(both sides asserted: engine empty, law says -2). Do NOT silence
that test by narrowing the residual; the law side is the signed
DS.0 kit.
"""
from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path
from typing import Final, cast

import yaml

from recon_gen.common.l2.primitives import POSTED_STATUS
from recon_gen.common.spine.residuals import fan_in_residual
from tests.enumeration.domains._base import (
    SPEC_EXAMPLE,
    WINDOW_START,
    BoundaryProfile,
    as_int,
    as_opt_str,
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

SPEC_CHAIN_PARENT: Final = "BatchPayoutTrigger"
SPEC_CHILD_TEMPLATE: Final = "BatchedPayoutBatch"
_SPEC_CHILD_RAIL: Final = "BatchPayoutClose"

VARIANT_CHAIN_PARENT: Final = "InternalBalanceMaintenance"
VARIANT_CHILD_TEMPLATE: Final = "CardLoadCycle"
_VARIANT_CHILD_RAIL: Final = "CardLoadCardholderCredit"
VARIANT_PREFIX: Final = "fanvar"

_POSTING: Final = dt.datetime.combine(WINDOW_START, dt.time(12, 0))
_PATTERNS: Final = ("all_posted", "all_pending", "all_zq9x", "first_failed")


def covered_parent_counts(
    profile: BoundaryProfile,
) -> dict[tuple[str, str], frozenset[int]]:
    """Parent counts exercised per declared fan_in child — the
    coverage-lint surface. For a set expectation e that is
    {e-1, e, e+1}; for the unset (orphan) rule the boundary is at 2:
    {1, 2, 3}. Count 0 is the documented blind-spot exclusion."""
    out: dict[tuple[str, str], frozenset[int]] = {}
    for key, expected in profile.fan_in_expected.items():
        boundary = 2 if expected is None else expected
        out[key] = frozenset({boundary - 1, boundary, boundary + 1})
    return out


def _build_cell(
    prefix: str,
    *,
    parent_count: int,
    pattern: str,
    anchor: bool,
    template: str,
    rail: str,
    chain_parent: str,
    expected_count: int | None,
) -> PackedCell:
    transfer = prefix
    account = f"{prefix}a"
    b = CellBuilder()
    for j in range(parent_count):
        status = POSTED_STATUS
        if pattern == "all_pending":
            status = "Pending"
        elif pattern == "all_zq9x":
            status = "Zq9x"
        elif pattern == "first_failed" and j == 0:
            status = "Failed"
        b.leg(
            id=f"{prefix}L{j}", account=account, amount=0, status=status,
            posting=_POSTING, transfer=transfer, parent=f"{prefix}P{j}",
            rail=rail, template=template, parent_role=None,
        )
    if anchor:
        b.leg(
            id=f"{prefix}Lx", account=account, amount=0,
            status=POSTED_STATUS, posting=_POSTING, transfer=transfer,
            parent=None, rail=rail, template=template, parent_role=None,
        )
    residual = fan_in_residual(b.state(), transfer, expected_count)
    expected: ViolationMap = {}
    if residual is not None and residual != 0:
        expected[(transfer, chain_parent)] = residual
    return PackedCell(
        *b.rows(), prefixes=(prefix,), expected={"fan_in": expected},
    )


def _firing_parent_claims(parent_count: int, pattern: str) -> int:
    """Parent-claim legs that survive the firing-status law — the
    engine can only see the cell when this is >= 1."""
    if pattern == "all_zq9x":
        return 0
    if pattern == "first_failed":
        return max(0, parent_count - 1)
    return parent_count


def cells(profile: BoundaryProfile) -> list[PackedCell]:
    expected_count = profile.fan_in_expected.get(
        (SPEC_CHAIN_PARENT, SPEC_CHILD_TEMPLATE),
    )
    assert expected_count is not None, (
        "spec_example's fan_in expectation moved — the {e-1, e, e+1} "
        "grid below assumes a SET expected_parent_count"
    )
    out: list[PackedCell] = []
    index = 0
    for parent_count in (
        expected_count - 1, expected_count, expected_count + 1,
    ):
        for pattern in _PATTERNS:
            for anchor in (False, True):
                if anchor and not _firing_parent_claims(
                    parent_count, pattern,
                ):
                    # The zero-parent blind-spot class (module
                    # docstring): the law gives these a cell, the
                    # engine structurally cannot. Excluded here;
                    # pinned loud in the gate's finding test.
                    continue
                out.append(_build_cell(
                    f"fi{index:04d}",
                    parent_count=parent_count, pattern=pattern,
                    anchor=anchor, template=SPEC_CHILD_TEMPLATE,
                    rail=_SPEC_CHILD_RAIL, chain_parent=SPEC_CHAIN_PARENT,
                    expected_count=expected_count,
                ))
                index += 1
    return out


def zero_parent_finding_rows() -> tuple[PackedCell, int]:
    """The blind-spot witness: one child transfer, one Posted
    template-stamped leg, ZERO parent claims. Returns the cell and
    the LAW's residual for it (what the engine cannot say)."""
    cell = _build_cell(
        "fz0000", parent_count=0, pattern="all_posted", anchor=True,
        template=SPEC_CHILD_TEMPLATE, rail=_SPEC_CHILD_RAIL,
        chain_parent=SPEC_CHAIN_PARENT, expected_count=2,
    )
    law_residual = cell.expected["fan_in"].get(("fz0000", SPEC_CHAIN_PARENT))
    assert isinstance(law_residual, int)
    return cell, law_residual


def read_engine(db: EnumerationDB) -> ViolationMap:
    """Shared by the spec + variant domains. The value is the
    residual reconstructed from the ROW'S OWN fields (parent_count vs
    its expected column — the orphan rule when expected is NULL); the
    disagreement_kind is cross-checked against the value's sign so a
    mislabeled row cannot slip through as a matching key."""
    rows = db.fetchall(
        f"SELECT child_transfer_id, chain_parent_name, parent_count, "
        f"expected_parent_count, disagreement_kind "
        f"FROM {db.prefix}_fan_in_disagreement",
    )
    out: ViolationMap = {}
    for row in rows:
        transfer = as_str(row[0])
        chain_parent = as_str(row[1])
        count = as_int(row[2])
        expected = None if row[3] is None else as_int(row[3])
        kind = as_opt_str(row[4])
        if expected is None:
            value = count - 2 if count < 2 else 0
            expected_kind = "orphan"
        else:
            value = count - expected
            expected_kind = "missing" if value < 0 else "extra"
        if kind != expected_kind:
            raise AssertionError(
                f"fan_in row {transfer!r} carries kind {kind!r} but its "
                f"own counts say {expected_kind!r}",
            )
        out[(transfer, chain_parent)] = value
    return out


CHECK: Final = DetectorCheck(detector="fan_in", read_engine=read_engine)


# ---------------------------------------------------------------------------
# The variant instance (orphan branch).


_VARIANT_PATH: list[Path] = []


def variant_l2_path() -> Path:
    """Write (once per process) the spec_example variant declaring a
    fan_in child with UNSET expected_parent_count. Generated from the
    checked-in yaml at run time so the two can never drift."""
    if _VARIANT_PATH:
        return _VARIANT_PATH[0]
    doc: dict[str, object] = yaml.safe_load(SPEC_EXAMPLE.read_text())
    assert isinstance(doc, dict)
    chains = cast("list[object]", doc["chains"])
    assert isinstance(chains, list)
    chains.append({
        "parent": VARIANT_CHAIN_PARENT,
        "children": [{"name": VARIANT_CHILD_TEMPLATE, "fan_in": True}],
        "description": (
            "DS.3.5 enumeration variant: fan_in child with UNSET "
            "expected_parent_count so the orphan branch is reachable."
        ),
    })
    path = Path(tempfile.mkdtemp(prefix="ds35-fanvar-")) / "variant.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    _VARIANT_PATH.append(path)
    return path


def build_variant_domain() -> PackedDomain:
    profile = profile_for(variant_l2_path())
    unset = profile.fan_in_expected.get(
        (VARIANT_CHAIN_PARENT, VARIANT_CHILD_TEMPLATE), 2,
    )
    assert unset is None, "variant chain failed to leave expected unset"
    cells_out: list[PackedCell] = []
    index = 0
    for parent_count in (1, 2, 3):
        for pattern in ("all_posted", "all_pending"):
            cells_out.append(_build_cell(
                f"fv{index:04d}",
                parent_count=parent_count, pattern=pattern, anchor=False,
                template=VARIANT_CHILD_TEMPLATE, rail=_VARIANT_CHILD_RAIL,
                chain_parent=VARIANT_CHAIN_PARENT, expected_count=None,
            ))
            index += 1
    return PackedDomain(
        name="fan_in_variant",
        artifacts=artifacts_for(variant_l2_path(), prefix=VARIANT_PREFIX),
        cells=tuple(cells_out),
        checks=(CHECK,),
    )
