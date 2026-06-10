"""BV.3.3.c.bug4 — pin the signature picker shape for chain-coherence
matviews.

Background: ``tests/e2e/app2/test_bv33_trainer_dogfood.py::
_v_matview_signatures`` derives a per-matview signature shape by
intersecting the matview's actual columns (read via
``cursor.description``) with two priority lists — ID columns
(``transfer_id`` / ``child_transfer_id`` / ``parent_transfer_id`` /
``account_id`` / ``rail_name`` / ``direction``) and date columns
(``business_day`` / ``business_day_start``). The diff against the
BEFORE snapshot is the load-bearing claim for "the planted row IS
in the matview".

BV.3.3.c.bug4 false-positive root cause: pre-fix the picker
collapsed any matview whose ID-column intersection happened to be
empty down to a date-only signature. A signature of
``(business_day,)`` matched EVERY row on that day in the rendered
HTML, so the surface assertion passed for any plant that happened
to land on a populated day — false positive.

Fix: the picker now SPLITS its priority list into ID + date
columns and raises ``AssertionError`` if no ID column intersects.
Date columns may augment a signature but never stand alone.

This test pins the shape per chain-coherence matview against a
hand-extracted column set drawn from
``src/recon_gen/common/l2/schema.py``. When a future refactor
renames a column or adds a new chain-coherence matview, this test
re-prompts the BV.3.3.c.bug4 reviewer to check whether the picker
still produces a non-date-only signature.
"""

from __future__ import annotations

import pytest

# The picker + its priority lists are test-tree internals (live with
# the e2e test that consumes them); import them directly so the
# unit-tier shape pin doesn't depend on running the e2e flow.
from tests.e2e.app2.test_bv33_trainer_dogfood import (
    _SIGNATURE_DATE_COLUMNS,
    _SIGNATURE_ID_COLUMNS,
)


# Hand-extracted column sets per L1 invariant matview, drawn from
# `src/recon_gen/common/l2/schema.py`. Keys are matview suffixes
# (everything after the `<prefix>_` token); values are the COMPLETE
# column lists in declaration order. Sync drift between this map and
# the schema is the failure mode this test catches — keep them
# matched by hand (low maintenance: matview columns turn over rarely).
_MATVIEW_COLUMNS: dict[str, tuple[str, ...]] = {
    # AB.2.3 chain_parent_disagreement
    "chain_parent_disagreement": (
        "transfer_id", "child_template_name", "business_day",
        "distinct_parent_count",
        "parent_transfer_id_min", "parent_transfer_id_max",
    ),
    # AB.3.3 xor_group_violation
    "xor_group_violation": (
        "transfer_id", "template_name", "xor_group_index",
        "firing_count", "fired_rails", "business_day",
    ),
    # AB.4.7 fan_in_disagreement
    "fan_in_disagreement": (
        "child_transfer_id", "chain_parent_name",
        "child_template_name", "parent_count",
        "expected_parent_count", "disagreement_kind", "business_day",
    ),
    # AB.6.5 multi_xor_violation
    "multi_xor_violation": (
        "parent_transfer_id", "parent_rail_or_template_name",
        "child_count", "fired_children", "disagreement_kind",
        "business_day",
    ),
}


def _picker(cols: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Inline reimplementation of the picker's column-selection
    logic. Mirroring the live code keeps this test honest — when
    `_v_matview_signatures` shape drifts, this scaffolding must
    drift in lock-step or the test stops asserting what it claims to.
    """
    cset = {c.lower() for c in cols}
    ids = [c for c in _SIGNATURE_ID_COLUMNS if c in cset]
    dates = [c for c in _SIGNATURE_DATE_COLUMNS if c in cset]
    return ids, dates


# ---------------------------------------------------------------------------
# BV.3.3.c.bug4 — every chain-coherence matview must yield ≥1 ID column.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "matview,cols",
    sorted(_MATVIEW_COLUMNS.items()),
    ids=lambda x: x if isinstance(x, str) else "cols",
)
def test_chain_coherence_signature_has_id_column(
    matview: str, cols: tuple[str, ...],
) -> None:
    """Every chain-coherence matview's signature picks at least one
    ID column. A date-only signature would re-introduce the
    BV.3.3.c.bug4 false-positive — match-on-anchor-date.
    """
    ids, _dates = _picker(cols)
    assert ids, (
        f"chain-coherence matview {matview!r} has no ID column in its "
        f"signature (cols={cols!r}). The picker would degrade to a "
        f"date-only signature — BV.3.3.c.bug4 false-positive vector. "
        f"Extend _SIGNATURE_ID_COLUMNS or pick a real identity column "
        f"for this matview."
    )


def test_chain_parent_disagreement_signature_shape() -> None:
    """Pin: chain_parent_disagreement's signature is
    ``(transfer_id, business_day)``. The matview's GROUP BY is
    ``(transfer_id, template_name)``, so transfer_id IS the row
    identity; business_day rides along for the surface-assertion
    cross-check.
    """
    ids, dates = _picker(_MATVIEW_COLUMNS["chain_parent_disagreement"])
    assert ids == ["transfer_id"]
    assert dates == ["business_day"]


def test_xor_group_violation_signature_shape() -> None:
    """Pin: xor_group_violation's signature is ``(transfer_id,
    business_day)``. The matview's GROUP BY adds template_name +
    xor_group_index but transfer_id alone uniquely identifies the
    plant row for diff purposes (the violation is per-Transfer).
    """
    ids, dates = _picker(_MATVIEW_COLUMNS["xor_group_violation"])
    assert ids == ["transfer_id"]
    assert dates == ["business_day"]


def test_fan_in_disagreement_signature_shape() -> None:
    """Pin: fan_in_disagreement's signature is ``(child_transfer_id,
    business_day)``. The matview's primary key is child_transfer_id
    (the multi-parent child); plant emits one child per firing so
    diff narrowing on child_transfer_id is tight.
    """
    ids, dates = _picker(_MATVIEW_COLUMNS["fan_in_disagreement"])
    assert ids == ["child_transfer_id"]
    assert dates == ["business_day"]


def test_multi_xor_violation_signature_shape() -> None:
    """Pin: multi_xor_violation's signature is ``(parent_transfer_id,
    business_day)``. The matview's GROUP BY is the parent firing's
    transfer_id; XOR-sibling children share that key.
    """
    ids, dates = _picker(_MATVIEW_COLUMNS["multi_xor_violation"])
    assert ids == ["parent_transfer_id"]
    assert dates == ["business_day"]


# ---------------------------------------------------------------------------
# BV.3.3.c.bug4 — the picker's ID + date lists must stay disjoint.
# ---------------------------------------------------------------------------


def test_id_and_date_priorities_are_disjoint() -> None:
    """The structural guard `picked_ids` vs `picked_dates` only
    holds if the two priority lists carry no overlapping columns.
    A column appearing in both would land in `picked_ids` AND
    `picked_dates` (via separate ``in`` checks against the same
    `cols` set), producing duplicate columns in the signature SQL
    — a DISTINCT-narrowing footgun.
    """
    overlap = set(_SIGNATURE_ID_COLUMNS) & set(_SIGNATURE_DATE_COLUMNS)
    assert not overlap, (
        f"_SIGNATURE_ID_COLUMNS and _SIGNATURE_DATE_COLUMNS overlap "
        f"on {overlap!r} — split each column into exactly one list."
    )
