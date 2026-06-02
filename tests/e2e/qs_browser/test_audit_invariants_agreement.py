"""CB.5 stage 2 — high-watermark validator for L1 audit agreement.

ONE validator per L1 invariant. Each reads the 4 producer artifacts
(db direct + db PDF + app2 + qs_browser) and asserts the chain:

    scenario_plants ⊆ direct == QS == App2  (== PDF, drift only)

The producers already each ran THEIR side of "did this renderer
render something?" + producer-side lower bound — by the time the
validator fires, every renderer's individual sanity is established
on its own tier. The validator's job is the cross-renderer equality.

The `@inputs(...)` marker on each validator names the producer test
nodeids; `tests/conftest.py::pytest_collection_modifyitems` checks
at collection time that every referenced nodeid exists, so
moving / renaming / deleting a producer SCREAMS at collection time
instead of silently detaching the validator at runtime.

**Important runner contract**: the validator can only run as part of
`./run_tests.sh up_to=browser` — that's the only invocation that
collects all 4 tiers (db + app2 + qs_browser) into one pytest run +
satisfies `@inputs(...)` resolution. Bare `pytest tests/e2e/qs_browser/`
fails at collection time because the producer nodeids in db/ and
app2/ aren't in the collection.
"""

from __future__ import annotations

from typing import Any

from tests._marks import inputs
from tests.e2e._agreement import read_rendered_rows


# Common nodeid prefixes — keep the @inputs() strings readable.
_DIRECT = "tests/e2e/db/test_audit_invariants_direct.py::test_l1_invariant_direct_extract"
_PDF = "tests/e2e/db/test_audit_invariants_pdf.py::test_l1_invariant_pdf_count"
_APP2 = "tests/e2e/app2/test_audit_invariants_app2.py::test_l1_invariant_app2_extract"
_QS = "tests/e2e/qs_browser/test_audit_invariants_qs.py::test_l1_invariant_qs_extract"


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


@inputs(_DIRECT, _PDF, _APP2, _QS)
def test_drift_four_way_agreement() -> None:
    """4-way agreement for `drift` — the row-identity case.

    Contract:
      scenario_plants ⊆ direct == QS == App2 == PDF (drift is the
      flat-shape invariant where the PDF section is one-row-per-
      matview-row, so PDF count equals the matview count exactly).

    Row-identity: direct == App2 == QS (set equality of natural keys).

    See module docstring for the runner-chain contract.
    """
    direct_count = _read_count_meta("db", "drift_direct_meta", "direct_count")
    expected_count = _read_count_meta(
        "db", "drift_direct_meta", "expected_count",
    )
    pdf_count = _read_count_meta("db", "drift_pdf_counts", "pdf_count")
    app2_count = _read_count_meta("app2", "drift_app2_meta", "app2_count")
    qs_available = _read_count_meta(
        "qs_browser", "drift_qs_meta", "qs_available",
    )
    qs_count = _read_count_meta("qs_browser", "drift_qs_meta", "qs_count")

    # Cross-renderer count equality (the meaningful drift contract).
    assert direct_count == app2_count, (
        f"drift count mismatch: direct={direct_count} "
        f"app2={app2_count}"
    )
    assert pdf_count == direct_count, (
        f"drift PDF disagrees with matview: pdf={pdf_count} "
        f"direct={direct_count} — credibility contract broken."
    )
    if qs_available:
        assert qs_count == direct_count, (
            f"drift QS disagrees with matview: qs={qs_count} "
            f"direct={direct_count}"
        )

    # Producer-side lower bound (kept here as well for symmetry; the
    # producers already assert their own, but this surfaces seed →
    # validator drift one extra place).
    assert direct_count >= expected_count, (
        f"drift direct count below scenario: direct={direct_count} "
        f"expected={expected_count}"
    )

    # Row-identity — direct keys == App2 keys; + QS keys when QS leg
    # ran (drift is the one QS keys are extracted for; see
    # `test_audit_invariants_qs.py`'s B.3-followon note).
    direct_keys = _row_keys("db", "drift_direct_rows")
    app2_keys = _row_keys("app2", "drift_app2_rows")
    assert direct_keys == app2_keys, (
        f"drift row-identity disagreement direct/app2:\n"
        f"  direct-only: {sorted(direct_keys - app2_keys)[:5]}\n"
        f"  app2-only: {sorted(app2_keys - direct_keys)[:5]}"
    )
    if qs_available:
        qs_keys = _row_keys("qs_browser", "drift_qs_rows")
        assert direct_keys == qs_keys, (
            f"drift row-identity disagreement direct/qs:\n"
            f"  direct-only: {sorted(direct_keys - qs_keys)[:5]}\n"
            f"  qs-only: {sorted(qs_keys - direct_keys)[:5]}"
        )


