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
   that carry their own precondition** (anchor = `as_of`, span, empty-behavior, and
   the data-coverage they require to be meaningful). Then: the renderer knows what
   the view assumes; the seed-coverage test can assert the scenario satisfies it
   (so a planted violation is *guaranteed* visible — the query-window ⟷
   plant-placement contract becomes checkable, not developer-memory); and the
   invariant/data windows stay owned by their definitions, no longer conflated with
   view choices. This is the same "encode the invariant in the type system, not a
   post-hoc test" principle ([[feedback_invariants_in_types]]) applied to *views*:
   a view that can't be satisfied by the data should fail loud at construction/seed
   time, not render blank.

This audit recommends proving the frame (D1, `as_of` in config) first — it's the
anchor layer and unblocks the immediate mess — then treating **typed views with
encoded preconditions** as the next step-back for the window-semantics layer.

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
3. **Default-resolution rule (addresses C1, the blocker).** For any param that is
   analysis-declared *and* dataset-mapped: the **analysis default is
   authoritative** (QS wins that way), so the dataset default must be set equal to
   it, OR the param must not be analysis-declared where the two would differ.
   Concretely for the balance date: pick ONE of —
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
   L2/data; **subjective views become first-class typed objects** carrying
   `(anchor=as_of, span, empty-behavior, required-coverage)`. The required-coverage
   precondition is what the seed-coverage test asserts (planted violation
   guaranteed visible) and what the renderer reasons about (graceful empty vs
   silent blank). This is the window-semantics layer; it follows once the frame
   (#0) lands.

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
- **D5 (the residual smell — next step-back after D1).** Adopt typed **views**
  with encoded preconditions (§6.5) as the home for subjective view-windows,
  keeping invariant- and data/deadline-windows owned by their definitions? This is
  the structural fix for "we don't know what should be where" — but it's a larger
  effort and should follow once the frame (D1) is proven and the release is
  unblocked.

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

## 9. Scope note

This audit is intentionally analysis-only. The AO.10 Oracle fix (ORA-00932,
`day_text`) and AO.S2.a (trainer pin) already landed and are independent of these
decisions. The QS balance-date blocker (C1/D2) is the one item gating the release;
everything else is consolidation that should follow the model chosen here.
