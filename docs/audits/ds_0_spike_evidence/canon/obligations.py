"""Standalone Z3 obligation set for the WSL2-vs-macOS determinism check
(pre-DS.0 spike per docs/audits/ds_z3_formal_tie_spike.md, "First spike"
paragraph). Push to CI on a branch; diff this script's stdout between
macOS-arm64 and WSL2-x86-64 — identical verdicts + fingerprints confirm
on-chain solving for semantic-fingerprint cache misses.

Obligations (all built from the COMMITTED spike encodings, unmodified):
  * drift equivalence (spec residual == faithful detector)        -> unsat
  * E1b carried-day theorem (drift never emits a carried-day row) -> unsat
  * xor_group equivalence                                          -> unsat
  * M1-M5 drift mutants + X1-X3 xor mutants (discriminator solves) -> sat

Each runs under a deterministic rlimit budget with a wall-clock interrupt
backstop (the design's bounded-wall-clock wrapper, rlimit inside). `unknown`
(rlimit, wall-cap, or solver giving up) prints as SolverInconclusive and
fails the run. Exit 0 iff every verdict matches its pin.

Output contract: stdout is byte-deterministic for a fixed z3 version +
platform (timings go to stderr). Lines starting with '#' carry environment
info — a cross-PLATFORM diff compares the non-'#' lines; a same-platform
3x rerun must be byte-identical including '#' lines.

Env knobs: DS_SPIKE_DIR (default: the repo's committed evidence dir),
DS_RLIMIT, DS_WALL_CAP_S.
"""
from __future__ import annotations

import hashlib
import os
import platform
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_spike_dir() -> str:
    """Env override, else walk up from this file looking for the committed
    evidence dir (works from any checkout path — macOS dev box or WSL2 CI),
    else the dev-box absolute path."""
    env = os.environ.get("DS_SPIKE_DIR")
    if env:
        return env
    d = HERE
    while d != os.path.dirname(d):
        cand = os.path.join(d, "docs", "audits", "ds_z3_spike_evidence")
        if os.path.isdir(cand):
            return cand
        d = os.path.dirname(d)
    return "/Users/chotchki/workspace/quicksight/docs/audits/ds_z3_spike_evidence"


SPIKE_DIR = _find_spike_dir()
sys.path.insert(0, HERE)
sys.path.insert(0, SPIKE_DIR)

import z3  # noqa: E402
from z3 import And, Not, Or, sat, unsat  # noqa: E402

import drift_z3  # noqa: E402  (sets smt.random_seed=0 at import)
import xor_z3  # noqa: E402
from canon import canonical_dump  # noqa: E402

z3.set_param("smt.random_seed", 0)
z3.set_param("sat.random_seed", 0)

RLIMIT = int(os.environ.get("DS_RLIMIT", 500_000_000))
WALL_CAP_S = float(os.environ.get("DS_WALL_CAP_S", 120.0))

DRIFT_MUTANTS = [
    ("M1-cmp-ne-to-lt", {"cmp": "<"}),
    ("M2-day-le-to-lt", {"day_op": "<"}),
    ("M3-drop-posted-filter", {"status_filter": False}),
    ("M4-sign-flip", {"sign": -1}),
    ("M5-drop-supersession", {"supersession": False}),
]
XOR_MUTANTS = [
    ("X1-having-ne1-to-gt1", {"having": ">"}),
    ("X2-notfailed-to-posted", {"status_mode": "posted"}),
    ("X3-count-distinct-rails", {"distinct_rails": True}),
]


def build_obligations() -> list[tuple[str, str, list]]:
    """(name, expected_verdict, assertions) triples — bounds match the
    committed spike's main() so verdicts are directly comparable."""
    obs: list[tuple[str, str, list]] = []

    st = drift_z3.State(k=6, m=4, A=2, D=4)
    obs.append(("drift-equiv[k6,m4,A2,D4]", "unsat",
                list(st.constraints) + [drift_z3.disagreement_formula(st, {})]))

    st = drift_z3.State(k=4, m=4, A=2, D=4)
    carried = Or(*[And(st.detector_flag(a, d), Not(st.emitted[a][d]))
                   for a in range(st.A) for d in range(st.D)])
    obs.append(("drift-E1b-carried-day[k4,m4]", "unsat",
                list(st.constraints) + [carried]))

    xst = xor_z3.XorState(k=6)
    obs.append(("xor-equiv[k6]", "unsat",
                list(xst.constraints) + [xor_z3.disagreement(xst, {})]))

    for name, mut in DRIFT_MUTANTS:
        st = drift_z3.State(k=4, m=3, A=2, D=4)
        obs.append((f"drift-{name}[k4,m3]", "sat",
                    list(st.constraints)
                    + [drift_z3.disagreement_formula(st, mut)]))

    for name, mut in XOR_MUTANTS:
        xst = xor_z3.XorState(k=4)
        obs.append((f"xor-{name}[k4]", "sat",
                    list(xst.constraints) + [xor_z3.disagreement(xst, mut)]))
    return obs


def solve_bounded(assertions: list) -> tuple[str, int, float]:
    """(verdict, rlimit_used, wall_seconds). rlimit is the deterministic
    budget; the wall-clock timer is the nondeterministic backstop — if it
    fires, the verdict is unknown -> SolverInconclusive anyway."""
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
    # NOTE: z3's 'rlimit count' statistic is CONTEXT-GLOBAL CUMULATIVE, not
    # per-check — raw values are order-dependent across obligations. The
    # caller diffs successive readings; the table shows per-obligation deltas.
    rused = 0
    for k in stats.keys():
        if k == "rlimit count":
            rused = int(stats.get_key_value(k))
    if res == unsat:
        verdict = "unsat"
    elif res == sat:
        verdict = "sat"
    else:
        verdict = f"SolverInconclusive({s.reason_unknown()})"
    return verdict, rused, wall


def main() -> int:
    print(f"# z3 {z3.get_version_string()} | {platform.system()}-{platform.machine()}"
          f" | python {sys.version.split()[0]}")
    print(f"# rlimit={RLIMIT} wall-cap={WALL_CAP_S:.0f}s"
          f" | fingerprint = sha256[:16] of canon.canonical_dump")
    header = (f"{'obligation':<34} | {'expected':<8} | {'got':<8} |"
              f" {'rlimit-used':>11} | canonical-fingerprint")
    print(header)
    print("-" * len(header))
    all_ok = True
    # Baseline probe: 'rlimit count' is context-global cumulative and charges
    # AST construction too; snapshot after building all obligations so row 1's
    # delta is its own check, not the process's setup cost.
    obligations = build_obligations()
    _, prev_rlimit, _ = solve_bounded([z3.BoolVal(True)])
    for name, expected, assertions in obligations:
        t0 = time.perf_counter()
        fp = hashlib.sha256(canonical_dump(assertions)).hexdigest()[:16]
        t_fp = time.perf_counter() - t0
        verdict, rcum, wall = solve_bounded(assertions)
        rused, prev_rlimit = rcum - prev_rlimit, rcum  # global counter -> delta
        ok = verdict == expected
        all_ok &= ok
        got = verdict if ok else f"{verdict} <-- MISMATCH"
        print(f"{name:<34} | {expected:<8} | {got:<8} | {rused:>11} | {fp}")
        print(f"[timing] {name}: solve={wall:.3f}s fingerprint={t_fp:.3f}s",
              file=sys.stderr)
    print(f"RESULT: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
