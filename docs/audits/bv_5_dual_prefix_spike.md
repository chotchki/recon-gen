# BV.5 — Dual-prefix Trainer architecture spike

> **Status:** DRAFT 2026-05-31. Triggered by the BU design-review
> SOFT-MISS finding (`docs/audits/bu_design_review.md` P1.3: the
> Tour Before/After toggle has no implementation surface). Operator
> proposed: pre-populate two prefix-scoped DB copies (one clean
> baseline, one with the planted scenario), let the Tour toggle
> flip which prefix the dashboards query against. This doc grapples
> with the cascade — App2 implementation, the
> "support-two-renderers" question, cfg.yaml shape impact.

---

## 0. The forcing function — per-dialect timing data

Probe: `_build_seeded_sqlite + plant_function + matview refresh`
against `sasquatch_pr` × all three dialects × the `phantom_rail`
plant (lightest L2 kind, no picker dependency). Probe script:
`/tmp/bv_timing_probe.py`.

| dialect  | reset (wipe + schema + config_kv + baseline + matviews) | plant + matviews |
|----------|---------------------------------------------------------|------------------|
| sqlite   | **24.04 s**                                             | **13.03 s**      |
| postgres | **28.24 s**                                             | **3.02 s**       |
| oracle   | **626.93 s** (10 min 27 s)                              | **17.06 s**      |

**Reads:**
- **Postgres** plant cycle is ~3 s — flow-state OK. The current
  Reset→Plant→Tour loop on PG already meets the "iterate freely"
  bar the BU mockup designed around.
- **sqlite** plant 13 s + reset 24 s = ~37 s round-trip. Sluggish
  but workable for solo iteration. Per-statement commit overhead is
  the bottleneck, NOT in-memory speed (this was the operator's
  initial suspicion — confirmed).
- **Oracle** reset is ~10 min. Completely unworkable. The current
  Trainer's "Reset to clean baseline" button cannot exist on
  Oracle — no operator iterates plants on a 10-minute reset cycle.
  Even plant-alone at 17 s breaks flow on every change.

**Conclusion:** the operator's per-toggle UX intuition was being
shaped by sqlite's "slow seconds" + Oracle's "minutes" both being
in the current loop. The dual-prefix pivot turns per-toggle cost
from "tens of seconds (sqlite) / minutes (Oracle)" into "zero (URL
swap + dashboard reload)" — at the cost of one-time setup.

**One-time setup cost** (1 baseline + 25 per-plant prefixes):
- Postgres:  ~28 s + 25 × ~3 s ≈ **~1.7 min**
- sqlite:    ~24 s + 25 × ~13 s ≈ **~6 min**
- Oracle:    ~10 min + 25 × ~17 s ≈ **~17 min**

Oracle goes from "unusable" → "wait 17 min once, then flow-state
forever." That's the architectural unlock.

---

## 1. The dual-prefix pivot — what it means

**Today:** one prefix per L2 instance. `<L2>_transactions`,
`<L2>_daily_balances`, `<L2>_drift`, etc. The Trainer's
`/training/reset` button wipes + reseeds *that one prefix*; the
`/training/plant/<kind>` button mutates *that one prefix*. Tour
iframes the dashboard reading from *that one prefix*.

### 1.1 Round-1 sketch (REJECTED — disk cost)

Original sketch: populate **N + 1 prefixes per L2** at trainer entry
(1 baseline + 1 per plant kind = 26 total for sasquatch_pr × 25
plants). Per-toggle cost ~zero (prefix flip), but disk cost grows
26× baseline — and grows with every registry expansion. Operator
flagged: **"prefix per plant will have a disk space storage
[challenge]; I think we should plan to just have a clean data set
and then a plant one that we've checked off what plants to enable."**

### 1.2 Round-2 lock — TWO prefixes, multi-plant compose

**Final shape:**
- **`<L2>__baseline_*`** — clean baseline, ONE copy. Built once at
  trainer entry, never mutated.
- **`<L2>__planted_*`** — composite of all operator-enabled plants
  applied on top of baseline. Re-built whenever the enabled set
  changes.

