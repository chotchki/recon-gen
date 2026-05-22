# Date / Range / Anchor Model Audit (AO.11)

**Status:** draft for decision · **Date:** 2026-05-22 · **Prompted by:** the AO.10 / QS
empty-KPI investigation, which surfaced that we've grown several overlapping and
mutually-inconsistent date concepts. A piecemeal fix of any one just moves the
problem; this doc inventories all of them, maps how they interact, names the
conflicts (with the bug each caused), and proposes one coherent model.

---

## 1. Why this exists — the bugs that forced it

- **AO.10 / QS Daily Statement KPIs empty (release blocker).** The analysis
  parameter `pL1DsBalanceDate` defaults to *rolling yesterday*; the dataset
  parameter of the same name defaults to the `2999-12-31` *latest-day sentinel*.
  Because the analysis param is bridged to the dataset param via
  `MappedDataSetParameters`, **QS pushes the analysis default (yesterday) into the
  dataset param — the sentinel never applies on QS.** "Yesterday" has no row for the
  picked account → 0 rows → the 5 KPIs don't render. App2 ignores the analysis
  RollingDate and uses the dataset sentinel → latest-day fallback → renders. The
  QS-vs-App2 split *is* this default-resolution mismatch.
- **AO.S2.a / trainer timeline broke on a date rollover.** The timeline's
  scenario-end anchor floated on wall-clock `today`, conflated with the
  trainer's load-up-to scrub head; passed 5/21, broke 5/22. (Fixed by pinning,
  but it's the same family of problem.)
- **Oracle ORA-00932 (AO.10, fixed separately).** `TRUNC('<iso string>')` —
  a date *literal* fed where a date *value* was assumed. Symptom of the same
  loose thinking about "what kind of date is this and who parses it."

---

## 2. Inventory — every date/range concept in the system

| # | Concept | Layer / file | Default | Semantics |
|---|---------|--------------|---------|-----------|
| 1 | **Seed anchor (live)** | `cli/data.py::apply` → `build_full_seed_sql(anchor=None)` → `now()` | wall-clock **today** | Plants + 90-day baseline land at `[today-90, today]` |
| 2 | **Seed anchor (locked)** | `cli/data.py::_CANONICAL_LOCK_ANCHOR` | **`2030-01-01`** | Byte-identity locked SQL; tests/CLI pass this |
| 3 | **Baseline window** | `seed.py::DEFAULT_BASELINE_WINDOW_DAYS` | **90** | `[anchor-89, anchor]` |
| 4 | **L1 universal date range** | `l1_dashboard/app.py` `_DATE_START/END_DEFAULT_EXPR` | RollingDate **now-7d .. now** | Narrows most L1 sheets |
| 5 | **L1 Daily Statement balance date** | `l1_dashboard/app.py::pL1DsBalanceDate` (analysis) | RollingDate **now-1d** | Single-day pick |
| 5b | … same param, dataset side | `l1_dashboard/datasets.py::P_L1_DS_BALANCE_DATE_DSP` | StaticValues **`2999-12-31T00:00:00`** | Far-future = "latest day" SQL fallback |
| 6 | **Exec date range** | `executives/app.py` `_EXEC_DATE_*_DEFAULT_EXPR` | RollingDate **now-30d .. now** | Narrows exec sheets |
| 7 | **L2FT date range** | `l2_flow_tracing/app.py` `_DATE_START/END_STATIC` | StaticValues **`1900-01-01` .. `2099-12-31`** | Wide bracket = "match all" |
| 8 | **App2 date binds** | `dataset_contract.py` `{date_filter}` → `:date_from/:date_to` | match-all sentinels | DB-level narrow for the HTMX renderer |
| 9 | **Trainer scenario-end** | `tg_cache.py::window_end` | wall-clock **today** | Plant-projection anchor (fixed positions) |
| 10 | **Trainer load-up-to** | `tg_cache.py::end_date`/`get_up_to()` | none → `window_end` | Scrub head; how far the trainer has "loaded" |
| 11 | **`{date_filter}` slot** | `dataset_contract.py` | QS: `""` (analysis filter narrows); App2: bind clause | Per-renderer narrowing seam |

---

## 3. The interaction that bites: seed anchor × dashboard default × renderer

Three independent axes have to line up for a visual to show data, and today they
don't share a source of truth:

1. **Where the data is** (axis: seed anchor). Live `data apply` → near *today*.
   Locked seeds / anything seeded for byte-identity → *2030*.
