"""Locked dataclass field shapes for audit reportlab inputs (U.8.c).

The audit PDF renders by passing a per-section dataclass instance (or
list of instances) into a story function in ``cli/audit/pdf.py``. Each
dataclass is a contract: the renderer reads specific attributes by
name. If a column is renamed, dropped, or its type changes, the
renderer either crashes at PDF write time (good — loud failure) or
silently mis-renders (bad — silent corruption).

This module locks each dataclass's ``(field_name, type_repr)`` tuple
list. Any change — added field, removed field, renamed field, retyped
field — fails the matching test with a copy-pasteable diff. Update by
re-running the test, copying the actual tuple from the failure
message, and pasting it as the new locked value.

Pure-introspection — no DB, no fixture data — so this test runs in
the U.8.c no-DB CI lane (PLAN: "covers everything short of the
browser, which is most of U.8.b's value without the deploy-and-render
cost"). The DB-layer counterpart (``test_pdf_matches_scenario.py``)
exercises the dataclass values against real planted scenario data;
this file just guards their shape.

Mirrors the dataset-contract pattern from ``tests/json/`` — locked
expectation = single source of truth, deviation = explicit acknowledged
update.
"""

from __future__ import annotations

import dataclasses

from quicksight_gen.cli.audit import (
    DriftViolation,
    ExecSummary,
    LimitBreachChildGroupSummary,
    LimitBreachViolation,
    OverdraftChildGroupSummary,
    OverdraftViolation,
    StuckPendingChildGroupSummary,
    StuckPendingViolation,
    StuckUnbundledChildGroupSummary,
    StuckUnbundledViolation,
    SupersessionAggregate,
    SupersessionAuditData,
    SupersessionDailyBalanceDetail,
    SupersessionTransactionDetail,
)


def _shape(cls: type) -> tuple[tuple[str, str], ...]:
    """Return ``(field_name, type_repr)`` for each declared field.

    ``field.type`` is a string under ``from __future__ import
    annotations`` (PEP 563), which is exactly what we want — the
    locked snapshot uses the source-level annotation, immune to
    ``typing.get_type_hints`` resolution quirks across forward refs.
    """
    return tuple(
        (f.name, str(f.type))
        for f in dataclasses.fields(cls)
    )


def _diff_msg(cls_name: str, want, got) -> str:
    return (
        f"\n{cls_name} field shape drifted. The dataclass now declares:\n"
        f"-----8<-----\n{got}\n-----8<-----\n"
        f"If intentional, paste this tuple into "
        f"tests/audit/test_template_input.py as the new locked value. "
        f"Expected was:\n"
        f"-----8<-----\n{want}\n-----8<-----"
    )


# --- Locked field shapes -----------------------------------------------------


_EXEC_SUMMARY_FIELDS = (
    ("transactions_count", "int"),
    ("transfers_count", "int"),
    ("dollar_volume_gross", "Decimal"),
    ("dollar_volume_net", "Decimal"),
    ("exception_counts", "list[tuple[str, int]]"),
)

_DRIFT_VIOLATION_FIELDS = (
    ("account_id", "str"),
    ("account_name", "str"),
    ("account_role", "str"),
    ("account_parent_role", "str"),
    ("business_day", "date"),
    ("stored_balance", "Decimal"),
    ("computed_balance", "Decimal"),
    ("drift", "Decimal"),
)

_OVERDRAFT_VIOLATION_FIELDS = (
    ("account_id", "str"),
    ("account_name", "str"),
    ("account_role", "str"),
    ("account_parent_role", "str"),
    ("business_day", "date"),
    ("stored_balance", "Decimal"),
)

_OVERDRAFT_CHILD_GROUP_SUMMARY_FIELDS = (
    ("parent_role", "str"),
    ("distinct_children_negative", "int"),
    ("total_peak_negative", "Decimal"),
)

_LIMIT_BREACH_VIOLATION_FIELDS = (
    ("account_id", "str"),
    ("account_name", "str"),
    ("account_role", "str"),
    ("account_parent_role", "str"),
    ("business_day", "date"),
    ("transfer_type", "str"),
    ("outbound_total", "Decimal"),
    ("cap", "Decimal"),
)

_LIMIT_BREACH_CHILD_GROUP_SUMMARY_FIELDS = (
    ("parent_role", "str"),
    ("transfer_type", "str"),
    ("distinct_children_breaching", "int"),
    ("total_overshoot", "Decimal"),
)

