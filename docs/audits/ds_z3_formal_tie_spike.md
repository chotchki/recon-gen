# Can we formally tie the invariant theorems to the SQL? (Z3 investigation, pre-DS.0)

2026-07-02. Input to DS.0 — nothing here is locked. Produced by a 13-agent investigation (codebase map, 3 research tracks, two LIVE spikes on this machine, 3 competing designs, a judge and 3 adversarial verification passes that checked every claim against source). Spike code + raw outputs: [`ds_z3_spike_evidence/`](ds_z3_spike_evidence/).

Scope: what is provable about the detector matviews, about what object, on what trusted base. NOT in scope: proving the engines themselves (no production RDBMS is formally verified — the research high-water mark is a Coq-verified toy kernel from POPL 2010; Oracle is a closed box forever).

## Verdict

Yes — and the winning shape is not the one I went in expecting. Hand-encoding each detector's SQL into Z3 and proving `spec ≡ encoding` WORKS (the spike did it for drift, the hardest money invariant, in ~150 lines solving in milliseconds), but it loses the architecture fight: the encoding is a fifth copy of the law (sim, matview, generator, audit-PDF, now Z3-model) and its agreement with the SQL is exactly as unverified as the four copies Phase DS exists to unify. The design that won is **exhaustive enumeration on the real engine, with Z3 demoted to three supporting jobs**. Three claims, each with its exact quantifier — "prove" without a quantifier is a doc bug:

1. **∀-ℤ theorems about the residual laws themselves.** Write each residual `Cents`-native and branch-free, symbolically execute the ACTUAL production function with z3py (zero transliteration — the same Python object runs concrete in tests and symbolic in the prover) and prove its algebraic laws for all integer values at bounded row counts: failed-leg inertness, supersession idempotence, the AU.2 composition lemma. Real theorems, tiny trusted base.
2. **Finite-universal engine equivalence.** For every database state in a boundary-derived finite domain, the REAL engine running the REAL emitted SQL (unmodified `emit_schema` + `refresh_matviews_sql`, real refresh ordering, real UNIQUE-index contracts) produces exactly the violation set `{cells : residual(s) ≠ 0}`. Proof by total enumeration — no sampling inside the quantifier, no model of the SQL anywhere. The spike enumerated the ENTIRE 2-day money-family domain (38,416 cells) through the unmodified emitter in 1.5s on DuckDB.
3. **Off-chain bounded SMT audits** for design-review questions of ∀ shape ("can drift EVER emit a carried-day row?" — answered below, in under an hour). Advisory instrument, never a CI gate.

What no design can deliver: unbounded values AND unbounded rows AND the engine, simultaneously. Unbounded-row aggregation equivalence is undecidable (CAV 2021), window functions and `WITH RECURSIVE` are outside every 2026 tool, and any pitch that says otherwise is calling a model of the detector "the detector."

## Why enumeration beat the SMT encoding

The single most instructive data point of the whole investigation: the enumeration spike's supersession-drop mutant was caught not by the comparator but by the **BV.6 UNIQUE index blowing up at refresh time**. That detection channel CANNOT exist in an SMT model — every model axiomatizes uniqueness instead of enforcing it. The engine, the emitted text, the refresh ordering and the index contracts belong INSIDE the verified object, and only execution puts them there. Enumeration also has the smaller trusted base: ~400 lines of harness with no SQL semantics in them, versus ~1kloc of re-implemented engine semantics (3-valued NULLs, COUNT null-skip, UNION dedup) whose failure mode is a silent false pass.

Where SMT genuinely dominates — the value dimension (ℤ vs a sampled grid) — it keeps that job, on the residual side only (claim 1), where symbolic execution of the production function makes the transliteration gap literally zero.

Both spikes' mutation batteries support the small-scope bet: every seeded regression (flipped comparison, off-by-one day boundary, dropped status filter, sign flip, supersession break) produced a concrete witness at ≤4 rows, in <25ms on the Z3 side and through the real matviews on the enumeration side. That's an argument, not a theorem, so domain adequacy is MEASURED (mutation-score gate), never assumed.

## Live findings — the investigation paid for itself before DS.0 even starts

Formalizing forces scoping questions into the open. Eight surfaced; each needs an operator decision or a fix at DS.0:

1. **Drift is provably blind to carried days.** Bounded proof (E1b, `unsat` in 0.000s): drift's INNER JOIN to `computed_subledger_balance` — built from `current_daily_balances`, i.e. emit days only — restricts its row set to emitted days, where `effective_money` equals the emitted money by COALESCE. The CL.5 carry-forward layer contributes NOTHING to drift's rows, despite the CL.5 comment saying the invariants "see the institution's REAL position every day." Witness (E1c): emit −2 on day 0, a posted −1 leg on day 1 with no balance emit, next emit day 3 at −3 — days 1-2 carry a stored −2 against a true −3 and drift can never flag it. Maybe overdraft/ledger_drift owns carry-day divergence, maybe nothing does — DECIDE, then write it down.
  - Comment: I expect a balance entry to be the source of balance UNTIL a new balance superseeds it for the purpose of drift. We MUST make a sub task to fix this and I would really like to prove its broken with this infrastructure so we can prove its fixed. Starting out with a known failure is a good thing!
2. **DN.5's four copies already disagree, untested.** The matview's `net_flow` excludes `Failed` legs (`schema.py:3617`), the dataset SQL (`apps/l1_dashboard/datasets.py:1423-1439`) and the audit walk (`cli/audit/__init__.py:1366-1379`) filter nothing, `computed_subledger_balance` requires `status = 'Posted'` (`schema.py:694`). The DN.5 fixture plants only Posted rows, so one Pending or Failed leg breaks the 4-way equality today and no test would fire. Decide which side is the law; the failed-leg metamorphic test then pins it day one
  - Comment: We should be planting other statuses and then fixing the tests to filter on Posted.
3. **`money_trail` refresh can hang production.** The recursive CTE (`schema.py:4189+`) has no depth cap and no cycle guard. A single-parent cycle in customer-fed `transfer_parent_id` terminates but SILENTLY omits the cycle members from the trail (arguably worse for an audit tool); a cycle plus a multi-parent row — exactly a `chain_parent_disagreement` corruption — makes the recursion diverge on PG/DuckDB and error ORA-32044 on Oracle. The detector of chain corruption hangs ON chain corruption, and it's reachable via the corrupt data this tool exists to catch. Typed L2 validation CANNOT fix this (cycles live in ETL data, not declarations); the guard belongs in the emitted SQL (portable depth cap) or a pre-refresh cycle check that fails loudly.
  - Comment: We should plan a DS task to test for and fix this.
4. **`status` is an open column.** NOT NULL but no CHECK (`schema.py:1971`) — its siblings `account_scope`/`amount_direction` both get CHECKs two lines up. Detectors fork on it asymmetrically (`= 'Posted'` in the money sums, `<> 'Failed'` in the cardinality counts), so a `'Settled'` or `'posted'` row is counted by one family and invisible to the other. Feed-contract decision: add the CHECK, or the extra value becomes an explicit domain axis.
  - Comment: status is purposefully open, '!= Posted' should count as failed from the check perspective. We should have a test case that sets a deterministically random value so the tests can't drift.
5. **Oracle `'' ≡ NULL` forks the detector path.** `bundle_id = ''` is non-NULL on PG/DuckDB but NULL on Oracle — `stuck_unbundled`'s `WHERE bundle_id IS NULL` flips per dialect; same fork for the `template_name`/`transfer_parent_id` scope predicates. Invisible locally (DuckDB default), which is the POLICY-1 shape of bug. Normalize at the feed boundary and add one `''` witness state per nullable-string predicate to the dialect lane.
  - Comment: agree, planned task
6. **The semantic locks are pinned but nothing checks them.** `recon-gen data semantic-lock --check` exists (`cli/data.py:414-471`); the byte-compare test died with CB.8 and NOTHING on the chain invokes the verb today. Re-wire it.
  - Comment: agree must be fixed
7. **`balance_cadence_gap` proves the completeness gate needs a different anchor.** Full matview + `l1_exceptions` branch, absent from `ALL_INVARIANTS` (`spine/registry.py:271`). A gate keyed to registry membership can't see emitter-only detectors — anchor DS.5 to the emitted-artifact walk (every invariant-shaped matview maps to a registered invariant with the full kit, refresh-names ⊇ emitted matviews, at import).
  - Comment: I really hate registries they fail silently, annotation with AST check that you must declare what you link too?
