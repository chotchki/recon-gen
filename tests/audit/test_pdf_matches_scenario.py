"""DB-layer audit/scenario agreement test (U.8.c — producer side).

Three-leg contract from U.8.b:
    expected (from scenario) == PDF (from extractor) == dashboard
                                                        (Playwright)

This test covers the first two legs — no browser needed. Seeds a
real DB via ``apply_db_seed``, renders an audit PDF, then asserts
``count_invariant_table_rows(pdf, X) == expected_audit_counts(
scenario, period).X_count`` for every L1 invariant. Catches
regressions in the SQL / matview / PDF rendering pipeline that
drift from what the scenario primitives planted, BEFORE the
gated three-way job (U.8.b.3 onward) ever runs.

**Destructive — drops + recreates schema for the test L2 instance.**
Gated behind ``QS_GEN_DB_TESTS=1`` so a developer's local DB
isn't wiped by accident. CI sets the env var; local dev opts in
explicitly when running the test.

Skips entirely when:
  - ``QS_GEN_DB_TESTS != "1"``
  - The configured ``demo_database_url`` is unset (skeleton mode
    — no DB to seed)
  - The required driver for the configured dialect isn't installed
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from quicksight_gen.cli import main
from quicksight_gen.common.config import load_config
from quicksight_gen.common.db import connect_demo_db
from quicksight_gen.common.env_keys import (
    EnvVarInvalid,
    QS_GEN_CONFIG,
    QS_GEN_DB_TESTS,
)
from quicksight_gen.common.l2 import load_instance
from quicksight_gen.common.l2.auto_scenario import default_scenario_for
from quicksight_gen.common.sql import Dialect

from tests.audit._pdf_extract import (
    Invariant,
    count_invariant_table_rows,
)
from tests.audit._scenario_expectations import expected_audit_counts


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _FIXTURES / "spec_example.yaml"

# Anchor plant effective dates on the real wall-clock today rather
# than a pinned future date (the harness's M.2a.8 convention). The
# stuck_pending / stuck_unbundled matviews compute age via
# ``CURRENT_TIMESTAMP - posting`` — plants pinned to a far-future
# date would land in the SQL future relative to NOW, making
# ``age > max_pending_age_seconds`` impossible to satisfy. Anchoring
# on real today keeps plant postings in the past where the matview
# can see them. Days-ago offsets stay deterministic; only the
# absolute calendar date varies.
_TODAY = date.today()
_PERIOD: tuple[date, date] = (
    _TODAY - timedelta(days=7),
    _TODAY - timedelta(days=1),
)


# Every L1 invariant the audit covers + the U.8.a expectations
# helper exposes. Keep aligned with _INVARIANT_TITLES in
# _pdf_extract.py and the X_count fields on ExpectedAuditCounts.
_ALL_INVARIANTS: tuple[Invariant, ...] = (
    "drift",
    "overdraft",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "supersession",
)


pytestmark = pytest.mark.skipif(
    not QS_GEN_DB_TESTS.get_or_none(),
    reason="Destructive DB tests gated on QS_GEN_DB_TESTS=1",
)


def _resolve_explicit_qs_gen_config() -> Path | None:
    """Read QS_GEN_CONFIG via the registry but soft-fall on the
    must_be_file validator failing — this fixture's discovery loop
    has fallback candidates, so a bad pin should degrade rather than
    raise inside test setup."""
    try:
        return QS_GEN_CONFIG.get_or_none()
    except EnvVarInvalid:
        return None


@pytest.fixture(scope="module")
def db_cfg():
    """Load cfg from the standard candidates; skip if no DB configured."""
    explicit = _resolve_explicit_qs_gen_config()
    candidates: tuple[Path, ...]
    if explicit is not None:
        candidates = (explicit,)
    else:
        candidates = (
            Path("config.yaml"),
            Path("run/config.yaml"),
            Path("run/config.postgres.yaml"),
            Path("run/config.oracle.yaml"),
        )
    cfg = None
    for candidate in candidates:
        if candidate.exists():
            cfg = load_config(str(candidate))
            break
    if cfg is None or cfg.demo_database_url is None:
        pytest.skip(
            "No demo_database_url configured — set "
            "QS_GEN_DEMO_DATABASE_URL or point QS_GEN_CONFIG at "
            "a config.yaml carrying it."
        )
    return cfg


@pytest.fixture(scope="module")
def db_cfg_path(db_cfg) -> Path:
    """Locate the cfg file on disk so we can pass `-c` to ``audit apply``."""
    explicit = _resolve_explicit_qs_gen_config()
    if explicit is not None:
        return explicit
    for candidate in (
        Path("config.yaml"),
        Path("run/config.yaml"),
        Path("run/config.postgres.yaml"),
        Path("run/config.oracle.yaml"),
    ):
        if candidate.exists():
            return candidate
    pytest.fail(
        "db_cfg loaded but no candidate config path on disk — "
        "QS_GEN_CONFIG override resolution mismatch."
    )


@pytest.fixture(scope="module")
def seeded_pdf(db_cfg, db_cfg_path, tmp_path_factory) -> tuple[Path, object]:
    """Seed DB with the spec_example scenario, render audit PDF.

    Module-scoped so the expensive seed + render runs once and
    every per-invariant assertion reuses the same artifacts.
    Returns ``(pdf_path, scenario)`` — the scenario carries the
    plant tuples U.8.a's expected_audit_counts needs.
    """
    from tests.e2e._harness_seed import apply_db_seed

    instance = load_instance(_SPEC_EXAMPLE)

    # 1. Apply schema + seed + matview refresh against the live DB.
    dialect = (
        Dialect.ORACLE
        if (db_cfg.dialect or "").lower() == "oracle"
        else Dialect.POSTGRES
    )
    conn = connect_demo_db(db_cfg)
    try:
        scenario = apply_db_seed(
            conn, instance,
            mode="l1_invariants",
            today=_TODAY,
            dialect=dialect,
            include_baseline=False,
        )
    finally:
        conn.close()

    # 2. Render audit PDF for the period that contains the planted
    # effective dates.
    out = tmp_path_factory.mktemp("audit-pdf") / "report.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(db_cfg_path),
            "--l2", str(_SPEC_EXAMPLE),
            "--from", _PERIOD[0].isoformat(),
            "--to", _PERIOD[1].isoformat(),
            "-o", str(out),
            "--execute",
        ],
    )
    assert result.exit_code == 0, result.output
    return (out, scenario)


def test_at_least_one_invariant_has_planted_rows(seeded_pdf):
    """Sanity guard: spec_example's auto-scenario plants at least
    one row in some invariant — otherwise every per-invariant
    assert below would be a vacuous 0 == 0."""
    _, scenario = seeded_pdf
    expected = expected_audit_counts(scenario, _PERIOD)
    counts = (
        expected.drift_count + expected.overdraft_count
        + expected.limit_breach_count
        + expected.stuck_pending_count
        + expected.stuck_unbundled_count
        + expected.supersession_count
    )
    assert counts > 0, (
        "spec_example scenario produced ZERO planted rows across "
        "all 6 invariants — the per-invariant asserts below would "
        "be trivially true. Either the L2 fixture stopped declaring "
        "the rails/templates the auto-scenario picks, or the "
        "scenario builder regressed."
    )


@pytest.mark.parametrize("invariant", _ALL_INVARIANTS)
def test_pdf_includes_planted_rows(seeded_pdf, invariant):
    """Inclusion assert: PDF section shows AT LEAST as many rows
    as the scenario planted for this invariant.

    Producer-side half of U.8.b's three-way contract. Failure here
    means one of: SQL drift (matview SQL doesn't match what the
    plant emits), seed pipeline drift (plant doesn't actually
    insert rows the matview can pick up), PDF-rendering drift
    (audit query / table layout regressed), or extractor drift
    (heuristic breaks on a new shape).

    Inclusion (``>=``) rather than equality (``==``) because cross-
    plant interactions and multi-sub-table sections legitimately
    produce more PDF rows than there are plants:
      - An overdraft plant emits a negative daily_balance with no
        offsetting postings, which the drift matview also flags
        (stored != computed) — so 1 overdraft plant adds 1 row to
        BOTH the overdraft and the drift sections.
      - A supersession plant emits 1 correcting transaction, which
        renders as 1 row in the aggregate-by-(table, category)
        sub-table AND 1 row in the in-period transaction details
        sub-table — 1 plant → 2 PDF rows in the section.
    Exact-count agreement is U.8.b's three-way contract over
    row IDENTITIES, not counts; that lives in U.8.b.4.
    """
    pdf_path, scenario = seeded_pdf
    expected = expected_audit_counts(scenario, _PERIOD)
    expected_count = getattr(expected, f"{invariant}_count")
    pdf_count = count_invariant_table_rows(pdf_path, invariant)
    assert pdf_count >= expected_count, (
        f"audit PDF shows {pdf_count} {invariant} rows but the "
        f"scenario planted {expected_count} — producer pipeline "
        f"is missing planted rows in the rendered output"
    )


def test_audit_verify_pins_to_embedded_hwm_against_newer_rows(
    seeded_pdf, db_cfg, db_cfg_path,
):
    """Re-verifiability under append: rows added to the base tables
    AFTER a PDF is rendered must NOT cause ``audit verify`` to flag
    a diff.

    The whole reason ``audit verify`` reads the high-water-mark from
    the PDF's embedded ``ProvenanceFingerprint`` (rather than
    recomputing ``MAX(entry)`` against the live DB) is so a
    regulator can come back to a 6-month-old report and still get a
    clean verify against a base table that's grown since. Without
    this property, the report rots the moment the next ETL load
    hits — useless as a permanent audit artifact.

    Regression guard: if a future refactor swapped
    ``embedded.transactions_hwm`` for a fresh ``SELECT MAX(entry)``,
    every other test in this file still passes (they all run against
    a freshly-seeded DB where the live max == the embedded hwm).
    This test is the only one that distinguishes the two
    implementations.
    """
    pdf_path, _ = seeded_pdf
    instance = load_instance(_SPEC_EXAMPLE)
    prefix = instance.instance
    sentinel_id = "verify-test-pinning-row"
    dialect = (
        Dialect.ORACLE
        if (db_cfg.dialect or "").lower() == "oracle"
        else Dialect.POSTGRES
    )
    limit_clause = (
        "WHERE ROWNUM = 1" if dialect == Dialect.ORACLE else "LIMIT 1"
    )

    conn = connect_demo_db(db_cfg)
    try:
        cur = conn.cursor()
        # Clone an existing row's NOT-NULL columns with a fresh ``id``
        # so ``entry`` auto-assigns above the PDF's embedded hwm. The
        # composite PK (id, entry) keeps this from colliding with the
        # source row.
        cur.execute(
            f"INSERT INTO {prefix}_transactions ("
            f"  id, account_id, account_scope, amount_money,"
            f"  amount_direction, status, posting, transfer_id,"
            f"  transfer_type, rail_name, origin"
            f") "
            f"SELECT "
            f"  '{sentinel_id}', account_id, account_scope, amount_money,"
            f"  amount_direction, status, posting, transfer_id,"
            f"  transfer_type, rail_name, origin "
            f"FROM {prefix}_transactions {limit_clause}"
        )
        conn.commit()
        try:
            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "audit", "verify", str(pdf_path),
                    "-c", str(db_cfg_path),
                    "--l2", str(_SPEC_EXAMPLE),
                ],
            )
            assert result.exit_code == 0, (
                "audit verify must pass after newer rows are inserted "
                "above the embedded hwm — the hwm-pinning property "
                f"regressed:\n{result.output}"
            )
        finally:
            cur.execute(
                f"DELETE FROM {prefix}_transactions "
                f"WHERE id = '{sentinel_id}'"
            )
            conn.commit()
    finally:
        conn.close()
