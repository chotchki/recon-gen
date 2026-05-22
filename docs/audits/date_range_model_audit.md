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

## 5. Proposed model (for decision)

**Principle: one source of truth for the data's calendar extent, one vocabulary
for "all"/"latest", and a single rule for which default wins per renderer.**

1. **Make the seed's calendar extent discoverable** (e.g. `[min, max]
   business_day` for the instance) so every default can derive from *where the
   data actually is* instead of guessing via `now()`. Kills C3 at the root: a
   dashboard default of "the data's latest day" / "the data's full span" is
   correct under *both* the live and locked anchors.
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
4. **Rolling vs static: pick one policy per "range" param** and apply it
   uniformly across L1/Exec/L2FT — driven by #1 (derive from data extent) rather
   than the current mix.

---

## 6. Decisions needed (open)

- **D1.** Adopt #1 (discoverable seed calendar extent as the single source)? This
  is the keystone — most other simplifications fall out of it.
- **D2.** Balance-date fix: option (a), (b), or (c) from §5.3? (This unblocks the
  release; (b) or (c) avoid the `2999` UI wart.)
- **D3.** One sentinel vocabulary in `common/sql` — yes, and what names?
- **D4.** Unify rolling-vs-static range defaults across all four apps?
- **D5.** Should locked-seed (2030) and live-seed (today) anchors converge, or is
  the split deliberate (byte-identity needs a fixed anchor)? If they stay split,
  every RollingDate default is wrong under the locked anchor — which only #1 (or a
  fixed anchor everywhere) resolves.

---

## 7. Intersection with test-data determinism / seed locking

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
   dashboard over locked-seed data, and the deep reason "single-day yesterday"
   (#5) was doomed.

**This elevates the keystone (D1) to the real fix:** seed-lock anchor, live-seed
anchor, and every dashboard "where to look" default should derive from **one
scenario clock** — the data's actual `[min, max] business_day` extent — instead
of three independent references (`2030`, `now()`, and per-app rolling/static
guesses). With a single clock:
- "latest day" / "full span" are computed from the data, correct under *any*
  anchor, and need no magic far-future constant in the UI;
- determinism holds because the clock is a function of the (locked) data, not of
  wall-clock time;
- the static-vs-rolling inconsistency (C3) dissolves — there's nothing to choose.

### New decision

- **D6.** Adopt a single **scenario clock** (derive all dashboard date defaults +
  the live-seed anchor from the data's `[min,max]` business-day extent), and
  decide whether the locked-seed anchor (`2030`) folds into it or stays a separate
  fixed determinism anchor that the clock reads from. This subsumes D1/D5 and makes
  D2 option (b) the natural balance-date fix (SQL "no rows for the picked day →
  latest", anchor-agnostic, no UI sentinel). Options (a)/(c) are dispreferred on
  the determinism grounds above.

## 8. Scope note

This audit is intentionally analysis-only. The AO.10 Oracle fix (ORA-00932,
`day_text`) and AO.S2.a (trainer pin) already landed and are independent of these
decisions. The QS balance-date blocker (C1/D2) is the one item gating the release;
everything else is consolidation that should follow the model chosen here.
