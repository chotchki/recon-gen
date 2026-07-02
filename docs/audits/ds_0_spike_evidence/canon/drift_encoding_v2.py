"""Second, textually-different implementation of the drift-equivalence encoding.

Same formula as docs/audits/ds_z3_spike_evidence/drift_z3.py (State +
disagreement_formula with no mutations), rewritten the way a refactor would:

  * every Python variable AND every z3 constant name is different
    (t{i}_amt -> leg{i}.cents, b{r}_money -> snap{r}.stored, ...)
  * independent construction reordered: balance table built BEFORE the
    transaction table; per-row bound constraints appended in a different
    field order; the disagreement Or iterates day-major instead of
    account-major; Xor takes (detector, spec) instead of (spec, detector)
  * helpers extracted (_half_open bounds, _newest_row supersession mask,
    _sum_if) where drift_z3 writes comprehensions inline
  * detector conjunction hand-inlined: drift_z3 nests csb_exists as an
    And inside the outer And (with `internal`/`leaf` repeated); here the
    flattened, deduped conjunct set is written directly
  * conjunct/disjunct orders shuffled throughout

Deliberately NOT changed (the canonicalizer does not normalize these, and a
real refactor that does them reads as a changed formula -> re-prove):
  * comparison direction/shape: x >= 0 stays >= 0 (not 0 <= x),
    status <= 2 stays <= 2 (not < 3)
  * the `weight * cents` multiplier shape (drift_z3's `sign * amt`)
  * subtraction operand order (stored - computed)
"""
from __future__ import annotations

from z3 import And, Bool, BoolRef, Distinct, If, Implies, Int, IntVal, Or, Sum, Xor

_POSTED = 0


def _half_open(x, lo: int, hi: int) -> list:
    """lo <= x < hi, same predicate shapes as drift_z3's inline bounds."""
    return [x >= lo, x < hi]


def _newest_row(alive, keys, seq, i: int, n: int):
    """Supersession mask: row i is alive and has the max seq among rows
    sharing its key tuple (drift_z3's t_curr / b_curr comprehension)."""
    rivals = [
        Implies(And(alive[j], *[k[j] == k[i] for k in keys]), seq[j] < seq[i])
        for j in range(n) if j != i
    ]
    return And(alive[i], *rivals)


def _sum_if(guards_and_terms) -> object:
    return Sum(*[If(g, t, IntVal(0)) for g, t in guards_and_terms])