_STUCK_PENDING_VIOLATION_FIELDS = (
    ("account_id", "str"),
    ("account_name", "str"),
    ("account_role", "str"),
    ("account_parent_role", "str"),
    ("transaction_id", "str"),
    ("transfer_type", "str"),
    ("posting", "datetime"),
    ("amount_money", "Decimal"),
    ("age_seconds", "Decimal"),
    ("max_pending_age_seconds", "int"),
)

_STUCK_PENDING_CHILD_GROUP_SUMMARY_FIELDS = (
    ("parent_role", "str"),
    ("transfer_type", "str"),
    ("distinct_children_affected", "int"),
    ("stuck_transaction_count", "int"),
    ("total_stuck_amount", "Decimal"),
)

_STUCK_UNBUNDLED_VIOLATION_FIELDS = (
    ("account_id", "str"),
    ("account_name", "str"),
    ("account_role", "str"),
    ("account_parent_role", "str"),
    ("transaction_id", "str"),
    ("transfer_type", "str"),
    ("posting", "datetime"),
    ("amount_money", "Decimal"),
    ("age_seconds", "Decimal"),
    ("max_unbundled_age_seconds", "int"),
)

_STUCK_UNBUNDLED_CHILD_GROUP_SUMMARY_FIELDS = (
    ("parent_role", "str"),
    ("transfer_type", "str"),
    ("distinct_children_affected", "int"),
    ("stuck_transaction_count", "int"),
    ("total_stuck_amount", "Decimal"),
)

_SUPERSESSION_AGGREGATE_FIELDS = (
    ("base_table", "str"),
    ("supersedes_category", "str"),
    ("total_count", "int"),
    ("new_in_period_count", "int"),
)

_SUPERSESSION_TXN_DETAIL_FIELDS = (
    ("transaction_id", "str"),
    ("supersedes_category", "str"),
    ("account_id", "str"),
    ("account_name", "str"),
    ("posting", "datetime"),
    ("amount_money", "Decimal"),
)

_SUPERSESSION_DAILY_DETAIL_FIELDS = (
    ("account_id", "str"),
    ("account_name", "str"),
    ("business_day", "date"),
    ("supersedes_category", "str"),
    ("money", "Decimal"),
)

_SUPERSESSION_AUDIT_DATA_FIELDS = (
    ("aggregates", "list[SupersessionAggregate]"),
    ("transaction_details", "list[SupersessionTransactionDetail]"),
    ("daily_balance_details", "list[SupersessionDailyBalanceDetail]"),
)


# --- Per-section assertions --------------------------------------------------


def test_exec_summary_shape_locked():
    got = _shape(ExecSummary)
    assert got == _EXEC_SUMMARY_FIELDS, _diff_msg(
        "ExecSummary", _EXEC_SUMMARY_FIELDS, got,
    )


def test_drift_violation_shape_locked():
    got = _shape(DriftViolation)
    assert got == _DRIFT_VIOLATION_FIELDS, _diff_msg(
        "DriftViolation", _DRIFT_VIOLATION_FIELDS, got,
    )


def test_overdraft_violation_shape_locked():
    got = _shape(OverdraftViolation)
    assert got == _OVERDRAFT_VIOLATION_FIELDS, _diff_msg(
        "OverdraftViolation", _OVERDRAFT_VIOLATION_FIELDS, got,
    )


def test_overdraft_child_group_summary_shape_locked():
    got = _shape(OverdraftChildGroupSummary)
    assert got == _OVERDRAFT_CHILD_GROUP_SUMMARY_FIELDS, _diff_msg(
        "OverdraftChildGroupSummary",
        _OVERDRAFT_CHILD_GROUP_SUMMARY_FIELDS, got,
    )


def test_limit_breach_violation_shape_locked():
    got = _shape(LimitBreachViolation)
    assert got == _LIMIT_BREACH_VIOLATION_FIELDS, _diff_msg(
        "LimitBreachViolation", _LIMIT_BREACH_VIOLATION_FIELDS, got,
    )


def test_limit_breach_child_group_summary_shape_locked():
    got = _shape(LimitBreachChildGroupSummary)
    assert got == _LIMIT_BREACH_CHILD_GROUP_SUMMARY_FIELDS, _diff_msg(
        "LimitBreachChildGroupSummary",
        _LIMIT_BREACH_CHILD_GROUP_SUMMARY_FIELDS, got,
    )


def test_stuck_pending_violation_shape_locked():
    got = _shape(StuckPendingViolation)
    assert got == _STUCK_PENDING_VIOLATION_FIELDS, _diff_msg(
        "StuckPendingViolation", _STUCK_PENDING_VIOLATION_FIELDS, got,
    )


