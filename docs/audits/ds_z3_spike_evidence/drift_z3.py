"""Z3 spike — bounded equivalence of the <prefix>_drift detector vs the pure
residual law, over symbolic databases of k transaction rows + m balance rows.

Real SQL modeled (src/recon_gen/common/l2/schema.py):

  current_transactions (line ~2090):
      SELECT * FROM {p}_transactions tx
      WHERE tx.entry = (SELECT MAX(entry) FROM {p}_transactions WHERE id = tx.id)

  computed_subledger_balance (line ~684):
      SELECT sb.account_id, sb.business_day_start, ...,
             COALESCE((SELECT SUM(tx.amount_money)
                       FROM {p}_current_transactions tx
                       WHERE tx.account_id = sb.account_id
                         AND tx.status = 'Posted'
                         AND tx.posting <= sb.business_day_end), 0) AS computed_balance
      FROM {p}_current_daily_balances sb
      WHERE sb.account_scope = 'internal' AND sb.account_parent_role IS NOT NULL

  effective_balances (CL.5 carry-forward, line ~2722): per (internal account,
      calendar day in fleet-wide [min emit, max emit]) the last emitted money
      at-or-before that day (LOCF); rows before an account's first emit dropped.

  drift (line ~2934):
      SELECT ..., sb.effective_money - cb.computed_balance AS drift
      FROM {p}_effective_balances sb
      JOIN {p}_computed_subledger_balance cb
        ON cb.account_id = sb.account_id AND cb.business_day_start = sb.business_day_start
      WHERE sb.account_scope = 'internal'
        AND sb.account_parent_role IS NOT NULL
        AND sb.effective_money IS NOT NULL
        AND sb.effective_money <> cb.computed_balance

Model abstractions (see report for the honest list):
  * days are ints 0..D-1; posting <= business_day_end becomes day_tx <= d
    (intraday ordering abstracted away).
  * account_scope / account_parent_role are per-ACCOUNT booleans (denormalized
    columns assumed consistent per account, as the seed guarantees).
  * NULL logic compressed to presence booleans; money/amount are mathematical
    ints (BIGINT overflow out of scope — proofs are over Z, not Z/2^64).
  * entry is globally unique per table (BIGSERIAL) -> Distinct().
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from z3 import (
    And, Bool, Distinct, If, Implies, Int, IntVal, Not, Or, Solver, Sum, Xor,
    sat, set_param, unsat,
)

set_param("smt.random_seed", 0)  # explicit; z3 is deterministic by default

POSTED, PENDING, FAILED = 0, 1, 2
STATUS_NAMES = {0: "Posted", 1: "Pending", 2: "Failed"}


@dataclass
class State:
    """Symbolic DB: k transaction rows, m daily_balance rows, A accounts, D days."""
    k: int
    m: int
    A: int
    D: int
    constraints: list = field(default_factory=list)

    def __post_init__(self) -> None:
        k, m, A, D = self.k, self.m, self.A, self.D
        c = self.constraints
        # --- transactions table ---------------------------------------
        self.t_pres = [Bool(f"t{i}_present") for i in range(k)]
        self.t_id = [Int(f"t{i}_id") for i in range(k)]        # logical id (PK sans entry)
        self.t_entry = [Int(f"t{i}_entry") for i in range(k)]  # supersession key
        self.t_acct = [Int(f"t{i}_acct") for i in range(k)]
        self.t_day = [Int(f"t{i}_day") for i in range(k)]      # posting day
        self.t_amt = [Int(f"t{i}_amt") for i in range(k)]      # signed cents
        self.t_status = [Int(f"t{i}_status") for i in range(k)]
        for i in range(k):
            c += [self.t_id[i] >= 0, self.t_id[i] < k,
                  self.t_acct[i] >= 0, self.t_acct[i] < A,
                  self.t_day[i] >= 0, self.t_day[i] < D,
                  self.t_status[i] >= 0, self.t_status[i] <= 2]
        if k > 1:
            c.append(Distinct(*self.t_entry))  # BIGSERIAL: globally unique
        # current_transactions: max entry per id
        self.t_curr = [
            And(self.t_pres[i],
                *[Implies(And(self.t_pres[j], self.t_id[j] == self.t_id[i]),
                          self.t_entry[j] < self.t_entry[i])
                  for j in range(k) if j != i])
            for i in range(k)
        ]
        # --- daily_balances table -------------------------------------
        self.b_pres = [Bool(f"b{r}_present") for r in range(m)]
        self.b_acct = [Int(f"b{r}_acct") for r in range(m)]
        self.b_day = [Int(f"b{r}_day") for r in range(m)]
        self.b_money = [Int(f"b{r}_money") for r in range(m)]
        self.b_entry = [Int(f"b{r}_entry") for r in range(m)]
        for r in range(m):
            c += [self.b_acct[r] >= 0, self.b_acct[r] < A,
                  self.b_day[r] >= 0, self.b_day[r] < D]
        if m > 1:
            c.append(Distinct(*self.b_entry))
        # current_daily_balances: max entry per (account, day)
        self.b_curr = [
            And(self.b_pres[r],
                *[Implies(And(self.b_pres[s], self.b_acct[s] == self.b_acct[r],
                              self.b_day[s] == self.b_day[r]),
                          self.b_entry[s] < self.b_entry[r])
                  for s in range(m) if s != r])
            for r in range(m)
        ]
        # --- per-account denormalized attributes ----------------------
        self.internal = [Bool(f"a{a}_internal") for a in range(A)]
        self.leaf = [Bool(f"a{a}_leaf") for a in range(A)]  # account_parent_role IS NOT NULL

        # --- derived per-(account, day) terms -------------------------
        # emitted(a,d): a current stored-balance row exists at that key
        self.emitted = [[Or(*[And(self.b_curr[r], self.b_acct[r] == a, self.b_day[r] == d)
                              for r in range(m)])
                         for d in range(D)] for a in range(A)]
        # stored(a,d): its money (0 when absent; always guarded by emitted)
        self.stored = [[Sum(*[If(And(self.b_curr[r], self.b_acct[r] == a, self.b_day[r] == d),
                                self.b_money[r], IntVal(0)) for r in range(m)])
                        for d in range(D)] for a in range(A)]
        # LOCF (effective_balances): last emitted money at-or-before d
        self.eff_present = [[None] * D for _ in range(A)]
        self.eff_money = [[None] * D for _ in range(A)]
        for a in range(A):
            for d in range(D):
                if d == 0:
                    self.eff_present[a][d] = self.emitted[a][d]
                    self.eff_money[a][d] = self.stored[a][d]
                else:
                    self.eff_present[a][d] = Or(self.emitted[a][d], self.eff_present[a][d - 1])
                    self.eff_money[a][d] = If(self.emitted[a][d], self.stored[a][d],
                                              self.eff_money[a][d - 1])

    # computed_subledger_balance's correlated SUM, with mutation knobs
    def computed(self, a: int, d: int, *, day_op: str = "<=", status_filter: bool = True,
                 sign: int = 1, supersession: bool = True):
        terms = []
        for i in range(self.k):
            row_in = self.t_curr[i] if supersession else self.t_pres[i]
            conds = [row_in, self.t_acct[i] == a]
            if status_filter:
                conds.append(self.t_status[i] == POSTED)
            conds.append(self.t_day[i] <= d if day_op == "<=" else self.t_day[i] < d)
            terms.append(If(And(*conds), sign * self.t_amt[i], IntVal(0)))
        return Sum(*terms)

    # ---- the three formulas under study ------------------------------
    def spec_flag(self, a: int, d: int):
        """Pure residual law, written independently of the SQL:
        residual(a, d) = stored(a, d) - SUM(posted current tx with day <= d);
        a violation is an EMITTED internal leaf account-day with residual != 0."""
        residual = self.stored[a][d] - self.computed(a, d)
        return And(self.emitted[a][d], self.internal[a], self.leaf[a], residual != 0)

    def detector_flag(self, a: int, d: int, *, cmp: str = "<>", **mut):
        """Faithful encoding of the drift matview:
        effective_balances row (eff_present) INNER JOIN csb row (which requires
        an emitted current_daily_balances row at the same (account, day) key,
        internal + leaf) + drift's own WHERE re-filter + eff_money <> computed."""
        csb_exists = And(self.emitted[a][d], self.internal[a], self.leaf[a])
        comp = self.computed(a, d, **mut)
        if cmp == "<>":
            pred = self.eff_money[a][d] != comp
        elif cmp == "<":
            pred = self.eff_money[a][d] < comp
        else:
            raise ValueError(cmp)
        return And(self.eff_present[a][d], self.internal[a], self.leaf[a],
                   csb_exists, pred)

    def spec_locf_flag(self, a: int, d: int):
        """The CL.5 comment's stated INTENT: the institution's reportable
        (carried-forward) position must equal computed EVERY day, not just
        emitted days. Domain capped at the fleet-wide max emit day to mirror
        the real spine."""
        in_spine = Or(*[self.emitted[a2][d2]
                        for a2 in range(self.A) for d2 in range(d, self.D)])
        return And(self.eff_present[a][d], self.internal[a], self.leaf[a], in_spine,
                   self.eff_money[a][d] != self.computed(a, d))

    def dump_model(self, mdl) -> str:
        out = []
        def v(x):
            r = mdl.eval(x, model_completion=True)
            return r
        out.append("  transactions (current* = survives max-entry-per-id):")
        for i in range(self.k):
            if not bool(v(self.t_pres[i])):
                continue
            cur = "*" if bool(v(self.t_curr[i])) else " "
            out.append(f"   {cur} id={v(self.t_id[i])} entry={v(self.t_entry[i])}"
                       f" acct={v(self.t_acct[i])} day={v(self.t_day[i])}"
                       f" amt={v(self.t_amt[i])}"
                       f" status={STATUS_NAMES[v(self.t_status[i]).as_long()]}")
        out.append("  daily_balances (current* = survives max-entry-per-(acct,day)):")
        for r in range(self.m):
            if not bool(v(self.b_pres[r])):
                continue
            cur = "*" if bool(v(self.b_curr[r])) else " "
            out.append(f"   {cur} acct={v(self.b_acct[r])} day={v(self.b_day[r])}"
                       f" money={v(self.b_money[r])} entry={v(self.b_entry[r])}")
        out.append("  accounts: " + ", ".join(
            f"a{a}(internal={bool(v(self.internal[a]))},leaf={bool(v(self.leaf[a]))})"
            for a in range(self.A)))
        return "\n".join(out)


