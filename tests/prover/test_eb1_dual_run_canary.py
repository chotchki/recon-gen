"""EB.1 — the dual-run canary: symbolic execution agrees with concrete.

For every MONEY known-answer vector, run the SAME residual function two
ways — concrete (Cents ints, the DS.1 path) and symbolic (SymCents over
z3 Int vars, the EB.1 adapter) — then substitute the vector's concrete
money values back into the symbolic term and assert the two results are
identical (both a value, or both ``None``).

This is a differential test of the ADAPTER, not a theorem: it proves the
z3 term the prover will quantify over actually computes the law at every
KAT point. If the SymCents / sym_when swap ever diverges from the real
Cents arithmetic on a body, a vector here catches it before any theorem
is trusted. The KAT vectors are hand-derived from the written laws
(``ds_1_kat_derivations.md``), so a residual bug and a KAT bug can't
cancel — the canary inherits that independence.
"""
# z3-solver ships no type stubs; relax ONLY the untyped-cascade rules
# for this z3-boundary file (the rest of tests/ stays fully strict).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from recon_gen.common.money import Cents
from recon_gen.common.spine.residuals import (
    drift_residual,
    expected_eod_residual,
    ledger_drift_residual,
    limit_breach_residual,
    overdraft_residual,
)
from tests.prover.symbolic import bind_and_eval, symbolic_execute
from tests.prover.symstate import symbolic_cap, symbolic_state
from tests.unit.test_ds1_residual_kats import _state  # concrete KAT state

_KATS = Path(__file__).parent.parent / "data" / "kats"
_MONEY = ("drift", "ledger_drift", "overdraft", "expected_eod", "limit_breach")


def _cell_args(invariant: str, vec: dict[str, Any]) -> tuple[Any, ...]:
    """The concrete (structural) cell arguments each money residual takes
    after ``state`` — identical concrete values for both runs."""
    cell = vec.get("cell", {})
    day = dt.date.fromisoformat(cell["day"])
    if invariant == "drift":
        return (cell["account_id"], day)
    if invariant == "ledger_drift":
        return (cell["parent_account_id"], day)
    if invariant in ("overdraft", "expected_eod"):
        return (cell["account_id"], day)
    if invariant == "limit_breach":
        return (cell["account_id"], day, cell["rail_name"], cell["direction"])
    raise AssertionError(invariant)


_FN = {
    "drift": drift_residual,
    "ledger_drift": ledger_drift_residual,
    "overdraft": overdraft_residual,
    "expected_eod": expected_eod_residual,
    "limit_breach": limit_breach_residual,
}


def _all_money_vectors() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for name in _MONEY:
        payload = json.loads((_KATS / f"{name}.json").read_text())
        assert payload["kind"] == "MONEY", name
        for vec in payload["vectors"]:
            out.append((name, vec))
    return out


_VECTORS = _all_money_vectors()


@pytest.mark.parametrize(
    ("invariant", "vec"),
    _VECTORS,
    ids=[f"{inv}-{v['name']}" for inv, v in _VECTORS],
)
def test_symbolic_execution_matches_concrete(
    invariant: str, vec: dict[str, Any],
) -> None:
    fn = _FN[invariant]
    args = _cell_args(invariant, vec)

    # Concrete run — the DS.1 path, the ground truth.
    concrete_state = _state(vec["state"])
    concrete_args: tuple[Any, ...] = args
    if invariant == "limit_breach":
        raw = vec.get("params", {}).get("cap")
        concrete_args = (*args, None if raw is None else Cents(int(raw)))
    concrete = fn(concrete_state, *concrete_args)

    # Symbolic run — the EB.1 adapter over z3 Int vars.
    sym_state, bindings = symbolic_state(vec["state"])
    sym_args: tuple[Any, ...] = args
    if invariant == "limit_breach":
        raw = vec.get("params", {}).get("cap")
        sym_args = (*args, symbolic_cap(bindings, None if raw is None else int(raw)))
    term = symbolic_execute(fn, sym_state, *sym_args)

    # Both must agree on cell-existence (structural guards decide
    # concretely, so they can't diverge — but assert it).
    if concrete is None:
        assert term is None, (
            f"{invariant}/{vec['name']}: concrete = None but symbolic "
            f"produced a term {term}"
        )
        return
    assert term is not None, (
        f"{invariant}/{vec['name']}: concrete = {concrete} but symbolic "
        f"= None (a cell-existence guard diverged)"
    )

    # Substitute the KAT's concrete money values into the symbolic term
    # and simplify to a numeral — must equal the concrete residual.
    evaluated = bind_and_eval(term, bindings)
    assert evaluated == concrete.value, (
        f"{invariant}/{vec['name']}: symbolic {evaluated} != "
        f"concrete {concrete.value}"
    )


def test_every_money_kat_covered() -> None:
    """No money KAT file silently drops out of the canary, and every one
    of the five money residuals is exercised."""
    assert len(_VECTORS) >= 15, len(_VECTORS)
    assert {inv for inv, _ in _VECTORS} == set(_MONEY)
    for name in _MONEY:
        assert any(inv == name for inv, _ in _VECTORS), name
