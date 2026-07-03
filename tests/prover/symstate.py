"""EB.1 — build a ``ResidualState`` whose money fields are symbolic.

Mirrors the DS.1 KAT loader (``test_ds1_residual_kats``) exactly, EXCEPT
every money slot (``leg.amount``, ``balance.money``,
``balance.expected_eod``, the ``limit_breach`` cap) becomes a
``SymCents`` over a fresh z3 Int variable. Everything else — ids,
statuses, dates, scope/role — stays concrete, because the residuals'
cell-existence guards and filters key off STRUCTURE, and only the
magnitudes go symbolic (the canary + the theorems quantify over the
magnitudes at a fixed structure).

Returns the state plus the list of ``(z3 var, concrete int)`` bindings,
so the dual-run canary can substitute the KAT's concrete values back in,
and the theorem obligations can quantify over the free vars.
"""
# z3-solver ships no type stubs; relax ONLY the untyped-cascade rules
# for this z3-boundary file (the rest of tests/ stays fully strict).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
from __future__ import annotations

import datetime as dt
from typing import Any

import z3

from recon_gen.common.spine.residuals import BalanceRow, LegRow, ResidualState
from tests.prover.symbolic import SymCents

Binding = tuple[z3.ArithRef, int]


class _VarFactory:
    """Fresh, collision-free z3 Int vars + their concrete bindings."""

    def __init__(self) -> None:
        self.bindings: list[Binding] = []
        self._n = 0

    def money(self, concrete: int, hint: str) -> SymCents:
        var = z3.Int(f"{hint}_{self._n}")
        self._n += 1
        self.bindings.append((var, concrete))
        return SymCents(var)


def _leg(vf: _VarFactory, d: dict[str, Any]) -> LegRow:
    return LegRow(
        id=d["id"],
        entry=d["entry"],  # typing-smell: ignore[no-inline-production-constants]: KAT JSON field key, not the migrate_mark column constant
        account_id=d["account_id"],
        amount=vf.money(int(d["amount"]), "amt"),  # type: ignore[arg-type]: SymCents duck-types Cents on the money-residual surface (the EB.1 adapter contract)
        status=d["status"],
        posting=dt.datetime.fromisoformat(d["posting"]),
        transfer_id=d["transfer_id"],
        transfer_parent_id=d.get("transfer_parent_id"),
        rail_name=d.get("rail_name"),
        template_name=d.get("template_name"),
        bundle_id=d.get("bundle_id"),
        account_scope=d.get("account_scope", "internal"),
        account_role=d.get("account_role"),
        account_parent_role=d.get("account_parent_role"),
    )


def _bal(vf: _VarFactory, d: dict[str, Any]) -> BalanceRow:
    expected_eod = d.get("expected_eod")
    day_end = d.get("day_end")
    return BalanceRow(
        account_id=d["account_id"],
        entry=d["entry"],  # typing-smell: ignore[no-inline-production-constants]: KAT JSON field key, not the migrate_mark column constant
        day=dt.date.fromisoformat(d["day"]),
        money=vf.money(int(d["money"]), "money"),  # type: ignore[arg-type]: SymCents duck-types Cents
        day_end=None if day_end is None else dt.datetime.fromisoformat(day_end),
        expected_eod=(
            None if expected_eod is None
            else vf.money(int(expected_eod), "eod")  # type: ignore[arg-type]: SymCents duck-types Cents
        ),
        account_scope=d.get("account_scope", "internal"),
        account_role=d.get("account_role"),
        account_parent_role=d.get("account_parent_role"),
    )


def symbolic_state(state_dict: dict[str, Any]) -> tuple[ResidualState, list[Binding]]:
    """The symbolic twin of the KAT ``state`` block: same rows, money
    slots symbolic. Returns ``(state, bindings)``."""
    vf = _VarFactory()
    legs = tuple(_leg(vf, x) for x in state_dict.get("legs", ()))
    balances = tuple(_bal(vf, x) for x in state_dict.get("balances", ()))
    return ResidualState(legs=legs, balances=balances), vf.bindings


def symbolic_cap(vf_bindings: list[Binding], concrete: int | None) -> SymCents | None:
    """A symbolic ``limit_breach`` cap sharing the state's binding list."""
    if concrete is None:
        return None
    var = z3.Int(f"cap_{len(vf_bindings)}")
    vf_bindings.append((var, int(concrete)))
    return SymCents(var)
