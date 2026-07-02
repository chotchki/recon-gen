"""DS.3.6 — the metamorphic suite: state transforms with exact
violation-set responses, on the real engine.

Every law here runs a TRANSFORM of a modest packed base state through
the unmodified emitters (real schema, real config populate, real
refresh order) and asserts EXACTLY how the engine's violation sets
respond — equality where the law says inert, a residual-predicted
patch where the law says moved. The base state alarms every
registered detector (non-vacuity is itself a test), so inertness is
proven against live sets.

Laws (the DS.0 §5 supersession/LOCF lemma assignments):

- FAILED-LEG INERTNESS — a Failed leg, and separately an
  unknown-status leg (deterministically random from
  RECON_GEN_FUZZ_SEED so no detector can hardcode it back into a
  pass), changes NO money-family violation set.
- BALANCED-EXTERNAL — a balanced two-leg Posted transfer between two
  EXTERNAL-scope accounts changes no internal-scope violation set.
- INSERT-ORDER PERMUTATION — with entry values PINNED EXPLICITLY,
  permuting the physical insert order changes nothing anywhere (the
  DS.0 correction: under sequence-assigned entries a permutation
  changes the supersession winners — that is a semantics change, not
  an invariance; pin the entries, permute only the order).
- SUPERSESSION IDEMPOTENCE — re-emitting a leg / balance claim with
  identical content at a higher entry changes nothing; re-emitting
  with a CHANGED amount moves exactly the residual-predicted cells.
- DEDUP-COMMUTE — superseding before or after adding an unrelated
  transfer commutes.
- ANOMALY z-invariances, scoped exactly as the DS.0 attack corrected:
  LOCATION invariance on DENSE per-pair histories only (a sparse
  frame makes the law false — the rolling window skips inactive days,
  so a per-day shift moves windows unevenly and a new active day
  mints a new population row); the min-n floor and the stddev=0 guard
  asserted EXACTLY (integer guards). Bucket-level SCALE claims are
  OUT of scope here — band-edge epsilon semantics are DS.4's
  tolerance contract, not a metamorphic law.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random

import pytest

from recon_gen.common.env_keys import RECON_GEN_FUZZ_SEED
from recon_gen.common.spine.residuals import ZERO
from tests.enumeration import metamorphic as meta
from tests.enumeration.domains import _locf
from tests.enumeration.harness import ViolationMap, diff_violations


def fuzz_status() -> str:
    """A deterministically-random unknown status, derived like
    tests/unit/test_ds33_status_law.py::fuzz_status (own salt so the
    two suites can't converge on one hardcodable value)."""
    seed = RECON_GEN_FUZZ_SEED.get_or_none() or "0"
    return "Zz" + hashlib.sha256(f"ds36-{seed}".encode()).hexdigest()[:6]


# ---------------------------------------------------------------------------
# Cached base run — rows built once, base DB read once per process.


_STATE: list[meta.BaseState] = []
_MAPS: list[dict[str, ViolationMap]] = []
_ANOM: dict[bool, dict[tuple[str, str, dt.date], meta.AnomalyRow]] = {}


def _base() -> meta.BaseState:
    if not _STATE:
        _STATE.append(meta.build_base())
    return _STATE[0]


def _base_maps() -> dict[str, ViolationMap]:
    if not _MAPS:
        base = _base()
        db = meta.build_db(list(base.tx), list(base.bal))
        try:
            _MAPS.append(meta.read_all(db))
        finally:
            db.close()
    return _MAPS[0]


def _read_transformed(
    extra_tx: list[meta.EntriedTx],
    extra_bal: list[meta.EntriedBal] | None = None,
) -> dict[str, ViolationMap]:
    base = _base()
    db = meta.build_db(
        list(base.tx) + extra_tx, list(base.bal) + (extra_bal or []),
    )
    try:
        # Sanity: the appended legs actually landed — a transform that
        # silently failed to insert would pass every inertness law.
        ids = sorted({row.id for row, _ in extra_tx})
        if ids:
            id_list = ", ".join(f"'{leg_id}'" for leg_id in ids)
            rows = db.fetchall(
                f"SELECT COUNT(DISTINCT id) "
                f"FROM {db.prefix}_transactions WHERE id IN ({id_list})",
            )
            assert rows[0][0] == len(ids), (
                f"transform rows missing from the base table: "
                f"landed {rows[0][0]!r} of {ids}"
            )
        return meta.read_all(db)
    finally:
        db.close()


def _assert_maps_equal(
    got: dict[str, ViolationMap],
    want: dict[str, ViolationMap],
    detectors: tuple[str, ...],
    label: str,
) -> None:
    problems: list[str] = []
    for detector in detectors:
        diff = diff_violations(
            got[detector], want[detector], label=f"{label}/{detector}",
        )
        if diff:
            problems.append(diff)
    assert not problems, "\n".join(problems)


ALL_DETECTORS = tuple(check.detector for check in meta.ALL_CHECKS)
NON_TRAIL_DETECTORS = tuple(
    d for d in ALL_DETECTORS if d != "money_trail"
)


def test_base_state_alarms_every_detector() -> None:
    """Non-vacuity gate for the whole suite: every registered
    detector's baseline violation set is non-empty, so the inertness
    laws below compare live sets, not empty ones."""
    maps = _base_maps()
    empty = sorted(d for d in ALL_DETECTORS if not maps[d])
    assert not empty, (
        f"metamorphic base state leaves detectors silent: {empty} — "
        f"inertness laws would pass vacuously"
    )


@pytest.mark.parametrize("status_kind", ["failed", "unknown"])
def test_failed_and_unknown_status_legs_are_money_inert(
    status_kind: str,
) -> None:
    """FAILED-LEG INERTNESS: a Failed leg — and separately a
    fuzz-derived unknown-status leg — added to the drift account (it
    would move the computed balance if counted) and to the at-cap
    limit account (it would mint a NEW breach key if counted) changes
    no money-family violation set."""
    status = "Failed" if status_kind == "failed" else fuzz_status()
    base = _base()
    got = _read_transformed(meta.status_probe_rows(base, status))
    _assert_maps_equal(
        got, _base_maps(), meta.MONEY_DETECTORS,
        f"failed-leg-inertness[{status}]",
    )


def test_balanced_external_transfer_is_internal_scope_inert() -> None:
    """BALANCED-EXTERNAL: a balanced two-leg Posted transfer between
    two EXTERNAL-scope accounts changes no internal-scope violation
    set. money_trail is asserted separately — the derivation walk is
    scope-blind by design (DS.0 §3: edges are labeled by reachability,
    not scope), so the transform mints EXACTLY the one new root edge
    and nothing else."""
    base = _base()
    got = _read_transformed(meta.balanced_external_rows(base))
    _assert_maps_equal(
        got, _base_maps(), NON_TRAIL_DETECTORS, "balanced-external",
    )
    expected_trail = dict(_base_maps()["money_trail"])
    expected_trail[(
        meta.EXTERNAL_TRANSFER, f"{meta.EXTERNAL_TRANSFER}s",
        f"{meta.EXTERNAL_TRANSFER}r", 0,
    )] = None
    diff = diff_violations(
        got["money_trail"], expected_trail,
        label="balanced-external/money_trail",
    )
    assert not diff, diff


def test_insert_order_permutation_with_pinned_entries() -> None:
    """INSERT-ORDER PERMUTATION: with every entry pinned explicitly,
    reversing the physical insert order — and a seeded random shuffle
    of it — yields identical violation sets across every detector.
    The base state carries pre-superseded pairs, so a physical-order
    dependence in the supersession argmax would surface loudly."""
    base = _base()
    baseline = _base_maps()
    seed = RECON_GEN_FUZZ_SEED.get_or_none() or 42
    rng = random.Random(seed)
    shuffled_tx = list(base.tx)
    shuffled_bal = list(base.bal)
    rng.shuffle(shuffled_tx)
    rng.shuffle(shuffled_bal)
    orders: dict[str, tuple[list[meta.EntriedTx], list[meta.EntriedBal]]] = {
        "reversed": (list(reversed(base.tx)), list(reversed(base.bal))),
        f"shuffled(seed={seed})": (shuffled_tx, shuffled_bal),
    }
    for name, (tx, bal) in orders.items():
        db = meta.build_db(tx, bal)
        try:
            got = meta.read_all(db)
        finally:
            db.close()
        _assert_maps_equal(
            got, baseline, ALL_DETECTORS, f"insert-order-permutation[{name}]",
        )


def test_supersession_idempotence_identical_re_emit() -> None:
    """SUPERSESSION IDEMPOTENCE, identical half: re-emitting the
    drift cell's leg AND its balance claim with byte-identical content
    at higher entries changes nothing anywhere."""
    base = _base()
    got = _read_transformed(
        [meta.superseding_leg_row(base, amount=40)],
        [meta.superseding_balance_row(base)],
    )
    _assert_maps_equal(
        got, _base_maps(), ALL_DETECTORS, "supersession-idempotence",
    )


def test_supersession_correction_moves_residual_predicted_cells() -> None:
    """SUPERSESSION IDEMPOTENCE, changed half: correcting the drift
    leg's amount moves EXACTLY the cells the DS.1 residuals predict —
    the drift account's money-family keys are re-derived from the
    corrected residual state (same construction site as the engine
    rows) and every other detector set stays byte-identical."""
    corrected_amount = 90
    base = _base()
    baseline = _base_maps()
    got = _read_transformed(
        [meta.superseding_leg_row(base, amount=corrected_amount)],
    )
    pre = _locf.money_family_expected(
        meta.drift_cell_state(corrected_amount=None),
        (base.drift_account,), meta.WINDOW,
    )
    post = _locf.money_family_expected(
        meta.drift_cell_state(corrected_amount=corrected_amount),
        (base.drift_account,), meta.WINDOW,
    )
    assert pre != post, "correction predicted no movement — vacuous"
    for detector in meta.MONEY_DETECTORS:
        predicted = {
            key: value
            for key, value in baseline[detector].items()
            if key not in pre.get(detector, {})
        }
        predicted.update(post.get(detector, {}))
        diff = diff_violations(
            got[detector], predicted,
            label=f"supersession-correction/{detector}",
        )
        assert not diff, diff
    non_money = tuple(
        d for d in ALL_DETECTORS if d not in meta.MONEY_DETECTORS
    )
    _assert_maps_equal(
        got, baseline, non_money, "supersession-correction/non-money",
    )


def test_dedup_commute() -> None:
    """DEDUP-COMMUTE: applying a supersession before or after adding
    an unrelated transfer commutes — same final sets either way (the
    supersession row keeps its pinned entry in both orders; only the
    physical interleaving differs). Both results must also differ
    from the untouched base, or the commute is vacuous."""
    base = _base()
    supersede = meta.superseding_leg_row(base, amount=90, entry_offset=1)
    unrelated = meta.unrelated_transfer_rows(base, entry_offset=2)
    order_a = list(base.tx) + [supersede] + unrelated
    order_b = list(base.tx) + unrelated + [supersede]
    results: list[dict[str, ViolationMap]] = []
    for order in (order_a, order_b):
        db = meta.build_db(order, list(base.bal))
        try:
            results.append(meta.read_all(db))
        finally:
            db.close()
    _assert_maps_equal(
        results[0], results[1], ALL_DETECTORS, "dedup-commute",
    )
    baseline = _base_maps()
    assert results[0]["drift"] != baseline["drift"], (
        "the supersession op changed nothing — commute is vacuous"
    )
    assert results[0]["xor_group"] != baseline["xor_group"], (
        "the unrelated-transfer op changed nothing — commute is vacuous"
    )


# ---------------------------------------------------------------------------
# Anomaly z-invariances (probabilistic detector — the integer window
# layer only; tolerance bands and band-edge epsilon are DS.4's).


def _anomaly_maps(
    *, shifted: bool,
) -> dict[tuple[str, str, dt.date], meta.AnomalyRow]:
    cached = _ANOM.get(shifted)
    if cached is None:
        db = meta.build_db(meta.anomaly_rows(shifted=shifted), [])
        try:
            cached = meta.read_anomalies(db)
        finally:
            db.close()
        assert cached, "anomaly fixture produced no pair windows"
        _ANOM[shifted] = cached
    return cached


def test_anomaly_location_invariance_on_dense_history() -> None:
    """LOCATION invariance, DENSE history only: shifting the dense
    pair's alternating days by a constant moves EVERY rolling window
    by exactly that constant (the exact preimage of a uniform
    window-level location shift), so per-window z_score AND z_bucket
    are IDENTICAL while window_sum and pop_mean shift by exactly the
    constant. Sparse frames are excluded by construction — the law is
    false there (uneven window coverage; new active days mint new
    population rows)."""
    plain = _anomaly_maps(shifted=False)
    shifted = _anomaly_maps(shifted=True)
    dense_keys = sorted(
        key for key in plain
        if key[0] == meta.DENSE_SENDER and key[1] == meta.DENSE_RECIPIENT
    )
    assert len(dense_keys) == len(meta.DENSE_DAY_SUMS), (
        f"dense pair should surface one window per active day, got "
        f"{dense_keys}"
    )
    assert set(dense_keys) == {
        key for key in shifted
        if key[0] == meta.DENSE_SENDER and key[1] == meta.DENSE_RECIPIENT
    }, "the shift minted or dropped dense-pair windows — not a pure shift"
    moved = 0
    for key in dense_keys:
        before, after = plain[key], shifted[key]
        assert after.window_sum == before.window_sum + meta.LOCATION_SHIFT, (
            f"{key}: window_sum shifted by "
            f"{after.window_sum - before.window_sum}, expected the "
            f"uniform {meta.LOCATION_SHIFT}"
        )
        assert after.pop_mean == before.pop_mean + meta.LOCATION_SHIFT, key
        assert after.pop_stddev == before.pop_stddev, key
        assert after.z_score == before.z_score, (
            f"{key}: z moved under a pure location shift — "
            f"{before.z_score} -> {after.z_score}"
        )
        assert after.z_bucket == before.z_bucket, key
        moved += 1
    assert moved and any(
        plain[key].z_score != 0 for key in dense_keys
    ), "dense pair has no live z — invariance asserted vacuously"
    # Untouched pairs are byte-identical across the two states.
    other_keys = sorted(set(plain) - set(dense_keys))
    for key in other_keys:
        assert shifted[key] == plain[key], key


def test_anomaly_min_n_floor_is_exact() -> None:
    """The min-n floor is an EXACT integer guard: a pair with history
    one window short of the floor reads z = 0 on every row — even
    against a wild spike, and provably via the FLOOR arm (its sample
    stddev is huge, so the stddev=0 arm cannot be the reason) — while
    a pair with exactly floor-many windows computes a live z."""
    maps = _anomaly_maps(shifted=False)
    floor_rows = [
        row for (s, r, _), row in maps.items()
        if s == meta.FLOOR_SENDER and r == meta.FLOOR_RECIPIENT
    ]
    assert len(floor_rows) == 2, "floor pair should carry two windows"
    for row in floor_rows:
        assert row.z_score == 0, (
            f"below-floor pair computed a live z: {row}"
        )
        assert row.z_bucket == "0-1 sigma", row
        assert row.pop_stddev != 0, (
            "floor witness degenerated — stddev is zero, so the guard "
            "arm (not the floor arm) produced the zero"
        )
    live_rows = [
        row for (s, r, _), row in maps.items()
        if s == meta.LIVE_SENDER and r == meta.LIVE_RECIPIENT
    ]
    assert len(live_rows) == 3, "live pair should carry three windows"
    assert any(row.z_score != 0 for row in live_rows), (
        "at-floor pair floored to z=0 — the boundary moved"
    )


def test_anomaly_stddev_zero_guard_is_exact() -> None:
    """The stddev=0 guard is exact: a pair whose windows are all equal
    (gap-spaced equal days — each rolling window is a single day)
    reads pop_stddev exactly 0 and z exactly 0, with enough history
    that the min-n floor is NOT the arm that fired. No division-by-
    zero escape, no NULL leak."""
    maps = _anomaly_maps(shifted=False)
    guard_rows = [
        row for (s, r, _), row in maps.items()
        if s == meta.GUARD_SENDER and r == meta.GUARD_RECIPIENT
    ]
    assert len(guard_rows) == 3, (
        "guard pair should carry one window per gap-spaced day"
    )
    for row in guard_rows:
        assert row.pop_stddev == 0, row
        assert row.z_score == 0, row
        assert row.z_bucket == "0-1 sigma", row


def test_zero_residual_sanity() -> None:
    """Guard the suite's own arithmetic: the drift cell used by the
    supersession laws is a REAL violation pre-correction (stored 100
    vs computed 40) and its residual sign convention matches the
    engine (reported minus calculated)."""
    from recon_gen.common.spine.residuals import drift_residual

    state = meta.drift_cell_state(corrected_amount=None)
    residual = drift_residual(state, meta.DRIFT_ACCOUNT, meta.WINDOW[0])
    assert residual is not None and residual != ZERO
    assert residual.value == 60
