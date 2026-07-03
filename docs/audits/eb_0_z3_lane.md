# EB.0 — the Z3 spec-theorem lane: decisions + open flags

**Verdict up front: the hard part (EB.1, the symbolic-execution adapter) is DONE and green — the real money residuals run over z3 with zero transliteration, 18/18 dual-run canary vectors agree with concrete. This doc records what EB.1 resolved and surfaces the EB.2 (theorem) design decisions that are chotchki's to confirm, not mine to guess.**

Promoted from the DS backlog ("DST") after DS-core landed green in v16.1.0. Design source: [`ds_z3_formal_tie_spike.md`](ds_z3_formal_tie_spike.md) (the investigation verdict + all operator decisions). This doc is the EB-specific delta, not a restatement.

## 1. Resolved building EB.1

**The symbolic-Cents TWO-seam finding (the one thing the DS.0 doc undersold).** The doc said swapping `when()` for a z3.If-emitter is the ONE seam. It isn't. `Cents` is `@dataclass(order=True)`, so `stored < ZERO` is a TUPLE comparison that calls `bool()` on the z3 term and raises; and residuals construct `Cents(...)` INTERNALLY (`over = flow - cap`) then compare the result (`when(over > ZERO, ...)`), so feeding symbolic INPUTS is insufficient — the `Cents` class the body SEES must be z3-aware too. Resolved: the adapter monkeypatches `residuals.Cents` / `when` / `ZERO` for the duration of one symbolic run and restores them (verified no leak). No law body is touched — the "no fifth copy" thesis holds. See `tests/prover/symbolic.py`.

**Scope confirmed: the MONEY family only.** Threshold residuals use floor division (out of the whitelist by design — Python floor vs SMT-LIB euclidean would diverge silently), cardinality residuals count over sets, derivation residuals walk a BFS. None is symbolically executable this way and all have near-vacuous ∀-ℤ theorems (DS.0), so they stay concrete.

## 2. Operator-locked (from the spike doc — restated for EB, not re-decided)

- **On-chain, tier MEASURED not assigned:** the theorem step's avg solve time decides its tier (<30s → unit, else the agreement layer). The canary is 0.09s; the spike's hand-encoded solves were ms-scale — likely unit, but MEASURE the real symbolically-executed obligations at EB.2 (a different, larger formula shape).
- **Pinned-expected-verdict:** each obligation pins its expected verdict (`unsat` for a law proof, `sat` for a discriminator). Any transition off the pin fails the runner as an ORDINARY failure. `sat` on a law obligation prints the witness — a real counterexample, NEVER xfail-able. `unknown`/timeout raises a distinct `SolverInconclusive` and fails; post-triage a hand-set `raises=SolverInconclusive` xfail (reason naming the pin bump + triage) is one valid outcome, never auto-set. The `raises=` scoping makes it structurally unable to mask a real counterexample. The solver is a dependency of unknown reliability, not a policy exception.
- **Pinning:** z3 4.16.0 (EXACT, part of the cache key), `rlimit=500_000_000`, bounded wall-clock (120s) per the existing nondeterministic-but-consistent pattern.
- **Semantic-fingerprint cache:** the pinned object is the CANONICALIZED SMT formula (sorted assertions, alpha-normalized names, deterministic print) + z3 version + rlimit budget — the semantic-lock discipline, not source-text hashing. A comment edit or refactor that leaves the formula unchanged stays proven; a semantic change reads stale and solves on-chain. One-sided error: a buggy canonicalizer can only produce spurious STALE (harmless re-prove), never spurious fresh. Productionize `ds_0_spike_evidence/canon/canon.py`; share normalization helpers with DS.7's lock `--check`.

## 3. Open EB.2 decisions — chotchki's to confirm (my recommendation stated)

**(a) The theorem set per money residual.** The doc names: failed-leg inertness, supersession idempotence, the AU.2 composition lemma, + scale homogeneity at concrete scalars {−1, 2, 3}. Two of those need pinning:

- **The AU.2 composition lemma has no written statement** — the spike doc only NAMES it, and the `test_spine_au2_composition.py` it points at is EMPIRICAL (plant two generators, check which fire). Its ∀-ℤ THEOREM form is **interference-freedom**: `residual(A ⊎ B, cell∈A) == residual(A, cell∈A)` when A and B are account-DISJOINT — the money residuals only sum the cell's account's legs, so disjoint B can't touch the result. **Recommend: encode exactly that.** Confirm the statement.
- **Additivity / sign — NOT in the doc; my call flagged.** drift / ledger_drift are LINEAR in leg amounts, so additivity (`residual(state with a leg bumped by δ) == residual ∓ δ`) is cheap and a strong law. **Recommend: ADD additivity for the linear residuals; SKIP a generic sign theorem** (invariant-specific, low marginal value over homogeneity). Your call.

**(b) money_trail k-unroll scope.** money_trail is DERIVATION (a BFS over `transfer_parent_id`), not symbolically executable by the money-family seam-swap — it needs a bespoke bounded-depth unroll, materially harder than "the same object runs symbolic." **Recommend: EB.2 lands the MONEY-family theorems FIRST (the clean wins), and money_trail k-unroll is a distinct EB.2 sub-task that DEFERS if its effort is disproportionate to the win** (its ∀-ℤ content is thin — the interesting money_trail bugs were structural, caught by enumeration). Flag: don't let it block the money-family theorems.

**(c) EB.3 (domain repair) stays deferred** — build only after hand-triage of a surviving mutant has hurt TWICE (operator-locked). Law-expressible mutants only; non-residual-image mutants (UNION-vs-UNION-ALL, JOIN-flavor, index/refresh-order) route to engine-side search.

## 4. What EB.1 shipped

`tests/prover/`: `symbolic.py` (SymCents + sym_when + the monkeypatch harness + `bind_and_eval`), `symstate.py` (the symbolic-money `ResidualState` builder mirroring the DS.1 KAT loader), `test_eb1_dual_run_canary.py` (the 5 money residuals × every money KAT, concrete vs symbolic, 18/18 agree). `z3-solver==4.16.0` pinned exact in the dev extra. A per-file pyright pragma relaxes only the untyped-cascade rules for the three z3-boundary files (z3 ships no stubs; the rest of `tests/` stays strict).
