"""AA.A.2 — unit tests for the single-value sentinel-guard helpers.

The L1 and L2FT dataset modules each carry a `_single_value_clause(col,
param_name)` companion to their existing multi-value helper. These tests
pin the SQL shape so a future refactor that changes the sentinel name or
flips the operator surfaces here, not in a deploy-time silent miss-filter.

Why dedicated unit tests:

- The clause's load-time behavior ("sentinel passes everything") depends
  on the operator being `=`, not `IN`. Swapping back to `IN` against a
  scalar bind would still parse but narrow to zero rows; pinning the
  literal shape catches that regression.
- The `_data_value_clause` / `_match_all_in_clause` predecessors weren't
  unit-tested directly (integration coverage only) — AA.A picks up the
  hygiene for the single-value variants.
"""

from __future__ import annotations

from quicksight_gen.apps.l1_dashboard.datasets import (
    L1_ALL_SENTINEL,
    _single_value_clause as l1_single_value_clause,
)
from quicksight_gen.apps.l2_flow_tracing.datasets import (
    L2FT_ALL_SENTINEL,
    _single_value_clause as l2ft_single_value_clause,
)


def test_l1_single_value_clause_shape() -> None:
    clause = l1_single_value_clause("account_id", "pL1DriftAccount")
    assert clause == (
        "('__l1_all__' = <<$pL1DriftAccount>>"
        " OR account_id = <<$pL1DriftAccount>>)"
    )


def test_l1_single_value_clause_uses_scalar_eq_not_in() -> None:
    """Regression guard: must be `=`, never `IN (...)`. A scalar bind
    against `IN` parses but narrows to zero rows."""
    clause = l1_single_value_clause("status", "pL1TxStatus")
    assert " = <<$pL1TxStatus>>" in clause
    assert " IN (<<$pL1TxStatus>>)" not in clause


def test_l1_single_value_clause_uses_l1_sentinel() -> None:
    clause = l1_single_value_clause("origin", "pL1TxOrigin")
    assert f"'{L1_ALL_SENTINEL}'" in clause


def test_l2ft_single_value_clause_shape() -> None:
    clause = l2ft_single_value_clause("rail_name", "pL2ftRail")
    assert clause == (
        "('__l2ft_all__' = <<$pL2ftRail>>"
        " OR rail_name = <<$pL2ftRail>>)"
    )


def test_l2ft_single_value_clause_uses_scalar_eq_not_in() -> None:
    clause = l2ft_single_value_clause("status", "pL2ftStatus")
    assert " = <<$pL2ftStatus>>" in clause
    assert " IN (<<$pL2ftStatus>>)" not in clause


def test_l2ft_single_value_clause_uses_l2ft_sentinel() -> None:
    clause = l2ft_single_value_clause("completion_status", "pL2ftChainsCompletion")
    assert f"'{L2FT_ALL_SENTINEL}'" in clause