@inputs(_DIRECT, _PDF, _APP2, _QS)
def test_overdraft_four_way_agreement() -> None:
    """4-way agreement for `overdraft` — flat-shape invariant.

    direct == App2 (row-identity); QS count == direct when QS ran;
    PDF count >= expected (the PDF section aggregates into a
    parent-per-row table for overdraft, so pdf == direct doesn't
    hold — only `>= expected`).
    """
    _assert_flat_shape("overdraft")


@inputs(_DIRECT, _PDF, _APP2, _QS)
def test_limit_breach_four_way_agreement() -> None:
    """4-way agreement for `limit_breach` — flat-shape invariant.
    Same shape as overdraft (row-identity for direct vs App2, count
    for QS, `pdf >= expected`).
    """
    _assert_flat_shape("limit_breach")


@inputs(_DIRECT, _PDF, _APP2, _QS)
def test_stuck_pending_four_way_agreement() -> None:
    """4-way agreement for `stuck_pending` — divergent-shape invariant.

    Count-level only: direct == App2 == (QS when available); PDF
    aggregates into a parent-per-row + child-grouped roll-up table
    so `pdf >= expected` is the meaningful PDF check.
    """
    _assert_divergent_shape("stuck_pending")


@inputs(_DIRECT, _PDF, _APP2, _QS)
def test_stuck_unbundled_four_way_agreement() -> None:
    """4-way agreement for `stuck_unbundled` — divergent-shape."""
    _assert_divergent_shape("stuck_unbundled")


@inputs(_DIRECT, _PDF, _APP2, _QS)
def test_supersession_four_way_agreement() -> None:
    """4-way agreement for `supersession` — special case.

    Has NO clean matview anchor (the dashboard's "Transactions Audit"
    table + the audit PDF each query their own shape over base tables).
    The validator falls back to renderer-vs-renderer count equality
    (App2 == QS when QS available) + producer-side bounds (`pdf >=
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
    qs_available = _read_count_meta(
        "qs_browser", "supersession_qs_meta", "qs_available",
    )
    qs_count = _read_count_meta(
        "qs_browser", "supersession_qs_meta", "qs_count",
    )

    assert pdf_count >= expected_count, (
        f"supersession PDF below scenario: pdf={pdf_count} "
        f"expected={expected_count}"
    )
    assert app2_count >= expected_count, (
        f"supersession App2 below scenario: app2={app2_count} "
        f"expected={expected_count}"
    )
    if qs_available:
        assert qs_count == app2_count, (
            f"supersession renderers disagree: qs={qs_count} "
            f"app2={app2_count}"
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
    qs_available = _read_count_meta(
        "qs_browser", f"{invariant}_qs_meta", "qs_available",
    )
    qs_count = _read_count_meta(
        "qs_browser", f"{invariant}_qs_meta", "qs_count",
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
    if qs_available:
        assert qs_count == direct_count, (
            f"{invariant} QS disagrees with matview: qs={qs_count} "
            f"direct={direct_count}"
        )

    # Row-identity for direct vs App2 (the QS row-identity check
    # extends only to drift currently — see qs producer's note).
    direct_keys = _row_keys("db", f"{invariant}_direct_rows")
    app2_keys = _row_keys("app2", f"{invariant}_app2_rows")
    assert direct_keys == app2_keys, (
        f"{invariant} row-identity disagreement direct/app2:\n"
        f"  direct-only: {sorted(direct_keys - app2_keys)[:5]}\n"
        f"  app2-only: {sorted(app2_keys - direct_keys)[:5]}"
    )


def _assert_divergent_shape(invariant: str) -> None:
    """Common shape for divergent-shape invariants (stuck_pending,
    stuck_unbundled). Count-level: direct == App2; QS count == direct
    when available; PDF `>= expected` (aggregates).
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
    qs_available = _read_count_meta(
        "qs_browser", f"{invariant}_qs_meta", "qs_available",
    )
    qs_count = _read_count_meta(
        "qs_browser", f"{invariant}_qs_meta", "qs_count",
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
    if qs_available and direct_count is not None:
        assert qs_count == direct_count, (
            f"{invariant} QS disagrees with matview: qs={qs_count} "
            f"direct={direct_count}"
        )
