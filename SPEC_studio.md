# SPEC — Studio (Phase X.4 / X.5: implementation tools)

> Status: drafted 2026-05-12 from `docs/x_4_5_design_thoughts.md`.
> Sibling docs: `docs/Schema_v6.md` (the L2 model spec), `SPEC_ARCHIVE.md` (the original PR/AR-era spec, archived), `PLAN.md` (the X.4 / X.5 work breakdown that derives from this SPEC).
> Vocabulary note: this SPEC uses **Studio** (was "App 1") and **Dashboards** (was "App 2") throughout. The `docs/x_4_5_design_thoughts.md` iteration log uses the old names in places — preserved as history.

## Goal

Studio is the **implementation tools** surface that turns this codebase from "a YAML + a CLI + dashboards" into "a place where the integrator, the trainer, and the ETL engineer get their job done." It runs in the same Starlette process as Dashboards (the read-only self-hosted dashboard surface), shares `config.yaml` + the L2 institution YAML + the database, and is reached via `quicksight-gen studio`.

Three personas, three loops Studio collapses into one front door:

- **Integrator** — designed the L2 YAML, bristles at editing it as a huge text file with subtle cross-references that break on rename. Studio gives them: one interactive diagram of the whole L2 (toggle by entity type, click to focus a subgraph), per-entity edit forms (with server-owned cascade so renames rewrite refs automatically), and live validation.
- **Trainer** — wants to demo "the system over time," planting specific exceptions and stepping students through days. Studio gives them: a data-shaping panel (which exceptions to plant, which day to advance to, which random seed produces the layout they want), a vertical plant-timeline showing where exceptions land in the current window, and a one-click "Deploy changes" that re-seeds the database and Dashboards reloads.
- **ETL engineer** — wants to know if their ETL pipeline covers what the L2 asks for. Studio gives them: the same diagram, tinted by data coverage (binary: rows / no rows per L2 primitive); an `etl_hook` shell command that runs their pipeline; an `etl_datasource` URL that Studio pulls rows from into the demo DB; and a generator scope (`uncovered_rails`) that fills only what the ETL didn't.

The thread tying it all together is the **"Deploy changes" pipeline** — one button in Studio, five steps (etl_hook gate → wipe + optional ETL pull → generator → matview refresh → Dashboards auto-reload), some conditional. That pipeline IS the implementation-tools loop.

## Non-goals

- **Not a generic ETL tool.** Studio orchestrates `etl_hook` (a shell command the ETL engineer fills in) and pulls from an `etl_datasource` URL — that's the ENTIRE ETL surface. Whatever extracts/transforms feed into the engineer's hook is opaque to Studio; we are deliberately not building a SQL step builder, a transformation DSL, or a job scheduler.
- **Not a hosted multi-tenant service.** Studio runs locally on the operator's machine. `etl_hook` is "run an arbitrary command from a button" — fine for local dev; never expose on a hosted Studio instance.
- **Not a replacement for Dashboards.** Studio mounts Dashboards under it (`studio` ⊃ `dashboards`); they share a process. Dashboards is independently runnable (`quicksight-gen dashboards`), and the architecture stays severable for the day Studio's writes need a different auth posture than Dashboards' read-only views.
- **Not changing QuickSight or Dashboards rendering.** The QS pipeline (`json apply --execute`) and Dashboards' visual rendering are settled (X.2 wrap, v9.0 → v9.4). Studio is *new routes* + a *fuller diagram projection* + *generator-shaping knobs* — zero touch on those subsystems.
- **Not preserving YAML comments.** Studio re-serializes the L2 YAML from the loaded `L2Instance` model on every save. `description:` fields survive (model data); freeform `# comments` don't. No `ruamel.yaml`.
- **Not a SPA.** The editor is dumb HTMX (server-owned cascade). No client-side state machine, no diffing, no React-shaped surface.
- **Not the X.2-era exhaustive scenario × dialect × target test matrix for Studio's own surface.** Studio is a dev tool with different exposure than the customer-facing Dashboards render surface that justified that fan-out. The codebase keeps cross-dialect support; we just don't force the matrix on every Studio test. See "Testing scope" below for what we DO test.

## Personas + their loops

