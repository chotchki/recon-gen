"""Tests for the audit PDF row-count extractor (U.8.b.1).

Two layers:
  1. Unit tests for the row/header/continuation heuristic — feed
     hand-crafted layout-extracted lines into the inner counter
     to pin its discrimination rules.
  2. Integration test against a real reportlab-rendered audit PDF
     in skeleton mode (no DB) — every invariant section returns
     0 because the audit emits a "Database not configured"
     placeholder. Proves the outline lookup + page slicing + empty
     marker handling all wire up correctly.

A non-zero integration test (real DB → planted rows → PDF →
extractor) lives in U.8.c.DB-layer (test_pdf_matches_scenario.py)
since it needs a writable DB fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from quicksight_gen.cli import main

from tests.audit._pdf_extract import (
    _count_data_rows,
    _is_data_row,
    count_invariant_table_rows,
)


_FIXTURES = Path(__file__).parent.parent / "l2"
_SPEC_EXAMPLE = _FIXTURES / "spec_example.yaml"


@pytest.fixture
def min_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "aws_account_id: '111122223333'\n"
        "aws_region: us-west-2\n"
        "deployment_name: qsgen-test\n"
        "db_table_prefix: spec_example\n"
        "datasource_arn: arn:aws:quicksight:us-west-2:111122223333"
        ":datasource/ds\n"
    )
    return cfg


@pytest.fixture
def skeleton_pdf(min_config: Path, tmp_path: Path) -> Path:
    """An audit PDF rendered without a DB.

    Every L1 invariant section emits the "Database not configured"
    placeholder; row counts are 0 across the board. Cheap to build
    (no live DB or QuickSight) so tests can use it as the
    "extractor sees a real PDF" fixture.
    """
    out = tmp_path / "skeleton.pdf"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit", "apply",
            "-c", str(min_config),
            "--l2", str(_SPEC_EXAMPLE),
            "-o", str(out), "--execute",
        ],
    )
    assert result.exit_code == 0, result.output
    return out


# -- Unit: _is_data_row --------------------------------------------------


def test_is_data_row_full_drift_row():
    line = (
        "  cust-0001-snb          SNB Customer            CustomerDDA"
        "             2026-04-27            $275.00         $-72,100.00"
        "        $72,375.00"
    )
    assert _is_data_row(line)


def test_is_data_row_overdraft_with_long_account_name_continuation():
    """Continuation line carrying just a wrapped account_id suffix is NOT a data row."""
    assert not _is_data_row("  aring")
    assert not _is_data_row("                        #0001")


def test_is_data_row_two_fragment_continuation_excluded():
    """A continuation line carrying two wrapped column suffixes is still NOT a row."""
    line = "  on                                 Reconciliation"
    assert not _is_data_row(line)


def test_is_data_row_header_line_no_numeric_signal():
    """Column-header rows have ≥ 3 fragments but no numeric data signal."""
    line = (
        " Account ID             Account name            Role"
        "                 Day             Stored              Computed"
        "           Drift"
    )
    assert not _is_data_row(line)


def test_is_data_row_supersession_aggregate_trailing_integers():
    """Supersession aggregate has no $ or date — just trailing integer counts."""
    line = (
        "  transactions                   TechnicalCorrection"
        "                                                    5"
        "                            1"
    )
    assert _is_data_row(line)


def test_is_data_row_age_suffix_signal():
    """Stuck pending / unbundled rows carry age suffixes like '5.2d'."""
    line = (
        "   ext-card-network-ac   Card Network          sale"
        "                   2026-02-02 00:00      $-109,235.43"
        "         88.7d          2.0d"
    )
    assert _is_data_row(line)


def test_is_data_row_blank_line_not_a_row():
    assert not _is_data_row("")
    assert not _is_data_row("   ")


# -- Unit: _count_data_rows ----------------------------------------------


def test_count_data_rows_empty_marker_short_circuits():
    lines = [
        " Drift Violations",
        " Reporting period: 2026-04-25 – 2026-05-01 (inclusive).",
        " Database not configured — table not populated.",
    ]
    assert _count_data_rows(lines) == 0


def test_count_data_rows_no_x_detected_message_short_circuits():
    lines = [
        " Drift Violations",
        " No drift detected for the period — congratulations.",
    ]
    assert _count_data_rows(lines) == 0


def test_count_data_rows_mixed_sub_tables_with_continuations():
    """Parents-per-row + children-grouped, with one wrapped account_id."""
    lines = [
        " Overdraft Violations",
        " Account-days where the stored end-of-day balance went negative.",
        " Parent Accounts (Per-Row Detail)",
        "  Account ID                  Account name        Role"
        "                Day             Stored balance",
        "  gl-1840-merchant-payable-cle    Merchant Payable Clearing"
        "    MerchantPayableClearing    2026-05-02    $-13,937,988.56",
        "  aring",
        "  gl-1810-ach-orig-settlement     ACH Origination Settlement"
        "    ACHOrigSettlement          2026-05-02     $-5,653,102.62",
        " Child Accounts Grouped by Parent Role",
        "  Parent role                       Children negative"
        "       Total peak negative",
        "  ConcentrationMaster                            3"
        "                          $-140,861.29",
    ]
    # 2 parent rows + 1 child grouped row = 3 data rows; the
    # 'aring' continuation does NOT count, neither column header.
    assert _count_data_rows(lines) == 3


# -- Integration: skeleton-mode PDF --------------------------------------


@pytest.mark.parametrize("invariant", [
    "drift",
    "overdraft",
    "limit_breach",
    "stuck_pending",
    "stuck_unbundled",
    "supersession",
])
def test_count_invariant_table_rows_skeleton_pdf_zero(
    skeleton_pdf: Path, invariant: str,
):
    """Without a DB, every invariant table emits the
    'Database not configured' placeholder → row count 0."""
    assert count_invariant_table_rows(skeleton_pdf, invariant) == 0


def test_count_invariant_table_rows_unknown_section_raises(
    skeleton_pdf: Path,
):
    """Calling with an invariant whose title isn't in the outline
    surfaces a clear ValueError, not a KeyError or silent 0."""
    # Force a bogus invariant via the title map by patching the
    # extractor's lookup. Simulates the case where a future PDF
    # changes section naming and the extractor needs updating.
    from tests.audit import _pdf_extract
    original = _pdf_extract._INVARIANT_TITLES.copy()
    try:
        _pdf_extract._INVARIANT_TITLES["drift"] = (  # type: ignore[index]: deliberately mutating Final mapping for this negative-path test
            "Nonexistent Section Heading"
        )
        with pytest.raises(ValueError, match="Nonexistent Section Heading"):
            count_invariant_table_rows(skeleton_pdf, "drift")
    finally:
        _pdf_extract._INVARIANT_TITLES.clear()
        _pdf_extract._INVARIANT_TITLES.update(original)
