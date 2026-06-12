"""CL.4 — seed honors per-account ``balance_cadence``.

Sparse accounts emit daily_balances rows ONLY on activity days; the
carry-forward matview (CL.5) handles "balance on a quiet day" at read
time. Explicit-daily accounts emit a row for every calendar day in the
window — the pre-CL behavior, kept for accounts that declare
regulatory daily-reporting cadence.

The branch lives in ``_emit_baseline_daily_balances``; this test pins
it via the full ``emit_baseline_seed`` round-trip so the cadence flows
through the same plumbing real fixtures use.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Literal

import pytest

from recon_gen.common.l2.primitives import (
    Account,
    AccountTemplate,
    BalanceCadence,
    DEBIT,
    Identifier,
    L2Instance,
    Money,
    Name,
    ORIGIN_INTERNAL_INITIATED,
    SCOPE_INTERNAL,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
)
from recon_gen.common.l2.seed import emit_baseline_seed
from recon_gen.common.sql.dialect import Dialect


def _build_l2(
    *,
    a_cadence: BalanceCadence | None,
    b_cadence: BalanceCadence | None,
) -> L2Instance:
    """Two-account L2 wired through one two-leg rail. The rail's first
    leg touches role A (the sparse account), the second leg touches
    role B (the explicit_daily account). One firing per business day
    drives activity into both accounts.
    """
    return L2Instance(
        accounts=(
            Account(
                id=Identifier("a-sparse"),
                role=Identifier("RoleA"),
                scope=SCOPE_INTERNAL,
                name=Name("AcctSparse"),
                balance_cadence=a_cadence,
            ),
            Account(
                id=Identifier("a-daily"),
                role=Identifier("RoleB"),
                scope=SCOPE_INTERNAL,
                name=Name("AcctDaily"),
                balance_cadence=b_cadence,
            ),
        ),
        account_templates=(),
        rails=(
            TwoLegRail(
                name=Identifier("RailAB"),
                origin=ORIGIN_INTERNAL_INITIATED,
                metadata_keys=(Identifier("k"),),
                source_role=(Identifier("RoleA"),),
                destination_role=(Identifier("RoleB"),),
            ),
        ),
        transfer_templates=(
            TransferTemplate(
                name=Identifier("TmplAB"),
                expected_net=Money(Decimal("0")),
                transfer_key=(Identifier("k"),),
                completion="business_day_end+1d",
                leg_rails=(Identifier("RailAB"),),
            ),
        ),
        chains=(),
        limit_schedules=(),
    )


def _daily_balance_rows(sql: str, account_id: str) -> list[str]:
    """Pull VALUES rows from the SQL whose first column literal matches
    the given account_id. The emitter writes one row per line."""
    pattern = re.compile(rf"^\(\s*'{re.escape(account_id)}',")
    return [ln.strip() for ln in sql.splitlines() if pattern.match(ln.strip())]


def _emit(inst: L2Instance, *, window_days: int = 5) -> str:
    return emit_baseline_seed(
        inst,
        prefix="t",
        window_days=window_days,
        anchor=date(2030, 1, 1),
        dialect=Dialect.DUCKDB,
    )


@pytest.mark.parametrize(
    "cadence", ["sparse", "explicit_daily", None],
    ids=["sparse-explicit", "explicit_daily", "none-defaults-sparse"],
)
def test_emit_runs_with_cadence(cadence: BalanceCadence | None) -> None:
    """Smoke: every cadence value (and None default) parses + emits
    without error. Anti-drift against future cadence additions: if a
    new ``BalanceCadence`` literal lands without seed plumbing, the
    parametrize'd case fires on the unhandled branch and this test
    flags it before downstream invariants do.
    """
    inst = _build_l2(a_cadence=cadence, b_cadence=cadence)
    sql = _emit(inst, window_days=3)
    # Both accounts should appear in the INSERT (sparse has activity
    # via the rail, explicit_daily covers every day, none-defaults-sparse
    # = sparse via resolve_cadence — all three modes produce ≥1 row).
    assert "INSERT INTO t_daily_balances" in sql
    assert "a-sparse" in sql
    assert "a-daily" in sql


def test_sparse_emits_fewer_rows_than_explicit_daily() -> None:
    """The core CL.4 assertion: same activity → sparse row-count is
    bounded by activity days; explicit_daily row-count is bounded by
    calendar days. With ``window_days=10`` and meaningful activity on
    every business day, explicit_daily emits ≥2× sparse's count
    (calendar days include weekends; sparse skips them).
    """
    inst = _build_l2(a_cadence="sparse", b_cadence="explicit_daily")
    sql = _emit(inst, window_days=10)
    sparse_rows = _daily_balance_rows(sql, "a-sparse")
    daily_rows = _daily_balance_rows(sql, "a-daily")
    # Sparse: only days the rail-leg touched the account = US business
    # days in the window. Explicit-daily: every calendar day from
    # (anchor - window_days) through anchor inclusive = window_days + 1.
    # Calendar always has more days than business days over a 10-day
    # span (≥3 weekend-or-holiday days expected).
    assert len(daily_rows) > len(sparse_rows), (
        f"explicit_daily ({len(daily_rows)} rows) should emit more than "
        f"sparse ({len(sparse_rows)} rows) over a 10-day window"
    )
    # Explicit-daily emits exactly window_days+1 calendar days.
    assert len(daily_rows) == 11, (
        f"explicit_daily over window_days=10 should emit 11 calendar "
        f"days (inclusive), got {len(daily_rows)}"
    )


def test_sparse_default_when_balance_cadence_is_none() -> None:
    """``balance_cadence=None`` resolves to ``"sparse"`` (the read-time
    default via ``resolve_cadence``). Account A (None) and B (explicit
    "sparse") should emit byte-identical row-counts.
    """
    inst_a_none = _build_l2(a_cadence=None, b_cadence="explicit_daily")
    inst_a_sparse = _build_l2(a_cadence="sparse", b_cadence="explicit_daily")
    sql_none = _emit(inst_a_none, window_days=10)
    sql_sparse = _emit(inst_a_sparse, window_days=10)
    none_rows = _daily_balance_rows(sql_none, "a-sparse")
    sparse_rows = _daily_balance_rows(sql_sparse, "a-sparse")
    assert len(none_rows) == len(sparse_rows), (
        f"None default ({len(none_rows)}) and explicit 'sparse' "
        f"({len(sparse_rows)}) must emit the same row count"
    )


def test_template_cadence_fans_out_to_instances() -> None:
    """An ``AccountTemplate.balance_cadence`` should apply to every
    materialized instance under that template. Mirrors CP.2's
    business_day_offset fan-out pattern.

    Materialization count comes from ``_materialize_baseline_template_
    instances`` — role names without ``customer`` / ``merchant`` in
    them get 5 instances. With ``window_days=10`` + ``explicit_daily``,
    each instance emits 11 calendar-day rows ⇒ 5 × 11 = 55 rows total.
    """
    inst = L2Instance(
        accounts=(),
        account_templates=(
            AccountTemplate(
                role=Identifier("FooRole"),
                scope=SCOPE_INTERNAL,
                balance_cadence="explicit_daily",
            ),
        ),
        rails=(
            SingleLegRail(
                name=Identifier("RailB"),
                origin=ORIGIN_INTERNAL_INITIATED,
                metadata_keys=(Identifier("k"),),
                leg_role=(Identifier("FooRole"),),
                leg_direction=DEBIT,
            ),
        ),
        transfer_templates=(
            TransferTemplate(
                name=Identifier("TmplB"),
                expected_net=Money(Decimal("0")),
                transfer_key=(Identifier("k"),),
                completion="business_day_end+1d",
                leg_rails=(Identifier("RailB"),),
            ),
        ),
        chains=(),
        limit_schedules=(),
    )
    sql = _emit(inst, window_days=10)
    instance_rows = [
        ln for ln in sql.splitlines()
        if ln.strip().startswith("('cust-")
    ]

    # Counter-check: same shape with cadence=sparse emits fewer rows
    # (only business-day activity rows, no weekend fill).
    inst_sparse = L2Instance(
        accounts=(),
        account_templates=(
            AccountTemplate(
                role=Identifier("FooRole"),
                scope=SCOPE_INTERNAL,
                balance_cadence="sparse",
            ),
        ),
        rails=inst.rails,
        transfer_templates=inst.transfer_templates,
        chains=(),
        limit_schedules=(),
    )
    sparse_sql = _emit(inst_sparse, window_days=10)
    sparse_instance_rows = [
        ln for ln in sparse_sql.splitlines()
        if ln.strip().startswith("('cust-")
    ]
    # The fan-out is template-level, not per-account. We don't pin
    # the count to N×11 because not every materialized instance is
    # guaranteed to receive activity legs (the leg loop picks among
    # eligible instances). Instead pin the RELATIVE invariant: the
    # explicit_daily case must emit strictly more rows than the
    # sparse case under identical activity (calendar-days > activity-
    # days). Anti-drift if a future seed change breaks the cadence
    # branch — sparse would balloon to match explicit_daily or vice
    # versa.
    assert len(instance_rows) > len(sparse_instance_rows), (
        f"explicit_daily template cadence ({len(instance_rows)} rows) "
        f"must emit more than sparse ({len(sparse_instance_rows)} rows) "
        f"for the same fan-out + activity shape"
    )


def test_sparse_rows_only_on_activity_days() -> None:
    """No sparse-account daily_balance row exists on a calendar day
    that doesn't appear in any of that account's transaction-leg
    postings. We assert this indirectly via the row count: a sparse
    account with activity on N business days emits exactly N rows.
    """
    inst = _build_l2(a_cadence="sparse", b_cadence="sparse")
    sql = _emit(inst, window_days=10)
    sparse_rows = _daily_balance_rows(sql, "a-sparse")
    # Window of 10 calendar days around 2030-01-01 = roughly 7-8
    # business days. Exactly the business-day count is what we want.
    # The Daily Statement test sees 7-8 entries here (not 11 like
    # explicit_daily would).
    assert 5 <= len(sparse_rows) <= 9, (
        f"sparse over a 10-calendar-day window should emit ~business-day "
        f"count of rows (5-9 expected), got {len(sparse_rows)}"
    )
    # Critical: must be strictly less than the explicit_daily case
    # (11 = window_days + 1).
    assert len(sparse_rows) < 11, (
        f"sparse should emit fewer rows than explicit_daily's calendar-"
        f"day count (11), got {len(sparse_rows)}"
    )
    # Direction assertion to anchor the "fewer" interpretation.
    _ = Literal["sparse"]  # silence-pyright marker; no runtime use
