"""CL.7 — unit tests for the balance_cadence_gap plant adapter.

The plant picks the alphabetically-first internal entity declaring
``balance_cadence='explicit_daily'`` and emits SQL that DELETEs the
target row from ``<prefix>_daily_balances`` for the (account_id,
anchor − days_ago) cell. The CL.6 invariant matview then surfaces
the row under ``gap_kind = 'declared_daily_missing'`` on next
refresh.

Test surface:

1. Singleton-Account path: DELETE keys on ``account_id``.
2. Template path: DELETE keys on ``account_role`` so every
   materialized instance under the template inherits the gap
   (consistent with CL.2 Lock 4's per-template fan-out).
3. Singleton wins over template when both are declared (singleton
   account_id comes first alphabetically by construction).
4. No explicit_daily entity → typed ValueError with operator copy.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from recon_gen.common.l2.plant_registry import (
    _invoke_balance_cadence_gap_plant,
    _pick_first_explicit_daily_target,
)
from recon_gen.common.l2.primitives import (
    Account,
    AccountTemplate,
    DEBIT,
    Identifier,
    L2Instance,
    Money,
    Name,
    ORIGIN_INTERNAL_INITIATED,
    SCOPE_EXTERNAL,
    SCOPE_INTERNAL,
    SingleLegRail,
    TransferTemplate,
)
from recon_gen.common.sql.dialect import Dialect


def _minimal_l2(
    *, accounts: tuple[Account, ...] = (),
    templates: tuple[AccountTemplate, ...] = (),
) -> L2Instance:
    return L2Instance(
        accounts=accounts,
        account_templates=templates,
        rails=(
            SingleLegRail(
                name=Identifier("R"),
                origin=ORIGIN_INTERNAL_INITIATED,
                metadata_keys=(Identifier("k"),),
                leg_role=(Identifier("RoleA"),),
                leg_direction=DEBIT,
            ),
        ),
        transfer_templates=(
            TransferTemplate(
                name=Identifier("T"),
                expected_net=Money(Decimal("0")),
                transfer_key=(Identifier("k"),),
                completion="business_day_end+1d",
                leg_rails=(Identifier("R"),),
            ),
        ),
        chains=(), limit_schedules=(),
    )


def test_picker_returns_singleton_when_only_one_declared() -> None:
    inst = _minimal_l2(accounts=(
        Account(
            id=Identifier("acct-1"), role=Identifier("RoleA"),
            scope=SCOPE_INTERNAL, name=Name("A1"),
            balance_cadence="explicit_daily",
        ),
    ))
    target = _pick_first_explicit_daily_target(inst)
    assert target == ("acct-1", "RoleA")


def test_picker_returns_template_when_only_one_declared() -> None:
    inst = _minimal_l2(templates=(
        AccountTemplate(
            role=Identifier("CustomerSubledger"),
            scope=SCOPE_INTERNAL,
            balance_cadence="explicit_daily",
        ),
    ))
    target = _pick_first_explicit_daily_target(inst)
    assert target == ("", "CustomerSubledger")


def test_picker_returns_none_when_no_declaration() -> None:
    inst = _minimal_l2(accounts=(
        Account(
            id=Identifier("acct-2"), role=Identifier("RoleA"),
            scope=SCOPE_INTERNAL, name=Name("A2"),
            balance_cadence="sparse",  # NOT explicit_daily
        ),
    ))
    assert _pick_first_explicit_daily_target(inst) is None


def test_picker_skips_external_scope() -> None:
    """External-scope accounts never appear in the picker — they
    don't emit balance rows in the first place (the seed never
    populates EOD for externals)."""
    inst = _minimal_l2(accounts=(
        Account(
            id=Identifier("acct-ext"), role=Identifier("ExtCounter"),
            scope=SCOPE_EXTERNAL, name=Name("Ext"),
            balance_cadence="explicit_daily",
        ),
    ))
    assert _pick_first_explicit_daily_target(inst) is None


def test_adapter_emits_singleton_delete() -> None:
    inst = _minimal_l2(accounts=(
        Account(
            id=Identifier("acct-1"), role=Identifier("RoleA"),
            scope=SCOPE_INTERNAL, name=Name("A1"),
            balance_cadence="explicit_daily",
        ),
    ))
    sql = _invoke_balance_cadence_gap_plant(
        prefix="t",
        dialect=Dialect.DUCKDB,
        anchor=datetime(2030, 1, 10, 0, 0, 0),
        days_ago=3,
        instance=inst,
    )
    # DELETE keys on the specific account_id with a one-day window.
    assert "DELETE FROM t_daily_balances" in sql
    assert "account_id = 'acct-1'" in sql
    # anchor=2030-01-10 − 3 days = 2030-01-07.
    assert "2030-01-07" in sql
    # No account_role clause (singleton path).
    assert "account_role" not in sql


def test_adapter_emits_template_delete_keys_on_role() -> None:
    inst = _minimal_l2(templates=(
        AccountTemplate(
            role=Identifier("CustomerSubledger"),
            scope=SCOPE_INTERNAL,
            balance_cadence="explicit_daily",
        ),
    ))
    sql = _invoke_balance_cadence_gap_plant(
        prefix="t",
        dialect=Dialect.DUCKDB,
        anchor=datetime(2030, 1, 10),
        days_ago=2,
        instance=inst,
    )
    # DELETE keys on the role — every materialized instance under
    # the template inherits the gap.
    assert "DELETE FROM t_daily_balances" in sql
    assert "account_role = 'CustomerSubledger'" in sql
    assert "2030-01-08" in sql


def test_adapter_raises_on_missing_declaration() -> None:
    """Per audit §8 + PLAN.md: typed ValueError when no
    explicit_daily entity exists on the L2 — the kind has nothing
    to plant. Operator-readable message."""
    inst = _minimal_l2(accounts=(
        Account(
            id=Identifier("acct-2"), role=Identifier("RoleA"),
            scope=SCOPE_INTERNAL, name=Name("A2"),
            balance_cadence="sparse",
        ),
    ))
    with pytest.raises(ValueError, match="explicit_daily"):
        _invoke_balance_cadence_gap_plant(
            prefix="t",
            dialect=Dialect.DUCKDB,
            anchor=datetime(2030, 1, 10),
            days_ago=1,
            instance=inst,
        )


def test_adapter_uses_date_literal_for_oracle_portability() -> None:
    """CT.0 — the plant's DELETE must use typed ``DATE 'YYYY-MM-DD'``
    literals on every dialect. The previous shape used bare ISO
    strings (``business_day_start >= '2030-01-07'``) which Oracle 19c
    rejected with ORA-01843 ("not a valid month") — the entire DELETE
    error'd out silently before reaching the matview, leaving the
    plant a no-op on Oracle. Pin the typed-literal shape across every
    dialect so the regression can't reappear."""
    inst = _minimal_l2(accounts=(
        Account(
            id=Identifier("acct-1"), role=Identifier("RoleA"),
            scope=SCOPE_INTERNAL, name=Name("A1"),
            balance_cadence="explicit_daily",
        ),
    ))
    for dialect in (Dialect.ORACLE, Dialect.POSTGRES, Dialect.DUCKDB):
        sql = _invoke_balance_cadence_gap_plant(
            prefix="t",
            dialect=dialect,
            anchor=datetime(2030, 1, 10),
            days_ago=3,
            instance=inst,
        )
        # Lower-bound: typed DATE literal at gap_day.
        assert "DATE '2030-01-07'" in sql, (
            f"{dialect.value}: expected typed lower-bound "
            f"DATE '2030-01-07' (gap_day); got:\n{sql}"
        )
        # Upper-bound: typed DATE literal at gap_day + 1 (half-open
        # window covers a full TIMESTAMP day without missing-by-a-
        # microsecond edge cases).
        assert "DATE '2030-01-08'" in sql, (
            f"{dialect.value}: expected typed upper-bound "
            f"DATE '2030-01-08' (gap_day + 1); got:\n{sql}"
        )
        # The pre-CT.0 footgun shapes must NOT appear — they're the
        # exact strings Oracle rejected.
        assert "'2030-01-07'" not in sql.replace("DATE '2030-01-07'", ""), (
            f"{dialect.value}: bare ISO string '2030-01-07' leaked "
            f"into the SQL — Oracle would emit ORA-01843. SQL:\n{sql}"
        )
        assert "T23:59:59" not in sql, (
            f"{dialect.value}: leftover 'gap_day T23:59:59' upper "
            f"bound — should be replaced by DATE 'gap_day+1'. SQL:\n{sql}"
        )