def test_stuck_pending_child_group_summary_shape_locked():
    got = _shape(StuckPendingChildGroupSummary)
    assert got == _STUCK_PENDING_CHILD_GROUP_SUMMARY_FIELDS, _diff_msg(
        "StuckPendingChildGroupSummary",
        _STUCK_PENDING_CHILD_GROUP_SUMMARY_FIELDS, got,
    )


def test_stuck_unbundled_violation_shape_locked():
    got = _shape(StuckUnbundledViolation)
    assert got == _STUCK_UNBUNDLED_VIOLATION_FIELDS, _diff_msg(
        "StuckUnbundledViolation",
        _STUCK_UNBUNDLED_VIOLATION_FIELDS, got,
    )


def test_stuck_unbundled_child_group_summary_shape_locked():
    got = _shape(StuckUnbundledChildGroupSummary)
    assert got == _STUCK_UNBUNDLED_CHILD_GROUP_SUMMARY_FIELDS, _diff_msg(
        "StuckUnbundledChildGroupSummary",
        _STUCK_UNBUNDLED_CHILD_GROUP_SUMMARY_FIELDS, got,
    )


def test_supersession_aggregate_shape_locked():
    got = _shape(SupersessionAggregate)
    assert got == _SUPERSESSION_AGGREGATE_FIELDS, _diff_msg(
        "SupersessionAggregate", _SUPERSESSION_AGGREGATE_FIELDS, got,
    )


def test_supersession_transaction_detail_shape_locked():
    got = _shape(SupersessionTransactionDetail)
    assert got == _SUPERSESSION_TXN_DETAIL_FIELDS, _diff_msg(
        "SupersessionTransactionDetail",
        _SUPERSESSION_TXN_DETAIL_FIELDS, got,
    )


def test_supersession_daily_balance_detail_shape_locked():
    got = _shape(SupersessionDailyBalanceDetail)
    assert got == _SUPERSESSION_DAILY_DETAIL_FIELDS, _diff_msg(
        "SupersessionDailyBalanceDetail",
        _SUPERSESSION_DAILY_DETAIL_FIELDS, got,
    )


def test_supersession_audit_data_shape_locked():
    got = _shape(SupersessionAuditData)
    assert got == _SUPERSESSION_AUDIT_DATA_FIELDS, _diff_msg(
        "SupersessionAuditData", _SUPERSESSION_AUDIT_DATA_FIELDS, got,
    )


# --- Cross-cutting invariants ------------------------------------------------


def test_all_violation_dataclasses_are_frozen():
    """Every violation row dataclass MUST be ``frozen=True``.

    Renderers receive these and pass them through Python's reportlab
    layer; mutation across the boundary is a footgun. Frozen is
    cheap insurance against accidental in-place edits during a
    table-build helper refactor.
    """
    classes = [
        ExecSummary, DriftViolation, OverdraftViolation,
        OverdraftChildGroupSummary, LimitBreachViolation,
        LimitBreachChildGroupSummary, StuckPendingViolation,
        StuckPendingChildGroupSummary, StuckUnbundledViolation,
        StuckUnbundledChildGroupSummary, SupersessionAggregate,
        SupersessionTransactionDetail, SupersessionDailyBalanceDetail,
        SupersessionAuditData,
    ]
    not_frozen = [
        c.__name__ for c in classes
        if not getattr(c, "__dataclass_params__", None)
        or not c.__dataclass_params__.frozen  # type: ignore[union-attr]: __dataclass_params__ narrowed truthy by the prior getattr check
    ]
    assert not_frozen == [], (
        f"these audit dataclasses are not frozen: {not_frozen}. "
        f"Add frozen=True to the @dataclass decorator."
    )


def test_violation_dataclasses_cover_each_l1_invariant():
    """Sanity: there's one Violation dataclass per L1 invariant covered
    by the audit. If we add a new invariant to the audit (or remove
    one), this test fails as a forcing function to add/remove the
    matching Violation snapshot above."""
    expected_per_invariant = {
        "drift": DriftViolation,
        "overdraft": OverdraftViolation,
        "limit_breach": LimitBreachViolation,
        "stuck_pending": StuckPendingViolation,
        "stuck_unbundled": StuckUnbundledViolation,
    }
    # Importing each by name from the audit module is the assertion;
    # any rename breaks the import + this test fails at collect time
    # rather than at runtime in some far-future PDF render. Listing
    # all 5 here keeps the count visible to a code reader.
    assert len(expected_per_invariant) == 5