2. **Where the dashboard looks by default** (axis: param default). RollingDate
   params (#4,#5,#6) look near *today*; static params (#7, #5b) look at fixed
   brackets.
3. **Which default the renderer honors** (axis: default resolution). **QS honors
   the *analysis* param default and pushes it into any mapped dataset param.
   App2 ignores analysis-level defaults and honors the *dataset* param default.**

Consequences:
- A dashboard seeded live (today-anchored) + RollingDate defaults *mostly* works
  on a wide range (#4 −7d, #6 −30d find data), which is why only the **single-day**
  balance date (#5) visibly broke — a 1-day window is far more likely to miss a
  given account than a 7/30-day window.
- The **same dashboard against locked/2030 seeds** (any preview or test that uses
  the locked anchor) would show **empty** RollingDate ranges — data is at 2030,
  defaults look at 2026. This is a latent trap, not yet a reported bug only
  because previews tend to use live seeds.
- **Axis 3 is the actual AO.10/QS bug:** a param that is both analysis-declared
  *and* dataset-mapped has **two** defaults, and the two renderers pick different
  ones. Whenever they disagree, QS and App2 diverge.

---

## 4. Conflicts (each = a real or latent bug)

- **C1 — Dual-default / renderer split (active blocker).** Mapped analysis↔dataset
  params carry two defaults; QS uses analysis, App2 uses dataset. They must be
  made to **agree**, or the dataset default must be the *only* one (param not
  analysis-declared) where they'd differ.
- **C2 — Three "special date" idioms, no shared vocabulary.** `2999-12-31`
  (latest-day trigger), `1900↔2099` (match-all bracket), App2 match-all binds —
  three encodings of "all" / "latest" with no shared helper. `2999-12-31`
  especially reads as a bug to anyone who hits it.
- **C3 — Rolling vs static defaults are inconsistent across apps.** L1/Exec roll
  off `now()`; L2FT is static. Rolling silently assumes "data is near now," which
  is an artifact of the live-seed anchor, not a guarantee — and is outright false
  under the locked-seed anchor.
- **C4 — Scenario-end vs load-up-to conflation (fixed in AO.S2.a, same family).**
  A "fixed extent of the scenario" and a "how far have I loaded" cursor were one
  field; pinning them apart fixed it. The general lesson — *anchor ≠ cursor* —
  applies to the dashboard params too.
- **C5 — Single-day defaults are fragile.** "Yesterday" (or any one day) routinely
  misses a specific account/rail. "Latest day with data" is the robust intent; it
  must be expressed in a way both renderers honor (ties to C1).

---

## 5. Step back — the hidden structure: time is the unowned coordinate

The recurring lesson on this project: when two things hit each other badly,
there's a hidden thing making it hard, and the move is to step back. Here are the
two things, coming from opposite directions and meeting at the data/query
boundary:

- **App (forward from definition):** `constraints + shape → queries that elevate
  violations → visuals to see them`.
- **Test / training (forward from scenario):** `seed + time + shape → generator →
  data that feeds those queries`.

They share **shape** — the L2 (accounts, rails, templates, chains, limits) — and
shape is a *first-class, owned, declared* artifact both directions read. They also
share **time** — but **time is not owned**. The app improvises its temporal
predicates from wall-clock `now()` (QS `now()`, RollingDate exprs, `date.today()`);
the generator improvises data placement from a seed anchor (today for live, 2030
for lock). Nobody declares the scenario's *temporal frame* the way the L2 declares
its shape.

**Every conflict in §4 is a symptom of that one gap.** The query's temporal
predicate and the data's temporal placement only line up by luck — both ≈ `now`
in a live deploy — and diverge the moment they don't: locked-2030 data vs
`now()`-rolling defaults (C3); a single-day predicate vs sparse placement (C5);
the QS-vs-App2 default split (C1) riding on top. They are not N date bugs; they
are one missing abstraction surfacing in N places.

**The hidden thing: there are two clocks that should be one.** Make time
first-class — a *scenario as-of* bound once per deployment, that **both**
directions read instead of `now()`:

- **Production:** `as_of = now()` — data genuinely flows up to now.
- **Demo / test:** `as_of = the fixed scenario anchor` — data frozen there.

The generator places its window relative to `as_of`; the dashboards set their
windows / "latest" / "today" relative to `as_of`. Then the temporal predicate and
the data placement are consistent **by construction under any binding** — the
float-vs-fixed tension dissolves because "now" *for the scenario* is the binding,
not wall-clock. Shape is owned (L2); time should be owned the same way (a scenario
temporal frame), and the bugs are the cost of its absence.

This is the keystone. §6 is the *mechanism* that implements it; the §8 determinism
story falls out of it for free (the frame's binding is the only thing that floats,
and it's an explicit input, so locked = byte-identical and live = ends-at-now by
the same code path).

### …but the frame is only the first layer (the residual tension)

A shared `as_of` aligns the *anchor point* — "where the data ends" = "where the
dashboard looks." It does **not** by itself align the *temporal semantics* on
either side, and that's the residual tension worth naming now rather than
rediscovering later. The two directions each independently encode what time
*means*:

- **App side:** each query carries a temporal predicate — "today's exceptions"
  (the `as_of` day), "rolling 2-day anomaly", "last 7 / 30 days", "latest day for
  this account". These windows live in query/analysis code.
- **Generator side:** plants land at *positions and spreads* on the calendar
  (a drift on day X, a fan-out over days X..X+2, a pending stuck N days).

For a planted violation to actually surface, the generator must place it **inside
the window the app's query will scan** — a contract about window *shape*, not just
the anchor. Today nothing owns that contract: the app defines windows in one place,
the generator places plants in another, and they agree only by a developer holding
both in their head. When they drift, you get exactly the AO symptoms — "Today's
Exceptions" spanning multiple days (AO.4), an average over the wrong window (AO.5),
a single-day balance that lands off the plant (the balance KPI). The `2999`/
`yesterday` mess is the *anchor* layer of this; AO.4/AO.5 are the *window-semantics*
layer.

So there are **two** hidden things, not one:

1. **Time as an unowned *anchor* (the frame).** Addressed by D1 — and its home is
   already obvious: `as_of` belongs in **config**, which already owns the
   *instantiation* of the L2 shape (the L2 declares the shape; config binds it to a
   deployment). `as_of` is the temporal half of that same binding.

2. **Temporal *window semantics* as an unowned contract.** This is the real smell —
   and the reason "we constantly don't know what should be where" is that we've
   been treating one homeless thing as if it were the same as two things that
   already have homes. Window semantics actually come from **three** sources:

   - **Invariant-derived (objective, owned by the check).** "Rolling 2-day anomaly"
     is *intrinsic to what the anomaly invariant means*; the window is part of the
     violation definition and lives in the matview SQL. No new home needed — it's
     owned by the invariant.
   - **Data/deadline-derived (objective, owned by the scenario data).** "Stuck
     pending N days" is defined relative to a *deadline populated in the data*; the
     window is a fact of the scenario, owned by the L2/data. No new home needed.
   - **Subjective view (a presentation choice — UNOWNED).** "I want to see the last
     X days", "open on the latest day", "today's statement". These are *analyst
     viewing preferences*, not properties of any invariant or datum — and they have
     **no home, and their *limitations* are unencoded.** A "last 7 days off `as_of`"
     view carries an implicit precondition — *data must exist in `[as_of-7, as_of]`*
     — that nobody wrote down, so when the view meets data that doesn't satisfy it,
     it silently goes empty and we call it a bug instead of "the view hit its
     stated limit."

   **The fix for the residual is to make subjective views first-class typed objects
   that carry their own definition** (anchor = `as_of`, span, empty-behavior, and
   the data-coverage they require to be meaningful) — **decoupled from any form
   picker.** They need *not* be end-user-configurable; this is an *authoring*
   abstraction (a tree primitive), not a config field. The point is the direction
   of derivation: today the view "lives" inside the QS param declaration + its
   picker control (the RollingDate expr, the control widget) — the picker *is* the
   definition. Invert it: the **view object is the source of truth, and the picker
   control, the analysis-param default, the dataset-param default, and the App2
   binding all *derive* from it** — exactly the project's "tree IS the source of
   truth" spine (Phase L) extended to time.

   This is what structurally kills **C1** (the release blocker's mechanism). C1
   exists *because the view is split across two hand-maintained encodings* — the
   analysis-param default (`RollingDate yesterday`) and the dataset-param default
   (`2999` sentinel) — and they were allowed to disagree. With one view object
   emitting *both*, there is only one default; the two renderers can't diverge
   because there's nothing to keep in sync. The dual-default split becomes
   unrepresentable, not merely "fixed."

   Then the rest follows: the renderer knows what the view assumes; the
   seed-coverage test asserts the scenario satisfies `required-coverage` (a planted
   violation is *guaranteed* visible — the query-window ⟷ plant-placement contract
   becomes checkable, not developer-memory); and invariant/data windows stay owned
   by their definitions, no longer conflated with view choices. Same "encode the
   invariant in the type system, not a post-hoc test" principle
   ([[feedback_invariants_in_types]]) applied to *views*: a view the data can't
   satisfy fails loud at construction/seed, not blank at render.

### The destination: the invariant is the typed spine

Pulling the thread all the way: the real target is to **define, in code/types**, a
small set of linked first-class types with **`Violation` as the currency that
flows** between them (candidate vocabulary — the AP spikes settle the exact shape):

- **`Violation[T]`** — a first-class **detected instance**: invariant `T` broken,
  here, by these rows, this magnitude. It is what `Invariant.detect()` returns and
  what a `View` renders. *This type does not exist today* — detected violations are
  just untyped matview rows.
- **`Invariant[T]`** — the rule/**detector**: `detect(data) -> set[Violation[T]]`
  (today: matview SQL). Also self-validates a candidate (AP.3).
- **`ViolationGenerator[T]`** — the **producer** (today's "plant"/`PlantKind`,
  poorly typed): `emit() -> seed rows` *intended* to manifest a `Violation[T]`.
  Distinct from the `Violation` itself — "how to seed it" ≠ "what got broken".
- **`View`** — the **presenter**: shows `Violation[T]`s over `as_of`±span.

…with the **invariant as the single source of truth** the others reference. The
clean link that replaces developer-memory: **`Invariant[T].detect(
ViolationGenerator[T].emit()) ⊇ the intended Violation[T]`** — checkable, ideally
in-memory (AP.3). This collapses the two-directions problem (§5 opener) into one
spine: the invariant declares its detector + the generators that should trip it +
the views that surface it *together*, so app and generator stop being two pipelines
that must be hand-aligned — they're two projections of one declaration.

(Why split `Violation` from `ViolationGenerator`: today "plant"/"failure" conflates
the *producer* with the *thing produced*. Separating them is what makes the link
above expressible and the seed-coverage assertion a pure function of the two.)

**Evidence the spine is currently fractured into three string/Literal spaces:**
`PlantKind` (20 typed values, generator side) vs `check_type` (~10 *untyped* SQL
strings, invariant + view side) — and they don't even align 1:1: a `drift` plant
trips both `drift` and `ledger_drift`; `xor_variant_missed_firing` +
`xor_variant_overlap` plants both trip the single `xor_group_violation` detector.
The plant→check→view relation is unowned and aligned only by developer memory —
exactly the kind of "two things hitting each other badly" with a hidden missing
abstraction (the invariant spine).

### Is Python expressive enough? (the worry)

For the realistic encoding, **yes — Python + pyright-strict (already the project's
gate) is enough**, and this is an *extension of idioms already in the codebase*
(`PlantKind` `Literal`, `NewType` discipline, `assert_never`, the 4-way agreement
test), not a new capability. Split by tier:

- **Compile-time (pyright) — STRUCTURAL/wiring validation, NOT semantic.** Pyright
  is *not* dependently typed: it cannot validate that data satisfies an invariant,
  ever. What it *can* validate is that the **spine is complete and consistent** — a
  single closed violation taxonomy (`Literal`/`Enum`); `Invariant`, `Failure`,
  `View` parameterized by it; the `invariant → {failures}` and `invariant →
  {views}` maps made *total* and exhaustiveness-checked (`assert_never`) so adding
  an invariant walks you to every failure/view that must grow an arm. That proves
  *"every invariant is fully wired into all three layers"* — **not** that the
  detection logic is right or that any datum conforms. This structural win is the
  same mechanism that kills C1 (one declaration → one default).
- **Boundary (smart constructors) — the only "value-fitting" Python offers.**
  Parse-don't-validate: a `Failure[Drift]` can only be *constructed* via a
  constructor that runtime-checks the drift shape, so the **type thereafter
  witnesses "a check happened"** even though the check ran at runtime. Illegal
  states unrepresentable downstream; the proof is "validated at the boundary," not
  a compile-time proof of the property.
- **Runtime (tests/property) — the actual invariant.** "Does this data violate
  invariant X?" is *running the invariant* (the matview query / a predicate). "Does
  the planted failure trip it, and land in a view's `as_of`±window?" — runtime. No
  pragmatic language type-checks SQL output or date-containment (that needs
  dependent/refinement types — Idris, F\*, Liquid Haskell — not Rust either). This
  is already the home turf of the 4-way agreement + `TestScenarioCoverage` tests;
  re-key those off the spine.

So, scoped precisely: Python+pyright gives **structural completeness + closed
taxonomy + boundary-validated types**; the semantic "does data fit the invariant"
is runtime, as it must be anywhere short of a proof assistant. The expressiveness
fear is real only at the dependent-types level, which isn't the target. Python
won't be the wall — but it also won't *prove* the invariants; it wires and witnesses.

### Candidate path — UNCERTAIN, needs spikes (not a locked plan)

The *destination* (invariant-as-spine) is clearer than the *path to it*. Evolving
what exists today — invariants as hand-written matview SQL, the
`PlantKind`/`check_type` string split, views wired into the tree + QS params — into
the typed spine is itself a research problem: we don't yet know the right
decomposition or order, and a confident linear plan here would be a vibes-lock.
Treat the below as a *hypothesis to validate by spike*, expecting it to change:

- **(likely first) Frame (D1, `as_of` in config)** — smallest, unblocks the
  immediate mess, low coupling to the rest.
- **View primitive (D5)** — source of truth; picker + defaults derive; kills C1.
- **Invariant spine (D6, the destination)** — unify the violation taxonomy, make
  `invariant → {failures, views}` total + asserted. The biggest lift; almost
  certainly last, on the foundation the first two lay.

**The honest first move is therefore a spike, not phase 1 of a build.** Phase
breakdown (spike-gated) lives in PLAN.md under Phase AP.

### AP.3 result — the make-or-break is GREEN (2026-05-22)

The spike ran first, because if an invariant *can't* self-validate a violation the
whole spine (and both contingent payoffs below) doesn't pay off. It holds.
`tests/unit/test_ap3_invariant_self_validation.py` proves, in-process and with **no
DB server**, the round-trip in both directions across three complexity classes:

| class | invariant (real emitted matview) | dirty detect | clean detect |
| --- | --- | --- | --- |
| arithmetic | `drift` (stored − Σ posted legs) | drift = 5.0 ✓ | ∅ ✓ |
| windowed | `inv_pair_rolling_anomalies` (rolling-2-day z) | spike pair z=4.36 / "4+ sigma" ✓ | z=0.0, ∅ ✓ |
| recursive | `inv_money_trail_edges` (`WITH RECURSIVE` walk) | depth-2 edge ✓ | depth 0 only, ∅ ✓ |

What makes it load-bearing: the detector under test is the **real** SQL from
`emit_schema` + `refresh_matviews_sql` (SQLite dialect) — the same definition QS /
App2 / PDF read in production. There is no re-encoded detection logic in the test;
`detect()` is a thin read of the matview output. So `Invariant.detect(
ViolationGenerator.emit()) ⊇ intended` (and `detect(clean) ⊉ intended`) is checkable
against the production detector, in-memory, in 0.3s. The spine's core bet — one
detector definition serving both detection and self-validation — is confirmed.

**Three findings that constrain the rollout (the spike earned these):**

1. **A focused `ViolationGenerator` must carry the detector's structural
   preconditions, not just the breach.** drift needs `account_scope='internal' AND
   account_parent_role IS NOT NULL` + the leg posting inside `[business_day_start,
   end)`; the recursive trail needs each chain member to be a *complete* 2-leg
   Posted transfer or the edge silently drops. These preconditions are exactly what
   a developer forgets today (→ silent-empty matview). The generator type has to
   own them — which is the point ([[feedback_invariants_in_types]]).
2. **`ViolationGenerator[windowed]` is intrinsically `(baseline + spike)`, never a
   single row.** A statistical invariant's z-score is computed across the whole
   population; a lone outlier among *n* points has a hard z ceiling of ≈√n, so a
   stable ≥3σ flag *requires* the generator also emit a baseline population (20 quiet
   pair-days here). The clean counterpart is the same topology with the spike
   magnitude normalized — the violation is purely the magnitude. This is a real
   shape constraint on the generator taxonomy, not an artifact of the spike.
3. **The z *threshold* is a `View` concern, not the invariant's.** The matview emits
   the z-score; "≥3σ is a violation" is the analyst band the view applies. The spike
   folded them into one assertion, but the spine should keep the detector emitting
   the continuous signal and let the `View` own the band — confirming the
   §5 three-sources split (invariant-derived signal vs subjective view threshold).
4. **(the biggest — reshapes the generator type) Invariants split Local vs
   Populational, and the `ViolationGenerator` shape follows from the kind.** A
   *Local* violation is absolute — per-row/per-group, definable from rows alone
   (`drift`, `overdraft`, `limit_breach`, `stuck_pending`); its generator is a
   constructor `() -> rows` and yields a *minimal standalone witness*. A
   *Populational* violation is **relative to a distribution** (`z = (this −
   pop_mean)/pop_stddev`) and **cannot be generated in a vacuum** — so its generator
   must consume a baseline stream: `Stream -> Stream` (perturb/amplify, not "filter"
   — a filter only selects). Finding #2 is the symptom; this is the cause. Two
   consequences for AP.2:
   - **Unification option:** make *every* generator `Stream -> Stream` (Local ones
     ignore the input). The baseline stream is exactly today's 90-day
     `emit_baseline_seed`; a scenario is a fold of transforms over it — which is
     *literally* what `emit_full_seed` does (baseline + layered plants). Then
     self-validation is `detect(gen(baseline)) ⊇ intended ∧ detect(baseline) ⊉
     intended`, and **generator and detector become duals over one stream** (detect
     filters the stream for breaches; gen perturbs it to plant one). Tidy, and
     matches the existing plant-layering.
   - **The cost / decision:** uniform `Stream -> Stream` loses the *minimal
     standalone witness*, which is what makes the docs/teaching payoff shine ("drift
     in 2 rows"). Resolve by having the `Invariant` **declare its kind (`Local |
     Populational`) and gate the valid generator shape on it** — a standalone-row
     generator for a Populational invariant is then *unrepresentable*
     ([[feedback_invariants_in_types]] one level up). **DECISION for AP.2:** is
     generator-shape one uniform `Stream -> Stream`, or kind-indexed (`() -> rows`
     for Local, `Stream -> Stream` for Populational)? Flagged, not silently
     deferred — AP.2's spike settles it.

The spine vocab (`Violation` / `Invariant` / `ViolationGenerator`) lives **local to
the spike**, deliberately not promoted to `src/` — the rollout decides the
production home + shape. AP.3 answered only "does the round-trip hold and does
Python express it cleanly?" Both: yes.

### AP.3 extension — the generator side crystallized (the request shape + the state medium)

Pushing on the generator side (in design dialogue) sharpened it well past finding
#4, and the spike grew four more passing tests that pin the conclusions. Two layers:

**(a) The request is `(invariant kind, shape selector)`, and the invariant owns its
own manufacture.** The blind `() -> rows` framing was wrong (finding #4 hinted; this
nails it): a generator *cannot* be authored without the shape. `limit_breach` is the
disproof — its cap comes from the L2's `LimitSchedule` (inlined CASE), so a made-up
`(parent_role, rail)` is **inert** (NULL cap → trips nothing; pinned by
`test_limit_breach_generator_is_not_constructible_without_a_shape`). So the real
shape is **`Invariant[T].scenario_for(shape, selector) -> ViolationGenerator[T]`**,
where the *selector is in shape vocabulary* ("drift for `account_role=X`",
"breach all outbound caps") and the invariant resolves it to concrete coordinates,
**failing loud** if the shape can't host it (no such role / no declared cap). The
invariant thus owns BOTH halves — `detect` (find itself) and `scenario_for`
(manufacture itself) — which is what makes "the invariant is the single source of
truth" literal. Magnitude is expressed **relative to the shape-derived threshold**
(`cap + ε`), so generators are portable across re-skins and **fuzzed shapes**
([[feedback_fuzzer_as_property_testing]] payoff: `random_l2_yaml(seed)` × the
scenario = valid planted violations in arbitrary topologies, self-validated). A
**scenario** is then a composition of such requests, fanning a kind across the
shape's declared coordinates (pinned by
`test_scenario_composes_many_generators_across_the_shape`).

**(b) The medium is STATE, not rows — and this is the same realization as D1.** The
inverse of "plant a violation" is "produce *non-violating* data", and conforming
data is not the absence of plants — it is data that **satisfies the invariants**,
which means it is a **consistent state evolution**: a daily balance equals the
accumulated signed legs, a chain progresses leg by leg, a pending leg posts. So
generation is fundamentally a **stateful temporal simulation** — step the
institution forward day by day maintaining consistent state — and **the invariants
are that simulation's conservation laws** (drift = 0 ⇔ stored balance equals the
state computed from legs). In that frame:
- **non-violating data = the simulation running clean** (every law holds); it is
  first-class, not "everything we didn't break";
- a **`ViolationGenerator[T]` is a perturbation** that breaks law `T` at a point —
  and may **propagate** through state (an overdraft persists, a missed chain leg
  cascades);
- the baseline a *Populational* generator perturbs (finding #4) **is this state
  stream** — confirmed by the co-mingling note in the scenario test (a windowed
  generator's population is the whole scenario, not a private fixture).

**This is the same act as D1 from the other side.** Owning `as_of` (D1) and owning
the *state evolution up to `as_of`* are one thing: the state at `as_of` is the fold
of all flows up to it; "latest balance" is the terminal state of the simulation, not
a date filter. That is *why* D1 is the keystone — own time as an evolution and the
generators become simulators, non-violating data becomes the clean run, and a view's
"`as_of` ± window" is a window onto the state stream. Today's `emit_baseline_seed`
is already an implicit, imperative, monolithic version of exactly this simulator
(it computes running balances); the spine makes it explicit, typed, and
per-invariant-decomposed.

**The honest LIMIT of AP.3 (→ the AP.2 core).** The spike proved *detection* against
hand-set, **single-day, stateless** data; it did **not** simulate multi-day state
evolution or propagation. So generation-as-stateful-simulation is a gap AP.3
surfaces *by omission* — and it is the heart of AP.2's generator side: a generator
is not `rows` nor even `Stream -> Stream` of independent rows but a **state step**
(`State -> (flows, State')`) folded forward, clean for baseline, perturbed for a
violation. AP.2 must settle: does the generator carry state, and is "non-violating"
the same generator with perturbation off?

### AP.2 result — the generator is a stateful fold; non-violating = perturbation off (2026-05-22)

`tests/unit/test_ap2_stateful_generator.py` (5 tests, in-process, **no DB server**,
real emitted `drift` matview) closes the gap AP.3 left. It steps one leaf account
forward over three days as a fold — each day's emitted stored balance IS the running
`State'` (Σ recorded legs so far) — and turns the perturbation knob. All three AP.2
questions land:

| case | `detect` (day → drift) | what it answers |
| --- | --- | --- |
| clean 3-day fold | `{}` | **Q1** — state is carried; the fold satisfies the law every day |
| recorded extra flow (+500, folded into stored) | `{}` | **Q2** — a real extra flow conforms; non-violating ≠ "no activity" |
| state-snapshot blip (+7 on day 1, balance carried clean) | `{D1: 7.0}` | **Q3** — **local**; detector is memoryless in `stored` |
| unrecorded flow (+13 on day 1, not folded) | `{D1: −13, D2: −13}` | **Q3** — **propagates forward**, never backward to D0 |
| minimal witness (1-day fold + blip) | `{D0: 5.0}` | **shape decision** — uniform state-step subsumes `()→rows` |

**The findings (each pinned by a passing test):**

1. **Q1 — YES, the generator carries state.** A `ViolationGenerator` is a fold
   `State -> (flows, State')` over days; the daily-balance row it emits is literally
   the running `State'`. This is not `Stream -> Stream` of *independent* rows
   (finding #4's intermediate framing) — the rows are *coupled by the fold*. Today's
   imperative `emit_baseline_seed` (which already computes running balances) is the
   un-typed, monolithic version of exactly this.
2. **Q2 — YES, non-violating is the same generator with the perturbation off, AND a
   *recorded* extra flow is equally non-violating.** Conformance is **flow/state
   agreement**, not the absence of activity. A violation is never "a flow"; it is the
   *disagreement* between the flow stream and the stored state. So "clean run" is
   first-class and parameterized by one knob (`perturb.kind == "none"`), not defined
   negatively as "everything we didn't break".
3. **Q3 — propagation is governed by WHICH SIDE you break, and it is predictable from
   the detector SQL (the spike verifies the prediction, doesn't discover it).** The
   real `drift` detector computes `computed_balance(D) = Σ posted legs WHERE posting
   ≤ business_day_end(D)` — cumulative over the absolute leg stream, re-derived per
   day, **no recurrence on `stored(D-1)`**. Therefore:
   - **State-snapshot perturbation** (a one-day stored typo, running balance carried
     clean) → **local**: only that day drifts, because the next day re-derives
     `computed` from the leg stream and `stored` is back on the fold.
   - **Unrecorded-flow perturbation** (a leg in the stream not folded into stored) →
     **propagates forward** to every later day (cumulative `computed` carries the
     stray leg; `stored` stayed on the clean fold) and **never backward** (days before
     the leg don't include it). A missed posting is a *persistent* break; a balance
     typo is a *transient* one. The generator taxonomy must distinguish them because
     they model different real failures (lost ETL leg vs balance-feed glitch).
4. **The shape decision finding #4 left open is settled: ONE uniform generator shape
   — the state-step fold — NOT a kind-indexed `()→rows | Stream→Stream` split.** The
   "minimal standalone witness" finding #4 feared losing (the docs/teaching payoff,
   "drift in 2 rows") is recovered as a **degenerate one-day simulation** — the same
   generator type, its shortest fold (`test_minimal_witness_is_a_one_day_simulation`).
   Local vs Populational survives **only as how the `Invariant` READS** the emitted
   stream (per-group rows vs across-distribution z-score), not as two generator types.
   This is the cleaner answer: the medium is the state-fold for *everything*;
   "Populational" just means the detector's window spans the stream the fold produced
   (the co-mingling note in the AP.3 scenario test is the same point from the read
   side).
5. **The carried state is `(balances, active-violation-set)`, not balances alone —
   and that is what makes effects checkable and violations stackable** (the
   refinement that closed AP.2). A generator that carries only the balance can emit
   rows and *hope* the detector finds them (AP.3's bogus-shape `limit_breach` plants
   rows that trip nothing — silently inert). Carry the active-violation set as state
   and a step's **effect is a delta**: `detect(after) − detect(before)`. Three
   consequences, each pinned:
   - **Effect is observable.** `violation_trajectory` refreshes+detects after each
     day (mirroring per-load ETL); the snapshot list IS the violation set carried
     through the fold. An inert step shows ∅ delta — the generator *knows* it didn't
     land instead of believing it did.
   - **Lifecycle / resolution is first-class.** A violation persists as carried state
     until a corrective booking closes it (the AN.1 supersession/`TechnicalCorrection`
     shape). `test_correction_closes_forward_propagation`: an unrecorded leg booked a
     day later stops the forward propagation (`{D1,D2}` → `{D1}`) while the historical
     breach correctly remains — the correction's measurable effect is exactly the
     closed `{D2}`.
   - **Scenarios STACK.** Composition (AP.3's spatial fan-out) becomes temporal: each
     perturbation adds its own violation to the carried set without masking the
     others (`test_stacked_violations_accumulate_without_interference`: `{}` →
     `{D1}` → `{D1,D2}`, each keeping identity). A scenario is a fold of perturbations
     over `(balances, violations)`, and you can assert the running set after each step
     — not just the final plant.

**The honest LIMIT of AP.2 (→ AP.0 / AP.1).** AP.2 simulated ONE account's own
balance fold. It did **not** simulate *cross-account* conservation (a transfer's two
legs must net to zero across two accounts' folds; `ledger_drift` rolls children into
a parent) — i.e. the state is really a *vector* over accounts with coupling
constraints, and a propagating perturbation can cross account boundaries. Nor did it
own `as_of` as the terminal state of the fold (that is AP.0/D1 — and AP.2 confirmed
the duality: "latest balance" = the fold's terminal `State'`, not a date filter). The
vector-state simulator is the next layer down, but AP.2 proved the core mechanic
(stateful fold, perturbation-as-knob, predictable propagation) on the scalar case.

### AP.0 result — own the `as_of` frame: GO, the dual-default is collapsible (2026-05-22)

`tests/unit/test_ap0_as_of_frame.py` (6 tests, in-process) spikes D1 on the balance-
date surface and a coupling survey maps the blast radius. Verdict: **GO** — a single
owned `AsOfFrame` (`as_of` + `window_days`) makes the C1 dual-default *unrepresentable*
and gives determinism + ends-at-now from one code path. What the spike pins:

- **(a) One frame → both renderers, equal by construction.** `qs_window_end() == as_of
  == app2_date_to()` (and the starts likewise). Today (surveyed) these are two
  hand-maintained encodings — `truncDate('DD', now())` on the QS side, a `1900`/`2999`
  sentinel on the App2 side — that are *allowed to disagree* (that disagreement IS C1).
  With one frame there is nothing to keep in sync: the divergence is unrepresentable,
  not merely fixed.
- **(b) Determinism + ends-at-now, one code path.** `AsOfFrame.locked()` (anchor =
  the existing `date(2030,1,1)`) and `AsOfFrame.live()` (anchor = `today`) differ ONLY
  in the bound anchor value; every derivation is the same method. Locked = byte-stable,
  live = ends-at-now, for free — the §8 determinism story falls out of the frame.
- **(c) Generator data-end and view window share ONE value.** The fold's terminal
  balance day == `frame.as_of` under both bindings (the AP.2 duality made concrete:
  "latest" is the terminal `State'`, not a `now()` filter), and a plant at `as_of` is
  inside the view's `[window_start, as_of]` *by construction* — the plant ⟷
  query-window contract becomes a property of the frame, not developer-memory. A
  too-narrow view fails its own stated coverage (the residual-tension hook for AP.1).

**Coupling inventory (the survey — the real blast radius):** the two clocks are
genuinely independent today.
- *Generator side is ALREADY pinned* — `cli/data.py::_CANONICAL_LOCK_ANCHOR =
  date(2030,1,1)` threaded as `anchor=` through `_helpers.build_full_seed_sql` →
  `seed.py` → `auto_scenario.py`; locked seeds never read wall-clock. Wall-clock
  fallback survives only on the ad-hoc / trainer paths (`seed.py:704/1247`,
  `auto_scenario.py:140`, `tg_cache.py:98` `window_end → date.today()`).
- *Dashboard side is fully independent, no shared helper* — QS rolling-date exprs live
  per-app (`l1_dashboard/app.py:1582-83,2076` 7-day + yesterday; `executives/app.py:
  287-88` 30-day); App2 sentinels live in `common/sql/app2_filters.py:45-46`
  (`1900-01-01`/`9999-12-30`) + `l1_dashboard/datasets.py:891` (`2999-12-31` latest) +
  `l2_flow_tracing/app.py:427-28` static bounds. **These two encodings never meet** —
  exactly why C1 was possible.
- SQL `CURRENT_TIMESTAMP` appears only in the `stuck_*` age matviews
  (`schema.py:531`) — that is *data/deadline-derived* window semantics (§5's second
  source), correctly owned by the invariant, NOT a view default; leave it.

**Rollout shape for AP's frame layer (the go/no-go output):**
1. `AsOfFrame` (or `as_of: date` + `window`) lands in **config** — the existing home
   that binds the L2 shape to a deployment (`common/config.py`, alongside the
   `TestGeneratorConfig.end_date`/trainer `window_end` it subsumes). `as_of` is the
   temporal half of that same binding; `locked`/`live` are the two bindings.
2. The generator reads `frame.as_of` as its anchor — a *rename + funnel* of the
   already-threaded `anchor=`, not new plumbing; collapse the ad-hoc `date.today()`
   fallbacks (4 sites) into "no frame ⇒ live frame".
3. The QS rolling-date exprs and App2 sentinels are **derived from the frame**, not
   authored per-app — this is the AP.1 view-primitive's job (the frame supplies the
   anchor; the view supplies the span + empty-behavior). C1's fix is *structural*:
   one view object emits both the analysis-param default and the dataset-param
   default, so they cannot disagree.
4. The `stuck_*` `CURRENT_TIMESTAMP` matviews stay as-is (deadline-derived, owned by
   the invariant) — the frame is for *view anchors*, not invariant-intrinsic windows.

**Honest limit of AP.0.** The spike proved the frame VALUES agree across renderers
in-process; live-rendered QS/App2 parity stays behind the parked deploy/e2e layers.
And it modeled the window as a single `[as_of-span, as_of]` look-back — the richer
view taxonomy (latest-day, today-only, rolling-N, empty-behavior, required-coverage)
is AP.1, which the `contains()`/coverage-limit tests here only foreshadow.

---

## 6. The mechanism (for decision)

**Principle: one owned temporal frame (§5), read by both directions; one
vocabulary for "all"/"latest"; and a single rule for which default wins per
renderer.**

0. **Own the temporal frame — in config.** `as_of` (+ `window`) lives in
   **config**, the same binding that already instantiates the L2 shape for a
   deployment (L2 = shape; config = this deployment's binding of it; `as_of` = the
   temporal half of that binding). Both the generator and the dashboards read it.
   `as_of` defaults to `now()` (prod) and is pinnable to the fixed anchor
   (demo/test). Replaces every direct `now()` / `date.today()` /
   RollingDate-off-`now()` with "off `as_of`". This is the keystone; the rest are
   how it lands per surface.

1. **Generation contract = `(as_of, window)`, not `(end_anchor, lookback)`.**
   Lock the *inputs* `(as_of, window, seed)` → byte-identical SQL (determinism).
   Live deploy passes `as_of = now()` → data **ends at `now()`** by the same
   generator. The window is the fixed shape; `as_of` is the single thing that
   floats. The dashboards read the *same* `as_of` (§6.0), so "where the data is"
   and "where the dashboard looks" are the same point by construction — kills C3
   at the root under both bindings, no `now()` guessing on either side.
2. **One sentinel vocabulary.** A `common/sql` helper pair — e.g. `MATCH_ALL`
   (an unbounded bracket) and a `latest`-day idiom — replacing `2999-12-31`,
   `1900↔2099`, and the ad-hoc App2 binds. Self-documenting; one place to reason
   about it. (Addresses C2.)
3. **Default-resolution rule (interim C1 fix; §6.5 is the structural one).** Until
   the view primitive lands, the two hand-maintained defaults must be kept in sync
   by hand: for any param that is analysis-declared *and* dataset-mapped, the
   **analysis default is authoritative** (QS wins that way), so the dataset default
   must be set equal to it, OR the param must not be analysis-declared where the two
   would differ. (§6.5 removes this chore entirely — one view emits both.)
   Concretely for the balance date, pick ONE of —
   - **(a)** analysis default = the same sentinel as the dataset (QS then takes the
     SQL latest-day fallback, matching App2). Trade-off: the picker control shows
     the sentinel date until the user picks. *Cosmetically poor with `2999`; fine
     if the sentinel is "latest day with data" derived per #1.*
   - **(b)** SQL fallback keys off "**picked day has no rows for this account →
     latest day**" instead of a magic sentinel; the analysis default can then stay
     a real, sensible recent date and still never shows empty. Trade-off: picking a
     real-but-empty day shows latest instead of an empty statement (changes
     "show me exactly 5/15" semantics).
   - **(c)** data-drive the analysis default at generate time to the instance's
     latest data day (per #1). No sentinel in the UI at all. Trade-off: the default
     is baked at deploy and goes stale as data advances past it (re-deploy
     refreshes it; acceptable for a delete-then-create pipeline).
4. **Rolling vs static: dissolved by §6.0/§6.1.** With every range read off
   `as_of`, there's no rolling-vs-static choice left — all four apps reference the
   same frame; "static" was only ever L2FT's workaround for `now()` being wrong
   against locked data.
5. **Classify every window by source, and give *views* a typed home (§5).**
   Invariant windows stay in the matview SQL; data/deadline windows stay in the
   L2/data; **subjective views become first-class tree primitives** carrying
   `(anchor=as_of, span, empty-behavior, required-coverage)` — the *source of
   truth*, from which the picker control, the analysis-param default, the
   dataset-param default, and the App2 binding all **derive** (not the reverse).
   Need not be end-user-configurable — it's an authoring abstraction. This kills C1
   structurally (one view → one default → renderers can't diverge) and makes
   `required-coverage` the checkable contract the seed-coverage test asserts. It's
   the window-semantics layer; follows once the frame (#0) lands.

---

## 7. Decisions needed (open)

- **D1 (keystone).** Own the temporal frame (§6.0) + the `(as_of, window)`
  generation contract (§6.1)? Everything else falls out of this. Subsumes the
  anchor-convergence question: the anchor *is* the demo binding of `as_of`, so
  locked (fixed `as_of`) and live (`as_of = now()`) stop being separate references.
  `as_of` lives in **config** (the existing L2-instantiation binding).
- **D2 (release blocker — cause UNCONFIRMED, see §8).** The QS Daily Statement
  KPIs are missing, but the date-default story is *not* a sufficient explanation:
  the KPI summary dataset and the (rendering) transactions table **share the same
  `pL1DsBalanceDate` param**, so a date filter that emptied one would empty both.
  Re-confirm at the live QS layer (`describe_data_set` on the summary +
  embed/spinner check) before choosing a fix. If it *is* date-related, prefer the
  frame (§6.0) + option (b) "no rows for the picked day → latest"; options (a)/(c)
  are dispreferred on the §8 determinism grounds.
- **D3.** One sentinel vocabulary in `common/sql` — yes, and what names?
- **D4.** Whether `window` is an L2/config field (author-controlled per instance)
  or a generator constant (your open question).
- **D5 (the residual smell — next step-back after D1).** Adopt a **view tree
  primitive** as the source of truth for subjective view-windows (picker control +
  all param/dataset/App2 defaults derive from it; §6.5), keeping invariant- and
  data/deadline-windows owned by their definitions? Not necessarily
  user-configurable — an authoring abstraction. This is the structural fix for "we
  don't know what should be where" AND for C1 (one view → one default), but it's a
  larger effort; land once the frame (D1) is proven and the release is unblocked.
- **D6 (the destination — biggest lift, do last).** Define **invariants in
  code/types** as the single spine, with **failures** (plants) and **views** both
  *referencing* an invariant (§5 "destination"). Unify the fractured
  `PlantKind`/`check_type`/view-filter spaces into one closed violation taxonomy;
  make `invariant → {failures}` and `invariant → {views}` total + exhaustiveness-
  checked (compile) and linkage-asserted (runtime, via the 4-way agreement +
  `TestScenarioCoverage`). Python + pyright-strict is expressive enough (§5 "is
  Python enough"). This collapses the two-pipelines hand-alignment into one
  declaration; build it on the D1+D5 foundation.

---

## 8. Intersection with test-data determinism / seed locking

The date model is co-mingled with the determinism story, and that's the deeper
reason the static-vs-rolling split exists. Two time references are in play and
they are deliberately *different*:

- **Determinism reference = `2030-01-01`.** The seed SQL is the byte-locked
  artifact (`tests/data/_locked_seeds/*.sql`, gated by
  `test_locked_seed_matches_fresh_emit`). Byte-identity demands a *fixed* anchor,
  so `data lock` pins `_CANONICAL_LOCK_ANCHOR = date(2030, 1, 1)` and the 90-day
  baseline + plants all derive from it. Data lives ~Oct 2029 – Jan 2030.
- **Deploy reference = wall-clock today.** `data apply` (live + e2e) passes no
  anchor → falls back to `now()`. Data lives ~`[today-90, today]`.

So "where the data is" is **2030 in the determinism context and today in the
deploy context.** The dashboard JSON is *not* byte-locked — it's structurally
tested (tree-walk) — but it must render correctly against **both** data sets:
the 2030 locked data the unit/json layer seeds, and the today data a real deploy
seeds. That dual obligation is what each default strategy passes or fails:

| Default strategy | Emission (deterministic?) | Correct vs 2030 locked data | Correct vs today live data |
|---|---|---|---|
| **RollingDate `now()-N`** (L1 #4, balance #5, Exec #6) | Yes — the *expression string* is fixed | **No** — looks at ~2026, data at 2030 | Yes |
| **Static sentinel** (`2999`, `1900↔2099`) (#5b, #7) | Yes — constant | **Yes** — anchor-agnostic (match-all / SQL-latest) | Yes |
| **Data-derived static** (option (c): bake "latest data day") | **No** — embeds a concrete date that moves with the anchor | only if generated at 2030 anchor | only if generated at today |

Three consequences that reframe the §5/§6 decisions:

1. **The static sentinels are determinism-motivated, not just a hack.** `2999` /
   `1900↔2099` are the *only* strategy that's both deterministic in emission and
   correct under both anchors. L2FT almost certainly chose static for this reason.
   The wart is purely how `2999` *surfaces in the UI* (C1), not the technique.
2. **Option (c) is determinism-hostile.** Baking the latest data day into the
   analysis default makes dashboard emission depend on the seed anchor, coupling a
   currently-decoupled pair (dashboard JSON ⟂ seed anchor). It would also be wrong
   unless generated against the same anchor as the deployed data — i.e. it forces
   the two anchors to converge. Drop (c) unless we deliberately unify anchors.
3. **RollingDate defaults are silently anchor-fragile.** They pass today only
   because live `data apply` happens to seed near `now()`. They are *wrong* against
   the locked 2030 data — a latent trap for any preview/test that renders a
   dashboard over locked-seed data, and part of why "single-day yesterday" (C5)
   was fragile.

**This is exactly why the §5 frame is the real fix, and why it's free on
determinism.** The only thing that floats is `as_of`, and it's an explicit
*input*: lock fixes it (byte-identical output), live binds it to `now()` (data
ends at now). Everything else — window, plant offsets, dashboard ranges — is a
deterministic function of `(as_of, window, seed)`. So:
- "latest day" / "full span" are read off the frame, correct under *any* binding,
  and need no magic far-future constant in the UI;
- determinism holds because emission is a pure function of the inputs, not of
  wall-clock time;
- the static-vs-rolling inconsistency (C3) dissolves — there's nothing to choose.

This is the determinism face of decision **D1** (§7); no separate decision is
needed here. The remaining open sub-question is **D4** — is `window` an L2/config
field or a generator constant.

### Payoff (AP.3 GREEN → now load-bearing): byte-locked seed SQL can retire

The locked seed SQL (`tests/data/_locked_seeds/*.sql` +
`test_locked_seed_matches_fresh_emit`) is doing **two jobs mashed into one byte
check**: proving (a) the generator is *deterministic* and (b) the data still has
the right *semantic content* (the planted violations are present). Both indirectly,
by byte-matching a checked-in golden — which is why it's brittle (couples to anchor
dates, dialect formatting) and forces the **per-dialect re-lock dance** on every
intentional change (AN.3, AO.1.impl, and most "re-lock seeds" toil in PLAN).

AP.3 landed green (§5), so self-validation works across all three complexity
classes — the contingency is discharged. The two jobs **split and both get
*direct* checks**, and the golden files can retire:

- **(b) semantic content → direct.** `Invariant[T].detect(
  ViolationGenerator[T].emit()) ⊇ intended Violation[T]` asserts the property we
  actually care about (the violation is present + detected), not a byte proxy for
  it. This is *stronger* than byte-identity — byte-identity never checked that the
  data still tripped the invariants.
- **(a) determinism → direct, lightweight.** Emission is a pure function of
  `(as_of, window, seed)`, so determinism is an emit-twice-equal or input-keyed
  *hash* — no checked-in per-dialect SQL, no re-lock dance.

So: **determinism stays load-bearing; byte-locked seed SQL does not.** AP.3
discharged the contingency — invariants self-validate in-memory across arithmetic,
windowed, and recursive (the windowed/recursive cases were the doubt, and both
held). The rollout can delete the `_locked_seeds` mechanism and the per-dialect
re-lock toil, replacing it with semantic coverage (`detect(emit) ⊇ intended`) + a
determinism hash — sequenced after the spine itself exists.

### Payoff: training + docs scenarios become declarative (and can't lie)

Today a scenario is hand-built: the Studio trainer toggles `PlantKind`s and places
them; a docs walkthrough hand-describes a scenario + a `TestScenarioCoverage`
assertion, and walkthrough rewrites have to *dogfood the dashboard* to catch drift
([[project_walkthrough_rewrites_dogfood]]). With the spine, a **scenario is a
declarative composition of `ViolationGenerator`s**, and the views that surface them
are *known* (each `Violation[T]` → its `View`). Two consequences:

- **Trainer**: building a scenario = picking `ViolationGenerator`s; the seed
  derives, and self-validation (AP.3) *guarantees* the scenario actually exhibits
  what it claims — a trainer scenario can't silently fail to demonstrate its point
  (the empty-dashboard-bug class disappears for authored scenarios too).
- **Docs**: a walkthrough for invariant `T` is *generated/validated from the spine*
  — `T`'s definition + the `ViolationGenerator` that produces the example + the
  `View` that shows it — and self-validated, so **the example provably exhibits the
  violation: the doc can't lie.** This is the direct enabler for the Greater Plan's
  "make the core domain model the source of the documentation site" and X.6 ("stop
  the documentation lying"): the invariant spine *is* that model, and the
  walkthrough-dogfooding toil collapses into a typed, self-validating link.

## 9. Scope note

This audit is intentionally analysis-only. The AO.10 Oracle fix (ORA-00932,
`day_text`) and AO.S2.a (trainer pin) already landed and are independent of these
decisions. The QS balance-date blocker (C1/D2) is the one item gating the release;
everything else is consolidation that should follow the model chosen here.
