"""Z3 spike — bounded equivalence for <prefix>_xor_group_violation (the
cardinality / COUNT+HAVING-shaped detector).

Real SQL modeled (src/recon_gen/common/l2/schema.py::_render_xor_group_violation_body,
line ~1167):

    WITH xor_groups(template_name, xor_group_index, member_rail_name) AS (VALUES ...),
    template_transfers AS (
      SELECT DISTINCT tx.transfer_id, tx.template_name, MIN(day) OVER (...) AS business_day
      FROM {p}_current_transactions tx
      WHERE tx.status <> 'Failed'
        AND tx.template_name IN (SELECT DISTINCT template_name FROM xor_groups)
    ),
    expected AS (
      SELECT tt.transfer_id, tt.template_name, g.xor_group_index, MIN(tt.business_day)
      FROM template_transfers tt
      JOIN xor_groups g ON g.template_name = tt.template_name
      GROUP BY tt.transfer_id, tt.template_name, g.xor_group_index
    )
    SELECT e.transfer_id, e.template_name, e.xor_group_index,
           COUNT(tx.transfer_id) AS firing_count, ...
    FROM expected e
    JOIN xor_groups g ON g.template_name = e.template_name
                     AND g.xor_group_index = e.xor_group_index
    LEFT JOIN {p}_current_transactions tx
      ON tx.transfer_id = e.transfer_id
      AND tx.template_name = e.template_name
      AND tx.rail_name = g.member_rail_name
      AND tx.status <> 'Failed'
    GROUP BY e.transfer_id, e.template_name, e.xor_group_index, e.business_day
    HAVING COUNT(tx.transfer_id) <> 1

The xor_groups VALUES rowset is L2-declared and compile-time constant — mirrored
here as a Python dict, exactly as the emitter inlines it.
"""
from __future__ import annotations

import time

from z3 import (
    And, Bool, Distinct, If, Implies, Int, IntVal, Or, Solver, Sum, Xor,
    sat, set_param, unsat,
)

set_param("smt.random_seed", 0)

POSTED, PENDING, FAILED = 0, 1, 2
STATUS_NAMES = {0: "Posted", 1: "Pending", 2: "Failed"}

# L2-declared XOR groups (compile-time constants, like the VALUES CTE).
# template 0: one group {rail0, rail1}; template 1: groups {rail2},{rail0,rail3}.
# template 2 exists but declares NO groups (must never be flagged).
XOR_GROUPS: dict[tuple[int, int], list[int]] = {
    (0, 0): [0, 1],
    (1, 0): [2],
    (1, 1): [0, 3],
}
TEMPLATES_WITH_GROUPS = sorted({t for (t, _g) in XOR_GROUPS})
N_TEMPLATES, N_RAILS, N_TRANSFERS = 3, 4, 2


class XorState:
    def __init__(self, k: int) -> None:
        self.k = k
        c: list = []
        self.pres = [Bool(f"x{i}_present") for i in range(k)]
        self.rid = [Int(f"x{i}_id") for i in range(k)]      # logical row id
        self.entry = [Int(f"x{i}_entry") for i in range(k)]  # supersession key
        self.transfer = [Int(f"x{i}_transfer") for i in range(k)]
        self.template = [Int(f"x{i}_template") for i in range(k)]
        self.rail = [Int(f"x{i}_rail") for i in range(k)]
        self.status = [Int(f"x{i}_status") for i in range(k)]
        for i in range(k):
            c += [self.rid[i] >= 0, self.rid[i] < k,
                  self.transfer[i] >= 0, self.transfer[i] < N_TRANSFERS,
                  self.template[i] >= 0, self.template[i] < N_TEMPLATES,
                  self.rail[i] >= 0, self.rail[i] < N_RAILS,
                  self.status[i] >= 0, self.status[i] <= 2]
        if k > 1:
            c.append(Distinct(*self.entry))
        self.curr = [
            And(self.pres[i],
                *[Implies(And(self.pres[j], self.rid[j] == self.rid[i]),
                          self.entry[j] < self.entry[i])
                  for j in range(k) if j != i])
            for i in range(k)
        ]
        self.constraints = c

    # template_transfers CTE: transfer x instantiates template t (any
    # non-failed current leg, any rail; t must declare >=1 group)
    def instantiates(self, x: int, t: int):
        assert t in TEMPLATES_WITH_GROUPS
        return Or(*[And(self.curr[i], self.transfer[i] == x, self.template[i] == t,
                        self.status[i] != FAILED) for i in range(self.k)])

    # firing_count: COUNT of non-failed current leg ROWS on member rails
    def firing_count(self, x: int, t: int, g: int, *, status_mode: str = "not_failed",
                     distinct_rails: bool = False):
        members = XOR_GROUPS[(t, g)]
        def status_ok(i):
            return (self.status[i] != FAILED if status_mode == "not_failed"
                    else self.status[i] == POSTED)
        if not distinct_rails:
            return Sum(*[If(And(self.curr[i], self.transfer[i] == x,
                                self.template[i] == t,
                                Or(*[self.rail[i] == r for r in members]),
                                status_ok(i)), IntVal(1), IntVal(0))
                         for i in range(self.k)])
        # mutant X3: COUNT(DISTINCT rail) instead of COUNT(rows)
        return Sum(*[If(Or(*[And(self.curr[i], self.transfer[i] == x,
                                 self.template[i] == t, self.rail[i] == r,
                                 status_ok(i)) for i in range(self.k)]),
                        IntVal(1), IntVal(0))
                     for r in members])

    def detector_flag(self, x: int, t: int, g: int, *, having: str = "<>",
                      **mut):
        fc = self.firing_count(x, t, g, **mut)
        pred = fc != 1 if having == "<>" else fc > 1
        return And(self.instantiates(x, t), pred)

    def spec_flag(self, x: int, t: int, g: int):
        """Independent residual: for every live instance of a grouped template,
        cardinality residual = |non-failed current legs on group rails| - 1;
        violation iff residual != 0."""
        residual = self.firing_count(x, t, g) - 1
        return And(self.instantiates(x, t), residual != 0)

    def dump_model(self, mdl) -> str:
        out = ["  transactions (current* = survives supersession):"]
        for i in range(self.k):
            if not bool(mdl.eval(self.pres[i], model_completion=True)):
                continue
            cur = "*" if bool(mdl.eval(self.curr[i], model_completion=True)) else " "
            g = lambda v: mdl.eval(v, model_completion=True)
            out.append(f"   {cur} id={g(self.rid[i])} entry={g(self.entry[i])}"
                       f" transfer={g(self.transfer[i])} template={g(self.template[i])}"
                       f" rail={g(self.rail[i])}"
                       f" status={STATUS_NAMES[g(self.status[i]).as_long()]}")
        return "\n".join(out)