### Integrator — "did I design my YAML right?"

Today: edits a 500-line YAML in a text editor; runs `schema apply --execute`, `data apply --execute`, `data refresh --execute`; opens four QuickSight dashboards or Dashboards (the self-hosted renderer); spots that a chain references a renamed template and broke; fixes; re-runs. Diagrams are static SVGs in the docs site, one per topic — hard to see the institution as a single thing.

With Studio: opens `quicksight-gen studio` → lands on the unified diagram of their L2. Toggles "show only Chains" to verify the supersession structure; clicks an account to focus its subgraph. Renames a role via the Account form; the server rewrites every rail/chain reference; validates; the diagram and the entity list refresh themselves; the YAML on disk is updated. Hits "Deploy changes" → regenerated demo data lands; Dashboards (open in another window) reloads with the new structure visible.

### Trainer — "show students how the system works over time"

Today: runs `data apply --execute`, all 90 days of data appear at once, all planted exceptions visible together; can't say "find the drift on day 12 before I tell you about overdraft on day 23."

With Studio: opens the data-shaping panel. Picks `plants: [drift]` (just drift) and a seed that produces a clean drift on day 12 (sees this on the plant-timeline view). Sets `end_date` to day 11 → Deploy → Dashboards shows clean data. Students inspect, find nothing wrong. Trainer advances `end_date` to day 12 → Deploy → drift appears on the dashboard, students find it. Then enables `plants: [drift, overdraft]` and steps to day 23. The day-stepper IS the lesson plan.

### ETL engineer — "is my ETL pipeline right?"

Today: writes their ETL pipeline; runs it; opens the database, runs ad-hoc queries to see if rows landed where the L2 expects them; argues with the integrator about whose schema is wrong.

With Studio: configures `etl_hook: ./run-my-etl.sh` and `etl_datasource: postgres://...` in `config.yaml`. Hits Deploy → Studio runs their ETL → pulls into the demo DB → diagram tints in coverage mode: green = rows present, red = no rows. Sees three rails are red. Toggles `scope: uncovered_rails` → Deploy again → generator fills only the gaps with synthetic data. Now Dashboards renders end-to-end with their real data plus synthetic-fill where their ETL doesn't yet feed.

## Architecture

### Process model

