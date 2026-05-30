# Phase BS — Studio + Dashboards rethink under DB-projected L2

> **Status:** SPEC draft 2026-05-29. Trace: `docs/bs_design_thoughts.md`.
> Next: lock decisions inline, then a thin PLAN.md (BS.0 locks, BS.1..N).

## Why now

Two layers crystallized in the last month:

1. **DB-projected L2 (Phase AW + BC.12).** `<prefix>_config_kv` is the
   runtime-accessible flattening of cfg + L2 yaml. The L2 yaml is the
   **authoring source of truth**; `_config_kv` is its **runtime
   projection**. Matviews + dashboards JOIN against `_config_kv` instead
   of re-deriving L2 shape in SQL — `[[project_date_model_audit]]`'s
   "time is unowned" closed via AW's owned `as_of` is the first
   instance of the pattern. The pattern has more reach the codebase
   hasn't realized yet.
2. **Three sub-apps under one binary.** `/studio` (editor), `/dashboards`
   (HTMX renderer parallel to QS), `/docs` (mkdocs site). Today they
   share a process but not a navigation contract — each ships its own
   chrome, and the deployed-set varies per usage model (Studio-only for
   ETL dev; Dashboards-only for production-QS; all three for App2 prod
   demo). Operators hit dead-end nav when a sub-app they expect isn't
   deployed, or stacked chrome when sub-apps are embedded.

The fuzzy parts these enable are:

- Studio has **three latent modes** (L2 Editor / ETL Support / Training)
  that are blurred on `/studio` today. Mode differentiation is a UX +
  IA question, not (yet) an implementation question.