Disk cost: 2× baseline. Constant — independent of registry size.

The Tour Before/After toggle still flips a `?state=before|after`
URL param → resolves to one of the two prefixes. Same UX as the
N+1 sketch; the difference is on the populate side.

### 1.3 Landing UX consequence — cards collapse into checkboxes

The current 25-card-per-kind landing accordion is the wrong shape
for the new model. The operator's primary action is now **"pick
which plants are enabled in this session,"** not "click into one
kind at a time." Landing becomes:

- A checkbox list (grouped by family — L1 Conservation / L1 Cap /
  L2 Triage / etc.) where each row is one plant kind with an
  on/off toggle.
- **Bulk-toggle affordances** so the operator isn't clicking 25
  boxes individually:
  - Per-family `[Select all] [None]` chip pair on each
    `<details>` accordion summary line.
  - Top-level `[Select all] [None]` chip pair at the page header.
  - Per-family + top-level current-state badge (e.g. `(7/9
    enabled)` next to the family title) so the operator can see
    selection density without expanding.
- An "Apply selection" button that re-builds the planted prefix
  (drops planted-* tables; copies from baseline-*; applies each
  enabled plant's SQL; refreshes matviews).
- The Tour link goes to the dashboard with the toggle — `Before`
  shows baseline (always clean); `After` shows the composite
  effect of every enabled plant.

This **collapses per-kind plant pages entirely.** The operator
doesn't navigate per-kind — they pick a set, see the combined
effect. To "study just chain_orphan in isolation," they enable
ONLY chain_orphan and disable the rest. To compare two scenarios,
they swap the checkbox set.

What we lose: the per-kind plant page (form to tune a kind's
specific knobs — count, days_ago, etc). For v1 every plant runs
with its registered defaults; per-kind tuning becomes a stretch.
Operator implication: BU's 25 plant-page mockups become 1 landing
+ 1 tour. Significant doc-versus-built drift here that BV.4
absorbs.

What we gain: cleaner mental model ("here's the demo state I want")
+ way less code + the toggle finally has a coherent backing model.

### 1.4 Per-toggle (enable/disable) cost — and the real-data reality

**Operator data point:** sasquatch_pr baseline at production
density is **3-4 GB on sqlite** (the spec_example-sized data my
probe ran against was much smaller — tens of MB). At that scale
the clone + matview refresh costs are minutes, not seconds.

When the operator checks/unchecks a plant + clicks Apply:
1. DROP planted-* tables (schema + data).
2. Clone from baseline-* (`CREATE TABLE planted_X AS SELECT * FROM
   baseline_X` for each base table, plus the matview-as-table
   shells).
3. Apply each enabled plant's SQL in registry order.
4. Refresh planted-* matviews.

Per-dialect estimate for ~5 enabled plants against a **3-4 GB
sqlite baseline** (very rough — needs measuring on the real data):

- PG: clone ~10-30s (SELECT-INTO + indexes) + 5×3s plants +
  matview refresh **probably 1-3 min on real data** ≈ **~3 min
  per Apply**
- sqlite: clone ~30-90s (3-4GB, disk-bound) + 5×13s plants +
  matview refresh on the same volume ≈ **~3-5 min per Apply**
- Oracle: clone ~30-60s (Oracle DDL still slow) + 5×17s plants
  + matview refresh **likely 2-4 min on real data** ≈ **~4-6
  min per Apply**

**The Tour Before↔After toggle is still ~zero cost** — only the
Apply (changing the enabled-plant set) pays the rebuild. So the
flow becomes:

1. Operator enters trainer mode → ~10 min baseline build (Oracle)
   / ~30s (PG). Banner: "preparing your baseline data, this takes
   ~10 min on Oracle…"
2. Operator picks plants from checkboxes → click Apply → ~3-5 min
   wait with progress. Banner: "applying 5 plants, refreshing
   matviews…"
3. Operator iterates Tour (Before/After) freely — instant.

For "I want to try a different plant set" — they re-check and
re-Apply, paying the per-Apply cost. This is the expensive step;
the operator should expect to pick their session's plant set
deliberately and Apply once, not iterate the checkboxes rapidly.

**Pre-cost** (one-time at trainer entry, baseline only, 3-4GB
sqlite-scale):
- PG: probably ~1-2 min on real data (vs 28s on spec_example)
- sqlite: ~1-3 min (vs 24s)
- Oracle: ~10-15 min (vs 627s — Oracle's DDL cost is the floor)

Disk: ~6-8 GB for sqlite (2 × 3-4 GB). PG / Oracle equivalent
absent the matview-as-table sqlite overhead — call it 5-7 GB
per dialect.

### 1.5 Optimization sketches (deferred unless real-data measure
confirms the pain)

Possible reductions if the per-Apply cost is too high:

- **Incremental matview refresh.** PG + Oracle support `REFRESH
  MATERIALIZED VIEW CONCURRENTLY` (PG) and partial refresh
  patterns. SQLite's DROP+CREATE-AS pattern can't be incremental.
- **Diff-only Apply.** Track which plants are currently in
  planted-*; on Apply, compute the diff (added/removed) and only
  emit the added plants' SQL + undo the removed ones'. Skip the
  full clone-from-baseline. Saves the clone cost but breaks if
  any plant's SQL has subtle interactions with another's.
- **Virtual planted layer.** Don't physically copy baseline →
  planted. Have the dashboards query a VIEW that's
  `baseline UNION ALL plant_overlay` where plant_overlay is the
  small delta table. Reads stay fast (indexed); writes (Apply)
  only mutate the small overlay. Requires the matview layer to
  be view-aware — meaningful refactor on the schema side.

Start with the naive clone-and-replay; measure the real cost;
reach for optimization only if operator's per-Apply wait crosses
the "go-make-coffee" threshold.

---

## 2. The cascade — what changes in the codebase

### 2.1 cfg.yaml — single prefix → list of prefixes (or runtime expansion?)

**Today** `Config.db_table_prefix: str` is the single source.
`cfg.deployment_name` is similar (used for QS resource naming).

**Option A — runtime expansion.** cfg.yaml keeps `db_table_prefix`
as ONE prefix (the "base prefix"). Trainer mode at session entry
expands it: `f"{base}__baseline"`, `f"{base}__planted_{kind}"` for
each kind in PLANT_REGISTRY. No cfg.yaml shape change; operator
doesn't think about prefixes. The Trainer's internal state carries
the active prefix list.

**Option B — declared list.** cfg.yaml ships a
`trainer_mode: { base_prefix: "sasquatch_pr", populate_kinds:
[...] }` block. Operator can subset which kinds get pre-populated
(e.g. just the 5 they're teaching this week). More flexible but
exposes a Trainer-only knob in the global cfg shape.

**Recommendation:** Option A for v1. Avoid cfg.yaml shape changes
until we see operator demand for kind-subsetting. Trainer mode
detection lives at Studio start: `recon-gen studio --trainer-mode
--l2 <yaml>` flag.

### 2.2 App2 (HTMX dashboards) — the cheap renderer

App2 reads `prefix` at request time from cfg or URL param. Survey:
- `_studio_routes.py:976, 1589, 2325, 2668` — all sites read
  `cfg.db_table_prefix` with a fall-through to `cache.path.stem`.
- `_db_fetcher.py` — same shape.
- App data fetchers in `apps/<app>/datasets.py` take `prefix` as a
  function param.

**Change shape:**
1. Add `?state=before|after` (or `?prefix=<explicit>`) URL param
   support on `/dashboards/...` routes.
2. Route handler resolves the URL param → active prefix → threads
   through to data fetchers.
3. Trainer's `/training/tour/<kind>` page iframes
   `/dashboards/.../sheet?prefix=<L2>__planted_<kind>` (the
   "after" view) by default, with a toggle that switches to
   `?prefix=<L2>__baseline`.

**Risk:** prefix is widely passed; one missed callsite means the
toggle silently shows the wrong data. Mitigation: anti-drift test
that walks every `cfg.db_table_prefix` reference + asserts each
is reachable from a URL-param resolution path.

**Cascading impact (App2):**
- Studio route handlers (`_studio_routes.py`) — moderate, ~10
  sites.
- Dashboard route handlers — small, prefix already a kwarg.
- App data fetchers — none, already prefix-parametric.
- L2 Editor (`/l2_shape/`) routes — NONE; editor operates on the
  L2 yaml + config_kv, not the data tables. Trainer prefixes are
  invisible to the editor surface.
- Studio cache (`L2InstanceCache`) — needs an "active prefix set"
  alongside the L2 instance. Trainer-mode aware.

Net App2 work: ~1-2 days. The plumbing is small because the
prefix is already a parameter everywhere.

### 2.3 Schema + seed — N+1 populate path

Trainer mode at session entry runs (in parallel where the
dialect's transactional model allows):

```
for kind in [None, *PLANT_REGISTRY]:
    p = f"{base_prefix}__baseline" if kind is None else f"{base_prefix}__planted_{kind.kind}"
    drop_schema_if_present(p)
    emit_schema + execute against new p
    build_config_populate_sql(cfg.with_prefix(p))
    emit_baseline_seed(prefix=p)
    if kind is not None:
        plant_function(prefix=p)
    refresh_matviews_sql(prefix=p)
```

**Parallelism:** on PG / Oracle, can run multiple `__planted_*`
populates in parallel (different prefixes, no contention). sqlite
serialized by the writer-lock. Oracle parallelism would cut the
17 min substantially — 5-way parallel = ~5 min.

**Setup progress UX:** required. Streaming progress page like
BTa.9 `/etl/run` live tail. Per-prefix status badges (✓ done /
… in progress / ✗ failed).

### 2.4 QS renderer — the expensive renderer (DEFERRED)

QS dashboards burn the prefix into emit-time JSON (datasets'
CustomSql, MappedDataSetParameters). To support dual-prefix on
QS, **each prefix needs its own full QS deployment**: datasource +
N datasets + analysis + dashboard. For 26 prefixes per L2:
- 26× datasets (× ~10 datasets per app × 4 apps = ~1000 dataset
  resources per L2 in QS)
- 26× analyses + dashboards

**Cost:** QS Standard has resource limits (1000 datasets per
account); 26 prefixes would max out at one L2. QS Enterprise has
higher limits but is $$. Per-prefix QS deploy is also ~minutes
on AWS.

**Decision:** **defer QS dual-prefix.** Trainer mode is App2-only
for v1. QS remains single-prefix (the operator's "shipped"
deployment view). The Trainer surface is a Studio-local affordance
for understanding the dashboards' shape; the production deployment
still hits QS with the operator's actual L2 yaml.

The architectural claim — "dashboards depend only on
`<prefix>_config_kv` + base tables, not on the L2 yaml file" —
gets proved by the App2 dual-prefix work. Whether QS gets the
same affordance is a separate cost call later.

---

## 3. "Support two output versions" — the framing

Operator: *"as much as I like the idea of supporting quicksight
too, that's a far bigger change. Let's grapple with what supporting
two output versions of the dashboard looks like and the cascading
impact."*

**The two output versions are App2 + QS today** (not "two states
of one renderer"). The dual-prefix question recasts this:

- **App2 dual-prefix** = one renderer, two data sets per L2,
  toggle between them. Cheap (App2 is prefix-parametric).
- **QS dual-prefix** = two renderers' worth of *deployed
  resources* per L2. Expensive (QS resources are emit-time
  prefix-bound + cost $$).
- **App2 + QS parity for Trainer mode** = the union of both —
  cost-prohibitive at the dual-prefix layer.

**Spike conclusion:** Trainer mode lives on App2 only. QS keeps
its current single-prefix shape. The
`apps/<app>/app.py` tree IS the shared SoT — same dataset
contracts, same SQL, same dashboard shape; only App2 gets the
Trainer toggle wired through. QS gets the same deployment it
already gets.

This means the existing App2 + QS parity guarantee weakens slightly
for the Trainer affordance only — QS will not have a
"Before/After" tour toggle. The mockup designed the toggle
renderer-agnostic, but the cost of making QS honor it (26+
deployments per L2) is the disqualifying number.

---

## 4. Cascading impacts checklist (App2-only path)

Must-update sites:

- [ ] `Config.db_table_prefix` — keep as single base; add
      `trainer_mode_prefixes: tuple[str, ...] | None` runtime-set
      field (NOT serialized to cfg.yaml).
- [ ] `recon-gen studio` CLI — `--trainer-mode` flag that triggers
      the N+1 populate at session entry.
- [ ] New: trainer-mode populate orchestrator (parallel-capable).
- [ ] New: `/training/setup` progress page (BTa.9 live-tail
      shape).
- [ ] `/training/tour/<kind>` page — embed the dashboards under
      `?prefix=<L2>__planted_<kind>` default + Before toggle.
- [ ] `/dashboards/<app>/sheets/<sheet>` route — accept
      `?prefix=` URL param, narrow the data fetcher.
- [ ] Studio cache — track the active prefix set.
- [ ] Schema emit — verify zero hardcoded references to the
      "single" prefix (any helper that assumes one prefix per L2
      is a footgun).
- [ ] L2 Editor — confirm it operates only on the L2 yaml +
      config_kv, never on data tables (anti-drift test).
- [ ] Probe / Triage / Coverage / ETL Run surfaces — these are
      Studio's "operate on the demo DB" surfaces. Decide: do they
      target the baseline prefix or the active planted prefix?
      Likely baseline (the operator is debugging the L2 against
      reality, not a planted scenario).
- [ ] `data apply` / `json apply` / `audit apply` CLI commands —
      these stay single-prefix (production deploy path). Trainer
      mode is Studio-only.

Anti-drift tests to add:

- [ ] No `<prefix>_*` SQL literal in source that isn't routed
      through the prefix kwarg.
- [ ] Every `/dashboards/*` route handler accepts `?prefix=`.
- [ ] Trainer-mode populate succeeds end-to-end against all three
      dialects (BV.3.1-style parameterization).

Out of scope (defer):

- QS dual-prefix support.
- Trainer mode against AWS-deployed PG/Oracle (use local Docker
  per `[[project_local_dev_env_unconstrained]]`).
- Per-kind subset selection in cfg.yaml (cfg.yaml shape unchanged).
- Lazy populate (build first 5 kinds eagerly, rest on first
  visit) — start with eager; revisit if 17 min Oracle setup is
  unacceptable.

---

## 5. Open questions for operator triage

1. **Trainer mode entry surface.** `recon-gen studio --trainer-mode`
   CLI flag, or a Studio in-app "Enter Trainer mode" button? CLI is
   simpler; in-app gives the operator the choice mid-session.

2. **Per-kind subset.** Start eager (all 25 kinds populated at
   entry) or let operator pick which kinds matter? Eager wins on
   teaching-flow simplicity; subset wins on Oracle's 17 min setup.

3. **State of probe / triage / coverage** during trainer mode.
   These look at the demo DB — should they keep pointing at the
   baseline prefix, or at the currently-toured-plant prefix? My
   read: always baseline (the "reality" reference); planted
   prefixes are tour-only.

4. **Failure mode when the populate fails.** A plant emitter
   throws (e.g. picker can't satisfy on this L2) — does the
   Trainer surface that kind as "✗ unavailable" and let the
   operator iterate the others? Or block trainer entry until
   every kind populates clean?

5. **Memory / disk cost.** Each baseline copy = ~60k transactions
   at sasquatch_pr's density. 26 copies = 1.5M rows per L2. PG
   handles this fine; sqlite is local file so disk-bound;
   Oracle's the same. No actual constraint at this scale, but
   call out for very-large L2 instances later.

6. **Tour iframe URL** — the toggle params need to survive the
   dashboard's own state (date pickers, filter selections). Does
   the toggle preserve operator-set filters? Probably yes — the
   `?prefix=` is independent of `?param_*` filter overrides.

7. **Trainer mode + the L2 Editor.** If the operator edits the L2
   yaml during a trainer session, the populated prefixes go
   stale. Either (a) re-populate on edit (re-pay setup cost), or
   (b) flag staleness and require explicit re-enter. (b) wins
   for predictability.

8. **CLI vs Studio split** — `data apply --execute` currently
   wipes + reseeds the single prefix. Trainer mode wants to NOT
   touch the production prefix. New `recon-gen data apply
   --trainer-populate` command that builds the N+1 prefixes,
   leaving the operator's main prefix alone?

---

## 6. Locked design decisions (pending operator confirmation)

- **DL.1** — Trainer mode is App2-only. QS remains single-prefix
  for v1.
- **DL.2** — cfg.yaml shape unchanged. Trainer mode adds a runtime
  `trainer_mode_prefixes` field that's not serialized.
- **DL.3** — Eager populate at trainer session entry. Streaming
  progress page; failures per kind surface as ✗ but don't block
  entry.
- **DL.4** — Probe / Triage / Coverage / ETL Run pages stay
  baseline-prefix-bound in trainer mode. Plant-prefixes are
  tour-only.
- **DL.5** — L2 Editor + cfg.yaml + CLI commands unchanged.
  Trainer mode is a Studio orthogonal capability.
- **DL.6** — Tour Before/After toggle implementation: `?state=`
  URL param on the tour-iframe URL → resolves to one of the
  populated prefixes → dashboard renders against that prefix.

## 7. Estimated work

| BV.4.x phase | Work | Estimate |
|---|---|---|
| BV.4.0 | Operator confirmation on §6 locks + §5 open Qs | 30 min |
| BV.4.1 | `--trainer-mode` CLI + N+1 populate orchestrator | 1-2 d |
| BV.4.2 | `/dashboards/*` accepts `?prefix=`; threading through | 1 d |
| BV.4.3 | `/training/setup` progress page (BTa.9 shape) | 0.5 d |
| BV.4.4 | `/training/tour/<kind>` Before/After toggle wiring | 0.5 d |
| BV.4.5 | Re-design plant pages — no per-plant form (defaults baked in at populate) | 0.5 d |
| BV.4.6 | Anti-drift tests (prefix-routing exhaustiveness) | 0.5 d |
| BV.4.7 | BV.3.1 extension over all 3 dialects | 0.5 d |
| BV.4.8 | Cold-read v2 against the dual-prefix surface | 90 min |

Total: ~5-6 days. Cuts BU.4's remaining polish work that's
trying-to-fix-around-the-cycle-cost (banner auto-dismiss, progress
indicators, "Re-plant" CTA) because the cycle cost goes to zero.

---

## 8. Cascading impact on existing BU work

What gets cancelled:
- BU.1.7 (progress indicator for reset) — reset becomes prefix-flip,
  no progress needed.
- BU.1.11 (TRAINER_CLEAN still emits plants) — moot; baseline
  prefix never has plants.
- BU.4 banner auto-dismiss, "Re-plant" CTA, per-form-field defaults
  rationale — moot; per-plant forms collapse (defaults already
  baked in at populate time).

What carries forward:
- BU.2a/b registry + typed sections + Lock 9 anti-drift — still
  the SoT.
- BU.3 plant emitters — still need them, just invoked once at
  populate not per-button-click.
- BU.4 stage 1-4 polish (breadcrumb, kind_qualifier, empty-primitive
  hint, default-collapsed accordion) — visual landing/plant page
  improvements stand.
- BV.3.1 parameterized round-trip — the test framework; gets
  extended to all 3 dialects (BV.4.7) once the populate path
  lands.

What becomes more important:
- Picker robustness — every plant's picker MUST satisfy on the
  L2 yaml at populate time (no lazy "fail when you click"). The
  BU.3.2.a `limit_breach_inbound` picker bug becomes a
  populate-time error not a tour-time blank.

---

**Operator next step:** confirm §6 design locks + answer §5 open
questions, then BV.4.0 → BV.4.1.