def check(name: str, st: State, disagreement, expect: str,
          det_kwargs: dict | None = None, spec: str = "emitted") -> tuple[str, float]:
    s = Solver()
    s.add(*st.constraints)
    s.add(disagreement)
    t0 = time.perf_counter()
    res = s.check()
    dt = time.perf_counter() - t0
    verdict = "unsat" if res == unsat else ("sat" if res == sat else str(res))
    ok = "OK" if verdict == expect else "!!! UNEXPECTED"
    print(f"[{name}] {verdict} in {dt:.3f}s (expected {expect}) {ok}")
    if res == sat:
        mdl = s.model()
        print(st.dump_model(mdl))
        # locate the disagreeing cell (spec vs the detector variant under test)
        spec_fn = st.spec_flag if spec == "emitted" else st.spec_locf_flag
        for a in range(st.A):
            for d in range(st.D):
                sv = mdl.eval(spec_fn(a, d), model_completion=True)
                dv = mdl.eval(st.detector_flag(a, d, **(det_kwargs or {})),
                              model_completion=True)
                if bool(sv) != bool(dv):
                    print(f"  -> cell (acct={a}, day={d}): spec_flag={sv} "
                          f"detector_flag={dv}")
    return verdict, dt


def disagreement_formula(st: State, det_kwargs: dict, spec="emitted"):
    spec_fn = st.spec_flag if spec == "emitted" else st.spec_locf_flag
    return Or(*[Xor(spec_fn(a, d), st.detector_flag(a, d, **det_kwargs))
                for a in range(st.A) for d in range(st.D)])


