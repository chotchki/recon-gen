"""DW.3 — high-watermark validator for L1 audit agreement.

ONE validator per L1 invariant. Each reads the 3 producer artifacts
(db direct + db PDF + app2) and asserts the chain:

    scenario_plants ⊆ direct == App2  (== PDF, drift only)

The producers already each ran THEIR side of "did this renderer
render something?" + producer-side lower bound — by the time the
validator fires, every renderer's individual sanity is established
on its own tier. The validator's job is the cross-renderer equality.

(QS was the fourth leg until DW.3; with QuickSight removed, direct-DB
is the truth anchor and App2 the corroborator — the PDF still joins
for drift, the one flat-shape invariant whose audit section is
one-row-per-matview-row.)

The `@inputs(...)` marker on each validator names the producer test
nodeids; `tests/conftest.py::pytest_collection_modifyitems` checks
at collection time that every referenced nodeid exists, so
moving / renaming / deleting a producer SCREAMS at collection time
instead of silently detaching the validator at runtime.

**Important runner contract**: the validator can only run as part of
`./run_tests.sh up_to=agreement` — that's the only invocation that
collects all the producer tiers (db + app2) into one pytest run +
satisfies `@inputs(...)` resolution. Bare `pytest tests/e2e/agreement/`
fails at collection time because the producer nodeids in db/ and
app2/ aren't in the collection.
"""

from __future__ import annotations

from typing import Any

from tests._marks import inputs
from tests.e2e._agreement import read_rendered_rows


# Common nodeid prefixes — keep the @inputs() strings readable.
_DIRECT = "tests/e2e/db/test_audit_direct.py::test_l1_invariant_direct_extract"
_PDF = "tests/e2e/db/test_audit_direct.py::test_l1_invariant_pdf_count"
_APP2 = "tests/e2e/app2/test_audit_invariants_app2.py::test_l1_invariant_app2_extract"


def _read_count_meta(layer: str, name: str, key: str) -> Any:
    """Read the first dict's `key` field from a meta artifact —
    the producer writes a single-element list of dicts (the count +
    expected_count sidecar). Standalone-friendly helper."""
    payload = read_rendered_rows(layer, name)
    assert payload, f"meta artifact {layer}/{name}.json is empty"
    return payload[0][key]


def _row_keys(layer: str, name: str) -> set[tuple[Any, ...]]:
    """Read row payload, project to a set of natural-key tuples for
    set-equality comparison."""
    rows = read_rendered_rows(layer, name)
    return {tuple(row["natural_key"]) for row in rows if "natural_key" in row}


# ---------------------------------------------------------------------------
# Per-invariant validators. The `@inputs(...)` marker names every producer
# nodeid this validator depends on; the conftest's collection-time check
# verifies they all exist before the test ever runs.
# ---------------------------------------------------------------------------


@inputs(_DIRECT, _PDF, _APP2)
def test_drift_agreement() -> None:
    """Agreement for `drift` — the row-identity case.

    Contract:
      scenario_plants ⊆ direct == App2 == PDF (drift is the
      flat-shape invariant where the PDF section is one-row-per-
      matview-row, so PDF count equals the matview count exactly).

    Row-identity: direct == App2 (set equality of natural keys).

    See module docstring for the runner-chain contract.
    """
    direct_count = _read_count_meta("db", "drift_direct_meta", "direct_count")
    expected_count = _read_count_meta(
        "db", "drift_direct_meta", "expected_count",
    )
    pdf_count = _read_count_meta("db", "drift_pdf_counts", "pdf_count")
    app2_count = _read_count_meta("app2", "drift_app2_meta", "app2_count")

    # Cross-renderer count equality (the meaningful drift contract).
    assert direct_count == app2_count, (
        f"drift count mismatch: direct={direct_count} "
        f"app2={app2_count}"
    )
    assert pdf_count == direct_count, (
        f"drift PDF disagrees with matview: pdf={pdf_count} "
        f"direct={direct_count} — credibility contract broken."
    )

    # Producer-side lower bound (kept here as well for symmetry; the
    # producers already assert their own, but this surfaces seed →
    # validator drift one extra place).
    assert direct_count >= expected_count, (
        f"drift direct count below scenario: direct={direct_count} "
        f"expected={expected_count}"
    )

    # Row-identity — direct keys == App2 keys.
    direct_keys = _row_keys("db", "drift_direct_rows")
    app2_keys = _row_keys("app2", "drift_app2_rows")
    assert direct_keys == app2_keys, (
        f"drift row-identity disagreement direct/app2:\n"
        f"  direct-only: {sorted(direct_keys - app2_keys)[:5]}\n"
        f"  app2-only: {sorted(app2_keys - direct_keys)[:5]}"
    )


@inputs(_DIRECT, _PDF, _APP2)
def test_overdraft_agreement() -> None:
    """Agreement for `overdraft` — flat-shape invariant.

    direct == App2 (row-identity); PDF count >= expected (the PDF
    section aggregates into a parent-per-row table for overdraft, so
    pdf == direct doesn't hold — only `>= expected`).
    """
    _assert_flat_shape("overdraft")


