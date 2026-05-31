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

**Naming convention** (operator-locked): `prefix_b` for baseline,
`prefix_v` for "with violations." Trainer mode operates on the
suffix pair; regular `data apply` / `json apply` / `audit apply`
still target a SINGLE base `prefix` (the operator's production
deploy view).

**Final shape:**
- **`<L2>_prefix_b_*`** — clean baseline, ONE copy. Built once at
  trainer entry, never mutated during the session.
- **`<L2>_prefix_v_*`** — composite of all operator-enabled plants
  applied on top of a clone of baseline. Re-built whenever the
  enabled set changes.
- **`<L2>_*`** (unsuffixed) — the regular production deploy
  prefix, untouched by trainer mode. This is what `recon-gen
  data apply` / `etl_hook` write to outside trainer mode.

Disk cost: 2× baseline. Constant — independent of registry size.

### 1.2.1 DETERMINISM LOCK — copy-once, not etl-twice

The Tour comparison is **only meaningful if `prefix_b` and
`prefix_v` share identical underlying baseline data.** Customer
ETL hooks read from upstream systems (production DBs, statement
exports, vendor APIs) — calling the ETL hook twice produces TWO
DIFFERENT baselines (different wall-clock, different row counts,
different sampled rows). The Before/After comparison would be
corrupted — operator wouldn't know if a delta they see is from
the plant or from the underlying baseline drift.

**Architecture:** call ETL hook ONCE per trainer session →
`prefix_b`. Then `prefix_v` = data-copy of `prefix_b` + apply
enabled plants + refresh matviews. Apply-cycle re-clones from
`prefix_b` (which never mutates), guaranteeing the operator's
"compare clean vs planted" is comparing literally the same
baseline data with planted deltas applied on top.

This is the **only architecture that survives a non-deterministic
ETL hook** — and customer ETL hooks are non-deterministic in
practice.

### 1.2.2 ETL hook gains a `prefix` kwarg

Today the etl_hook signature is `etl_hook(cfg)` — implicitly
writes to `cfg.db_table_prefix`. Trainer mode needs:

```python
def etl_hook(cfg, *, prefix: str) -> None:
    # write into <prefix>_transactions, <prefix>_daily_balances
```

- Regular `data apply` calls with `prefix=cfg.db_table_prefix` —
  backward-compat shim if customers' existing hooks don't accept
  the kwarg.
- Trainer "Session start" populates `prefix_b` via etl_hook —
  first time tables are made for the session. **Plus** an explicit
  "Re-fetch baseline" button on the Trainer page so the operator
  can re-pull from upstream mid-session (without re-entering the
  full session). Re-fetch re-clones `prefix_b → prefix_v` + replays
  enabled plants automatically.
- All Apply cycles operate on `prefix_v` only (clone from
  `prefix_b` + plants).
- A "Cleanup" button on the Trainer page drops both `prefix_b_*`
  and `prefix_v_*` tables for the session — reclaims the 2× disk
  when the operator's done.

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
- **Tour uses TWO LINKS, not a toggle.** Operator-locked: a single
  toggle is mode-confusing ("am I on Before or After?"); two
  distinct links per family / per kind are explicit.
  - `[ Clean dashboard → ]` — opens the dashboard reading from
    `prefix_b`. The "this is what healthy looks like" view.
  - `[ Violation dashboard → ]` — opens the dashboard reading from
    `prefix_v`. The "this is what the operator's enabled plants
    cause" view.
  - When a kind ISN'T in the operator's enabled set, the
    Violation link STILL points to the violation-dashboard URL —
    page renders an empty-state callout: "this violation isn't
    present in your current enabled set; tick the
    `<kind>` checkbox above and click Apply to surface it." The
    operator gets a prove-it-to-yourself reading even when no
    plants are enabled — confirms the dashboard doesn't lie.

**Cards stay as the per-kind anchor** (operator-locked): each
plant kind still gets its card on the landing — the card carries
the title, description, "What to do about it" copy, and the two
Tour links (Clean / Violation). What changes is that the card
also has the checkbox + the form-tuning section goes away
(defaults bake at populate time). The card is the instructional
anchor and the navigation anchor; the checkbox is the compose
control.

**Active-plants filter** (top-level affordance): a "Show:
[All / Only enabled / Only with errors]" filter at the page header
collapses families that don't have any operator-enabled plants —
so when the session settles on a 5-plant set the operator isn't
scrolling past 4 disabled families to get to the active ones.

What we lose: the per-kind plant page's tunable-form section
(count, days_ago, etc). For v1 every plant runs with its
registered defaults; per-kind tuning becomes a stretch. The
card+checkbox replaces "click into a per-kind page, fill the form,
submit" — operator composes via the landing's checkbox set + Apply.