class DriftWorldV2:
    """Mirror of drift_z3.State(k, m, A, D) — baseline (unmutated) shapes only."""

    def __init__(self, n_legs: int, n_snaps: int, n_accounts: int, n_days: int):
        self.n_legs, self.n_snaps = n_legs, n_snaps
        self.n_accounts, self.n_days = n_accounts, n_days
        facts: list = []

        # --- daily_balances first (drift_z3 builds transactions first) ----
        self.snap_alive = [Bool(f"snap{r}.alive") for r in range(n_snaps)]
        self.snap_owner = [Int(f"snap{r}.owner") for r in range(n_snaps)]
        self.snap_when = [Int(f"snap{r}.when") for r in range(n_snaps)]
        self.snap_stored = [Int(f"snap{r}.stored") for r in range(n_snaps)]
        self.snap_seq = [Int(f"snap{r}.seq") for r in range(n_snaps)]
        for r in range(n_snaps):
            # day bounds before account bounds (drift_z3: account then day)
            facts += _half_open(self.snap_when[r], 0, n_days)
            facts += _half_open(self.snap_owner[r], 0, n_accounts)
        if n_snaps > 1:
            facts.append(Distinct(*self.snap_seq))
        self.snap_newest = [
            _newest_row(self.snap_alive, [self.snap_owner, self.snap_when],
                        self.snap_seq, r, n_snaps)
            for r in range(n_snaps)
        ]

        # --- account attributes ------------------------------------------
        self.acct_internal = [Bool(f"who{a}.internal") for a in range(n_accounts)]
        self.acct_is_leaf = [Bool(f"who{a}.leaf") for a in range(n_accounts)]

        # --- transactions --------------------------------------------------
        self.leg_alive = [Bool(f"leg{i}.alive") for i in range(n_legs)]
        self.leg_logical = [Int(f"leg{i}.logical") for i in range(n_legs)]
        self.leg_seq = [Int(f"leg{i}.seq") for i in range(n_legs)]
        self.leg_owner = [Int(f"leg{i}.owner") for i in range(n_legs)]
        self.leg_when = [Int(f"leg{i}.when") for i in range(n_legs)]
        self.leg_cents = [Int(f"leg{i}.cents") for i in range(n_legs)]
        self.leg_state = [Int(f"leg{i}.state") for i in range(n_legs)]
        for i in range(n_legs):
            # status bounds first, then day, owner, logical id
            # (status keeps drift_z3's exact `>= 0` / `<= 2` shapes — a
            # "cleanup" to `< 3` would read as a changed formula)
            facts += [self.leg_state[i] >= 0, self.leg_state[i] <= 2]
            facts += _half_open(self.leg_when[i], 0, n_days)
            facts += _half_open(self.leg_owner[i], 0, n_accounts)
            facts += _half_open(self.leg_logical[i], 0, n_legs)
        if n_legs > 1:
            facts.append(Distinct(*self.leg_seq))
        self.leg_newest = [
            _newest_row(self.leg_alive, [self.leg_logical], self.leg_seq, i, n_legs)
            for i in range(n_legs)
        ]
        self.facts = facts

        # --- derived (account, day) grid ----------------------------------
        self.has_emit = [[Or(*[And(self.snap_newest[r],
                                   self.snap_owner[r] == a,
                                   self.snap_when[r] == d)
                               for r in range(n_snaps)])
                          for d in range(n_days)] for a in range(n_accounts)]
        self.emit_money = [[_sum_if(
            (And(self.snap_newest[r], self.snap_owner[r] == a,
                 self.snap_when[r] == d), self.snap_stored[r])
            for r in range(n_snaps))
            for d in range(n_days)] for a in range(n_accounts)]
        # LOCF carry-forward chain
        self.carried_known = [[None] * n_days for _ in range(n_accounts)]
        self.carried_money = [[None] * n_days for _ in range(n_accounts)]
        for a in range(n_accounts):
            for d in range(n_days):
                if d == 0:
                    self.carried_known[a][0] = self.has_emit[a][0]
                    self.carried_money[a][0] = self.emit_money[a][0]
                else:
                    self.carried_known[a][d] = Or(self.has_emit[a][d],
                                                  self.carried_known[a][d - 1])
                    self.carried_money[a][d] = If(self.has_emit[a][d],
                                                  self.emit_money[a][d],
                                                  self.carried_money[a][d - 1])

    def ledger_sum(self, a: int, d: int):
        """computed_subledger_balance's correlated SUM (baseline knobs:
        supersession on, Posted filter on, <=, weight 1). Conjunct order
        shuffled vs drift_z3.computed()."""
        weight = 1
        return _sum_if(
            (And(self.leg_owner[i] == a,
                 self.leg_when[i] <= d,
                 self.leg_state[i] == _POSTED,
                 self.leg_newest[i]),
             weight * self.leg_cents[i])
            for i in range(self.n_legs))

    def law_flag(self, a: int, d: int):
        """drift_z3.spec_flag, conjuncts reordered."""
        gap = self.emit_money[a][d] - self.ledger_sum(a, d)
        return And(self.acct_internal[a], self.acct_is_leaf[a],
                   self.has_emit[a][d], gap != 0)

    def matview_flag(self, a: int, d: int):
        """drift_z3.detector_flag with the nested csb_exists And hand-inlined
        (flattened + deduped conjunct set, different order)."""
        return And(self.has_emit[a][d],
                   self.carried_known[a][d],
                   self.acct_is_leaf[a],
                   self.acct_internal[a],
                   self.carried_money[a][d] != self.ledger_sum(a, d))

    def disagreement(self) -> BoolRef:
        """Day-major cell walk; Xor args swapped vs drift_z3."""
        cells = [Xor(self.matview_flag(a, d), self.law_flag(a, d))
                 for d in range(self.n_days) for a in range(self.n_accounts)]
        return Or(*cells)

    def assertions(self) -> list:
        return [self.disagreement(), *self.facts]