- The **YAML dogfood** isn't closed — some L2 entity kinds still can't
  round-trip the editor (Phase BF picked off Persona / Theme + a few
  scalar fields; the long tail isn't audited).
- **L2 plant generation** ships L1-shape violations only; L2 invariants
  (chain orphans, dead limit schedules, unmatched rail names) get a
  matview but no scenario-pre-planted seed.
- **`/docs`-vs-`_kv` posture** — should the docs site read from
  `_config_kv` so it speaks the institution's deployed shape, or stay
  L2-yaml-only as a static training reference? Open question; this
  phase decides.

## Usage models

| Mode                  | Deployed surface                | DB backends             |
| --------------------- | ------------------------------- | ----------------------- |
| **ETL dev / training**| App2 (Studio + Dashboards + Docs) | Oracle / Postgres / SQLite |
| **ETL dev / training**| QuickSight (Dashboards only)    | Oracle / Postgres       |
| **Production**        | QuickSight (Dashboards only)    | Oracle / Postgres       |
| **Production**        | App2 (Dashboards + Docs)        | Oracle / Postgres / SQLite |

Studio is dev-only; Dashboards + Docs are both dev *and* prod surfaces.
QS has no Docs equivalent (acceptable divergence).

## Personas

- **Integrator** — edits L2 yaml to define the institution's shape.
  Lives in `/studio/l2`.
- **ETL Engineer** — implements the data feed; needs to verify their
  feed aligns with the L2 shape declared by the Integrator. Lives in
  `/studio/etl`.
- **Trainer** — generates L1/L2 violation scenarios + walks End Users
  through how each violation surfaces. Lives in `/studio/training`.
- **End User** — operates / learns via Dashboards. May read `/docs`.
  Does not enter `/studio`.

## Architecture decisions

Five decisions have lock-direction (D1, D2, D4, D5, D6); D3 + D7
remain open. The locks aren't binding until BS.0 — but they shape
the PLAN tasks. D6 promoted to early spike.

| Decision | Status |
| -------- | ------ |
| D1 nav contract | lock-direction (always-on nav + cfg `studio_enabled` toggle) |
| D2 Studio modes | lock-direction (URL split + flat top-nav) |
| D3 dogfood gap close | extends to three round-trips (L2 + ETL + Training) |
| D4 ETL Support identity | lock-direction (arch shift + 3-page surface) |
| D5 L2 plant generation | verify-then-expose |
| D6 `_kv` ownership contract | **EARLY SPIKE — BS.1** |
| D7 `/docs` ↔ `_kv` | open (likely static default + future overview page) |

### D1 — Navigation contract — **lock direction**

**Lock:** the top nav is **always present** on App2 (Dashboards + Docs
are baseline). Studio is the only toggle. Source of truth for the
toggle is the **cfg yaml** (`studio_enabled: bool`), since cfg is the
runtime descriptor of what the binary serves.

Sub-asks still open:
- Cfg field name + default (`studio_enabled: true` for dev, set false
  for production deployments? Or invert?).
- The current dual-nav-shape bug (BF.11) — same problem at a smaller
  scale; BF.11 closes as a byproduct of D1's lock.
- QS is unaffected (no App2 binary, no nav contract).

### D2 — Studio mode differentiation — **lock direction**

**Lock:** URL split + **flat top-level nav** across the whole binary.
The Studio container intermediate goes away; Studio's three modes
become first-class nav entries alongside Dashboards + Docs.

Top-nav order (proposed):

```
L2 Editor | ETL Support | Training | L1 Dashboard | L2 Dashboard | Investigation | Executives | Docs
```

(Studio entries first — they're authoring; Dashboards next — they're
viewing; Docs last — they're reading.)

When `studio_enabled=false` (D1), the first three drop. Order stays
stable; entries just disappear.

Sub-asks:
- Does "Training" need a sub-nav for scenario/violation kinds, or is
  one landing enough? Deferred to D5 work.
- Per-page chrome (back-to-list, breadcrumb) is a smaller question;
  D2's flat-nav lock leaves it untouched.

- Comment: even though its flat, visually breaking them up with a divider will help orient people

### D3 — Dogfood gap close (L2 + ETL + Training)

The dogfood claim generalizes from L2-editor-only to **three parallel
round-trips**:

1. **L2 round-trip** — every L2 yaml in the corpus rebuilds via the
   editor (current AI scope).
2. **ETL round-trip** — every L2 yaml's seed-as-feed lands in
   `demo_database` via the ETL hook + matview refresh produces a
   green coverage report (D4's mode).
3. **Training round-trip** — every L1/L2 invariant violation kind
   can be planted via the Training mode + surfaces correctly on the
   relevant dashboard sheet.

BS tasks:
- **L2 audit redux** — walk every primitive in the corpus
  (spec_example + sasquatch + 5 fuzz seeds), inventory editor
  coverage *post-BF*. Output:
  `docs/audits/bs_3_l2_editor_coverage_audit.md`. **Side ask:** for
  each primitive, document *why it exists* (the curriculum gap the
  user flagged — e.g. "what's the point of personas?"). This is a
  doc deliverable, not just an inventory.
- **ETL round-trip claim** — fits in D4's scope (test data generator
  hooked as ETL hook = automated dogfood).
- **Training round-trip claim** — fits in D5's scope.

Common axis: extend the fuzz pool so every seed exercises all three
round-trips. AI.6 caught a wave-ordering bug; the broader axis will
catch more.

- Comment: There is an e2e dogfood possible here. Take a L2 YAML, recreate in the editor, deploy with the trainer (all defaults), the dashboards should match our existing test deploys. (Huge scope, but we're getting close to having the parts).

### D4 — ETL Support identity — **lock direction + arch shift**

`/studio/etl` becomes a first-class mode owning three surfaces +
**carries a substantive deploy-model change**.

#### D4.arch — drop the intermediate upstream-DB copy

**Lock:** the deploy pipeline collapses from `upstream → demo_db →
matview refresh` to `truncate(demo_db) → ETL hook (writes to demo_db
directly) → matview refresh`.

Why: the current "pull from upstream, copy into demo_db" step is
too many moves for an ETL engineer's iteration loop. The ETL hook
is the contract; the binary owns `demo_db`; iteration = truncate +
hook + refresh. Side benefit: D3's "ETL round-trip" claim becomes
automatable — point the hook at the test data generator and the
honest dogfood path is literally the same code path operators use.

This is an architectural lock with reach beyond the ETL Support
mode itself — `cli/data.py::data_apply`, the existing `etl_hook`
plumbing, and the runner's seed flow all touch this. Spike before
implementing (see BS.0 sequencing).

#### D4.surface — three ETL Support pages

1. **L2-slice probe** — operator picks a slice of the L2 (one rail,
   one template, one chain); the page answers "what does this slice
   need to see in `<prefix>_transactions` + `<prefix>_daily_balances`
   to match?" Column-by-column expectation derived from the L2.
2. **ETL execution + coverage** — operator clicks "run"; the binary
   truncates demo_db, invokes the ETL hook, refreshes matviews; the
   page reports a per-kind coverage tally (rails / templates /
   metadata / chains), **including required-metadata-landed-yes/no
   per template**. Coverage report green = ETL contract satisfied.
3. **Exception triage with handoff** — for rows the ETL produced that
   didn't fully match, surface partial-match shape:
   - "Row matched `account_role` ✓ but the L2 doesn't declare
     rail=`ach`. The L2 declares: ach_credit, ach_debit, wire.
     Open the L2 editor on the rails block to add it? [link]"
   - "You partially matched on these (role, rail). The L2 only
     declares these transfer templates for the role — open templates
     to extend. [link to templates]"

   Each partial match links into `/studio/l2` *pre-filled to the
   edit that would close the gap*. This is the Integrator / ETL
   Engineer handoff baked into the UX.
  - Comment: pre-filled may be a stretch, we may have to evaluate when we get here what is possible

#### D4.sub-asks

- The match-vs-not-matched view (#3) implies a per-column-pair
  contract derivable from the L2. Audit `common/l2/` to see whether
  the typed primitives already carry it, or whether a new derivation
  step is needed.
- After D4.arch lands, what does the existing upstream-pull code
  become — deleted, or kept as an opt-in "import from external" path
  for one-shot migrations? Defer to implementation; both viable.

### D5 — L2 plant generation — **verify then expose**

The matviews exist for every L2 invariant (chain orphans, dead limit
schedules, unmatched rail names, …) and the spine generators may
already carry the plant primitives — we just don't surface them in
Training mode.

BS tasks:
- **Audit existing plant coverage** — inventory `common/spine/*.py`
  + scenario primitives, map each L2 invariant matview to the plant
  (if any) that fires it. Output: a per-invariant table in
  `docs/audits/bs_5_l2_plant_inventory.md`.
- **Expose in Training mode** — plants that exist but aren't reachable
  from Training mode get a UI surface. Plants that genuinely don't
  exist yet get added.
- **Round-trip claim** (D3.3) — every L2 invariant has at least one
  fuzz-pool plant that surfaces correctly on its matview-bound
  dashboard sheet.

### D6 — L2 ↔ `_config_kv` ownership contract — **early spike (BS.1)**

The L2 yaml stays authoritative. `_config_kv` is derived per deploy.
This is an **early spike** before BT/BU lock — the framing answer
reshapes how everything downstream thinks about test surface size.

**Reframe (user 2026-05-29):** the goal isn't "shift more SQL to
dynamic JOINs." The goal is the inverse — with `_kv` carrying the
L2 shape at runtime, the **SQL emit layer can become *more static***.
Less Python branching at emit time; more uniformity across L2s
because per-institution variation is absorbed by `_kv` joins. Test
surface shrinks: instead of N SQL forms × N L2s, it becomes
**one canonical SQL × N `_kv` contents**.

The pattern AW already proved: `as_of` used to be a Python-side
constant baked into each emit; now it's a `_kv` row the matview
reads at refresh time. Same matview SQL serves every deploy.

Spike scope:
- Inventory current emit paths in `common/`/`apps/<app>/datasets.py`
  + matview SQL in `common/l2/schema.py`. For each, classify:
  - **(P0)** could collapse to canonical-SQL + `_kv` join — clear win.
  - **(P1)** could collapse but cost-of-conversion outweighs benefit.
  - **(P2)** legitimately per-L2-variable; keep dynamic emit.
- Per-row: estimated test-surface delta (e.g. "this matview's per-L2
  output drops from N variants to 1 — N parametrized tests collapse
  into 1 + a `_kv` fixture matrix").
- Output: `docs/audits/bs_6_kv_static_collapse_audit.md`. Explicit
  recommendation on which P0 items to pursue in this phase vs. defer.

This audit is **deep work** — the user has flagged it as the most
valuable scaffolding deliverable. Land a thorough inventory even
if the conversion itself slips to a follow-on phase.

### D7 — `/docs` ↔ `_config_kv`

Today's mkdocs is L2-yaml-only at build time.

Options:
- **(a) Static, L2-yaml only** — `/docs` stays the institution-agnostic
  training site. Operator-flavored content (e.g. "your DDAControl is
  named X") lives elsewhere.
- **(b) Templated via `_config_kv`** — `/docs` pages substitute live
  institution names. Closer to the L1 dashboard's persona-resolution,
  costlier to author.

Likely (a) for the static training corpus + (b) for a future "your
deployment overview" page if/when an operator asks. Lock the default
here; design (b) as a follow-on.

## Non-goals

- Re-architecting the QS deploy path — QS stays as-is; the divergence
  it carries (no Docs, no Studio editor) is acceptable per the usage
  model table.
- Re-implementing the dashboard renderer — Phase X.2 covers App2; this
  phase touches navigation + Studio modes, not visual emission.
- Mobile / responsive — separately deferred; not surfaced here.

## Constraints

- **Don't drift L2 ↔ `_config_kv`** — the same yaml that emits the
  schema also populates `_config_kv` at schema-apply. Already in
  `cli/data.py::data_apply` via `replace_config`.
- **Don't break the QS leg** — every Studio change has to preserve
  the App2-vs-QS parity gates (existing e2e tests + the new BR.x
  unmapped-DSP unit gate).
- **No new sub-app process boundaries** — Studio / Dashboards / Docs
  stay one Python binary, one port, one cfg.

## Phase decomposition — realistic chunks

The decision set is bigger than one phase. Proposed split into four
coherent, individually-shippable phases. Sequencing matters: BS lands
the scaffolding the user-facing phases build on.

### Phase BS — Foundation (scaffolding) — *this phase*

Internal-scaffolding + nav cleanup. No new user-facing surfaces, but
the deploy model + nav contract shifts that everything downstream
depends on.

- **BS.1** (early spike) — D6 `_kv` dynamic-potential audit. Output:
  `docs/audits/bs_6_kv_dynamic_potential.md`. Decides whether to
  pursue further dynamic conversion this phase or defer.
- **BS.2** — D1 cfg `studio_enabled` toggle + always-on nav.
- **BS.3** — D2 flat top-level nav, collapse Studio container.
  Closes BF.11 as byproduct.
- **BS.4** — D4.arch deploy-model shift: drop intermediate
  upstream-DB copy; truncate(demo_db) → ETL hook → matview refresh.
  Touches `cli/data.py`, runner seed flow, the existing `etl_hook`
  plumbing. The spike-before-implementing call belongs in BS.0
  (lock + sequencing).
- **BS.5** — opt-in dynamic SQL conversion per BS.1's recommendation,
  if scope-positive.

**Done when:** App2 ships with one shared top nav + cfg-gated Studio;
the deploy pipeline matches D4.arch; BF.11 closes; BS.1 audit
recommends a path for further `_kv` adoption.

### Phase BT — ETL Support surface (user-facing)

Builds on BS.4's deploy-model shift. The first new operator surface.

- **BT.1** — `/studio/etl` flat-nav entry + landing page.
- **BT.2** — D4.surface #1: L2-slice probe (per-rail / per-template /
  per-chain expectation viewer).
- **BT.3** — D4.surface #2: ETL execution + coverage report
  (per-kind tally, including required-metadata-landed yes/no).
- **BT.4** — D4.surface #3: Exception triage + partial-match
  handoff back into `/studio/l2`.
- **BT.5** — Per-column-pair contract derivation in `common/l2/`
  (or wherever the type system lives) backing BT.4's match view.

**Done when:** an ETL engineer can iterate (truncate → run → triage
→ jump to L2 editor → fix → repeat) on a real feed against a real
L2 in under one minute per cycle.

### Phase BU — L2 plant generation + Training mode (user-facing)

Builds on BS.4 + BT (the plant generator may want to reuse BT's
coverage-report shape).

- **BU.1** — D5 plant inventory audit. Output:
  `docs/audits/bs_5_l2_plant_inventory.md`.
- **BU.2** — Expose existing plants under `/studio/training`.
- **BU.3** — Add missing plants per BU.1.
- **BU.4** — Training landing + sub-nav (D2's deferred sub-nav
  question lands here).
- **BU.5** — Per-invariant "before/after dashboard" tour: pick a
  plant kind, see the dashboard sheet that surfaces it, no plant
  vs. with plant side-by-side.

**Done when:** a Trainer can demo any L1 or L2 invariant on demand;
every L2 invariant matview has at least one reproducible plant.

### Phase BV — Dogfood completion (the cap-stone)

The full round-trip claim. Glues L2 editor + ETL + Training under
one e2e test.

- **BV.1** — D3.1 L2 audit redux on the post-BF / post-BS surface.
  Output:
  `docs/audits/bs_3_l2_editor_coverage_audit.md` + the
  per-primitive curriculum doc (the "what's persona for?" question
  the user raised).
- **BV.2** — D3.2 ETL round-trip claim wired (test data generator
  hooked as ETL hook; BT.3's coverage report green per fuzz seed).
- **BV.3** — D3.3 Training round-trip claim wired (every plant kind
  surfaces correctly per fuzz seed).
- **BV.4** — **The HUGE one** (user's D3 comment): take an L2 yaml
  → recreate it in the editor → deploy via Training mode (all
  defaults) → assert dashboards match the existing test deploys.
  Single e2e test, browser layer. Closes the dogfood claim end-to-end.

**Done when:** BV.4 is green on every L2 in the fuzz pool;
`tests/e2e/test_studio_dogfood_browser.py` + descendants assert the
full round-trip.

### Design-review cadence (BT + BU + BV)

The later phases ship user-facing surfaces. Two complementary
review loops *before* each phase exits:

**Agent-based design cold-reads.** Same shape as the existing
`docs/audits/v11_XX_X_feedback.md` cold-reads, but pre-implementation.
Give an agent the SPEC section + any wireframes / page sketches /
prose / partial implementations. Ask it to read as a named persona
("you are the ETL Engineer arriving at `/studio/etl` for the first
time") and produce a per-phase audit:

- What's confusing without a docstring next to it?
- What's missing for your job-to-be-done?
- What's there that you'd skip?
- What concept names don't land?

Per-phase audits → `docs/audits/bt_cold_read.md` / `bu_cold_read.md`
/ `bv_cold_read.md`. Feasible today — the dashboard cold-reads
already prove the model; design cold-reads are the pre-impl variant.

**Hands-on iteration.** Plenty of operator-driven cycles. Phase
exit isn't gated only on tests passing — operator dogfoods the
surface against a real (or sasquatch_pr) L2 + signs off.

For BS itself (this phase): scaffolding-only, no user-facing surface
change beyond nav cleanup. BS.0 lock check + standard test gate is
sufficient; cold-read deferred until BT.

### Phase BW (optional / deferred) — Docs posture

- **BW.1** — D7 lock + minimal implementation (likely static-default,
  per the SPEC).
- **BW.2** — "Your deployment overview" page reading from `_kv`, if
  operator demand surfaces. Could be a "follow-on if asked" rather
  than scheduled work.

---

## What this SPEC does NOT lock

Specific tech for the top-nav widget. Mode-switcher UX flavor.
Whether to ship D5 in the same release as D1. Those wait for BS.0
discussion + PLAN.md.

## Glossary refs

- **AW** (Phase AW, 2026-05-23): owned-time spike that landed
  `<prefix>_config_kv`. See `docs/audits/date_range_model_audit.md`.
- **BC.12** (2026-05-23): reshaped `_config(as_of, cfg, l2)` →
  `_config_kv(node_id, parent_id, key, value)` to dodge Oracle's
  ORA-32368 on JSON_TABLE matviews.
- **BF** (Phase BF, archived 2026-05-25): Studio editor form
  completeness — persona + theme yaml-block → structured form, plus
  field-level coverage for the singletons.
- **AI** (Phase AI, in-progress): Studio editor dogfood — every L2
  yaml in the corpus must round-trip the editor.