What we gain: cleaner mental model ("here's the demo state I want")
+ no per-kind page navigation friction + the Tour Before/Violation
links finally have a coherent backing model (zero per-toggle cost).

### 1.4 Per-toggle (enable/disable) cost — and the real-data reality

**Operator data point:** sasquatch_pr baseline at production
density is **3-4 GB on sqlite** (the spec_example-sized data my
probe ran against was much smaller — tens of MB). At that scale
the clone + matview refresh costs are minutes, not seconds.

When the operator checks/unchecks a plant + clicks Apply:
1. DROP `<L2>_prefix_v_*` tables (schema + data).
2. Clone from `<L2>_prefix_b_*` — `CREATE TABLE prefix_v_X AS
   SELECT * FROM prefix_b_X` for each base table, plus the
   matview-as-table shells. **The ETL hook is NOT re-invoked** —
   it ran once at trainer entry into `prefix_b`; clone is pure
   data-copy, deterministic by construction.
3. Apply each enabled plant's SQL in registry order against
   `prefix_v_*`.
4. Refresh `prefix_v_*` matviews.

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

### 1.5 Optimizations — operator-graded

Re-read with operator triage:

- **Incremental matview refresh — PROMOTE TO PRODUCTION MANDATE
  (not trainer-only).** Operator: *"We should do this for ALL
  postgres/oracle refreshes, its the only thing that will be sane
  in a production environment and even dev should be better."*
  Today's `refresh_matviews_sql` does DROP+CREATE-AS for SQLite
  (correct — only path); PG/Oracle should swap to `REFRESH
  MATERIALIZED VIEW CONCURRENTLY` (PG) and the equivalent
  fast-refresh-on-commit / `REFRESH FAST` patterns on Oracle.
  This benefits **every** matview refresh in production deploys,
  not just trainer mode. Tracked as **BV.6 production matview
  refresh modernization** (separate phase — orthogonal to BV.4
  trainer work).
- **Diff-only Apply — LOW-HANGING FRUIT.** Operator: *"this may
  be VERY easy to do now (with the exception of the deletions)
  due to our use of the metadata.plants fields. For deletes we
  may need to save the deleted rows in the _kv table."*
  Implementation shape:
  - Track currently-applied plants in `<L2>_prefix_v_config_kv`
    (the existing config_kv table, extended with a
    `trainer_applied_plants` row).
  - On Apply with new enabled-set: diff against the stored set
    → `added`, `removed`.
  - For each `added` plant: emit its plant_function SQL into
    `prefix_v`. Plants use stable id-prefixes (`__demo_gap_*`,
    `__demo_plant_*`) so they're cleanly recoverable. INSERT path.
  - For each `removed` INSERT-style plant: `DELETE FROM
    <prefix_v>_transactions WHERE id LIKE '__demo_<kind>%'`.
    Clean undo via id-prefix namespace.
  - For each `removed` DELETE-style plant (`uncovered_rail` /
    `uncovered_template` / `dead_*` — emitters that DELETE rows
    from baseline): **save the deleted-rowsets in
    `<prefix_v>_config_kv` at plant-time** so undo can re-INSERT
    them. Per-plant `trainer_undo_payload` rows.
  - Refresh matviews (incremental once BV.6 lands; full now).
  - Save the new applied-plants set back to `prefix_v_config_kv`.

  **No clone-from-baseline cost.** Apply becomes O(delta) not
  O(full baseline). Combined with BV.6 incremental matview
  refresh: Apply on PG drops from ~3 min → maybe 5-10 s; sqlite
  from ~3-5 min → maybe 30-60 s; Oracle from ~4-6 min → maybe
  30-60 s.

  Promote to **BV.4.4** alongside the Tour-link wiring — the
  per-Apply latency goes from "click + go-make-coffee" to "click
  + watch a progress bar for a few seconds." Materially changes
  the operator UX from "compose then commit" to "iterate freely."

- **Virtual planted layer.** Don't physically copy baseline →
  planted. Have the dashboards query a VIEW that's
  `baseline UNION ALL plant_overlay` where plant_overlay is the
  small delta table. Reads stay fast (indexed); writes (Apply)
  only mutate the small overlay. Requires the matview layer to
  be view-aware — meaningful refactor on the schema side.
  **Defer** — diff-only Apply gets us most of the perf win without
  the schema refactor.

