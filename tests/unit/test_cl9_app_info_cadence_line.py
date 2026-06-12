"""CL.9 — App Info deploy-stamp cadence-count line.

The helper counts internal singletons + templates by resolved
cadence (None → sparse). External-scope entities are excluded —
they don't emit balance rows.
"""

from __future__ import annotations

from decimal import Decimal

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
from recon_gen.common.sheets.app_info import _cadence_summary_line


def _l2(
    *,
    accounts: tuple[Account, ...] = (),
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
                leg_role=(Identifier("X"),),
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


def test_empty_l2_counts_zero_zero() -> None:
    assert _cadence_summary_line(_l2()) == "cadence: 0 sparse, 0 explicit_daily"


def test_singleton_explicit_daily_counted() -> None:
    inst = _l2(accounts=(
        Account(
            id=Identifier("a"), role=Identifier("R"),
            scope=SCOPE_INTERNAL, name=Name("A"),
            balance_cadence="explicit_daily",
        ),
    ))
    assert _cadence_summary_line(inst) == "cadence: 0 sparse, 1 explicit_daily"


def test_none_defaults_to_sparse() -> None:
    """None resolves to sparse per CL.0 audit Lock 3 — the line
    reflects the runtime behavior, not the literal YAML."""
    inst = _l2(accounts=(
        Account(
            id=Identifier("a"), role=Identifier("R"),
            scope=SCOPE_INTERNAL, name=Name("A"),
            balance_cadence=None,
        ),
    ))
    assert _cadence_summary_line(inst) == "cadence: 1 sparse, 0 explicit_daily"


def test_external_scope_excluded() -> None:
    """External-scope accounts don't emit balance rows — they don't
    contribute to either count."""
    inst = _l2(accounts=(
        Account(
            id=Identifier("ext"), role=Identifier("ExtR"),
            scope=SCOPE_EXTERNAL, name=Name("Ext"),
            balance_cadence="explicit_daily",
        ),
    ))
    assert _cadence_summary_line(inst) == "cadence: 0 sparse, 0 explicit_daily"


def test_template_counted_once_not_per_instance() -> None:
    """A 1-template fan-out counts as 1, not as N materialized
    instances — the line reflects the *declaration* shape."""
    inst = _l2(templates=(
        AccountTemplate(
            role=Identifier("CustomerSubledger"),
            scope=SCOPE_INTERNAL,
            balance_cadence="explicit_daily",
        ),
    ))
    assert _cadence_summary_line(inst) == "cadence: 0 sparse, 1 explicit_daily"


def test_mixed_singletons_and_templates() -> None:
    inst = _l2(
        accounts=(
            Account(
                id=Identifier("a1"), role=Identifier("R1"),
                scope=SCOPE_INTERNAL, name=Name("A1"),
                balance_cadence="explicit_daily",
            ),
            Account(
                id=Identifier("a2"), role=Identifier("R2"),
                scope=SCOPE_INTERNAL, name=Name("A2"),
                balance_cadence="sparse",
            ),
            Account(
                id=Identifier("a3"), role=Identifier("R3"),
                scope=SCOPE_INTERNAL, name=Name("A3"),
                balance_cadence=None,
            ),
        ),
        templates=(
            AccountTemplate(
                role=Identifier("T1"), scope=SCOPE_INTERNAL,
                balance_cadence="explicit_daily",
            ),
            AccountTemplate(
                role=Identifier("T2"), scope=SCOPE_INTERNAL,
                balance_cadence="sparse",
            ),
        ),
    )
    assert _cadence_summary_line(inst) == "cadence: 3 sparse, 2 explicit_daily"
