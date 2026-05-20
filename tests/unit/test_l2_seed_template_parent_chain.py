"""AG.1 (Gap B): chain emit must thread ``transfer_parent_id`` through
to child rows for ALL chain shapes, including Template-parent shapes.

Pre-fix: ``state.firings[template_name]`` was empty for Template-parent
chains because no code populated template-level firings at baseline.
``_emit_baseline_chains`` at ``state.firings.get(chain.parent, [])``
returned ``[]`` for any chain whose parent is a TransferTemplate (not a
Rail), and the loop body's ``continue`` silently skipped the chain.
Children either didn't emit (Template-child) or emitted standalone via
the rail loop with NULL parent (Rail-child).

This test parameterizes across the 4-cell {Rail-parent, Template-parent}
× {Rail-child, Template-child} matrix per AG.0 lock — each fix's
regression test parameterizes across shapes the gap is shape-sensitive
to. Rail-parent + Rail-child + Rail-parent + Template-child were healthy
pre-fix (chain emit works when ``state.firings[chain.parent]`` is
populated — the rail loop populates it for the Rail-parent case).
Template-parent shapes were the Gap B bites.
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
    Chain,
    ChainChildSpec,
    Identifier,
    L2Instance,
    Money,
    Name,
    SingleLegRail,
    TransferTemplate,
)
from recon_gen.common.l2.seed import emit_baseline_seed
from recon_gen.common.sql.dialect import Dialect


def _single_leg(
    name: str, *, direction: Literal["Debit", "Credit"] = "Debit",
) -> SingleLegRail:
    return SingleLegRail(
        name=Identifier(name),
        origin="InternalInitiated",
        metadata_keys=(Identifier("k"),),
        leg_role=(Identifier("R"),),
        leg_direction=direction,
    )


def _template(name: str, leg_rails: tuple[str, ...]) -> TransferTemplate:
    return TransferTemplate(
        name=Identifier(name),
        expected_net=Money(Decimal("0")),
        transfer_key=(Identifier("k"),),
        completion="business_day_end+1d",
        leg_rails=tuple(Identifier(r) for r in leg_rails),
    )


def _build_l2(*, parent_kind: str, child_kind: str) -> L2Instance:
    """1-chain L2 instance where parent + child are each Rail or Template.

    Each Template carries one leg_rail so the template-as-unit firing
    pattern is exercised but the test stays small. The single role ``R``
    is wired to ``Acct`` and ``AcctTmpl`` so ``_eligible_accounts_for_role``
    resolves something for every leg.
    """
    parent_rails: list[SingleLegRail] = []
    child_rails: list[SingleLegRail] = []
    templates: list[TransferTemplate] = []
    chain_parent: Identifier
    chain_child_name: Identifier

    if parent_kind == "rail":
        parent_rails.append(_single_leg("ParentRail", direction="Debit"))
        chain_parent = Identifier("ParentRail")
    else:
        parent_rails.append(_single_leg("ParentLeg", direction="Debit"))
        templates.append(_template("ParentTmpl", ("ParentLeg",)))
        chain_parent = Identifier("ParentTmpl")

    if child_kind == "rail":
        child_rails.append(_single_leg("ChildRail", direction="Credit"))
        chain_child_name = Identifier("ChildRail")
    else:
        child_rails.append(_single_leg("ChildLeg", direction="Credit"))
        templates.append(_template("ChildTmpl", ("ChildLeg",)))
        chain_child_name = Identifier("ChildTmpl")

    return L2Instance(
        accounts=(
            Account(
                id=Identifier("a1"),
                role=Identifier("R"),
                scope="internal",
                name=Name("Acct1"),
            ),
        ),
        account_templates=(
            AccountTemplate(
                role=Identifier("R"),
                scope="internal",
            ),
        ),
        rails=tuple(parent_rails + child_rails),
        transfer_templates=tuple(templates),
        chains=(
            Chain(
                parent=chain_parent,
                children=(ChainChildSpec(name=chain_child_name),),
            ),
        ),
        limit_schedules=(),
    )


_PARENT_PREFIX_BY_SHAPE = {
    # Rail-parent chains emit children via _emit_chain_child_leg with
    # transfer_id format `tr-base-chain-<rail_slug>-NNNNNN` (Rail-child)
    # or `tr-base-chain-tmpl-NNNNNN` (Template-child, via the
    # _emit_chain_child_template_legs shared_transfer_id allocator).
    # Template-parent chains use the same downstream shape — the AG.1
    # fix routes through the same _emit_chain_child_leg / template-legs
    # path once state.firings[template_name] is populated.
    ("rail", "rail"): "tr-base-chain-",
    ("rail", "template"): "tr-base-chain-tmpl-",
    ("template", "rail"): "tr-base-chain-",
    ("template", "template"): "tr-base-chain-tmpl-",
}


def _emit_sql(inst: L2Instance) -> str:
    return emit_baseline_seed(
        inst,
        prefix="t",
        window_days=10,
        anchor=date(2030, 1, 1),
        dialect=Dialect.SQLITE,
    )


def _chain_child_rows(sql: str, expected_prefix: str) -> list[str]:
    """Return the VALUES rows from the SQL whose transfer_id starts
    with ``expected_prefix``. One VALUES row per line per the emitter's
    layout."""
    return [
        ln for ln in sql.splitlines()
        if f"'{expected_prefix}" in ln
    ]


def _row_transfer_parent(row: str) -> str | None:
    """Extract the literal token in the transfer_parent_id column slot.

    Row shape (from ``_txn_row`` in seed.py):
        ``(id, account_id, account_name, account_role, account_scope,
        parent_role, money, direction, status, posting, transfer_id,
        NULL, transfer_parent_id, rail_name, ...)``

    The literal sits two commas past the transfer_id. ``NULL`` means
    unset; a quoted ``'tr-...'`` means set.
    """
    # Find the transfer_id quoted token, then look 2 commas ahead.
    match = re.search(r"'(tr-[A-Za-z0-9_-]+)',\s*NULL,\s*(NULL|'tr-[A-Za-z0-9_-]+')", row)
    if match is None:
        return None
    tok = match.group(2)
    if tok == "NULL":
        return None
    return tok.strip("'")


@pytest.mark.parametrize("parent_kind", ["rail", "template"])
@pytest.mark.parametrize("child_kind", ["rail", "template"])
def test_chain_child_carries_parent_transfer_id(
    parent_kind: str, child_kind: str,
) -> None:
    """AG.1 Gap B: chain-emit rows for the child must carry
    ``transfer_parent_id`` for every {parent_kind, child_kind}
    combination.

    Pre-fix: Template-parent shapes produced ZERO chain-emit rows
    (the loop's ``continue`` silently skipped). Post-fix: shape and
    count match the Rail-parent path because ``state.firings[template]``
    is populated by ``_emit_baseline_template_firings`` before
    ``_emit_baseline_chains`` runs.
    """
    inst = _build_l2(parent_kind=parent_kind, child_kind=child_kind)
    sql = _emit_sql(inst)
    expected_prefix = _PARENT_PREFIX_BY_SHAPE[(parent_kind, child_kind)]

    chain_rows = _chain_child_rows(sql, expected_prefix)
    assert chain_rows, (
        f"No chain-emit rows for parent={parent_kind}, child={child_kind}. "
        f"Expected transfer_id prefix {expected_prefix!r}. "
        f"Likely state.firings[chain.parent] is empty for Template-parent "
        f"chains (Gap B)."
    )

    null_rows = [r for r in chain_rows if _row_transfer_parent(r) is None]
    assert not null_rows, (
        f"transfer_parent_id is NULL in {len(null_rows)}/{len(chain_rows)} "
        f"chain-emit rows for parent={parent_kind}, child={child_kind}.\n"
        f"First offender:\n{null_rows[0][:300]}"
    )
