"""AA.A.3 — unit tests for the single-value sentinel-guard helpers.

AA.A.2 added parallel ``_single_value_clause`` helpers in L1 + L2FT;
AA.A.3 collapsed those into the existing ``_data_value_clause`` /
``_match_all_in_clause`` functions by rewriting their bodies from the
multi-value ``IN`` form to the single-value ``=`` form (the function
names stayed for call-site continuity — every helper call became scalar
in the same flip).

These tests pin the SQL shape so a future refactor that flips back to
``IN`` (e.g. someone "re-enabling" multi-select on a single dropdown
without re-checking the predicate operator) fails here, not silently in
a deployed dashboard. A scalar bind against ``IN`` parses but narrows
to zero rows — the worst kind of regression.

Why dedicated unit tests:

- The clause's load-time behavior ("sentinel passes everything") depends
  on the operator being ``=``, not ``IN``. Swapping back to ``IN``
  against a scalar bind would still parse but narrow to zero rows;
  pinning the literal shape catches that regression.
- The integration coverage paths through the test_l1_dashboard.py and
  test_l2_flow_tracing.py JSON snapshot tests — this file is the
  narrow-and-fast unit-level guard.
"""

from __future__ import annotations

from recon_gen.apps.l1_dashboard.datasets import (
    L1_ALL_SENTINEL,
    P_L1_DRIFT_ACCOUNT,
    P_L1_OVERDRAFT_ACCOUNT,
    P_L1_TX_ACCOUNT,
    P_L1_TX_ORIGIN,
    P_L1_TX_STATUS,
    _account_display_clause,
    _data_value_clause,
)
from recon_gen.apps.l2_flow_tracing.datasets import (
    L2FT_ALL_SENTINEL,
    _match_all_in_clause,
)


def test_l1_data_value_clause_shape() -> None:
    clause = _data_value_clause("account_id", P_L1_DRIFT_ACCOUNT)
    assert clause == (
        f"('{L1_ALL_SENTINEL}' = <<${P_L1_DRIFT_ACCOUNT}>>"
        f" OR account_id = <<${P_L1_DRIFT_ACCOUNT}>>)"
    )


def test_l1_data_value_clause_uses_scalar_eq_not_in() -> None:
    """Regression guard: must be ``=``, never ``IN (...)``. A scalar bind
    against ``IN`` parses but narrows to zero rows."""
    clause = _data_value_clause("status", P_L1_TX_STATUS)
    assert f" = <<${P_L1_TX_STATUS}>>" in clause
    assert f" IN (<<${P_L1_TX_STATUS}>>)" not in clause


def test_l1_data_value_clause_uses_l1_sentinel() -> None:
    clause = _data_value_clause("origin", P_L1_TX_ORIGIN)
    assert f"'{L1_ALL_SENTINEL}'" in clause


def test_l2ft_match_all_in_clause_shape() -> None:
    clause = _match_all_in_clause("rail_name", "pL2ftRail")
    assert clause == (
        "('__l2ft_all__' = <<$pL2ftRail>>"
        " OR rail_name = <<$pL2ftRail>>)"
    )


def test_l2ft_match_all_in_clause_uses_scalar_eq_not_in() -> None:
    clause = _match_all_in_clause("status", "pL2ftStatus")
    assert " = <<$pL2ftStatus>>" in clause
    assert " IN (<<$pL2ftStatus>>)" not in clause


def test_l2ft_match_all_in_clause_uses_l2ft_sentinel() -> None:
    clause = _match_all_in_clause("completion_status", "pL2ftChainsCompletion")
    assert f"'{L2FT_ALL_SENTINEL}'" in clause


# ---------------------------------------------------------------------------
# AA.E.2 — _account_display_clause shape
# ---------------------------------------------------------------------------


def test_aa_e_2_account_display_clause_shape() -> None:
    """Inline-concat WHERE shape against the source view's ``account_name``
    + ``account_id`` columns. Sentinel-guard same as
    :func:`_data_value_clause`, so a show-all default keeps every row on
    initial load."""
    clause = _account_display_clause(P_L1_DRIFT_ACCOUNT)
    assert clause == (
        f"('{L1_ALL_SENTINEL}' = <<${P_L1_DRIFT_ACCOUNT}>>"
        f" OR (account_name || ' (' || account_id || ')') = <<${P_L1_DRIFT_ACCOUNT}>>)"
    )


def test_aa_e_2_account_display_clause_uses_l1_sentinel() -> None:
    """Pin that the show-all sentinel is the same one
    ``_data_value_clause`` uses — flipping a single dropdown's WHERE
    to the display clause shouldn't change which sentinel its
    ``_all_sentinel_sv_param`` default already emits."""
    clause = _account_display_clause(P_L1_OVERDRAFT_ACCOUNT)
    assert f"'{L1_ALL_SENTINEL}'" in clause


def test_aa_e_2_account_display_clause_concats_name_then_id() -> None:
    """Pin the display *order* — ``name (id)``, not ``id (name)``. The
    dropdown options dataset must produce the same shape (the
    ``DS_L1_ACCOUNTS`` SQL aliases ``account_name || ' (' || account_id
    || ')' AS account_display``). If the two ever diverge, every L1
    account dropdown silently narrows to zero rows."""
    clause = _account_display_clause(P_L1_TX_ACCOUNT)
    assert "account_name || ' (' || account_id || ')'" in clause