**Revised path:** start with **diff-only Apply (BV.4.4) +
incremental matview refresh (BV.6 — parallel work, benefits
prod)**; skip the naive clone-and-replay path entirely. The
extra complexity is modest (deleted-rows config_kv stash for
DELETE-shaped plants) and the operator UX improvement is the
difference between "compose-and-wait" vs "iterate-freely."

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
until we see operator demand for kind-subsetting.

**Trainer-mode detection is intrinsic, not a flag.** No
`--trainer-mode` CLI flag — Trainer mode "is on" once the operator
clicks Session Start on the Trainer page. That action triggers
schema creation (the base-table DDL) + config_kv populate + etl_hook
invocation into `prefix_b`, then a clone of `prefix_b → prefix_v`.
After that, Trainer-mode is implicit: the existence of
`<base>_prefix_b_*` tables is the signal. A Cleanup button on the
Trainer page drops both prefixes when the operator's done.

This means **the Trainer surface is responsible for the entire
schema lifecycle of its own prefixes** — not just data populate.
The production schema (`<base>_*`) is created by `recon-gen
schema apply --execute` as today; trainer schemas (`<base>_prefix_b`
+ `<base>_prefix_v`) are created by Session Start.

### 2.2 App2 (HTMX dashboards) — the cheap renderer

App2 reads `prefix` at request time from cfg or URL param. Survey:
- `_studio_routes.py:976, 1589, 2325, 2668` — all sites read
  `cfg.db_table_prefix` with a fall-through to `cache.path.stem`.
- `_db_fetcher.py` — same shape.
- App data fetchers in `apps/<app>/datasets.py` take `prefix` as a
  function param.

**Change shape:**
1. Add `?prefix=<value>` URL param support on `/dashboards/...`
   routes. (Per DL.7 — `?prefix=` is first-class; the abandoned
   `?state=before|after` shim from the earlier draft is dropped.)
2. Route handler resolves the URL param → active prefix → threads
   through to data fetchers. Defaults to `cfg.db_table_prefix`
   when the param is absent.
3. Trainer's landing cards render the two Tour links per kind
   with `?prefix=<base>_prefix_b` (Clean) and `?prefix=<base>_prefix_v`
   (Violation). Operator follows the link → fully-formed dashboard
   URL with prefix baked in → URL-as-truth (DL.10).

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

## 5. Open questions — RESOLVED 2026-05-31

1. **Trainer entry surface.** RESOLVED — Trainer page Session
   Start button does the entire lifecycle (schema create + config_kv
   populate + etl_hook for `prefix_b` + clone to `prefix_v`). A
   Cleanup button drops both trainer prefixes when done. Promoted
   to DL.10.
2. **Per-kind subset / eager-vs-lazy.** RESOLVED — Session Start
   sets up `prefix_b` AND a clean `prefix_v` (clone of baseline,
   zero plants enabled). Operator then picks plants from
   checkboxes; Select-all is their handy do-it-all. No CLI
   subset; UI checkbox set IS the subset mechanism. Promoted to
   DL.11.
3. **Probe / Triage / Coverage state in trainer mode.** RESOLVED —
   always baseline (`prefix_b`). Top nav routes also hit baseline.
   Trainer baseline is always separate from the production prefix.
   DL.4 already captured; restated for clarity.
4. **Failure mode when plant populate fails.** RESOLVED — show
   "error planting" on the kind card, still provide the
   Violation link (so operator can navigate + see empty-state).
   Promoted to DL.12.
5. **Memory / disk cost.** RESOLVED — 2 copies (DL.3) bounds the
   disk hit; no further concern.
6. **URL-as-truth.** RESOLVED — everything drives off the URL.
   Setting a filter and sharing the link reproduces the same
   view. Info sheet shows what prefix the dashboard is hitting
   so operators can always confirm. Promoted to DL.13.