def disagreement(st: XorState, det_kwargs: dict):
    return Or(*[Xor(st.spec_flag(x, t, g), st.detector_flag(x, t, g, **det_kwargs))
                for x in range(N_TRANSFERS) for (t, g) in XOR_GROUPS])


def check(name: str, st: XorState, formula, expect: str,
          det_kwargs: dict | None = None) -> tuple[str, float]:
    s = Solver()
    s.add(*st.constraints)
    s.add(formula)
    t0 = time.perf_counter()
    res = s.check()
    dt = time.perf_counter() - t0
    verdict = "unsat" if res == unsat else ("sat" if res == sat else str(res))
    ok = "OK" if verdict == expect else "!!! UNEXPECTED"
    print(f"[{name}] {verdict} in {dt:.3f}s (expected {expect}) {ok}")
    if res == sat:
        mdl = s.model()
        print(st.dump_model(mdl))
        for x in range(N_TRANSFERS):
            for (t, g) in XOR_GROUPS:
                spec = mdl.eval(st.spec_flag(x, t, g), model_completion=True)
                det = mdl.eval(st.detector_flag(x, t, g, **(det_kwargs or {})),
                               model_completion=True)
                if bool(spec) != bool(det):
                    fc = mdl.eval(st.firing_count(x, t, g), model_completion=True)
                    print(f"  -> (transfer={x}, template={t}, group={g}"
                          f" members={XOR_GROUPS[(t, g)]}): spec_flag={spec}"
                          f" detector_flag={det} true_firing_count={fc}")
    return verdict, dt


def main() -> None:
    print("=" * 72)
    print("EXPERIMENT 4 — xor_group_violation: equivalence + mutations")
    print("=" * 72)
    st = XorState(k=6)
    check("xor equiv k=6", st, disagreement(st, {}), "unsat")
    print()
    mutations = [
        ("X1 HAVING <> 1 -> > 1 (misses 0-firings)", {"having": ">"}),
        ("X2 status <> 'Failed' -> = 'Posted' in count", {"status_mode": "posted"}),
        ("X3 COUNT(rows) -> COUNT(DISTINCT rail)", {"distinct_rails": True}),
    ]
    for name, mut in mutations:
        st = XorState(k=4)
        check(name, st, disagreement(st, mut), "sat", det_kwargs=mut)
        print()

    print("timing sweep k = 2,4,6,8,12,16,24,32 (equivalence, expect unsat)")
    rows = []
    for k in (2, 4, 6, 8, 12, 16, 24, 32):
        st = XorState(k=k)
        _, dt = check(f"xor equiv k={k}", st, disagreement(st, {}), "unsat")
        rows.append((k, dt))
    print("\n k   solve_s")
    for k, dt in rows:
        print(f" {k:<3} {dt:.3f}")


if __name__ == "__main__":
    main()