`quicksight-gen studio` and `quicksight-gen dashboards` both run the same Starlette app (a descendant of today's `common/html/server.py`), differing only in which routes they mount:

- `dashboards` mounts: the dashboard view routes (`/dashboards/...`), `/docs`, `/dev_log`, the static-asset mount.
- `studio` mounts: everything `dashboards` mounts, **plus** Studio's editor routes (`/l2_shape/...`), data-shaping routes (`/data/...`), the orchestration endpoint (`POST /deploy`), and the Studio landing page (`/`).

**Severability contract:** `dashboards` MUST keep working with Studio's routes absent. Studio routes never assume Dashboards-side state (no shared in-memory cache that Dashboards reads). When phase.2 auth lands and Studio needs writes-grade auth, splitting Studio into its own process is a routing-table edit, not a rewrite.

### Source-of-truth

The L2 YAML on disk is the **hard truth**. Studio:

- Loads it once at startup into an in-memory `L2Instance` (existing `common/l2/loader.py::load_l2`).
- Holds that model in memory for performance (no per-request YAML parse).
- Writes back to the YAML file on every save — the in-memory model is a cache of the file, never a parallel source of truth.
- Re-validates with the existing strict validator (`common/l2/validate.py`) on every PUT before persisting.

There is no Studio-side database for state. No `studio.db`. No second YAML. The integrator can `git diff` their L2 YAML at any time and see exactly what Studio wrote.

### Editor cascade discipline

The editor's complexity-limiter (carried forward from `docs/x_2_design_thoughts.md`):

> **The server owns the entire cascade.** PUT entity → re-validate → rewrite references in the model → re-serialize the *whole* YAML → respond with the updated entity body + (if anything rippled) `HX-Trigger: l2-cascade-reload`. The client is dumb HTMX: it swaps the returned form fragment in place, and on the cascade-reload trigger the diagram + entity list `hx-get` themselves. **No client-side cascade computation, no diffing, no SPA state.**

Two cascade kinds:

- **Rename = auto-rewrite refs.** Renaming an identifier walks the model and replaces every field that references the old value (rails' `source_role` / `destination_role` / `leg_role`, chains' parent/child references, templates' leg-rail composition). The model knows what references what — the strict validator already enforces those references; the rename uses the same knowledge to mutate them.
- **Structural break = reject, don't auto-cascade.** Deleting or restructuring something another entity depends on (e.g., dropping a rail that a template composes) → the strict validator catches it post-mutation → Studio returns 400 with the validator error inline → the user fixes the dependent first. We do NOT cascade-delete dependents; the ripple is bounded.

### Validation-error UX

A bad PUT returns 400 + the validator's error rendered inline in the form fragment ONLY (targeted HTMX swap). The user's typed-but-invalid content is preserved in the form so they can fix it. The diagram and the rest of the entity cards are untouched.

### Allowlist expansion

`config.yaml`'s strict allowlist (V.1.b) gains three keys, all operator-machine-local (env-ish, not L2-institution structure):

- **`etl_hook: <shell command string>`** — optional. The ETL engineer's pipeline command. Studio runs it as the first step of "Deploy changes."
- **`etl_datasource:`** block — optional. Connection URL + the table allowlist (`transactions`, `daily_balances`). The source DB Studio pulls rows from into `demo_database_url`. Read-only from Studio's perspective.
- **`test_generator:`** block — the operator's preferred shaping-knob defaults for THIS machine. See "Data-shaping model" below.

All three absent → today's behavior (no ETL pipeline run, no pull, generator runs at `data apply` defaults). Hard constraint: every field in `test_generator:` has a default such that the absent-block case emits SQL byte-identical to `tests/data/_locked_seeds/*` — `test_locked_seed_matches_fresh_emit` keeps passing.

## The "Deploy changes" pipeline

One button in Studio, available in every mode (editor / data-shaping / coverage). Conceptually:

```
[Deploy changes]
    │
    ▼
1. (if etl_hook set)  → run shell command  [GATE — non-zero halts; demo DB untouched]
    │ (gate passes)
    ▼
2. WIPE demo_database_url base tables (unconditional once we reach this step)
   (if etl_datasource set)  → copy rows ≤ end_date from etl_datasource
3. (if test_generator.enabled) → emit_full_seed (always additive — wipe was step 2)
4. refresh matviews
5. ping Dashboards: data-generation-id bumped
                   ↓
              Dashboards' open page reloads its current URL
              (URL-driven → state mostly survives the swap)
```

**Step 1 is a gate.** If `etl_hook` is set and exits non-zero, the pipeline halts BEFORE step 2 — the demo DB is never wiped, the previous successful Deploy's data is preserved, and the user fixes their hook and clicks Deploy again. If `etl_hook` is unset, there's no failure to gate on and the pipeline proceeds straight to step 2.

Once we're past the gate: step 2's wipe is unconditional ("always" in the sense of "always when we reach this step"). Step 2's pull and step 3 are conditional on their respective config keys. A failure inside steps 2-5 halts the chain (safe-fail; demo DB is left in whatever consistent-ish state the failing step produced — possibly wiped + partial pull, or wiped + partial generation).

### Step 1 — etl_hook (the gate)

If `etl_hook` is set, Studio executes it as a shell command. Stdout/stderr stream to Studio's `/dev_log` so the user sees the ETL pipeline's output live.

**The hook's exit code gates the entire downstream pipeline.** Non-zero exit → halt **before** step 2 fires. The demo DB is *never touched* on a hook failure: no wipe, no pull, no generator, no matview refresh, no Dashboards reload. The previous successful Deploy's data is preserved as-is, the user sees the hook's stderr in `/dev_log`, fixes their command, and clicks Deploy again. (If `etl_hook` is unset, there's no failure to gate on; the pipeline proceeds to step 2.)

This gate is the safety floor: a broken ETL pipeline can never wipe out a working demo DB.

### Step 2 — wipe demo DB + (optional) etl_datasource pull

Studio **always wipes** `<prefix>_transactions` + `<prefix>_daily_balances` in `demo_database_url` at this step (via the existing `wipe_demo_data_sql(l2_instance, dialect)` primitive in `common/l2/seed.py`). The wipe is unconditional because **Studio owns the refresh of `demo_database_url`** — every Deploy starts from a clean base.

Then, **if `etl_datasource` is set**, Studio opens it as a separate read-only connection and copies rows from its `transactions` + `daily_balances` (filtered to `posted_at ≤ end_date` / `balance_date ≤ end_date`) into `demo_database_url`. The pull is **cross-dialect**: the ETL DB may be PostgreSQL or Oracle, the demo DB may be SQLite. Reuses the existing dialect machinery (`common/sql/dialect.py`) and the Oracle INSERT-ALL batcher (`common/db.py::batch_oracle_inserts`).

**Ownership boundary:** the `etl_hook` is in charge of refreshing the *`etl_datasource`* (the ETL engineer's pipeline owns its own DB). Studio owns the refresh of *`demo_database_url`* — that's why the wipe is unconditional and at the top of step 2. After step 2, the demo DB is either empty (no `etl_datasource`) or a clean snapshot of the `etl_datasource` rows (filtered to `≤ end_date`). Whatever the generator does in step 3 layers cleanly on top of that known starting point.

### Step 3 — generator (always additive)

If `test_generator.enabled`, Studio runs the generator (`emit_full_seed`) with the current `test_generator:` shaping params, writing into `demo_database_url`. **Always additive** — the generator does NOT wipe; the wipe was step 2's job. The generator's `scope` knob (see "Data-shaping model") picks what it adds:

- `scope: full` on top of an empty demo DB (no `etl_datasource`) = today's `data apply --execute` output, byte-identical to the locked seeds.
- `scope: full` on top of an `etl_datasource` snapshot = the full 90-day baseline + plants layered over real data (probably redundant; `uncovered_rails` or `exceptions_only` is the natural pick when ETL data is present).
- `scope: uncovered_rails` = inspects what step 2 put in the demo DB and only generates baseline rows for rails that don't already have data.
- `scope: exceptions_only` = no baseline, just the planted violations.

(Note: today's CLI `data apply --execute` keeps its existing wipe-then-emit behavior — that lives in `cli/data.py`, not in `emit_full_seed`. The "always additive" rule above is Studio's pipeline composition, not a behavior change to `emit_full_seed` itself.)

### Step 4 — refresh matviews

The existing `refresh_matviews_sql(l2_instance)` re-runs the matview refresh chain (REFRESH MATERIALIZED VIEW for PG/Oracle, DROP+CREATE TABLE AS SELECT for SQLite). All Dashboards visuals key off these matviews; they need to be current after a re-seed.

### Step 5 — Dashboards reload

Studio bumps a process-local `data_generation_id` counter. Dashboards' open page polls (or subscribes to) that counter and reloads its current URL when the counter advances. Because Dashboards is URL-driven (X.2's all-GET REST design), reloading the same URL re-fetches data into the same view — the user's navigation state (which sheet, which filters) mostly survives the swap.

The **killer demo:** trainer changes `end_date` from day 12 to day 13 in Studio, clicks Deploy → step 3 re-runs the generator with the new cutoff → step 4 refreshes matviews → step 5 makes Dashboards reload → the new day's planted exceptions just appear on the screen. No restart, no reconnect, no manual reload.

### Edge cases (when we hit them)

- **A rail/account that the open Dashboards page references just got deleted** — refresh will show "no data" for that visual. Acceptable; we'll handle it specifically when it bites.
- **etl_hook failure** — pipeline halts at the step 1 gate; Studio surfaces the hook's stderr in `/dev_log`; **demo DB is never touched** (no wipe, no pull, no generator, no matview refresh, no Dashboards reload). The previous successful Deploy's data is preserved.
- **PK collisions** between real `etl_datasource` `transaction_id`s and generator-synthetic ones — deferred. Natural fix when it bites: exclude the colliding accounts from generation (= `scope: uncovered_rails`).

## Data-shaping model

The `test_generator:` block in `config.yaml` carries the shaping knobs. Every field is optional; the all-absent case → today's behavior, byte-identical to the locked seeds.

```yaml
test_generator:
  enabled: true              # default true; set false to skip step 3 entirely
  scope: full                # full | uncovered_rails | exceptions_only (default: full)
  end_date: null             # ISO date or null; null = full 90-day window
  seed: <default-int>        # int; default = today's locked-seed value
  plants:                    # subset of exception kinds; absent = all
    - drift
    - overdraft
    - limit_breach
    - stuck_pending
    - stuck_unbundled
    - supersession
  only_template: null        # template name or null; null = all templates
  derive_balances: false     # bool; future expansion (subledger → GL)
```

### scope

Three modes, dial-able by the user:

- **`full`** (default) — today's behavior: 90-day baseline + planted exceptions on top. Byte-matches the locked seeds. The integrator's everyday demo.
- **`uncovered_rails`** — generator inspects `demo_database_url` (post step 2 of the pipeline) and only generates baseline rows for rails that don't already have data. Pairs symmetrically with the ETL coverage overlay: the coverage view shows the integrator what's missing; this scope fills only what's missing. The natural ETL-engineer choice.
- **`exceptions_only`** — skip the 90-day baseline, only emit the planted violations on top of whatever's already there. The natural trainer choice when planting teaching scenarios on top of real ETL data.

### plants

Subset of exception kinds to plant. Absent / empty = all kinds (today's behavior). UI-only knob — not exposed as a CLI flag (it's an interactive trainer tool, not a build-pipeline thing).

### end_date

ISO date cutoff. Generator emits rows with `posted_at ≤ end_date`; pulling from `etl_datasource` honors the same cutoff. Null = the full 90-day window. The trainer's "advance one day" stepper is a thin frontend over this knob: click "next day" → end_date += 1 → Deploy changes → Dashboards reloads with the new day's data + any plants that hit on or before it.

### seed

The generator's random seed. Deterministic generator → same seed → byte-identical output. Default = today's locked-seed value, so the all-absent case stays byte-identical to the locked seeds. The trainer scrubs through seeds to find a planted-exception layout that suits their lesson; the integrator pins a seed to repro a specific scenario across machines.

### only_template

Generate only this template (and the rails it composes, and the accounts those rails touch — its dependency closure). Produces a deliberately partial-but-consistent dataset for demoing one flow without the whole institution. **Additive build** — ships when it's the next valuable knob, not in the first cut.

### derive_balances

Subledger → GL derivation: feed real subledger transactions, generate the control-account daily balances that satisfy double-entry (the drift invariant run forward instead of checked). The most well-defined of the three derived-data flavors and the most valuable for the ETL engineer (subledger feeds are often easy/real, GL feeds are derived/hard). **Additive build** — placeholder field now, implemented when it's the next valuable knob.

## The plant-timeline view

Studio's data-shaping panel renders a **vertical timeline column** — one row per day in the generation window — annotating each day with the planted exceptions that hit on it, computed from the *current* shaping config (scope + plants + seed + only_template). So the trainer SEES before they advance:

```
Day  1  ─────────────
...
Day  7  ▌ stuck_pending  (txfr-spec-1234)
...
Day 12  ▌ drift          (FRB_Master, +$1,234.56)
...
Day 23  ▌ overdraft      (Customer_DDA, -$500.00)
        ▌ limit_breach   (Concentration_GL, day +1)
...
```

Click a day → `end_date` jumps to that day → Deploy changes. Re-renders as the shaping knobs change (a different seed → different planted positions → different annotations).

The scenario object (`default_scenario_for(l2)` and friends) already encodes "this plant hits day N." The timeline is a **UI projection of data we already have**, not new generator logic.

## The unified diagram

Studio's defining visual: ONE interactive diagram of the L2 — replaces the static, multi-diagram training material. Three personas use it:

- **Integrator:** comprehension + click-to-focus subgraph.
- **ETL engineer:** same diagram, tinted by data coverage (binary: rows / no rows per L2 primitive, row-count on hover).
- **Trainer:** same diagram, "what's planted / current day" annotated.

ONE renderer, mode-switched overlays. The exact UI shape "falls out of the implementation" — we're not over-planning the mode-switching machinery.

### The graph model — typed projection + per-rail dot renderer

`common/l2/topology.py::topology_graph_for(instance)` walks an `L2Instance` once into a typed `TopologyGraph` (frozen value object). `build_topology_graph_per_rail(instance, *, focus_node_id=None)` then emits a `graphviz.Digraph` where every Rail is a first-class node — `src_role → rail → dst_role` becomes a 3-rank chain dot lays out deterministically. Bundle nodes consolidate parallel pure-connectivity rails (anchored rails — chain endpoints / template leg-rails — stay individual). Templates render as `cluster_*` subgraphs around their leg-rails. Chains as dashed edges between rail/template nodes. Control-parent (subledger → control role) as dashed gray edges with `arrowhead="onormal"`.

Compactness defaults baked in: `nodesep=0.15`, `ranksep=0.35`, `mclimit=2.0` (more crossing-reduction iterations), `concentrate=true`. Trades CPU for visual density; sasquatch_pr lays out under 100ms.

### Renderer locked: graphviz dot, rails-as-nodes (X.4.b spike, 2026-05-13)

The X.4.b spike compared D3 + d3-force against post-processed graphviz. Dot won: the rails-as-first-class-nodes model insight let dot's deterministic rank algorithm produce the user's mental "roles → rails → roles" reading with zero knobs, while d3-force required per-graph manual tuning that never converged (28 chrome sliders accumulated before the user named the treadmill). Full judgment in `docs/audits/x_4_b_diagram_renderer_spike.md`.

Diagram surface "good enough" criteria, all met:

- `sasquatch_pr` renders without overlap or unreadable label collisions ✅ (deterministic dot layout).
- All four entity-type toggles (Roles / Rails / Chains / Templates) ✅ (CSS-class visibility toggles via `data-kind` post-processing in `diagram.js`).
- Click-a-node → focus-subgraph ✅ (server-side filter + `?focus=<node_id>` URL navigation; dot re-lays out the focused subset cleanly. Click-empty-canvas / Esc / Reset drop the param. Smart-default hops by node kind: roles/templates default to 2 to cross a rail; rails/bundles default to 1).
- Coverage-tint mode hook ✅ (mode-stub overlay wired; real fetcher is X.4.c.5 work).

The d3 ForceGraph in `common/tree/visuals.py` is unrelated and stays for any future sheet-visual use; the diagram surface is dot-only.

### What the diagram lets you do

- **Toggle entity types** on/off — Roles (Internal / External), Rails, Chains, Templates, Control hierarchy each get a checkbox in the diagram chrome.
- **Click a node** → re-renders the focused subset (focus + smart-default hops; roles/templates cross one rail, rails/bundles show endpoints). Click-empty-canvas / Esc / Reset all clear focus.
- **Reset filters** — back to the full graph, all types visible.
- **(Coverage mode)** — nodes/edges tinted by data presence; row-count on hover.
- **(Trainer mode)** — planted exceptions visually located on their host entities.

The diagram is **navigate-only** — clicking focuses, but you can't draw-to-edit. Edits happen in the cards below (the editor surface).

## The editor

Five entity types get per-entity card forms, in additive build order:

1. **Account**
2. **Rail** (TwoLegRail + SingleLegRail subtypes)
3. **Theme**
4. **Chain** (richer: required/xor-group child list; sub-list editor)
5. **TransferTemplate** (richer: composes leg-rails; sub-list editor)

Account / Rail / Theme are flat forms — easy. Chain / TransferTemplate need a sub-list editor — more work, lands once the flat-form pattern is proven on the first three. Each form shape is "trivial agent-worthy work" once the cascade pattern is set; the diagram spike is the serial bottleneck.

### Per-entity routes

Mechanical pattern, repeated per entity kind:

- `GET /l2_shape/<kind>/` — list view (entity rows, click to expand).
- `GET /l2_shape/<kind>/<id>` — read-only card.
- `GET /l2_shape/<kind>/<id>/edit` — editable form fragment.
- `PUT /l2_shape/<kind>/<id>` — save (server-owns-cascade as above).
- `POST /l2_shape/<kind>/` — create new.
- `DELETE /l2_shape/<kind>/<id>` — delete (subject to validator's reject-on-structural-break rule).

Field labels and helper text in the form templates come from `common/l2/primitives.py` field docstrings, **NOT hand-written in the editor**. Same source X.6's auto-reference will eventually consume — keeps editor + docs aligned by construction.

## CLI surface

```
$ quicksight-gen studio -c config.yaml --l2 inst.yaml
   → launches Studio on http://localhost:8765 (Studio + Dashboards mounted)
   → opens browser to http://localhost:8765/  (Studio landing)

$ quicksight-gen dashboards -c config.yaml --l2 inst.yaml
   → launches Dashboards alone (read-only) on http://localhost:8765
   → opens browser to http://localhost:8765/dashboards/
```

### Replaces: serve app2 apply → dashboards

`serve app2 apply` is **removed outright** when `dashboards` lands — no deprecation window, no alias. The user is the only one who ever saw it; there's no third-party scripting to protect, so the deprecation cycle is pure cost.

The `serve` Click group goes away with it (the only thing under it was `app2 apply`). Top-level verbs (`schema apply`, `data apply`, `json apply`, `audit apply`, `studio`, `dashboards`) are the going-forward shape.

## Open / deferred

These are explicitly deferred (per the standing "don't silently defer" rule — flagged here, not buried):

- **Persistence of in-flight shaping params.** Today: ephemeral per-session Studio state; on restart, reads the `test_generator:` block from `config.yaml` for defaults. If a trainer wants to save "today's lesson configuration" → small `scenario.yaml` later, when asked. Not in the first cut.
  - Later I'll advocate for saving an updated config.yaml
- **PK collisions** between real `etl_datasource` `transaction_id`s and the generator's synthetic IDs. The natural fix (exclude colliding accounts from generation) overlaps with `scope: uncovered_rails`, so there's no need to pre-build a guard. Address if/when it bites.
- **Auth (phase.2).** Studio has writes; eventually it needs a different auth posture than Dashboards' read-only views. The same-Starlette-process design is severable for that day. Not in the first cut.
- **Dangling-reference Dashboards refresh** — when a re-seed deletes an entity the open Dashboards page was viewing. Show "no data" for now; specific UX when it bites.
- **X.6 docs re-point.** Once Studio + the diagram are usable, the bulk of `docs/walkthroughs/` shrinks toward "load your YAML and look"; the long-form walkthroughs become can't-run-it-locally fallbacks. X.6 picks this up; not Studio's job.

## Testing scope

Studio inherits the existing project-wide testing discipline (the locked-seed determinism test, the dialect-portable SQL constraints, the QS↔App2 4-way agreement test, etc.) — those keep running unchanged. What Studio **does NOT do** is extend the X.2-era exhaustive `scenario × dialect × target` test matrix to its own surface. That matrix existed because Dashboards is the customer-visible render surface and had to be battle-hardened; Studio is a dev tool with different exposure.

What this means concretely:

- **Editor + cascade + shaping knobs → unit tests** against the in-memory `L2Instance` model + scenario object. No DB needed for the bulk of editor coverage. High coverage warranted (the cascade-rewrite logic is the riskiest correctness piece).
- **Deploy changes pipeline → orchestration tests**, one per pipeline shape: gated halt on `etl_hook` failure (demo DB untouched), wipe-then-pull when `etl_datasource` is set, additive generator on top, refresh + reload bump. In-process; no full chain-runner matrix.
- **Cross-dialect pull (step 2) → narrow targeted matrix.** This is the dialect-sensitive part of Studio. The short-term common case the SPEC targets: `etl_datasource` is PostgreSQL or Oracle (the operator's real DB), `demo_database_url` is SQLite (the fast local-iteration target). Test priority:
  1. **PG → SQLite** — the integrator-local + ETL-engineer common case.
  2. **Oracle → SQLite** — the codebase already handles Oracle; same shape, different source dialect.
  3. **SQLite → SQLite** — the degenerate case (same dialect on both sides); verifies the code path doesn't choke.
  4. *(Longer-term, not gated on Studio shipping)* — `demo_database_url` of PG or Oracle re-adds cells. Skipped in the short-term Studio matrix; the codebase keeps the dialect support, we just don't force the matrix.
- **Diagram → JS unit tests** in the existing `tests/js/` shape (Playwright harness, one per renderer feature: render the full graph for `sasquatch_pr`, toggle entity types, click-to-focus, coverage-tint mode). Renderer-specific once the spike picks D3 or graphviz.
- **Studio + Dashboards integrated loop → 1-2 browser e2e tests** end-to-end against `sasquatch_pr` + SQLite (edit the YAML in Studio → Deploy → Dashboards reloads with the new data). NOT parametrized over `[qs, app2]` — Studio is App2-only; there's no QS-side analog to test parity against.
- **Existing Dashboards / QS / 4-way agreement tests are untouched** by Studio. The X.2-era 13-cell `scenario × dialect × target` matrix and the agreement test keep running as they do today. Studio doesn't add to them and doesn't subtract from them.

The principle: keep good coverage, but don't pay X.2's matrix-fan-out cost on a dev tool whose primary user is the developer wielding it.

## Reuse inventory (what Studio builds ON, not from scratch)

- **`common/l2/topology.py`** — already builds the full L2 relationship graph; the diagram is a new renderer of an existing model.
- **`common/tree/visuals.py::ForceGraph` + `renderForceGraph`** (bootstrap dispatch) — the d3-force tech demo to grow into the real diagram (or replace, per the spike).
- **`common/l2/loader.py` + `common/l2/validate.py`** — Studio reads + writes via the existing loader; the editor's live validation IS the existing strict validator.
- **`common/l2/seed.py`** (`emit_full_seed` + `default_scenario_for` + `densify_scenario` + `add_broken_rail_plants` + `boost_inv_fanout_plants`) — the data generator. Plant-toggle = filter the scenario's plant list by exception kind. Seed = expose the existing seed as a config field. Scope modes = three branches on the existing pipeline.
- **`common/sql/dialect.py` + `common/db.py`** (`batch_oracle_inserts`, `_AsyncSqlitePool`, `AsyncConnectionPool`) — the cross-dialect copy in step 2 reuses these.
- **`common/html/server.py`** + the `serve` CLI group (becoming `dashboards`) + `/dev_log` POST + the `/docs` mount — Studio is new routes mounted on this server, not a new server.
- **The hash-locked seeds** at `tests/data/_locked_seeds/<instance>.<dialect>.sql` + `test_locked_seed_matches_fresh_emit` — Studio's "default knobs = today's behavior" rule keeps this test green even as knobs accumulate. Same pattern `data apply --seed-density=N` already follows.

## Hard invariants

- **Defaults preserve today's behavior, byte-for-byte.** Every `test_generator:` field absent → emit_full_seed output is byte-identical to `tests/data/_locked_seeds/*`. The locked-seed determinism test must keep passing as new knobs land.
- **YAML on disk is authoritative.** Studio's in-memory `L2Instance` is a cache of the file, never a parallel source of truth. Every save writes the file before the response returns.
- **Server owns cascade; client is dumb HTMX.** No client-side cascade computation, no diffing, no SPA state.
- **Severability.** `dashboards` runs without `studio`. Studio routes never assume Dashboards-side state.
- **No QuickSight / Dashboards-renderer changes.** Studio is additive; the existing renderers stay locked.
- **Generator is deterministic.** Same `(L2 instance, dialect, seed, density, scope, plants, end_date, only_template, derive_balances)` → byte-identical SQL.
- **Spike before lock.** The diagram renderer choice is gated on the X.4 spike's deliverable, not pre-committed.

## Forward links

- The PLAN derived from this SPEC lives in `PLAN.md` as Phase X.4 (Studio editor + diagram) and Phase X.5 (Studio data-shaping orchestrator + ETL coverage). The current X.4 / X.5 sub-tasks predate this SPEC and get rewritten when the SPEC settles.
- X.6 (model-driven docs) consumes Studio: `common/l2/primitives.py` field docstrings drive both the editor's form labels (X.4 editor discipline) and the auto-generated docs reference (X.6.a).
- Phase Q (CLI / YAML ergonomics) overlaps with Studio's launch surface — `quicksight-gen studio` is the primary place a non-CLI-comfortable user touches the tool, so the CLI's discoverability and error messages matter more once Studio lands.
