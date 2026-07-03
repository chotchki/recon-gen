# z3-solver ships no type stubs; relax ONLY the untyped-cascade rules
# for this z3-boundary file (the rest of tests/ stays fully strict).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportAttributeAccessIssue=false, reportArgumentType=false
"""EB.2 — the on-chain solver runner: pinned verdict, bounded budget.

Productionized from the DS.0 WSL2-determinism spike
(``ds_0_spike_evidence/canon/obligations.py::solve_bounded``). The solver
is a DEPENDENCY OF UNKNOWN RELIABILITY (nondeterministic near a timeout),
handled like the browser tier's bounded-timeout — never a policy
exception. Two bounds:

- ``rlimit`` — the DETERMINISTIC budget (z3's internal resource counter);
  the same input hits it identically on every machine, so it's the
  reproducible cap.
- a wall-clock timer — the nondeterministic BACKSTOP; if it fires the
  verdict is ``unknown`` → ``SolverInconclusive`` anyway.

Every obligation pins its EXPECTED verdict (``unsat`` for a law proof —
"no state violates the law"; ``sat`` for a discriminator — "here's a state
that separates the mutant"). Any transition off the pin fails the runner
as an ORDINARY failure:

- ``sat`` on a law obligation is a real COUNTEREXAMPLE — it prints the
  witnessing model and fails, and it can NEVER be xfail-ed away.
- ``unknown`` / timeout raises ``SolverInconclusive`` and fails; the
  operator triages and, only after, may hand-set a ``raises=
  SolverInconclusive`` xfail (reason naming the pin bump). Never
  auto-set. The ``raises=`` scoping makes it structurally unable to mask
  a real counterexample.

z3 version + rlimit are PINNED (they're part of the EB cache fingerprint,
so a bump must be deliberate). The pin values live in ``eb_config``.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import z3

#: Pinned solver budget — z3 version is enforced separately (eb_config).
#: rlimit is the deterministic cap (same input, same count, any machine).
RLIMIT: int = 500_000_000
#: The nondeterministic wall-clock backstop; a fire → unknown → fails.
WALL_CAP_S: float = 120.0

#: The pinned z3 version — part of the proof-cache fingerprint. A bump is
#: a deliberate re-pin (the solver's behaviour can change between
#: versions; a proof under one is not automatically a proof under another).
PINNED_Z3 = "4.16.0"


class SolverInconclusive(Exception):
    """z3 returned ``unknown`` (rlimit, wall-cap, or gave up). A distinct
    type so a hand-set ``pytest.raises`` / ``xfail(raises=...)`` after
    triage can scope to EXACTLY this and never mask a ``sat`` (a real
    counterexample) or an off-pin ``unsat``."""


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: str  # "unsat" | "sat"
    wall_s: float
    rlimit_used: int
    model: str | None  # the witness, on sat


def _deterministic_z3() -> None:
    z3.set_param("smt.random_seed", 0)
    z3.set_param("sat.random_seed", 0)


def solve(assertions: list[Any]) -> Verdict:
    """Solve one obligation under the pinned budget. Raises
    ``SolverInconclusive`` on ``unknown`` / timeout; otherwise returns
    the verdict (with the witnessing model on ``sat``)."""
    _deterministic_z3()
    s = z3.Solver()
    s.set("rlimit", RLIMIT)
    for a in assertions:
        s.add(a)
    timer = threading.Timer(WALL_CAP_S, z3.main_ctx().interrupt)
    timer.daemon = True
    timer.start()
    t0 = time.perf_counter()
    try:
        res = s.check()
    finally:
        timer.cancel()
    wall = time.perf_counter() - t0
    stats = s.statistics()
    rused = 0
    for k in stats.keys():
        if k == "rlimit count":
            rused = int(stats.get_key_value(k))
    if res == z3.unsat:
        return Verdict("unsat", wall, rused, None)
    if res == z3.sat:
        return Verdict("sat", wall, rused, str(s.model()))
    raise SolverInconclusive(
        f"z3 returned unknown after {wall:.2f}s "
        f"(rlimit={RLIMIT}, reason={s.reason_unknown()!r})",
    )


def discharge(name: str, expected: str, assertions: list[Any]) -> Verdict:
    """Solve and enforce the pin. ``expected`` is ``unsat`` (a law: no
    state violates it) or ``sat`` (a discriminator: a separating state
    exists). Any transition off the pin raises with the reason — a
    ``sat`` where ``unsat`` was pinned prints the witnessing model (the
    counterexample), which is the whole point and never xfail-able."""
    v = solve(assertions)
    if v.verdict == expected:
        return v
    if expected == "unsat" and v.verdict == "sat":
        raise AssertionError(
            f"LAW VIOLATED — {name} pinned unsat but z3 found a "
            f"counterexample.\nWitnessing model:\n{v.model}",
        )
    raise AssertionError(
        f"{name}: pinned {expected!r} but got {v.verdict!r} "
        f"(a discriminator that no longer discriminates, or a law that "
        f"now holds — investigate before re-pinning)",
    )