def main() -> None:
    A, D = 2, 4

    print("=" * 72)
    print("EXPERIMENT 1 — bounded equivalence: spec residual vs faithful detector")
    print("=" * 72)
    st = State(k=6, m=4, A=A, D=D)
    check("equiv k=6,m=4", st, disagreement_formula(st, {}), "unsat")

    print()
    print("EXPERIMENT 1b — theorem: drift never emits a 'carried'-source row")
    st = State(k=4, m=4, A=A, D=D)
    carried_drift = Or(*[And(st.detector_flag(a, d), Not(st.emitted[a][d]))
                         for a in range(A) for d in range(D)])
    check("no-carried-drift-rows", st, carried_drift, "unsat")

    print()
    print("EXPERIMENT 1c — intent gap: CL.5 'real position every day' spec vs detector")
    st = State(k=4, m=4, A=A, D=D)
    check("locf-intent-gap", st, disagreement_formula(st, {}, spec="locf"), "sat",
          spec="locf")

    print()
    print("=" * 72)
    print("EXPERIMENT 3 — mutation sensitivity (each must yield a witness DB)")
    print("=" * 72)
    mutations = [
        ("M1 '<>' -> '<'", {"cmp": "<"}),
        ("M2 posting <= day -> <", {"day_op": "<"}),
        ("M3 dropped status='Posted' filter", {"status_filter": False}),
        ("M4 sign-flipped SUM", {"sign": -1}),
        ("M5 supersession dropped (all rows, not max-entry)", {"supersession": False}),
    ]
    for name, mut in mutations:
        st = State(k=4, m=3, A=A, D=D)
        check(name, st, disagreement_formula(st, mut), "sat", det_kwargs=mut)
        print()

    print("=" * 72)
    print("EXPERIMENT 5 — timing: equivalence check, growing k (and one big cell)")
    print("=" * 72)
    rows = []
    for k in (2, 4, 6, 8, 12, 16, 24, 32):
        m = max(2, k // 2)
        st = State(k=k, m=m, A=A, D=D)
        _, dt = check(f"equiv k={k},m={m},A={A},D={D}", st,
                      disagreement_formula(st, {}), "unsat")
        rows.append((f"k={k},m={m},A=2,D=4", dt))
    # scale the day/account grid too — the sums widen AND the cell count grows
    for (k, m, a_, d_) in ((16, 8, 4, 8), (24, 12, 4, 10)):
        st = State(k=k, m=m, A=a_, D=d_)
        _, dt = check(f"equiv k={k},m={m},A={a_},D={d_}", st,
                      disagreement_formula(st, {}), "unsat")
        rows.append((f"k={k},m={m},A={a_},D={d_}", dt))
    print("\n config                  solve_s")
    for cfg, dt in rows:
        print(f" {cfg:<23} {dt:.3f}")

    print()
    print("Determinism: 3 identical runs at k=6")
    for run in range(3):
        st = State(k=6, m=4, A=A, D=D)
        check(f"determinism run {run + 1}", st, disagreement_formula(st, {}), "unsat")


if __name__ == "__main__":
    main()
