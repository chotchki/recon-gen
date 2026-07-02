# DS.0 — invariant parity audit + design lock

2026-07-02, at HEAD `9e5e26e3`. The sign-off doc for Phase DS. The investigation ([`ds_z3_formal_tie_spike.md`](ds_z3_formal_tie_spike.md)) owns the design decisions and their rationale — this doc makes its inventory canonical, locks the kit shapes + the claim vocabulary and records the pre-lock spike measurements. Every file:line was re-verified against HEAD by an adversarial pass (60+ claims: 54 exact, 8 citation-precision corrections — the corrections win wherever the investigation drifted, and none touched a design decision). Status: **SIGNED 2026-07-02** — all §8 boxes ticked with operator comments inline; the four decision comments (archive artifacts, zero-start documented-with-synthetic-anchor, supersession bypass = bug, no counts in comments) are applied to PLAN.md DS.1/DS.3.3a/DS.3.3b/DS.3.8 and the archive lives at [`ds_0_spike_evidence/`](ds_0_spike_evidence/). DS.0 is complete; DS.1 is unblocked.

## 1. What DS.0 locks (and what it does not)

Locked by this doc:

- the **claim vocabulary** (§2) — "prove" without a quantifier is a doc bug, everywhere in Phase DS;
- the **invariant inventory** (§3) — 13 registered invariants plus the one unregistered matview, each with bucket, emitter site, current law copies, residual signature and enumeration-domain sketch;
- the **MathInvariant kit shapes** (§4) — kind enum, per-kind residual signatures, the definition-site annotation with its both-ways cross-check, the KAT format + hand-derived seed vectors;
- the **shared-infrastructure lemma list** (§5) — what sits under the detectors and where each layer gets verified;
- the **tier assignments** (§6.5) — measured against the <30s-average rule, not assigned by vibes.

NOT locked: how DS.1–DS.8 implement any of it. Residual authoring style beyond the signatures, harness internals, per-invariant domain construction, the Hypothesis profile, DST.2's cache implementation — those get designed inside their tasks, against these shapes. Operator decisions are also not re-litigated here; they live inline in the investigation doc and §7 is a pointer table.

## 2. Claim-ledger vocabulary

Four claim levels, strongest first. Each carries its quantifier; a claim stated without one is a bug in the doc making it. (Notation gloss: ∀ = "for all", no exceptions; ℤ = the set of ALL integers, unbounded both directions. So PROVEN-∀-ℤ = proven for every integer value that could ever exist — the solver reasons about the amounts symbolically, algebra-style, rather than plugging in samples. The "for all" covers the VALUES; row count stays bounded at ≤ k.)

| claim | quantifier | object verified | trusted base |
|---|---|---|---|
| **PROVEN-∀-ℤ** | all integer values, row count ≤ k | the PRODUCTION residual function, symbolically executed (zero transliteration — the same Python object runs concrete in tests and symbolic in the prover) | z3 (pinned) + the DST.1 op-whitelist adapter + the dual-run KAT canary |
| **PROVEN-on-D** | every database state in the finite enumerated domain D (stated per invariant, §3) | the real engine running the real emitted SQL — unmodified `emit_schema` + `refresh_matviews_sql`, real refresh order, real UNIQUE-index contracts | the ~400-line enumeration harness + the engine itself |
| **TESTED** | the specific states exercised (fixtures, metamorphic transforms, Hypothesis samples) | whatever the test drives — evidence/attribution columns, rollup plumbing, render paths | the ordinary test chain |
| **TRUSTED-with-mitigation** | none available | a NAMED dependency | the named mitigation, written down next to the claim |