8. **The sim speaks float dollars, the law is ℤ cents.** `AccountSimulation.balance: float` in dollars (`spine/account_simulation.py:119`), converted once at the insert boundary; violation identities round-trip through 2-decimal floats. The exact type already exists (`common/money.py::Cents`) — DS.1 residuals must be Cents-native end to end, and today NO Python path is.
  - Comment: I'm fine updating the laws to cents

## What the adversarial passes corrected

The recommended architecture survived all three passes structurally; the wording did not. Corrections absorbed into the task list below (full findings in the workflow record):

- **Boundary profiles must derive from the L2-RESOLVED comparison values, not SQL-text literals.** The decisive thresholds of 4 invariants (limit caps, both stuck age caps, fan_in expected counts) never appear in the emitted SQL — they're config_kv DATA joined at query time. A text lint passes vacuously on exactly those detectors. Profile = SQL literals ∪ config-delivered caps (×100 cents shift included) ∪ the `as_of` frame ∪ residual-side constants ∪ an equality-witness state per column↔column comparison site (noon-only synthesized postings never exercise `posting <= business_day_end` at the boundary).
- **Cell packing is NOT independent for the LOCF family.** The `effective_balances` day spine is fleet-wide MIN..MAX over ALL internal accounts (`schema.py:2764+`), so co-packed cells change each other's carried-day sets for drift/ledger_drift/overdraft. Pack window-aligned (or run that family unpacked) and restate the quantifier over the states actually executed. Spine-free families pack fine.
- **Solver placement — DECIDED 2026-07-02 (operator): on-chain; the TIER is measured, not assigned.** Runtime was never the issue (obligations are milliseconds against a 35-min CI) and multiple independent test approaches beat a monoculture — the theorems RUN on the chain. The spike measures average runtime for the enumeration and z3 steps separately: either averaging <30s moves down to the UNIT tier (pre-push then covers it), else it lands in the `agreement` layer (whose cross-check identity fits — "independent encodings agree" extended to the law level). Either way the step is directly invokable for DS-work iteration. Verdict handling: each obligation pins its EXPECTED verdict (`unsat` for equivalence/law proofs, `sat` for discriminator solves) and any transition off the pin fails the runner as an ordinary failure — `sat` on a law obligation prints the witness (a real counterexample, never xfail-able); `unknown`/timeout raises a distinct `SolverInconclusive` and fails, and it's then on the operator to triage and MANUALLY change the code — a diagnosed xfail (`raises=SolverInconclusive`, reason naming the pin bump + triage note) is one reasonable option, never auto-set, weighed against fixing the encoding, adjusting the rlimit budget or retiring the obligation with written rationale; the `raises=` scoping makes it structurally unable to mask a real counterexample. If unknown becomes constant noise, the stance gets revisited. Bounded wall-clock wrapper per the existing nondeterministic-but-consistent pattern, rlimit budget inside, z3 pinned. **Fingerprinting is SEMANTIC, not source-hashing (operator):** the pinned object is the canonicalized SMT formula each obligation symbolically executes to (sorted assertions, normalized names, deterministic AST print) + z3 version + rlimit budget — the same pin-the-meaning discipline as the semantic locks, whose own `--check` rewire is DS.7 (share the normalization helpers where they fit). A comment edit or refactor leaving the formula unchanged stays proven; a semantic change reads stale and solves on-chain — so ledger-vs-on-chain collapses into a cache, and cross-platform determinism exposure narrows to genuinely-changed obligations. A buggy canonicalizer can only produce spurious STALE (harmless re-prove), never spurious fresh. The WSL2 determinism check (first-spike list below) still gates the on-miss solves. NOT a POLICY 1/2 exception (operator): the solver is treated as a dependency of unknown reliability at this point — the same class as the browser tier's bounded-timeout handling of nondeterministic-but-fairly-consistent dependencies; a hand-xfail after triage is the standard unreliable-dependency response, not a deferred failing test.
- **Symbolic-vs-concrete divergence is silent for `/`, `//`, `%`** (Python floor vs SMT-LIB Euclidean division on negatives). Branch-freedom isn't sufficient: the residual op set is a WHITELIST (add/sub/neg/int-literal-mul/comparisons/when), plus a dual-run canary per residual — concrete result == Z3-model-evaluated symbolic result on the same KAT vectors.
- **Scale homogeneity with a symbolic scalar is nonlinear** (outside the decidable fragment). Prove it for concrete scalars ({−1, 2, 3}) and claim exactly that. Anomaly is excluded from claim 1 by name; money_trail's laws are the k-unrolled BFS kind.
- **The domain-repair guarantee only covers law-expressible mutants.** Comparison/filter mutants project onto residual perturbations and the solver is guaranteed to find a discriminating state within bounds; UNION-vs-UNION-ALL, JOIN-flavor and index/refresh-order mutants have no residual image — those route to engine-side domain search, and the verdict vocabulary is closed: {discriminator-found, none-within-k, unknown-budget}, the last two escalating to me.
- **The rollups regulators actually see are outside the 12 detectors.** `l1_exceptions` (11-branch UNION ALL), `drift_summary`, `daily_statement_summary` — a row can be verified-correct in `<p>_drift` and mangled en route to the PDF. `l1_exceptions` has a trivial residual (union of the others) — add it as a 13th enumeration target; evidence/attribution columns stay TESTED, not proven, and the claim ledger says so.
- **The escape class is a disjunction, not a conjunction.** What survives claims 1+2: (values off the enumerated grid OR cardinalities above domain bounds) AND unlucky Hypothesis sampling. The interaction tiers (supersession×LOCF, long gaps) exist because of the second disjunct — they're not decorative.
- **Honest cost is ~3 + ~1.5 dev-weeks, split.** The judge's "3.5 weeks total" hid four infrastructure builds. Cut for v1: promoting the hand-written Z3 spike scripts into `scripts/audits/` (they'd rot into confidently-wrong artifacts — archived here instead, regenerate on demand), the domain-repair loop (build after hand-triaging a surviving mutant has hurt twice), branch-free style outside the money family (the CPA-readable residual is the point; don't tax the 8 residuals whose ∀-ℤ theorems are near-vacuous).

## Spike evidence (measured, this machine)

**Z3 encoding spike** ([`drift_z3.py`](ds_z3_spike_evidence/drift_z3.py) / [`xor_z3.py`](ds_z3_spike_evidence/xor_z3.py), raw output alongside): bounded symbolic DB in QF_LIA, supersession encoded as argmax over `entry`, LOCF as day-recursion (encodable, not skipped). Drift equivalence `unsat` at k=6/A=2/D=4 in 0.030s; all five mutation classes produce readable witness databases in <25ms; xor_group ≤2ms even at k=32. Scaling knee: row count k is gentle (k=32 in 0.32s), the A×D cell grid is the real cost (A=4/D=10 at 4.4s and cliffs beyond — bounded gates live at k≈6-12, not 50). Deterministic across runs (`smt.random_seed=0`).

**Enumeration spike** ([`throughput.py`](ds_z3_spike_evidence/throughput.py) / [`linearity.py`](ds_z3_spike_evidence/linearity.py)): the full 2-day money-family domain — per day, balance ∈ 7 options × leg-multisets ≤2 over amount {−1,0,1} × status {Posted,Pending} = 196/day, 196² = 38,416 cells — packed into one in-memory DuckDB, real `emit_schema` + `refresh_matviews_sql` (spec_example, unmodified), drift/overdraft/expected_eod violation sets compared against an independent Python residual: **1.5s total**. Three emitter mutants: two killed by the comparator, one (supersession drop) by the UNIQUE index at refresh. One honest miss — a boundary-value mutant survived the initial domain, which is what produced the boundary-profile rule (and its config-kv correction above). CAVEAT: these numbers come from the design agent's run; DS.0's verify-file:line discipline extends to re-running them before lock.

## Proposed Phase DS reshape (FORMATv2 — decide at DS.0, split is my recommendation)

```
## Phase DS - Residual laws + exhaustive engine verification
- [ ] DS.0 - Audit + design lock. Adopt this doc; verify its file:lines + re-run the enumeration
  spike numbers. DECIDED via doc comments 2026-07-02: drift compares carried days (a balance entry
  is the source of balance UNTIL superseded — fix red-first, finding 1); DN.5 law = Posted-only +
  plant non-Posted statuses (2); status stays OPEN, no CHECK, deterministically-random status value
  planted as a domain axis so tests can't drift (4); '' normalization (5); completeness via
  definition-site annotation + AST cross-check, not a registry (7); laws go Cents-native (8).
  Status law DECIDED (operator, follow-up Q&A 2026-07-02): unknown tail -> failed — money laws
  = 'Posted', firing/netting counts = IN ('Posted','Pending') so Pending keeps its in-flight
  standing and any UNKNOWN status value lands on the failed side in BOTH families (conservative:
  weird data surfaces as violations, never silently passes); alignment covers PRODUCTION copies +
  tests. REMAINING decision: mutation-gate tier only.
- [ ] DS.1 - Cents-native residual per invariant, authored from SPEC intent (NEVER transliterated
  from matview SQL — an SQL-derived residual verifies the SQL against itself). Branch-free when()
  style + op-whitelist lint for the money family only; plain Python elsewhere. Hand-derived KATs.
- [ ] DS.2 - Generators plant to residual (unchanged).
- [ ] DS.3 - Engine-equivalence gate:
  - [ ] DS.3.1 - money_trail refresh guard FIRST (finding 3, operator: "test for and fix"): planted
    single-parent cycle (silent-omission class) + multi-parent cycle (divergence class) as red-first
    tests; portable depth cap or pre-refresh cycle check, loud-fail; statement-timeout in the
    harness. Ordered before any trail domain.
  - [ ] DS.3.2 - Drift carried-day fix (finding 1, operator-decided): red-first — the E1c witness
    state lands as a FAILING enumeration case proving it broken, then drift's join is fixed so
    carried days compare (per-day firing rows, overdraft-consistent — flag if onset-dedup wanted
    instead); fix in the same commit chain per POLICY 2.
  - [ ] DS.3.3 - Status-law alignment (findings 2+4, operator-decided): PRODUCTION + tests. Money
    copies converge on = 'Posted' — net_flow (schema.py:3617 '<> Failed'), the dataset running-
    balance SQL and the audit walk change behavior (Pending stops moving closing_balance_recomputed).
    Cardinality firing counts go '<> Failed' -> IN ('Posted','Pending') — unknown tail lands failed.
    Plant non-Posted legs + the deterministically-random status value in the DN.5 fixture + every
    domain; the random-status plant asserts BOTH families exclude it (invisible to money sums,
    counts as failed in firing counts — may legitimately trip xor when it was the only leg, the
    conservative-alarm direction an audit tool wants).
  - [ ] DS.3.4 - Enumeration harness: cell packer with per-family packing contracts (LOCF family
    window-aligned or unpacked), bulk load, violation-set comparator, packed-vs-isolated lemma.
  - [ ] DS.3.5 - Per-invariant exhaustive domains (12 detectors + l1_exceptions as the 13th;
    anomaly excluded with written rationale) + BoundaryProfile from L2-resolved values + coverage
    lint + planted-boundary smoke per threshold detector. CI tier <=5s; nightly 3-day + interactions.
  - [ ] DS.3.6 - Metamorphic suite: failed-leg inertness (pins finding 2's Posted law),
    balanced-external, insert-order permutation (entry pinned explicitly), supersession idempotence,
    dedup-commute, anomaly z-invariances (dense-frame conditional, band edges +-epsilon owned by DS.4).
  - [ ] DS.3.7 - Hypothesis tail (NEW dep): derandomized profile, seed from RECON_GEN_FUZZ_SEED;
    int64 magnitudes, cardinality tail, random_l2_yaml topology axis.
  - [ ] DS.3.8 - Mutation-score gate (tier per DS.0 decision; unit tier stays enumeration-only).
- [ ] DS.4 - Probabilistic tolerance contract (unchanged; anomaly's structured waiver lands here).
- [ ] DS.5 - Completeness via definition-site annotation, not a registry (finding 7, operator:
  registries fail silently): each Invariant class DECLARES its kit (matview name + residual +
  generators + KAT) in a decorator at its own definition site; an import-time/AST check
  cross-validates declarations both ways against the emitted-artifact walk — every emitted
  invariant-shaped matview maps to a declaring class and vice versa, refresh-names superset-of
  emitted matviews. The annotation is the declaration site; the emitted artifacts stay the ground
  truth (an annotation nothing cross-checks is a registry in another costume).
- [ ] DS.6 - Per-dialect lane: boundary-states replay on PG + Oracle in the db tier (full-domain
  nightly optional; full-domain per-dialect stays an on-demand LOCAL run — POLICY 1, same
  containers both venues). Claim ledger: PROVEN-on-D for DuckDB, PROVEN-on-D_boundary for
  PG/Oracle — the subscript names the domain subset, never the venue.
- [ ] DS.7 - Semantic-lock --check re-wired onto the chain (finding 6) + lock identity upgraded to
  exact-cents (today the JSON pins rounded-float dollars against an exact-ZZ law — finding 8's
  round-trip in another costume); normalization helpers shared with the DST.2 proof cache.
- [ ] DS.8 - Phase exit + release.

## Backlog (promote after DS-core lands green)
- [ ] DST.1 - Symbolic-execution adapter (when() combinator, op whitelist, dual-run KAT canary).
- [ ] DST.2 - Claim-1 theorems: forall-ZZ laws per residual kind; scale homogeneity at concrete
  scalars; money_trail k-unrolled; anomaly excluded by name. On-chain step, tier by the 30s
  measurement rule (avg <30s -> unit, else agreement layer), directly invokable for iteration
  (operator-decided 2026-07-02): each obligation pins its expected verdict; any transition off the
  pin fails the runner — sat on a law obligation prints the witness (never xfail-able);
  unknown/timeout = SolverInconclusive -> ordinary failure + operator triage; post-triage hand-set
  xfail is one valid outcome (raises=-scoped; never auto-set; stance revisited if unknown becomes
  constant noise). Pinned z3 + rlimit + bounded wall-clock wrapper; semantic-fingerprint cache
  (canonical SMT formula + z3 version + budget, semantic-lock discipline) so only genuinely-changed
  obligations solve on the chain. Solver = dependency of unknown reliability, not a policy exception.
- [ ] DST.3 - Z3 domain-repair loop, built after hand-triage of surviving mutants hurts twice;
  closed verdict vocabulary, law-expressible mutants only.
```

**First spike before DS.0 locks** (afternoon-scale, scratchpad-only): exhaustive enumeration of the cardinality family's hardest cell — `multi_xor_violation` with `xor_group` as warm-up — on the real emitter. It exercises everything the money spike didn't: transfer-keyed partitioning, per-instance VALUES rowsets inside the emitted text, zero-firing LEFT-JOIN cells that need "expected" rows with NO legs, COUNT-of-CASE null-skip + UNION dedup. Plus a half-day Oracle bulk-load probe (the dialect lane's throughput is entirely unmeasured, on the historical footgun dialect), X1-X3 run through a GENERALIZED mutation harness rather than hand-applied, and the WSL2 solver-determinism check — run the spike's Z3 obligation set on the CI runner, diff verdicts against local; identical verdicts confirm on-chain solving for cache misses, divergence pushes the solves dev-side. The spike also records AVERAGE runtimes for the enumeration and z3 steps — the tier rule (<30s avg → unit, else agreement) reads straight off those numbers. Piggyback the formula-canonicalization proof-out: two textually-different but semantically-identical residuals must produce byte-identical canonical SMT dumps. If cells or packing break, this is the cheapest place to learn it.

## Rejected paths (recorded so we don't re-litigate)

- **Compile-one-IR-to-SQL-and-SMT** (the by-construction dream): right theory, wrong price — a big-bang rewrite of the most battle-tested strings in the codebase, the IR→SMT lowering visitor re-imports ~1kloc of engine semantics into the trusted base anyway, and the endgame theorem still doesn't touch the engine. Documented escalation path if a regulator ever demands proof-shaped language about the detector object itself.
- **Off-the-shelf SQL equivalence checkers**: VeriEQL is non-commercial-licensed, SQLSolver is a JVM+Calcite stack for thin marginal value over what enumeration covers, Cosette has been dead since ~2018. Unbounded-aggregation equivalence is undecidable; windows and WITH RECURSIVE are outside every tool surveyed.
- **Co-emitted SMT detector models as CI gates**: the fifth-copy problem plus solver-cliff merges. Claim-3 audits (off-chain, regenerated on demand) keep the useful part.
- **z-score in the solver**: STDDEV + division is nonlinear real arithmetic — wrong tool; the anomaly bucket keeps tolerance-band testing (DS.4) and its exact integer guards (min-n floor, stddev=0) go in the metamorphic suite.

Full agent outputs (invariant map with per-invariant SMT-hardness ratings, research tracks, three designs, judge, attack passes): workflow `wf_3c66982a-c02`, results archived in the session transcript.