7. **L2 edit during trainer session.** RESOLVED — option (b):
   flag staleness, suggest (don't demand) a re-setup. Soft
   banner on the Trainer page when the L2 yaml has changed
   since Session Start. Promoted to DL.14.
8. **CLI `--trainer-populate` split.** BACKLOGGED — no direct
   value at the moment; trainer mode is Studio-only by design.

---

## 6. Locked design decisions (pending operator confirmation)

- **DL.1** — Trainer mode is App2-only. QS remains single-prefix
  for v1.
- **DL.2** — cfg.yaml shape unchanged. Trainer mode adds a runtime
  pair `(prefix_b, prefix_v)` derived from `cfg.db_table_prefix`,
  not serialized.
- **DL.3** — **TWO prefixes** (not N+1): `<base>_prefix_b` (clean
  baseline, etl-hook'd once per session) + `<base>_prefix_v`
  (composite of all enabled plants, rebuilt per Apply). Disk =
  2× baseline regardless of registry size.
- **DL.3.a** — **Copy-once, not etl-twice.** ETL hook invoked
  ONCE per session into `prefix_b`. `prefix_v` is always built by
  data-copy from `prefix_b` + apply enabled plants. Survives
  non-deterministic customer ETL hooks (the load-bearing
  assumption).
- **DL.3.b** — ETL hook signature gains `prefix: str` kwarg.
  Regular `data apply` keeps current call shape; trainer mode
  passes `prefix=<base>_prefix_b` at session entry.
- **DL.4** — Probe / Triage / Coverage / ETL Run pages target the
  **base prefix** (`cfg.db_table_prefix`) in production mode. In
  trainer mode they target `prefix_b` (the deterministic baseline,
  which is what the operator's L2 should be debugged against).
  Planted prefix (`prefix_v`) is Tour-only.
- **DL.5** — L2 Editor + cfg.yaml + CLI commands unchanged.
  Trainer mode is a Studio orthogonal capability.
- **DL.6** — **Tour: two distinct links, not a toggle.** Per kind
  + per family, render `[ Clean dashboard → ]` (points at
  `prefix_b`) AND `[ Violation dashboard → ]` (points at
  `prefix_v`). When the operator hasn't enabled a kind, the
  Violation link still points at the violation-dashboard URL +
  the page renders an empty-state callout reinforcing "tick the
  checkbox + Apply to see it surface here." Self-reinforcing
  prove-it-to-yourself UX.
- **DL.7** — **App2 `?prefix=` URL param is first-class.**
  Every `/dashboards/<app>/...` route accepts `?prefix=<value>`;
  defaults to `cfg.db_table_prefix` when absent. Single deployment
  supports multiple prefix views without re-deploying. This is
  the mechanism the Tour's two-link UX rides on.
- **DL.8** — Landing UX: checkbox list per kind + per-family
  `[Select all] [None]` bulk-toggle chips + top-level
  `[Select all] [None]` + per-family/top selection-density
  badges (`(7/9 enabled)`). Apply button at the bottom. Cards
  stay as anchors (carry kind title + description + "What to do
  about it" + the two Tour links); checkbox is the compose
  control on the same card. Top-level "Show: [All / Only enabled
  / Only with errors]" filter collapses inactive families when
  the session settles on a small enabled set.
- **DL.9** — **Diff-only Apply (not clone-and-replay).** Apply
  computes the added/removed plant diff against the
  currently-applied set (stored in `prefix_v_config_kv`) and emits
  only the deltas. Added INSERT-style plants: run plant_function
  SQL. Removed INSERT-style plants: `DELETE WHERE id LIKE
  '__demo_<kind>%'` (the stable id-prefix namespace). Removed
  DELETE-style plants (uncovered_*, dead_*): re-INSERT from the
  saved-rowsets payload in `prefix_v_config_kv` (per-plant
  `trainer_undo_payload`). Apply becomes O(delta) not O(baseline
  copy). Per-Apply UX goes from "go-make-coffee" to "watch a
  progress bar for a few seconds."
- **DL.10** — **No `--trainer-mode` CLI flag.** Trainer mode is
  intrinsic — detected by the existence of `<base>_prefix_b_*`
  tables. Session Start button on the Trainer page creates the
  schema + populates baseline + clones to `prefix_v`. Cleanup
  button on the Trainer page drops both trainer prefixes. A
  "Re-fetch baseline" button on the Trainer page re-runs etl_hook
  into `prefix_b` then re-clones to `prefix_v` (preserving
  enabled-plant state).
- **DL.11** — **Initial state at Session Start: zero plants
  enabled.** Both `prefix_b` and `prefix_v` ship a clean baseline.
  Operator picks plants via checkboxes + Apply. Select-all chip is
  the do-it-all shortcut.
- **DL.12** — **Per-kind failure tolerates partial set.** A plant
  that fails to apply (picker can't satisfy on this L2, plant SQL
  raises) gets an "error planting" badge on its card; Apply
  continues with the remaining enabled plants. Violation link on
  the failing kind still renders — operator follows it to see
  the empty-state (proves the failure didn't surface as a
  silent-blank elsewhere).
- **DL.13** — **URL-as-truth + Info-sheet-as-mirror.** Everything
  drives off the URL (prefix, filters, date range). Sharing a
  link reproduces the exact same view. Every app's Info sheet
  carries a row reading the current `?prefix=` value so the
  operator can always confirm which dataset they're hitting.
- **DL.14** — **L2 edits flag staleness, don't force re-setup.**
  When the operator edits the L2 yaml mid-session, the Trainer
  page shows a soft "your baseline is from L2 yaml as-of
  HH:MM; current L2 has changed since — `[Re-fetch baseline]` to
  re-sync" banner. No force re-entry.
- **DL.15** — **Promote incremental matview refresh to PRODUCTION
  (parallel BV.6 phase) — but ORACLE PATH IS NON-TRIVIAL.**
  Today's DROP+CREATE-AS matview refresh pattern is the only sane
  shape on SQLite but is wrong-shaped on PG. Three dialect paths
  diverge:
  - **PG**: `REFRESH MATERIALIZED VIEW CONCURRENTLY <name>` — clean
    swap, requires a UNIQUE index on the matview. Existing matviews
    likely have primary-key-equivalent indexes already; gap-check
    + add where missing. Low complexity.
  - **Oracle**: `REFRESH FAST` is the incremental verb but **requires
    `CREATE MATERIALIZED VIEW LOG ON <every source table>` + the
    matview DDL declared `WITH ROWID ... REFRESH FAST ON COMMIT`
    (or ON DEMAND).** This is a real schema-emit change, not a
    refresh-flag swap. Two sub-options:
    - **(O.a) Add MV LOGs to every source.** Schema emit gains
      `emit_materialized_view_log_for(source_table)` calls;
      matview DDL declares `REFRESH FAST`. Operator pays:
      tracking-log overhead on every base-table INSERT/UPDATE/DELETE
      (modest write-side cost). Win: matview refresh becomes
      delta-time on Oracle.
    - **(O.b) Stay on `REFRESH COMPLETE` for Oracle.** Accept the
      current Oracle full-rebuild cost. Document the constraint;
      revisit if customer demand justifies the MV-log build-out.
  - **SQLite**: DROP+CREATE-AS stays — no incremental refresh
    primitive exists. App-level "delta refresh" (DELETE changed
    rows + INSERT new ones) would work cross-dialect but is its
    own large rewrite (`Path C` in §1.5 — virtual planted layer
    territory). Defer.

  **Recommendation:** v1 of BV.6 ships PG concurrent refresh only;
  Oracle stays on full-rebuild with documented operator warning;
  SQLite unchanged. Path (O.a) lives as BV.6.x sub-task if
  Oracle's matview cost becomes the operator-pain bottleneck.

  Tracked separately as BV.6; orthogonal to BV.4 trainer work.
  BV.4 Apply-cost numbers improve on PG once BV.6 lands; sqlite
  + Oracle Apply costs unchanged (still rely on diff-only Apply
  per DL.9 for those).

## 7. Estimated work

| BV.4.x phase | Work | Estimate |
|---|---|---|
| BV.4.0 | Operator confirmation on §6 design locks | 15 min |
| BV.4.1 | Trainer page Session Start (schema + config_kv + etl_hook into `prefix_b` + clone to `prefix_v`); Cleanup button; Re-fetch baseline button | 1-2 d |
| BV.4.2 | `/dashboards/*` accepts `?prefix=`; threading through; default to `cfg.db_table_prefix` when absent | 1 d |
| BV.4.3 | `/training/setup` streaming progress page (BTa.9 live-tail shape) | 0.5 d |
| BV.4.4 | Diff-only Apply (DL.9) + Tour two-link wiring (DL.6) + landing checkbox UX (DL.8) | 1-1.5 d |
| BV.4.5 | Per-kind failure cards (DL.12) + L2-staleness banner (DL.14) | 0.5 d |
| BV.4.6 | Anti-drift tests (prefix-routing exhaustiveness; Info-sheet prefix row per DL.13) | 0.5 d |
| BV.4.7 | BV.3.1 extension over all 3 dialects (PG + Oracle Docker variants) | 0.5 d |
| BV.4.8 | Cold-read v2 against the dual-prefix surface | 90 min |

Total: ~5-6 days. Cuts BU.4's remaining polish work that's
trying-to-fix-around-the-cycle-cost (banner auto-dismiss, progress
indicators, "Re-plant" CTA) because diff-only Apply makes the
cycle cost approachable.

**Parallel: BV.6 — production matview refresh modernization
(DL.15).** v1 ships PG concurrent refresh only (~1 day).
Oracle's `REFRESH FAST` path requires materialized view logs +
matview DDL rewrite — meaningful schema-emit work tracked as
BV.6.x sub-task, deferred unless Oracle's matview refresh cost
becomes operator-painful. SQLite unchanged (no incremental
primitive). Benefits PG production deploys + the BV.4 trainer
Apply-cost numbers on PG.

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
