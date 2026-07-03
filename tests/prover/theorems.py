# z3-solver ships no type stubs; relax ONLY the untyped-cascade rules
# for this z3-boundary file (the rest of tests/ stays fully strict).
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportAttributeAccessIssue=false, reportArgumentType=false
"""EB.2 — the ∀-ℤ theorems about the residual LAWS themselves.

Each theorem builds TWO symbolic states related by a transformation (add
a Failed leg, supersede with an identical copy, bolt on a disjoint
account, scale every amount by k, bump one leg by δ), runs the ACTUAL
residual over each via the EB.1 adapter, and asserts a relation between
the two z3 terms holds for ALL integer amounts. "Holds for all" is
proven by handing z3 the NEGATION and pinning ``unsat`` — no state
violates it.

The theorem set is per-residual-CLASS, because the laws genuinely differ:

- Every money residual: **failed-leg inertness** (a Failed leg changes
  nothing — money moves on Posted), **supersession idempotence**
  (superseding a leg with an identical-value copy changes nothing),
  **interference-freedom** (a disjoint account can't shift this account's
  cell — the ∀-ℤ form of the AU.2 composition lemma).
- The LINEAR residuals (drift, ledger_drift, expected_eod): also
  **homogeneity at {−1, 2, 3}** (scaling every amount by k scales the
  residual by k) and **additivity** (bumping one contributing leg by δ
  shifts the residual by ∓δ).
- The CLAMPED residuals (overdraft, limit_breach): homogeneity only at
  POSITIVE k. They are one-sided (``when(x < 0, x, 0)``), so k=−1
  genuinely breaks it — and that's CORRECT, not a bug; we pin a
  discriminator (``sat``) proving they are NOT (−1)-homogeneous rather
  than claim a false theorem.

anomaly is excluded by name (its z-score is nonlinear real arithmetic —
the wrong tool; its contract is DS.4's tolerance sweep).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable

import z3

from recon_gen.common.spine.residuals import (
    BalanceRow,
    LegRow,
    ResidualState,
    drift_residual,
    expected_eod_residual,
    ledger_drift_residual,
    limit_breach_residual,
    overdraft_residual,
)
from tests.prover.symbolic import SymCents, symbolic_execute

_DAY = dt.date(2030, 1, 1)
_NOON = dt.datetime.combine(_DAY, dt.time(12, 0))
_EOD = dt.datetime.combine(_DAY, dt.time(23, 59, 59))
_POSTED = "Posted"


# ---------------------------------------------------------------------------
# Symbolic row builders (money slots are z3 Int vars).


def _sym(name: str) -> tuple[SymCents, z3.ArithRef]:
    v = z3.Int(name)
    return SymCents(v), v


def _money(name: str) -> SymCents:
    """A fresh symbolic money value (the z3 var is unneeded at the call
    site — it lives inside the SymCents)."""
    return SymCents(z3.Int(name))


def _leg(
    *, id: str, entry: int, account: str, amount: SymCents, status: str = _POSTED,
    posting: dt.datetime = _NOON, transfer: str = "t", rail: str = "RailX",
    parent_role: str | None = "CustomerLedger", scope: str = "internal",
    role: str | None = "CustomerSubledger",
) -> LegRow:
    return LegRow(
        id=id, entry=entry, account_id=account, amount=amount,  # type: ignore[arg-type]: SymCents duck-types Cents on the money-residual surface
        status=status, posting=posting, transfer_id=transfer,
        rail_name=rail, account_scope=scope, account_role=role,
        account_parent_role=parent_role,
    )


def _bal(
    *, account: str, entry: int, money: SymCents, day: dt.date = _DAY,
    day_end: dt.datetime | None = _EOD, expected_eod: SymCents | None = None,
    parent_role: str | None = "CustomerLedger", scope: str = "internal",
    role: str | None = "CustomerSubledger",
) -> BalanceRow:
    return BalanceRow(
        account_id=account, entry=entry, day=day, money=money,  # type: ignore[arg-type]: SymCents duck-types Cents
        day_end=day_end,
        expected_eod=expected_eod,  # type: ignore[arg-type]: SymCents duck-types Cents
        account_scope=scope, account_role=role, account_parent_role=parent_role,
    )


def _term(fn: Callable[..., Any], state: ResidualState, *args: Any) -> z3.ArithRef:
    """The residual's z3 term for a state whose target cell EXISTS (the
    theorems build the structure so it does — a None here is a bug in
    the setup, not an expected outcome)."""
    t = symbolic_execute(fn, state, *args)
    if t is None:
        raise AssertionError("theorem setup produced no cell (residual None)")
    return t


@dataclass(frozen=True, slots=True)
class Obligation:
    name: str
    expected: str  # "unsat" (law) | "sat" (discriminator)
    assertions: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# drift — the linear exemplar. A leaf internal account: one balance + K
# Posted legs on the same day; drift = stored − Σ legs.

_ACCT = "A"
_ARGS = (_ACCT, _DAY)


def _drift_base(leg_vars: list[SymCents], bal: SymCents) -> ResidualState:
    legs = tuple(
        _leg(id=f"L{i}", entry=1, account=_ACCT, amount=a)
        for i, a in enumerate(leg_vars)
    )
    return ResidualState(legs=legs, balances=(_bal(account=_ACCT, entry=1, money=bal),))


def drift_theorems() -> list[Obligation]:
    out: list[Obligation] = []
    # Two Posted legs + a balance — the working structure for every
    # transformation below.
    l0 = _money("l0")
    l1 = _money("l1")
    b = _money("bal")
    base = _drift_base([l0, l1], b)
    t_base = _term(drift_residual, base, *_ARGS)

    # failed-leg inertness: a Failed leg (its own var) changes nothing.
    f = _money("failed")
    plus_failed = ResidualState(
        legs=(*base.legs, _leg(id="Lf", entry=1, account=_ACCT, amount=f, status="Failed")),
        balances=base.balances,
    )
    out.append(Obligation(
        "drift/failed-leg-inertness", "unsat",
        [t_base != _term(drift_residual, plus_failed, *_ARGS)],
    ))

    # supersession idempotence: superseding l0 with an identical-value
    # copy (same id, higher entry, same var) changes nothing.
    superseded = ResidualState(
        legs=(*base.legs, _leg(id="L0", entry=2, account=_ACCT, amount=l0)),
        balances=base.balances,
    )
    out.append(Obligation(
        "drift/supersession-idempotence", "unsat",
        [t_base != _term(drift_residual, superseded, *_ARGS)],
    ))

    # interference-freedom (AU.2): a disjoint account B (own balance +
    # legs, own vars) can't move A's drift.
    ob = _money("Bbal")
    obl = _money("Bleg")
    with_b = ResidualState(
        legs=(*base.legs, _leg(id="BL", entry=1, account="B", amount=obl)),
        balances=(*base.balances, _bal(account="B", entry=1, money=ob)),
    )
    out.append(Obligation(
        "drift/interference-freedom", "unsat",
        [t_base != _term(drift_residual, with_b, *_ARGS)],
    ))

    # homogeneity at k ∈ {−1, 2, 3}: scale the balance + every leg by k,
    # the residual scales by k.
    for k in (-1, 2, 3):
        scaled = _drift_base([l0 * k, l1 * k], b * k)
        out.append(Obligation(
            f"drift/homogeneity-k{k}", "unsat",
            [_term(drift_residual, scaled, *_ARGS) != t_base * k],
        ))

    # additivity: bump one contributing leg by δ, drift moves by −δ
    # (drift = stored − Σ legs).
    d, dv = _sym("delta")
    bumped = _drift_base([l0 + d, l1], b)
    out.append(Obligation(
        "drift/additivity", "unsat",
        [_term(drift_residual, bumped, *_ARGS) != t_base - dv],
    ))
    return out


# ---------------------------------------------------------------------------
# ledger_drift — linear: parent.stored − (Σ child stored + parent direct
# legs). Structure: a parent (parent_role None, role PR) + one child
# (parent_role PR) + one parent direct leg.

_PR = "PR"
_LEDGER_ARGS = ("P", _DAY)


def _ledger_base(p_bal: SymCents, c_bal: SymCents, p_leg: SymCents) -> ResidualState:
    return ResidualState(
        legs=(_leg(id="PL", entry=1, account="P", amount=p_leg,
                   role=_PR, parent_role=None),),
        balances=(
            _bal(account="P", entry=1, money=p_bal, role=_PR, parent_role=None),
            _bal(account="C", entry=1, money=c_bal, role="CR", parent_role=_PR),
        ),
    )


def ledger_drift_theorems() -> list[Obligation]:
    out: list[Obligation] = []
    p, c, pl = _money("p"), _money("c"), _money("pl")
    base = _ledger_base(p, c, pl)
    t = _term(ledger_drift_residual, base, *_LEDGER_ARGS)

    failed = ResidualState(
        legs=(*base.legs, _leg(id="Pf", entry=1, account="P", amount=_money("pf"),
                               status="Failed", role=_PR, parent_role=None)),
        balances=base.balances,
    )
    out.append(Obligation("ledger_drift/failed-leg-inertness", "unsat",
                          [t != _term(ledger_drift_residual, failed, *_LEDGER_ARGS)]))

    superseded = ResidualState(
        legs=(*base.legs, _leg(id="PL", entry=2, account="P", amount=pl,
                               role=_PR, parent_role=None)),
        balances=base.balances,
    )
    out.append(Obligation("ledger_drift/supersession-idempotence", "unsat",
                          [t != _term(ledger_drift_residual, superseded, *_LEDGER_ARGS)]))

    # A disjoint account D that is NOT a child of P (parent_role != PR).
    with_d = ResidualState(
        legs=(*base.legs, _leg(id="DL", entry=1, account="D", amount=_money("dl"),
                               role="DR", parent_role="ZZ")),
        balances=(*base.balances,
                  _bal(account="D", entry=1, money=_money("db"),
                       role="DR", parent_role="ZZ")),
    )
    out.append(Obligation("ledger_drift/interference-freedom", "unsat",
                          [t != _term(ledger_drift_residual, with_d, *_LEDGER_ARGS)]))

    for k in (-1, 2, 3):
        scaled = _ledger_base(p * k, c * k, pl * k)
        out.append(Obligation(f"ledger_drift/homogeneity-k{k}", "unsat",
                              [_term(ledger_drift_residual, scaled, *_LEDGER_ARGS) != t * k]))

    d, dv = _sym("l_delta")
    bumped = _ledger_base(p, c + d, pl)  # bump child stored by δ → residual −δ
    out.append(Obligation("ledger_drift/additivity", "unsat",
                          [_term(ledger_drift_residual, bumped, *_LEDGER_ARGS) != t - dv]))
    return out


# ---------------------------------------------------------------------------
# overdraft — CLAMPED, balance-only: when(stored < 0, stored, 0) =
# min(stored, 0). Reads no legs; homogeneous only at positive k.

_OD_ARGS = ("A", _DAY)


def _overdraft_base(bal: SymCents) -> ResidualState:
    return ResidualState(legs=(), balances=(_bal(account="A", entry=1, money=bal),))


def overdraft_theorems() -> list[Obligation]:
    out: list[Obligation] = []
    b = _money("od_bal")
    base = _overdraft_base(b)
    t = _term(overdraft_residual, base, *_OD_ARGS)

    # leg-inertness: overdraft reads only the balance, so ANY leg (even a
    # Posted one) leaves it unchanged.
    with_leg = ResidualState(
        legs=(_leg(id="L", entry=1, account="A", amount=_money("odl")),),
        balances=base.balances,
    )
    out.append(Obligation("overdraft/leg-inertness", "unsat",
                          [t != _term(overdraft_residual, with_leg, *_OD_ARGS)]))

    superseded = ResidualState(
        legs=(),
        balances=(_bal(account="A", entry=1, money=_money("old")),
                  _bal(account="A", entry=2, money=b)),
    )
    out.append(Obligation("overdraft/supersession-idempotence", "unsat",
                          [t != _term(overdraft_residual, superseded, *_OD_ARGS)]))

    with_c = ResidualState(
        legs=(),
        balances=(*base.balances, _bal(account="C", entry=1, money=_money("cb"))),
    )
    out.append(Obligation("overdraft/interference-freedom", "unsat",
                          [t != _term(overdraft_residual, with_c, *_OD_ARGS)]))

    # Homogeneity at POSITIVE k only.
    for k in (2, 3):
        scaled = _overdraft_base(b * k)
        out.append(Obligation(f"overdraft/homogeneity-k{k}", "unsat",
                              [_term(overdraft_residual, scaled, *_OD_ARGS) != t * k]))

    # At k=−1 it CORRECTLY breaks (a one-sided clamp is not sign-symmetric):
    # there EXISTS a state where overdraft(−1·s) != −1·overdraft(s). Pinning
    # the discriminator sat proves the residual is NOT (−1)-homogeneous —
    # a fact about the law, not a bug.
    neg = _overdraft_base(b * -1)
    out.append(Obligation("overdraft/not-homogeneous-at-neg1", "sat",
                          [_term(overdraft_residual, neg, *_OD_ARGS) != t * -1]))
    return out


# ---------------------------------------------------------------------------
# expected_eod — linear, balance-only: money − expected (an emitted claim
# carrying an expectation on the target day).

_EOD_ARGS = ("A", _DAY)


def _eod_base(money: SymCents, expected: SymCents) -> ResidualState:
    return ResidualState(
        legs=(),
        balances=(_bal(account="A", entry=1, money=money, expected_eod=expected),),
    )


def expected_eod_theorems() -> list[Obligation]:
    out: list[Obligation] = []
    m, e = _money("eod_m"), _money("eod_e")
    base = _eod_base(m, e)
    t = _term(expected_eod_residual, base, *_EOD_ARGS)

    with_leg = ResidualState(
        legs=(_leg(id="L", entry=1, account="A", amount=_money("eodl")),),
        balances=base.balances,
    )
    out.append(Obligation("expected_eod/leg-inertness", "unsat",
                          [t != _term(expected_eod_residual, with_leg, *_EOD_ARGS)]))

    with_c = ResidualState(
        legs=(),
        balances=(*base.balances,
                  _bal(account="C", entry=1, money=_money("cm"),
                       expected_eod=_money("ce"))),
    )
    out.append(Obligation("expected_eod/interference-freedom", "unsat",
                          [t != _term(expected_eod_residual, with_c, *_EOD_ARGS)]))

    for k in (-1, 2, 3):
        scaled = _eod_base(m * k, e * k)
        out.append(Obligation(f"expected_eod/homogeneity-k{k}", "unsat",
                              [_term(expected_eod_residual, scaled, *_EOD_ARGS) != t * k]))

    d, dv = _sym("eod_delta")
    bumped = _eod_base(m + d, e)  # bump money by δ → residual +δ
    out.append(Obligation("expected_eod/additivity", "unsat",
                          [_term(expected_eod_residual, bumped, *_EOD_ARGS) != t + dv]))
    return out


# ---------------------------------------------------------------------------
# limit_breach — CLAMPED: max(0, flow − cap), flow = Σ |matching legs|.
# Matching = Posted, this day, this rail, sign matches direction. The abs +
# the direction filter make it homogeneous at positive k only.

_LB_RAIL = "RailX"
_LB_ARGS = ("A", _DAY, _LB_RAIL, "Outbound")


def _lb_base(leg_amts: list[SymCents], cap: SymCents) -> ResidualState:
    legs = tuple(
        _leg(id=f"LB{i}", entry=1, account="A", amount=a, rail=_LB_RAIL)
        for i, a in enumerate(leg_amts)
    )
    # A balance is not needed (limit_breach reads legs + cap only), but the
    # cap is a residual ARG, not state.
    return ResidualState(legs=legs, balances=())


def limit_breach_theorems() -> list[Obligation]:
    out: list[Obligation] = []
    a0, a1, cap = _money("lb0"), _money("lb1"), _money("cap")
    base = _lb_base([a0, a1], cap)
    t = _term(limit_breach_residual, base, *_LB_ARGS, cap)

    failed = ResidualState(
        legs=(*base.legs, _leg(id="LBf", entry=1, account="A",
                               amount=_money("lbf"), status="Failed", rail=_LB_RAIL)),
        balances=(),
    )
    out.append(Obligation("limit_breach/failed-leg-inertness", "unsat",
                          [t != _term(limit_breach_residual, failed, *_LB_ARGS, cap)]))

    superseded = ResidualState(
        legs=(*base.legs, _leg(id="LB0", entry=2, account="A", amount=a0, rail=_LB_RAIL)),
        balances=(),
    )
    out.append(Obligation("limit_breach/supersession-idempotence", "unsat",
                          [t != _term(limit_breach_residual, superseded, *_LB_ARGS, cap)]))

    # A disjoint account B's legs don't count toward A's flow.
    with_b = ResidualState(
        legs=(*base.legs, _leg(id="BLB", entry=1, account="B",
                               amount=_money("blb"), rail=_LB_RAIL)),
        balances=(),
    )
    out.append(Obligation("limit_breach/interference-freedom", "unsat",
                          [t != _term(limit_breach_residual, with_b, *_LB_ARGS, cap)]))

    # Positive homogeneity: scale amounts AND cap by k>0.
    for k in (2, 3):
        scaled = _lb_base([a0 * k, a1 * k], cap * k)
        out.append(Obligation(f"limit_breach/homogeneity-k{k}", "unsat",
                              [_term(limit_breach_residual, scaled, *_LB_ARGS, cap * k) != t * k]))

    # k=−1 breaks (the direction filter flips outbound↔inbound + max clamps):
    # a discriminating state exists. Pinning sat proves it's NOT
    # (−1)-homogeneous — correct for a one-sided, direction-scoped cap.
    neg = _lb_base([a0 * -1, a1 * -1], cap * -1)
    out.append(Obligation("limit_breach/not-homogeneous-at-neg1", "sat",
                          [_term(limit_breach_residual, neg, *_LB_ARGS, cap * -1) != t * -1]))
    return out


# The residual functions that participate (anomaly excluded by name).
_ = (
    drift_residual, ledger_drift_residual, overdraft_residual,
    expected_eod_residual, limit_breach_residual,
)


def all_theorems() -> list[Obligation]:
    return (
        drift_theorems()
        + ledger_drift_theorems()
        + overdraft_theorems()
        + expected_eod_theorems()
        + limit_breach_theorems()
    )
