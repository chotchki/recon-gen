"""PART A proof-out driver for canon.canonical_dump.

Property 1 (REFACTOR-STABLE): drift_z3.State's equivalence obligation and
DriftWorldV2's (renamed vars, reordered construction, extracted helpers,
shuffled conjunct/disjunct orders) must canonicalize to byte-identical dumps.

Property 2 (SEMANTICS-SENSITIVE): the M1 mutation (detector `<>` -> `<`)
must change the dump.

Plus a randomized torture test: N trials of (shuffle assertion order +
alpha-rename every constant to a random fresh name via z3.substitute) must
all reproduce the baseline dump byte-for-byte — this exercises the
individualization tie-break far harder than the single v2 refactor does.
"""
from __future__ import annotations

import hashlib
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SPIKE_DIR = os.environ.get(
    "DS_SPIKE_DIR",
    "/Users/chotchki/workspace/quicksight/docs/audits/ds_z3_spike_evidence")
sys.path.insert(0, HERE)
sys.path.insert(0, SPIKE_DIR)

import z3  # noqa: E402
import drift_z3  # noqa: E402  (sets smt.random_seed=0 at import)
from canon import canonical_dump  # noqa: E402
from drift_encoding_v2 import DriftWorldV2  # noqa: E402

K, M, A, D = 6, 4, 2, 4


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def build_v1(mut: dict | None = None) -> list:
    st = drift_z3.State(k=K, m=M, A=A, D=D)
    return list(st.constraints) + [drift_z3.disagreement_formula(st, mut or {})]


def main() -> int:
    ok = True

    t0 = time.perf_counter()
    a1 = build_v1()
    d1 = canonical_dump(a1)
    t1 = time.perf_counter() - t0
    print(f"baseline v1 dump: {len(d1)} bytes, sha256[:16]={sha(d1)}, "
          f"canonicalized in {t1:.2f}s")

    # ---- Property 1: refactor-stable --------------------------------------
    t0 = time.perf_counter()
    w = DriftWorldV2(n_legs=K, n_snaps=M, n_accounts=A, n_days=D)
    d2 = canonical_dump(w.assertions())
    t2 = time.perf_counter() - t0
    p1 = d1 == d2
    ok &= p1
    print(f"P1 REFACTOR-STABLE  : {'PASS' if p1 else 'FAIL'} "
          f"(v2 sha={sha(d2)}, {t2:.2f}s)")
    if not p1:
        _diff(d1, d2)

    # ---- Property 2: semantics-sensitive (M1: '<>' -> '<') ----------------
    dm1 = canonical_dump(build_v1({"cmp": "<"}))
    p2 = dm1 != d1
    ok &= p2
    print(f"P2 SEMANTICS-SENSITIVE (M1): {'PASS' if p2 else 'FAIL'} "
          f"(M1 sha={sha(dm1)} vs baseline {sha(d1)})")

    # ---- Torture: random permutation + random alpha-rename ----------------
    n_trials, n_pass = 5, 0
    for trial in range(n_trials):
        rng = random.Random(1000 + trial)
        perm = build_v1()
        rng.shuffle(perm)
        # rename every constant to a random fresh name
        consts: dict[int, z3.ExprRef] = {}
        for e in perm:
            _collect(e, consts)
        pairs = [(c, z3.Const(f"r{rng.getrandbits(64):016x}", c.sort()))
                 for c in consts.values()]
        renamed = [z3.substitute(e, *pairs) for e in perm]
        dt = canonical_dump(renamed)
        good = dt == d1
        n_pass += good
        if not good:
            print(f"  torture trial {trial}: FAIL sha={sha(dt)}")
    p3 = n_pass == n_trials
    ok &= p3
    print(f"P3 TORTURE (shuffle+rename x{n_trials}): "
          f"{'PASS' if p3 else 'FAIL'} ({n_pass}/{n_trials})")

    with open(os.path.join(HERE, "baseline_v1.canonical.smt2"), "wb") as f:
        f.write(d1)
    loc = sum(1 for line in open(os.path.join(HERE, "canon.py"))
              if line.strip() and not line.strip().startswith("#"))
    total = sum(1 for _ in open(os.path.join(HERE, "canon.py")))
    print(f"canon.py: {total} lines total ({loc} non-blank non-comment)")
    print("OVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _collect(e: z3.ExprRef, out: dict[int, z3.ExprRef]) -> None:
    stack = [e]
    seen: set[int] = set()
    while stack:
        n = stack.pop()
        if n.get_id() in seen:
            continue
        seen.add(n.get_id())
        if z3.is_app(n) and n.num_args() == 0 \
                and n.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            out[n.get_id()] = n
        elif z3.is_app(n):
            stack.extend(n.children())


def _diff(d1: bytes, d2: bytes) -> None:
    l1, l2 = d1.decode().splitlines(), d2.decode().splitlines()
    print(f"  line counts: v1={len(l1)} v2={len(l2)}")
    for i, (x, y) in enumerate(zip(l1, l2)):
        if x != y:
            print(f"  first diff at line {i}:\n   v1: {x[:200]}\n   v2: {y[:200]}")
            break


if __name__ == "__main__":
    sys.exit(main())