@inputs(_DIRECT, _PDF, _APP2)
def test_limit_breach_agreement() -> None:
    """Agreement for `limit_breach` — flat-shape invariant.
    Same shape as overdraft (row-identity for direct vs App2,
    `pdf >= expected`).
    """
    _assert_flat_shape("limit_breach")


@inputs(_DIRECT, _PDF, _APP2)
def test_stuck_pending_agreement() -> None:
    """Agreement for `stuck_pending` — divergent-shape invariant.

    Count-level only: direct == App2; PDF aggregates into a
    parent-per-row + child-grouped roll-up table so `pdf >= expected`
    is the meaningful PDF check.
    """
    _assert_divergent_shape("stuck_pending")


@inputs(_DIRECT, _PDF, _APP2)
def test_stuck_unbundled_agreement() -> None:
    """Agreement for `stuck_unbundled` — divergent-shape."""
    _assert_divergent_shape("stuck_unbundled")


@inputs(_DIRECT, _PDF, _APP2)
def test_supersession_agreement() -> None:
    """Agreement for `supersession` — special case.

    Has NO clean matview anchor (the dashboard's "Transactions Audit"
    table + the audit PDF each query their own shape over base tables).
    The validator falls back to producer-side bounds (`pdf >=
    expected`, `app2 >= expected`).
    """
    expected_count = _read_count_meta(
        "db", "supersession_direct_meta", "expected_count",
    )
    pdf_count = _read_count_meta(
        "db", "supersession_pdf_counts", "pdf_count",
    )
    app2_count = _read_count_meta(
        "app2", "supersession_app2_meta", "app2_count",
    )

    assert pdf_count >= expected_count, (
        f"supersession PDF below scenario: pdf={pdf_count} "
        f"expected={expected_count}"
    )
    assert app2_count >= expected_count, (
        f"supersession App2 below scenario: app2={app2_count} "
        f"expected={expected_count}"
    )


# ---------------------------------------------------------------------------
# Shared helpers — pulled out so the per-invariant body stays a one-liner
# call + the agreement contract is in one place per shape.
# ---------------------------------------------------------------------------


def _assert_flat_shape(invariant: str) -> None:
    """Common assertion shape for flat-shape invariants (overdraft,
    limit_breach). drift gets its own body because PDF count == direct
    for it; here PDF only needs `>= expected`.
    """
    direct_count = _read_count_meta(
        "db", f"{invariant}_direct_meta", "direct_count",
    )
    expected_count = _read_count_meta(
        "db", f"{invariant}_direct_meta", "expected_count",
    )
    pdf_count = _read_count_meta(
        "db", f"{invariant}_pdf_counts", "pdf_count",
    )
    app2_count = _read_count_meta(
        "app2", f"{invariant}_app2_meta", "app2_count",
    )

    assert direct_count >= expected_count, (
        f"{invariant} direct < expected: direct={direct_count} "
        f"expected={expected_count}"
    )
    assert pdf_count >= expected_count, (
        f"{invariant} PDF < expected: pdf={pdf_count} "
        f"expected={expected_count}"
    )
    assert direct_count == app2_count, (
        f"{invariant} direct/App2 count disagree: direct={direct_count} "
        f"app2={app2_count}"
    )

    # Row-identity for direct vs App2.
    direct_keys = _row_keys("db", f"{invariant}_direct_rows")
    app2_keys = _row_keys("app2", f"{invariant}_app2_rows")
    assert direct_keys == app2_keys, (
        f"{invariant} row-identity disagreement direct/app2:\n"
        f"  direct-only: {sorted(direct_keys - app2_keys)[:5]}\n"
        f"  app2-only: {sorted(app2_keys - direct_keys)[:5]}"
    )


def _assert_divergent_shape(invariant: str) -> None:
    """Common shape for divergent-shape invariants (stuck_pending,
    stuck_unbundled). Count-level: direct == App2; PDF `>= expected`
    (aggregates).
    """
    direct_count = _read_count_meta(
        "db", f"{invariant}_direct_meta", "direct_count",
    )
    expected_count = _read_count_meta(
        "db", f"{invariant}_direct_meta", "expected_count",
    )
    pdf_count = _read_count_meta(
        "db", f"{invariant}_pdf_counts", "pdf_count",
    )
    app2_count = _read_count_meta(
        "app2", f"{invariant}_app2_meta", "app2_count",
    )

    if direct_count is not None:
        assert direct_count >= expected_count, (
            f"{invariant} direct < expected: direct={direct_count} "
            f"expected={expected_count}"
        )
    assert pdf_count >= expected_count, (
        f"{invariant} PDF < expected: pdf={pdf_count} "
        f"expected={expected_count}"
    )
    if direct_count is not None:
        assert direct_count == app2_count, (
            f"{invariant} direct/App2 count disagree: "
            f"direct={direct_count} app2={app2_count}"
        )
