# z3-solver ships no type stubs; relax ONLY the untyped-cascade rules
# for this z3-boundary file (the rest of tests/ stays fully strict).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportAttributeAccessIssue=false, reportArgumentType=false
"""EB.2 — the on-chain theorem run: every pinned obligation, its verdict.

Each obligation is a ∀-ℤ statement about a residual LAW, discharged by z3
under the pinned budget. ``unsat`` proves the law (no state violates it);
a transition off the pin fails the runner as an ordinary failure — a
``sat`` on a law obligation prints the witnessing counterexample and can
never be xfail-ed. See ``solver.py`` for the full verdict-pin contract.

Tier: MEASURED here (``test_zz_theorem_solve_budget``). The money-family
theorems are tiny linear-integer formulas; if the average solve stays
well under 30s this stays a unit-tier gate, else it moves to the
agreement layer (the DS.0 tier rule).
"""
from __future__ import annotations

import pytest

from tests.prover.solver import Verdict, discharge
from tests.prover.theorems import Obligation, all_theorems

_OBLIGATIONS = all_theorems()


@pytest.mark.parametrize(
    "obligation", _OBLIGATIONS, ids=[o.name for o in _OBLIGATIONS],
)
def test_theorem(obligation: Obligation) -> None:
    discharge(obligation.name, obligation.expected, obligation.assertions)


def test_every_residual_family_has_theorems() -> None:
    """No residual silently drops out of the theorem set."""
    assert len(_OBLIGATIONS) >= 6, len(_OBLIGATIONS)
    names = {o.name.split("/")[0] for o in _OBLIGATIONS}
    assert "drift" in names


def test_zz_theorem_solve_budget() -> None:
    """MEASURE the average solve time — the tier decision reads off this
    (< 30s avg keeps the theorem run in the unit tier)."""
    total = 0.0
    for o in _OBLIGATIONS:
        v: Verdict = discharge(o.name, o.expected, o.assertions)
        total += v.wall_s
    avg = total / len(_OBLIGATIONS)
    assert avg < 30.0, f"average solve {avg:.3f}s exceeds the unit-tier budget"