PROVEN-on-D variants: plain (DuckDB, full domain) and PROVEN-on-D_boundary (PG/Oracle, the boundary-state subset, DS.6). The subscript names the DOMAIN SUBSET, never the venue — POLICY 1 means every lane runs the identical containers + runner shape locally and on CI, so every PROVEN-on-D claim is locally reproducible on demand, and full-D on PG/Oracle is an ordinary local run (~11.5 min Oracle measured, §6.3) whenever the stronger per-dialect quantifier is wanted. Boundary-subset-by-default is a CHAIN-COST choice, not a capability ceiling. The escape class surviving the first two combined is a disjunction, not a conjunction — see the [investigation's adversarial corrections](ds_z3_formal_tie_spike.md#what-the-adversarial-passes-corrected).

TRUSTED-with-mitigation entries known today: the SQL engines themselves (mitigated by the 3-way agreement gate plus the fact PROVEN-on-D runs THROUGH them, not around them); z3 (pinned version + per-obligation expected-verdict pins + the WSL2 determinism diff); the canonicalizer (torture gate + the one-sided-error argument — a bug can only produce spurious STALE, §6.4).

## 3. Invariant inventory (canonical)

Emitter cites are `src/recon_gen/common/l2/schema.py` at HEAD `9e5e26e3` — template-wrap line, body-render fn where separate. "Law copies today" = every place the SAME law is independently encoded right now; DS.1 collapses that multiplicity onto one residual. Buckets: money ×5, threshold ×2, cardinality ×4, probabilistic ×1, derivation ×1.

| # | invariant (matview suffix) | bucket | emitter | law copies today | residual signature | domain sketch |
|---|---|---|---|---|---|---|
| 1 | `drift` | money | :2934 (computed_subledger_balance :629-701, effective_balances :2722-2883) | matview; sim fold implicitly (`account_simulation.py:135`); audit reads the matview (`cli/audit/__init__.py:439`) | Cents: `stored_effective(a,d) − Σ Posted legs posting ≤ day_end` | 2-day money domain: bal ∈ 7 opts × leg-multisets ≤2 over amount {−1,0,1} × status = 196/day → 38,416 cells, + 3,600-cell boundary domain (§6.1); LOCF packing contract (§5) |
| 2 | `ledger_drift` | money | :2982 (computed_ledger_balance :2650-2689) | matview; sim's `Transfer.is_balanced` (`ledger_simulation.py:111`, deliberately unenforced) | Cents: `parent_stored − (Σ child_stored + Σ parent direct Posted postings)` | money domain + parent/child topology axis; AU.2 composition is the ∀-ℤ side (DST.2) |
| 3 | `overdraft` | money | :3094 | matview only | Cents-predicate: violation iff `effective_money < 0` | rides the money domain; carried-day cells are the interesting ones (CL.5 rewired it onto effective_balances) |
| 4 | `expected_eod_balance_breach` | money | :3124 | matview; generator inline arithmetic (`expected_eod.py:145-157` — exactly the duplication DS.2 retires) | Cents: `money − expected_eod` (Option-guarded on NULL expected) | money domain, expected axis ∈ {None, 0} |
| 5 | `limit_breach` | money | :3167 (cap JOIN :907-923; caps are CONFIG DATA via `v_config_limit_schedules`, ×100 cents shift :919-923) | matview; audit reads the matview (:653) | Cents: `max(0, Σ|amount| − 100·cap)` per (account, day, rail, direction) | per-direction sums straddling the L2-RESOLVED caps (the boundary profile MUST read resolved config — a SQL-text lint is vacuous here) |
| 6 | `stuck_pending` | threshold | :3246 (age plumbing :965-980; `as_of` kv read `config_table.py:516`) | matview; audit reads the matview (:795) | int seconds-over-cap: `(as_of − posting) − cap`, `as_of` an EXPLICIT parameter | ages straddling the cap × cap ∈ {None, k} × the as_of frame |
| 7 | `stuck_unbundled` | threshold | :3296 | matview; audit reads the matview (:937) | same shape on (Posted, `bundle_id IS NULL`) | twin domain; `bundle_id` ∈ {NULL, '', 'b1'} — the Oracle ''≡NULL fork axis (finding 5) |
| 8 | `chain_parent_disagreement` | cardinality | :3355 (NOT-IN inline literal :1040-1075 — emit-time SQL text) | matview only | int count-delta: `|distinct claimed parent ids| − 1` | legs claiming 1–3 distinct parents × status × fan_in-excluded templates |
| 9 | `xor_group_violation` | cardinality | :3404 (body :1078; VALUES-vs-dual :1145-1165, `WHERE 1=0` fallback :1112-1136) | matview only | int count-delta: `firing_count − 1` per (transfer, group) | MEASURED (§6.2): member-leg multisets ≤2 over (status × day) × extra slot = 22,275 cells + a 512-cell two-group variant |
| 10 | `fan_in_disagreement` | cardinality | :3481 (body :1210; reads `transfer_parents` :3441-3447 — refresh-order dependency :417-425) | matview only | int count-delta: `parent_count − expected`; expected `Option[int]`, None ⇒ require ≥ 2 | child parent-counts 0–3 × expected ∈ {None, 1, 2} covering orphan/missing/extra |
| 11 | `multi_xor_violation` | cardinality | :3517 (body :1316; UNION set-semantics :1391) | matview only — and the matview DIVERGES from its own docstring today (§6.2) | int count-delta: `|fired sibling names| − 1` per (parent transfer, chain) | MEASURED (§6.2): 27,000 cells — chain-kind × parent multisets × child multisets × cross-chain extra child |
| 12 | `inv_pair_rolling_anomalies` | probabilistic | :3980 (base-table read :4002 — supersession-BYPASSING by construction; AT.0 ceiling :1512-1516) | matview only | NONE — tolerance contract (DS.4), excluded from residual typing by name | excluded from enumeration with written rationale; the integer window layer gets metamorphic laws (DS.3.6) |
| 13 | `inv_money_trail_edges` | derivation | :4174 (base reads :4184 + :4244-4251; recursive CTE :4189-4207 — no depth cap, no cycle guard) | matview only | edge-set membership: symmetric difference of expected vs emitted edges | k-bounded chains + BOTH cycle classes; DS.3.1 lands FIRST — the detector hangs on the corruption it exists to detect (finding 3) |
| 14 | `balance_cadence_gap` | (unregistered) | :704-868 + `l1_exceptions` branch :3785 | matview + rollup, NOT in `ALL_INVARIANTS` (`registry.py:271-275`), no spine generator | unassigned — the annotation gate's born-failing canary | gets a kit or a written rationale at DS.5; silent absence is exactly what the gate exists to kill (finding 7) |

Outside the 14: the rollups regulators actually see — `l1_exceptions` (12 SELECT branches; the in-source ":3690 10-branch" comment is stale, fix in the DS.3.5 pass), `drift_summary` (:3039), `daily_statement_summary` (:3557). `l1_exceptions` gets a trivial residual (union of the others) as an extra enumeration target per the investigation; its evidence/attribution columns stay TESTED and the ledger says so.

**The multiplicity poster child** — DN.5's running-balance law, FOUR textually-independent copies: matview `net_flow` excludes Failed (:3617), the dataset SQL filters nothing (`apps/l1_dashboard/datasets.py:1423-1437`), the audit walk filters nothing (`cli/audit/__init__.py:1367-1380`), `computed_subledger_balance` requires Posted (:694). One Pending leg breaks the 4-way equality TODAY and no test fires (the fixture plants only Posted — `tests/data/test_dn5_running_balance.py:159`). DS.3.3 aligns all four onto the decided status law.

## 4. The MathInvariant kit

### 4.1 Kind enum + residual signatures

```python
class MathKind(Enum):
    MONEY = auto()          # residual -> Cents; zero = law holds
    CARDINALITY = auto()    # residual -> int count-delta; zero = law holds
    THRESHOLD = auto()      # residual -> int seconds-over-cap; > 0 = violation;
                            #   as_of is an EXPLICIT parameter, never wall-clock
    DERIVATION = auto()     # residual -> edge-set symmetric difference; empty = law holds
    PROBABILISTIC = auto()  # NO residual -- tolerance contract owned by DS.4
```

The signatures are the lock; authoring style is DS.1's (branch-free `when()` + op-whitelist for the money family only, plain Python elsewhere — per the [investigation's cost cuts](ds_z3_formal_tie_spike.md#what-the-adversarial-passes-corrected)). Residuals are authored from SPEC intent, NEVER transliterated from the matview SQL — an SQL-derived residual verifies the SQL against itself.

### 4.2 Definition-site annotation (finding 7: registries fail silently)

```python
@math_invariant(
    kind=MathKind.MONEY,
    matview="drift",                    # emitted-name suffix
    residual=drift_residual,            # the Cents-native law, one place
    generators=(DriftGenerator, ...),   # spine generators that plant to THIS residual
    kats="tests/data/kats/drift.json",  # hand-derived vectors (4.3)
)
class DriftInvariant: ...
```

An import-time check cross-validates BOTH directions against the emitted-artifact walk (render `emit_schema` + `refresh_matviews_sql` for a probe instance, collect the invariant-shaped matviews):

1. every annotation's `matview` appears in the emitted set AND in the refresh list (refresh-names ⊇ emitted matviews);
2. every emitted invariant-shaped matview maps back to exactly one annotated class.

The annotation is the declaration site; the emitted artifacts stay the ground truth (an annotation nothing cross-checks is a registry in another costume). `balance_cadence_gap` fails direction 2 on day one, by DESIGN — red-first, resolved at DS.5.

### 4.3 KAT format + hand-derived seed vectors

One JSON file per invariant: vectors of `{state, params, expected}` — `state` in the Cents-native sim vocabulary, `params` the L2-resolved constants the law needs (caps, as_of, member sets), `expected` the residual per key or an asserted ABSENCE. The loader runs the residual over the state and compares; the SAME vectors later feed DST.1's dual-run canary (concrete result == Z3-model-evaluated symbolic result on identical inputs).

The vectors below were derived BY HAND from the written law statements — no expected value was read out of code (a KAT derived from the implementation tests nothing). **Operator: spot-check the arithmetic at sign-off.** Two vectors are deliberately born-failing against today's SQL and say so.

Reading the vectors — the state vocabulary, defined once:

- **leg** = one row in `<prefix>_transactions` (one money-movement leg; signed cents). Only `Posted` legs move money in the money laws.
- **emit** = the ETL writing a `<prefix>_daily_balances` row for that (account, day) — the institution's CLAIMED/stored end-of-day balance, the side drift checks AGAINST the legs. "NO emit" = no balance row that day, so the prior claim carries forward (LOCF).
- **start balance = zero, implicitly.** The computed side is `SUM(amount_money)` over ALL Posted legs from the beginning of the fed history through day end — there is no opening-balance term in the law. The feed contract this assumes: transaction history is complete from account origin.
- **residual sign** = `stored − computed`: negative = the institution claims LESS money than the fed legs explain; positive = claims more.
- **emits and legs are never added together.** The emit is the CLAIM, the legs are the EVIDENCE — they sit on opposite sides of the equation and the residual compares them. A day reading "emit −200¢; leg −200¢ Posted" is a day where the claim exactly matches the evidence (residual 0), not a −400 day.

**MONEY — drift.** Law (finding 1, operator-decided): a balance entry is the source of an account's balance UNTIL a newer entry supersedes it; on EVERY day, emitted or carried, the effective stored balance equals the cumulative sum of Posted leg amounts with posting ≤ that day's end. Residual = `reported − calculated`, Cents. Rendered as a running statement — one row per day, calculated = the running Σ of Posted legs — the shape that keeps evolution vectors like M3 legible; DS.1's money-family KAT derivation docs adopt this format. All amounts integer cents.

| vec | day | legs that day (status) | calculated (running Σ Posted) | reported balance | residual (reported − calculated) |
|---|---|---|---|---|---|
| M1 | d0 | +300 Posted, +200 Posted, +50 Pending | 500 | 500 (emitted) | 0 — no row (Pending excluded: money moves on Posted only) |
| M2 | d0 | −100 Posted, −50 Pending | −100 | −200 (emitted) | **−100 — violation: the claim is 100¢ lower than the legs explain** |
| M3 | d0 | −200 Posted | −200 | −200 (emitted) | 0 |
| M3 | d1 | −100 Posted | −300 | −200 (CARRIED — no emit; LOCF holds the d0 claim) | **+100 — violation on the carried day. BORN-FAILING: E1b proves today's matview cannot emit carried-day rows; this row IS the E1c witness and pins DS.3.2's fix** |

**CARDINALITY — xor_group.** Law (findings 2+4 status law, operator-decided): each transfer of a declaring template fires EXACTLY ONE member rail per xor group, counting legs with status IN ('Posted','Pending'); an unknown status lands on the failed side. Residual = `firing_count − 1`. Instance: spec_example's group {SettlementAuto, SettlementStandard} on SettlementTimingCycle, SettlementSlow a non-member.

| vec | state | derivation | expected |
|---|---|---|---|
| C1 | T1: one Posted leg on SettlementAuto | count = 1 | 0 — no row |
| C2 | T2: Posted on SettlementAuto + Pending on SettlementStandard | count = 2 (Pending keeps its in-flight standing) | **+1 — overlap** |
| C3 | T3, three legs: Posted on SettlementSlow (non-member — anchors the transfer's existence); Failed on SettlementAuto; status `'Zq9x'` on SettlementStandard | member count = 0 (Failed out; unknown → failed side) | **−1 — missed. BORN-DIVERGENT: today's `<> 'Failed'` counts the Zq9x leg (count 1, no row); this vector pins DS.3.3's law change** |

**THRESHOLD — stuck_pending.** Law: a Pending leg whose age — `(as_of − posting)`, `as_of` the owned temporal frame passed explicitly — EXCEEDS its rail's `max_pending_age_seconds` is stuck; a rail with no declared cap is never stuck. Residual = `age − cap` seconds, violation iff > 0.

| vec | state | derivation | expected |
|---|---|---|---|
| T1 | posting 2030-01-01 12:00:00, as_of 2030-01-03 12:00:00, cap 86,400s | age = 2 × 86,400 = 172,800; 172,800 − 86,400 | **+86,400 — violation** |
| T2 | posting 2030-01-02 12:00:00, same as_of + cap | age = 86,400; residual = 0 — strict `>`, exactly-at-cap is NOT stuck | 0 — no row (the boundary witness the noon-only blindness demands, §6.1) |
| T3 | same leg, rail declares no cap (NULL) | no cap ⇒ never stuck, any age | ABSENT — no row |

**DERIVATION — money_trail.** Law: for every transfer reachable from a root (`transfer_parent_id IS NULL`), emit one edge per (src leg: amount < 0, Posted) × (tgt leg: amount > 0, Posted) pair, labeled with the root's transfer_id and the member's depth. Residual = symmetric difference of expected vs emitted edge sets.

| vec | state | derivation | expected |
|---|---|---|---|
| D1 | root R: −100¢ from A, +100¢ to B; child C (parent R): −100¢ from B, +100¢ to C1 (all Posted) | one src × one tgt at each depth | edges {(R, A→B, d0), (R, B→C1, d1)}; symdiff ∅ |
| D2 | root R2: legs −60¢ A1, −40¢ A2, +100¢ B (all Posted) | cross product within the transfer: 2 src × 1 tgt | 2 edges {(R2, A1→B, d0), (R2, A2→B, d0)} |
| D3 | P parent-of Q, Q parent-of P — a cycle, no null-parent root | unreachable from any root: TODAY's law emits nothing (the silent-omission class, finding 3) | symdiff ∅ against current semantics — and the vector carries the DS.3.1 flag: silence must become a loud pre-refresh failure, at which point this expectation flips WITH the fix |

PROBABILISTIC gets no vectors — excluded from residual typing by name; tolerance bands, the min-n floor and the stddev=0 guard are DS.4's contract.

## 5. Shared-infrastructure lemmas

Five layers sit under the detectors. Each is named, located and assigned a verification home — nothing under the detectors goes unclaimed.

| lemma | where (schema.py unless noted) | verification home |
|---|---|---|
| supersession argmax (Current* views) | :578-626 + :2090-2147 (byte-equivalent twins) | metamorphic: supersession idempotence + insert-order permutation (DS.3.6); the BV.6 UNIQUE index is a LIVE detection channel (MUTANT C crashed refresh, §6.1); 48-cell isolated lemma domain in the enumeration |
| LOCF carry (effective_balances) | :2722-2883; fleet-wide day spine :995-1009, CROSS JOIN :2800-2804, cumulative-MAX :2815-2824 | enumeration under the PACKING CONTRACT — the spine is fleet-wide, so co-packed cells interact; the LOCF family packs window-aligned or runs unpacked, and the quantifier restates over the states actually executed. E1b/E1c z3 obligations pin the intent gap |
| balance helpers (computed_subledger / computed_ledger) | :629-701 / :2650-2689 | the money enumeration domain; the AU.2 composition lemma as PROVEN-∀-ℤ (DST.2) |
| config injection A — typed config views | `v_config_*` :2270-2636 (chain_children :2419-2491) | ground facts at verification time; the boundary profile derives from L2-RESOLVED values including the ×100 cents shift (:919-923) — a SQL-text lint passes vacuously on exactly the 4 detectors these feed |
| config injection B — emit-time SQL text | xor VALUES rowset :1145-1165, chain_parent NOT-IN :1040-1075, cadence CASE arms | the theorem is instance-parametric; the annotation kit ties the declaring instance to the emitted text, and per-instance enumeration covers it (the two-group xor variant is the worked example, §6.2) |
| refresh ordering | :342-489 (PG/Oracle), :492-575 (DuckDB); fan_in-after-transfer_parents :417-425 | INSIDE the verified object by construction — enumeration runs the real refresh; the annotation cross-check keeps refresh-names ⊇ emitted matviews |

## 6. Pre-lock spike results (measured on this machine, HEAD `9e5e26e3`)

### 6.1 Enumeration re-run — the investigation's own-run caveat is discharged

[`throughput.py`](ds_z3_spike_evidence/throughput.py) + [`linearity.py`](ds_z3_spike_evidence/linearity.py) re-run unmodified, 3 runs: 42,064 states total (38,416 primary + 3,600 boundary + 48 supersession), 197,568 rows on the primary domain. Per-stage: insert 0.45–0.55s, refresh 0.81–0.85s, python residual 0.08s, comparator 0.03s — the primary stages sum to 1.36–1.51s, matching the investigation's "1.5s". Full-script wall (all 3 domains + 4 mutant refreshes + interpreter start): 4.35 / 3.98 / 3.97s → **average 4.10s**, and THAT is the number the tier rule reads. Baseline zero disagreements every run. Mutants identical 3/3: A (dropped status filter) killed by the comparator (6,804 py-only / 10,416 engine-only), B (`<=`→`<`) MISSED on the noon-only domain and killed on the boundary domain (784/472 — the honest miss that produced the boundary-profile rule, and the reason T2 above exists), C (supersession drop) killed by the BV.6 UNIQUE index crashing the refresh. Linearity: refresh scales sub-linearly (1×/2×/4× rows → 0.81/1.42/2.20s).

Z3 side ([`drift_z3.py`](ds_z3_spike_evidence/drift_z3.py) / [`xor_z3.py`](ds_z3_spike_evidence/xor_z3.py)): all 33 obligations returned their pinned verdicts, witness databases byte-identical to the committed outputs, only solve-time decimals moved (max delta 0.052s). Total wall **8.71s** (drift 8.01 + xor 0.70). `smt.random_seed=0` determinism confirmed locally AND cross-platform — the WSL2 diff ran on the self-hosted CI runner (spike branch `spike/ds0-wsl2-determinism`, run 28602235048): **verdicts identical, all 11 canonical fingerprints byte-identical** across macOS-arm64 python 3.14.6 ↔ Linux-x86_64 python 3.14.5, same pinned z3 4.16.0. The only cross-platform variance: the rlimit-used column on 7 of 8 sat obligations (±3% — solver search paths to a MODEL differ slightly by platform; the three unsat proofs + M1 consumed bit-identical rlimit). Design consequence for DST.2: verdict + fingerprint are the hard byte-compared gate (confirmed sound), rlimit stays diagnostic-only, and rlimit BUDGETS need headroom (≥2× observed max) so a near-budget obligation can't pass locally and flip to unknown on CI inside that ±3%. On-chain solving is CONFIRMED viable: a formula that proves locally proves identically on the runner.

### 6.2 Cardinality spike (xor_group + multi_xor) — the backbone go/no-go

**GO. The backbone held everywhere it was stressed — and the spike caught a REAL spec-vs-SQL divergence, which is the approach doing its job before DS.1 even exists.**

What held: xor_group EXACT match, 19,719 violation rows over 22,275 exhaustive cells with zero divergence (zero-firing LEFT-JOIN cells, all-Failed vanishing, unknown-status counting, per-leg counting all behave as the spec-reading residual predicts). The 512-cell two-group variant proved `xor_group_index` partitioning. Transfer-keyed packing is CLEAN: combined-DB (246k rows, both families) equals per-family packed sets exactly, and a 219-cell isolated sample (fresh DB per cell, forced interesting classes) showed 0 mismatches — the packing failure this spike existed to catch did not materialize. Mutation battery: 9/10 killed, with one kill landing EXACTLY on its analytic prediction (216 = 6×6×6 zero-firing cells — the harness's cell accounting is self-consistent, not just green). The one survivor (MX6, `UNION`→`UNION ALL`) is a PROVEN-EQUIVALENT mutant — the duplicate rows re-collapse in the downstream `SELECT DISTINCT` — which retires the invariant map's UNION-dedup encoding worry rather than exposing a domain gap.

What broke: **multi_xor day-multiplication.** `fired_children_distinct` includes `pf.business_day` in its DISTINCT, so the final COUNT = |distinct non-Failed parent posting days| × |fired sibling names|, not |names|. Exhaustively characterized: 4,914 of 27,000 cells diverge from the docstring spec, 2,268 of them outright production-shaped FALSE POSITIVES — a parent that did exactly the right thing but posted legs across midnight reads `('overlap', child_count=2, 'Wire,Wire')`. An engine-model residual (count = days × names) matches the engine on all 15,840 rows, so nothing else lurks in the divergence. No existing test can see it: every multi_xor spine generator plants parent legs at a single `anchor_day` (`spine/multi_xor_violation.py:254,338,368`). Fix shape (red-first, needs a DS.3 sub-task beside DS.3.2's drift fix — §7): drop `pf.business_day` from the DISTINCT/grouping, carry `MIN(business_day)` separately.

Also surfaced: the decided status law WIDENS a blind spot. An all-unknown-status transfer today yields a firing_count=0 violation (conservative alarm); once unknown legs stop creating the existence row it vanishes entirely. The count filter and the EXISTENCE predicate are separate decisions — DS.3.3 must state both explicitly.

Timing: xor packed full-step 0.76s, multi_xor 1.01s, combined-DB refresh 1.33s, mutants 0.01–0.05s each, isolated lemma 0.088s/cell (full-domain isolated would be ~70 min — isolation stays a sampled lemma, packed is the gate).

### 6.3 Oracle probe vs DS.6's cost shape

DS.6 assumes boundary-state replay on PG/Oracle in the db tier with full-domain nightly OPTIONAL — until now on zero throughput data for the historical footgun dialect. Measured against a live Oracle 19c container through the REAL repo paths (`emit_schema` → `execute_script` → `refresh_matviews_sql`, spec_example):

- **Quarter slice (9,604 cells / 48,020 rows): end-to-end real path 42.19s.** Schema apply 0.76s (168 stmts), DPL bulk insert 1.20s (40,081 rows/s), matview refresh **40.18s** (48 stmts — refresh is the entire cost), detector SELECTs 12–22ms. **Agreement CLEAN**: engine violation sets == the same python residual the DuckDB spike used (drift 11,592 / overdraft 5,880 / eod 5,488) — the dialect lane's first live 3-way datapoint.
- **The INSERT-ALL batching path is 108× slower than DPL**: 129.31s for the same rows (371 rows/s, 7,123 round-trips). The DS.6 lane must seed through the `execute_script` DPL path, never the batcher.
- **Full domain (38,416 cells / 197,568 rows): end-to-end ~685s.** Schema 0.80s, DPL insert 4.94s (39,982 rows/s — linear with the quarter), refresh **679.40s** with 673.5s of it in exactly TWO statements: `computed_subledger_balance` 429.18s + `current_daily_balances` 244.32s — the correlated-argmax pair from §5, quadratic on Oracle (4× rows → 16.9× refresh; every other statement is sub-second). Detector SELECTs 42–87ms. **Agreement CLEAN, and the violation counts EQUAL the DuckDB run exactly** (drift 46,592 / overdraft 23,520 / eod 21,952) — full-domain cross-dialect violation-set equality, measured rather than assumed.

**PostgreSQL probe (follow-up, same domain, postgres:17 container):** steady-state NOT quadratic — and auto_explain caught the real first-refresh mechanism, which turns out to be OUR bug. PG does NOT decorrelate — the plan shows `SubPlan 1` executed per outer row, the same evaluation strategy Oracle uses — but each probe hits the emitted `idx_<p>_curr_tx_account_posting` index at ~1µs, so the defining query runs 29-38ms (quarter) → 125-269ms (full): near-linear. The 49.74s `computed_subledger_balance` first refresh IS the subquery going quadratic, inside a STALE-STATS window: the refresh planned against a never-analyzed, just-populated `current_transactions` (outer estimated 22 rows, actual 65,856; the SubPlan flipped to a BitmapAnd whose status arm scans ~half the matview PER OUTER ROW ≈ 4.3e9 index-entry touches — auto_explain has the plan; the in-log "pre-ANALYZE" EXPLAINs are misleading, they ran after the script's trailing ANALYZEs). ROOT CAUSE: `refresh_matviews_sql`'s PG arm emits every `ANALYZE` at the END of the script, after all REFRESHes — first refresh after populate always plans blind. Second refresh (stats present): linear, 1.72s quarter → 6.10s full (3.5× at 4× rows; `computed_subledger` 0.59s). Fix candidate for DS.6 (placement + shape operator-decided 2026-07-02): stats CASCADE with the refresh dependency order the script already encodes — every object's stats gathered immediately after its own materialization, before any dependent refreshes (base tables are the cascade roots post-load: a bulk seed leaves `<p>_transactions` itself unanalyzed, which is exactly what the Current* refresh plans against; then Current*, then dependents). A dependent can never plan against an unanalyzed upstream BY CONSTRUCTION — the invariants-in-process shape, not "put the ANALYZEs somewhere better." Lives in the `refresh_matviews_sql` emission, so production ETL and the shared test fixtures inherit it through the one script they already run; never a fixture-local patch. Pure plan hygiene, violation sets cannot change. Full-domain PG end-to-end ~70s today, ~25s with the placement fixed (insert's 12s then dominates); boundary replay = seconds. Agreement CLEAN at both scales, counts identical to DuckDB AND Oracle — **three-engine violation-set equality on the full domain, measured.** The no-index control completes the picture: the same defining query with the index dropped runs 24.75s at QUARTER scale (per-row seq scans, the true quadratic shape), and ×16 for the full domain extrapolates to ~400s ≈ Oracle's measured 429s for the same statement.

**Oracle stats-cascade spike (post-sign-off, same day — the hypothesis MEASURED, archived in [`ds_0_spike_evidence/oracle/`](ds_0_spike_evidence/oracle/)):** the emitted Oracle refresh script has ZERO stats calls before any refresh (all `GATHER_TABLE_STATS` trail the refreshes — same disease as PG's ANALYZE-at-end). Three variants, quarter then full domain:

| variant | quarter refresh | full refresh | the tell |
|---|---|---|---|
| baseline (script as emitted) | 41.31s | 679.4s | csb 429s + cdb 244s = 99% of cost |
| matview stats interleaved only | 17.67s | 256.98s | csb FIXED (0.14s) but cdb still 250.47s — its supersession MAX(entry) subquery probes the unanalyzed BASE table |
| full cascade (base tables as roots, then per-object) | **2.66s** | **7.63s** | csb 0.13s, cdb 0.54s; agreement CLEAN, drift = 46,592 exactly |

**89× on the full-domain refresh, zero SQL changes** — and the middle row is the operator's cascade design proven necessary at full scale, not decorative: matview-interleaving without base-table roots leaves the biggest statement unfixed. Full-domain Oracle end-to-end is now ~13s (insert 4.94s + refresh 7.63s + detector SELECTs), which moves it from nightly-class to casually-local.

Verdict for DS.6, final: boundary-scale replay is db-tier viable everywhere; full-domain is an ordinary on-demand local run on ALL THREE engines (DuckDB 0.85s / PG ~70s pre-fix, ~25s post / Oracle ~13s post-cascade). The stats cascade in `refresh_matviews_sql` emission is MEASURED on both server dialects (PG 49.7s → 0.6s on csb; Oracle 679s → 7.63s total) — and on those numbers the operator PROMOTED it out of DS.6 to **DS.0a, a mandatory predecessor before anything touches PG/Oracle again, including routine `up_to=db` runs** (every db-tier cell pays the first-refresh stale-stats window on its fresh schema today). Safe pre-DS.3 because it is plan-only: stats statements added, zero query-shape changes, violation sets cannot change. The window-form decorrelation rewrite (supersession → ROW_NUMBER-per-key, computed balance → SUM OVER) stays on the table as the structural convergence play — single-pass on every engine, no optimizer reliance, kills the stale-stats sensitivity class entirely — but it is a post-DS.3 rewrite (the enumeration gate is what makes touching these two emitters mechanical instead of scary), not a prerequisite for cheap runs.

### 6.4 Canonicalization + obligation set (the DST.2 semantic-fingerprint proof-out)

Both properties PASS. Refactor-stable: a second drift encoding with every Python AND z3 name changed, construction order shuffled and helpers extracted canonicalizes to the byte-identical 94,110-byte dump (sha256[:16] `7c79c45d0542f310`). Semantics-sensitive: the M1 mutation fingerprints differently (`feda026d718676fe`). A 5/5 shuffle+rename torture (random assertion order + alpha-rename via `z3.substitute`) reproduces baseline bytes. The 11-obligation runner produced byte-identical stdout across 3 local runs, all pinned verdicts, all 11 fingerprints distinct.

The cost surprise: **solves are 0.000–0.031s each; CANONICALIZATION dominates at 0.8–5.7s per obligation, ~26s total.** The semantic-fingerprint cache pays at canonicalization, not solving — DST.2's tier measurement must include the canon step, not just `s.check()`.

Honest fragility (carried into DST.2's design): normalization is syntactic-modulo-(names, AC-order), not semantic — `x >= 0` vs `0 <= x` reads as changed → spurious STALE → harmless re-prove (the intended one-sided error, but expect cache misses on cosmetic comparison rewrites). The symmetric-tie individualization is guaranteed byte-stable only for true automorphisms; the torture test stays as a cheap per-obligation gate rather than trusting the tie-break. No `simplify()` anywhere — deliberate, it would couple fingerprints to z3-version internals. rlimit deltas are diagnostic-only in the cross-platform diff; verdicts + fingerprints are the hard gate.

### 6.5 Tier verdicts (<30s average → unit, else agreement — measured, not assigned)

| step | measured | tier |
|---|---|---|
| money-family enumeration (full script, 3 domains + mutants) | 4.10s avg over 3 runs | **unit** |
| cardinality enumeration (xor + multi_xor packed, incl. mutants) | ~1.8s combined | **unit** |
| z3 spike obligations (committed scripts, solve-heavy) | 8.71s single run | **unit** |
| z3 obligations + semantic fingerprinting (the DST.2 shape) | ~26s canon + ~2s solves, single run | on the 30s edge, canon-dominated — RE-MEASURE at DST.2 with the real obligation count; unit if it holds, else agreement |
| Oracle boundary replay (DS.6 db tier) | quarter-slice 42.19s end-to-end; boundary-scale ~6s under the measured quadratic | db tier (per-dialect lane, not the unit bar) |
| Oracle full domain | ~685s end-to-end, 679.4s refresh (§6.3) | nightly-only, confirmed |
| mutation battery (see §7) | ~4s today (money 3 × ~0.8s refresh + cardinality 10 × ≤0.05s) | **unit** — recommended below |

## 7. Decisions record

All decided 2026-07-02, inline in the [investigation doc](ds_z3_formal_tie_spike.md#live-findings--the-investigation-paid-for-itself-before-ds0-even-starts) — one line each, follow the links for the reasoning:

1. Drift compares carried days; red-first fix at DS.3.2 (finding 1 comment — "starting out with a known failure is a good thing"). KAT M3 is that failure, in vector form.
2. DN.5 money law = Posted-only; plant non-Posted statuses (finding 2 comment).
3. money_trail cycle guard is a planned task, ordered first (finding 3 comment) → DS.3.1.
4. `status` stays an OPEN column, no CHECK; unknown counts as failed; a deterministically-random status value plants as a domain axis (finding 4 comment + the follow-up status-law Q&A: money = 'Posted', firing counts = IN ('Posted','Pending')).
5. `''` normalization at the feed boundary + a `''` witness per nullable-string predicate (finding 5 comment).
6. Semantic-lock `--check` re-wired onto the chain (finding 6 comment) → DS.7.
7. Completeness via definition-site annotation + AST cross-check, not a registry (finding 7 comment) → DS.5, kit shape in §4.2.
8. Laws go Cents-native (finding 8 comment) → DS.1.
9. Solver on-chain; tier measured not assigned; expected-verdict pins; semantic fingerprinting; not a POLICY 1/2 exception (the solver-placement correction bullet, operator-decided in full).

New since the investigation — task-list edits APPLIED at sign-off (2026-07-02): the **multi_xor day-multiplication fix** → PLAN DS.3.3a (red-first); the **existence-predicate decision** → folded into DS.3.3's wording; the **refresh-script stats-cascade fix** (§6.3) → DS.6's first move; the **Investigation supersession alignment** (operator: bug — anomaly + money_trail go through Current*; only the audit PDF keeps raw rows) → DS.3.3b (red-first); the **drift zero-start precondition** (operator: document loudly + the synthetic opening transaction + balance row as the cutover workaround) → DS.1.

**The ONE decision the investigation left open: mutation-gate tier (DS.3.8).** Recommendation from the measured timings: **unit tier.** The full battery today costs ~4s (each money mutant is one 0.8s DuckDB refresh; each cardinality mutant is a 0.01–0.05s targeted matview re-create) — an order of magnitude under the bar, and a surviving mutant is exactly the signal you want BEFORE push, not at the nightly. Growth caveat: 13 detectors × a per-detector battery could multiply; the 30s rule stays the arbiter and DS.3.8 re-measures — demotion to agreement is a config change, not a redesign. Per-dialect mutation replay is NOT recommended (mutants target the emitted text; the dialect lane verifies baseline equivalence, which is the per-dialect question).

## 8. Sign-off checklist (operator)

- [x] **KAT vectors spot-checked** (§4.3) — the arithmetic in all 12, and explicitly the two born-failing ones (M3 carried-day, C3 unknown-status) as the shapes DS.3.2/DS.3.3 must turn green.
- [x] **Tier assignments accepted** (§6.5), including the DST.2 re-measure note (canon dominates, not solving).
- [x] **Mutation-gate tier decided** — recommendation: unit (§7).
- [x] **multi_xor day-multiplication accepted as a new red-first DS.3 sub-task** (§6.2) + the existence-predicate wording folded into DS.3.3.
- [x] **WSL2 solver-determinism result acknowledged** (§6.1 — MEASURED, no longer owed): verdicts + all 11 fingerprints byte-identical cross-platform; rlimit ±3% on sat obligations only → rlimit budgets get ≥2× headroom in DST.2. Raw outputs in session scratchpad pending the artifact-disposition decision below; the spike branch is deleted.
- [x] **Spike-artifact disposition**: the DS.0 harnesses (cardinality enumeration, canonicalizer + obligation runner, Oracle probe) live in session scratchpad; archive beside [`ds_z3_spike_evidence/`](ds_z3_spike_evidence/) or accept regenerate-on-demand (the investigation chose archiving for its own spikes to avoid scripts rotting into confidently-wrong artifacts).
  - Archive, its cheap disk to keep around
- [x] **Drift's zero-start precondition — document it loudly or add an opening anchor.** The law is `stored = Σ Posted legs over the account's WHOLE history` — no opening-balance term exists, so the feed contract silently requires transaction history complete from account origin. Schema_v6 states the law (`src/recon_gen/docs/Schema_v6.md:234-235`) but never the precondition — a real institution cutting over mid-history and feeding transactions from the cutover date would false-positive drift on every pre-existing account. Options: state the full-history requirement loudly in Schema_v6 + the ETL onboarding docs, or plan an opening-anchor variant of the law (the running-balance dataset SQL already carries exactly that opening-anchor shape; drift does not). Surfaced by the operator's KAT spot-check question, which is the KAT discipline working.
  - Comment - A clean work around is a synthetic transaction + balance at the beginning. I would document it loudly as suggested
- [x] **Supersession bypass — bug or feature?** `inv_pair_rolling_anomalies` joins raw `{p}_transactions` (schema.py:4002-4004) and money_trail's `distinct_transfers` reads the base table (:4184) — NEITHER goes through the Current* supersession views, so a superseded (corrected) leg still contributes pair-flows and trail edges. This operator decision was in the investigation's judge output but fell out of the findings list — surfacing it here rather than letting it vanish. Decide: intentional (Investigation wants the full history including corrections) or a bug (align on Current*).
  - Comment - I think this is a bug, investigation should go through current. its only the audit pdf that cares about the real rows for reproducability
- [x] **Small housekeeping folded into DS.3.5**: the stale "10-branch" comment at `schema.py:3690` (actual: 12 branches / 11 UNION ALLs).
  - I would stop keeping the counts in comments, leads to tech debt that can't be checked
  - Applied as the general rule, not the one-off fix: DS.3.5's pass STRIPS counted-quantity claims from emitter comments (describe structure, never counts — an uncheckable number in a comment is drift waiting to happen); `schema.py:3690` is the exemplar, the sweep covers all of schema.py.
