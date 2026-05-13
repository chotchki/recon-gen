# X.4 / X.5 Theme is Implementation Tools

# What we have
- An extremely flexible reconciliation system
- A good domain model realized via a YAML spec
- A system that can run in AWS/Locally and support 3 different database dialects
- A test generation system for exceptions

# What we still need
- For the integrator
  - This YAML is overwhelming to edit!
    - Huge text file
    - Subtle references that if you rename something other stuff could break
  - The training diagram(s) are amazing but there are so many its hard to understand
    - Being able to see everything on one diagram and toggle off an on types and/or focus in on a part see what it connects to would be awesome
- For the trainer
  - I need to be able to show people how the system works over time, the current test data generator shotguns all the data in.
    - It would be really nice to be able to load test data in day by day.
    - I'd like to be able to control when the errors show up, aka plant this exception and test my students ability to find it.
      - This could be done by letting the test data generator toggle of and on the errors it will plant.
  - I really need to make a change and then have it very quickly show up in App2
- The ETL integrator needs help knowing if what they are doing is working
  - How do I know if my ETL process is covering everything the YAML asks for
    - look at a datasource and show on the diagram mentioned above what is covered and what isn't
  - Some data feeds are much harder than others get good data for, could we model certain parts of the test data off other parts of the system?
    - For example:
      - For a given YAML, only generate test data for this account template
      - Given these subledger accounts, generate reasonable ledger balances
      - provide a better range of amounts for rail simulations 

# What we DON'T need
- None of the scenarios above need to be done in quicksight itself.
  - Ability to implement and ITERATE on these features quickly is why App2/sqlite support was built in the first place.
- App2 / Quicksight is already in a good place, no changes are needed.

---

# Iteration log (Claude — react inline, edit freely)

## Decided so far (2026-05-12)

- **Editor = full per-entity CRUD**, but with the complexity-limiter from `x_2_design_thoughts.md`: **the server owns the whole loop** (PUT entity → re-validate → rewrite references → re-serialize the *whole* YAML → decide what changed); the client is dumb HTMX (swap the returned fragment; on `HX-Trigger: l2-cascade-reload` the diagram + entity list `hx-get` themselves). No client-side cascade computation, no diffing, no SPA state. The diagram is navigate-only (click a node → focus its subgraph) — edits happen in the cards below it, not by drawing on the graph.
- **Rename = auto-rewrite refs; structural break = reject, don't auto-cascade.** Renaming an identifier walks the model and replaces every field holding the old value. Deleting/restructuring something another entity depends on → the strict validator catches it → 400 + inline error → the user fixes the dependent first. Keeps the ripple bounded.
- **YAML comments are not preserved** — re-serialize from the loaded model. (`description:` fields survive; they're model data. Freeform `# comments` don't. No `ruamel.yaml`.)
- **Plant-toggle = UI-only**, no CLI flag. This is an interactive trainer tool, not something fed through a build system. It pairs with the day-by-day stepper — together they're the trainer's control panel ("plant only drift; advance to day 16; now find it"). Interactive training > the reams of walkthrough docs no one reads.
- **Day-by-day = re-emit with an `--end-date` cutoff**, re-run with an advancing date. The SQLite win: the generator is deterministic, so each step is a clean superset, regenerates in ~seconds, and looks seamless — the wipe-and-reload under the hood is invisible. No incremental-append mode.
- **Derived / scoped test data = all three flavors** (subledger txns → derived GL/control daily balances; scoped per-template generation; better rail-amount ranges) — but **incrementally**, layered in over time as knobs on ONE "shape the data generation" surface alongside plant-toggle and the day cutoff. They're all the same kind of thing: shaping what the generator emits.
- **Hard constraint on every generation knob:** all knobs at their default → emitted SQL is byte-identical to `tests/data/_locked_seeds/*` (`test_locked_seed_matches_fresh_emit` must keep passing). So `--plant`=all-kinds, full 90-day window, no template scoping, no balance derivation = today's output, byte for byte. (Nice side effect: the locked-seed test keeps catching accidental generator drift even as knobs accumulate. Same pattern `data apply --seed-density=N` already follows.)
- **Same Starlette process** for App 1 + App 2 in the dev-tool era (App 2's server already has the routing / static-asset / `/docs`-mount machinery; split later only if phase.2 auth lands). Zero changes to the QS pipeline or App 2's visual rendering — App 1 is *new routes* + a *fuller d3 graph projection* + *generator-shaping knobs*.
- **Forward-looking (X.6, not acting yet):** "shrink the training site while being MORE effective" — X.6 (model-driven docs) re-points toward auto-generated *reference* (entity / visual / dataset / config — the drift-prone parts) + a thin "here are the interactive tools" page; the bulk of `docs/walkthroughs/` shrinks rather than getting model-driven scaffolds.

## Don't-reinvent inventory (what X.4/X.5 builds ON, doesn't rebuild)

- `common/l2/topology.py` — **already models the full L2 relationship graph**: roles + internal/external scope, TwoLegRail bundled edges, SingleLegRail self-loops, TransferTemplate clusters, Chain dashed edges (required/xor-group badged). Renders to static Graphviz today. The unified interactive diagram = project this *same model* to the d3-force JSON shape and render via `ForceGraph` + `renderForceGraph`. Refactor `topology.py` into "build the abstract relationship graph" + "render it (Graphviz | d3-JSON)" so the two projections can't drift.
  - COMMENT: I don't think we need to keep parity between the graphiz vs d3-JSON. If the d3-JSON becomes more useful, I'll push to cut the graphviz in the vein of, just go look in the tool as opposed to a static render.
- `common/tree/visuals.py::ForceGraph` + the `renderForceGraph` bootstrap dispatch — the interactive d3-force renderer, already shipped (X.2). Needs: full-graph input (today's `_db_fetcher::_topology_to_force_graph` is an accounts+rails-only subset — templates skipped, chains absent), plus a "tint by coverage value" mode for the ETL overlay.
  - COMMENT: I am expecting to need to do significant work so that this view is useful and not overwhelming (even with the toggles). The current force graph is barely a tech demo.
- `common/l2/validate.py` + the loader — the editor's live-validation (400 + inline error on a bad PUT) and the rename-ref-rewrite (the model already knows what references what). No new validation logic.
  - COMMENT: inline error shouldn't lose all the other content, just validation failure should be rejected
- The scenario / plant machinery — `default_scenario_for(l2)` + `densify_scenario` + `add_broken_rail_plants` + `boost_inv_fanout_plants`. Plant-toggle = filter the scenario's plant list by exception kind before `emit_full_seed`.
- `data apply --end-date` — the existing X.5.b plan already calls it "trivial in `emit_full_seed`."
- App 2's Starlette server + the `serve` CLI group + `/dev_log` (POST) + the `/docs` mount — App 1 is new routes in the same process.
  - COMMENT: I'm supportive but we need to be able to sever App1 vs App2 since App2 is readonly vs App1 which is editable. The YAML must still be the hard truth between the two (even if for speed reasons we use an object inbetween)
- `data apply --seed-density=N` — the proof-of-pattern for "a generation knob whose default output is byte-identical to the locked seeds."
  - COMMENT: agreed

## Open questions (please react / edit inline)

1. **One diagram with modes — or three pages?** The editor, the data-shaping panel, and the ETL coverage view all want the diagram at the top. Proposal: ONE `/l2_shape` page, one `ForceGraph` render, mode-switched *overlays* — bare in edit mode, coverage-tinted in ETL mode, "what's planted / current day" annotated in trainer mode — and the panels below the diagram swap per mode. Agree, or do you want distinct routes?
  - I like modes but I'm not picky how we implement it, what I'm trying to say is that the answers will probably naturally fall out of the implementation.
2. **The "see it in App 2" loop — what exactly happens?** App 2 is direct-query, so a re-seed + matview refresh is *probably* enough — no App 2 restart, just tell the open App 2 tab to reload. Proposal: an App 1 "Rebuild" action = `data apply --execute` (with the current shaping knobs) + `data refresh --execute`, then bump a "data-generation id"; App 2's open page notices the bump (poll, or an SSE it subscribes to since it's the same process) and reloads. Open: (a) confirm re-seed-without-restart is safe, (b) where does the button live — global toolbar or the data-shaping panel — (c) poll vs SSE, decide now or just poll?
  - I suspect the user(s) are going have app1 and app2 open on two different windows. I think a button in App1 (in any mode) that says, deploy changes and an automatic refresh in app2 (however its implemented) would be great. This is where I'm hoping app2's REST architecure helps keep the user state mostly intact even as stuff changes. I suspect we'll run into edge cases of "this rail disapeared" what should happen in the refresh but the BIG win will be if you could change the day, and the app2 refreshes and the new data/errors just pops onto the screen. 
3. **One `serve` command or two?** Since App 1 + App 2 share a process: does `quicksight-gen serve -c config.yaml --l2 <yaml>` bring up both (browser → App 1 landing), folding in today's `serve app2 apply` and the planned `edit` command? Or keep two CLI entry points into the one process?
  - I think app1 implies app2 is also present, however vice versa isn't true. so `quicksight-gen edit -c config.yaml --l2 <yaml>` launches 1+2 but `quicksight-gen serve -c config.yaml --l2 <yaml>` is just app2
4. **Where do the shaping knobs live between re-seeds?** When the trainer sets "plant only drift, day 16, scoped to template X" — ephemeral (in-memory, gone on restart) or persisted? `etl.yaml` is reserved for ETL load steps and `config.yaml` stays env-only (per the x_2 doc), so: a third file (`scenario.yaml`?), or in-memory + an "export current shaping" button? (Whatever it is, default values must byte-match the locked seeds — see Decided.)
  - So think we need to think about where the data comes from for App1. For example we could say, configure in config.yaml a app2 data source of transactions/daily_balances, we don't edit it but just use it pull data in to whatever "demo_database_url" is pointing at. So it looks like this:
    - datasource(url, transactions, daily_balance) -> pulled in -> test data is added (depending on the trainers toggles/yaml) -> demo_datasource -> read by app2
    - So in this setup the etl load steps are external, other than we give a key in the config.yml that the etl engineer can fill in to execute whatever they are building for their etl process.
    - So what this means is, you click "deploy changes" in app1, the following steps happen (with a bunch being optional):
      - IF the etl_hook is configured, its executed as a command. (Its the ETL engineer's job to make that command do whatever is neede to the etl_datasource )
      - Next if a etl_datasource is configured, up to the requested "end_date" is pulled from etl_datasource to the demo_datasource.
      - If the test data generator is enabled it runs with its parameters against demo_datasource.
      - App2 is refreshed.
    - If a trainer is just using the test data generator against demo_datasource of sqlite, its REALLY fast.
    - If a ETL engineer is testing its still pretty fast AND keeps app1 simple, its NOT a generic ETL tool.
5. **ETL coverage — what's the bar, and what DB?** The "datasource" here is the ETL integrator's real DB (whatever `config.yaml` points at — Aurora/Oracle, not the demo SQLite), so `coverage_for(connection, prefix, l2)` is just dialect-portable `COUNT(*)` queries. Proposal for the bar: binary presence per L2 primitive (has rows / no rows) tinted on the diagram, with the row-count on hover — no saturation gradient, no "covered *adequately*" judgment. Agree?
  - Agree, the data generation has artifically generated transactions that in no way match a real system, the integrator is really just concerned are they getting the feeds right.
6. **Chain / TransferTemplate forms — X.4 or later?** Account / Rail / Theme are flat forms. Chain (required/xor-group child list) and TransferTemplate (composes leg-rails) need a sub-list editor — more work. Proposal: ship Account/Rail/Theme forms first; those two stay text-edited until the form pattern's proven, then add them. OK?
  - I think we should plan account/rail/theme/chain/transfer as an step of additive steps, we'll shake out the details as we go.
7. **X.6 docs — how far do we cut?** Given "shrink the training site, be MORE effective": auto-generate the *reference* (entity/visual/dataset/config), keep a thin "getting started → use App 1 / App 2" page, and stop expanding `docs/walkthroughs/` but keep it as the can't-run-it-locally fallback? Or actively delete the bulk of it? (X.6 question — can wait, but a steer now shapes how much X.6 scope survives.)
  - If this goes well, I'll be angling for this to be a method to actively cut the content as long form reading. Doesn't mean the content goes away but for example on the trainer's plant errors, we'll probably provide link to explanations. Where we may see more of a shrink is the need to personalize the docs since its easier to just load a yaml and see your stuff.

---

# Convergence summary (2026-05-12) — Claude's read-back; confirm or poke

## Now settled (folding in the comments + OQ answers above)

- **`edit` ⊃ `serve`.** `quicksight-gen edit -c config.yaml --l2 <yaml>` launches App 1 + App 2 in one process (browser → App 1). `quicksight-gen serve -c config.yaml --l2 <yaml>` launches App 2 alone. App 1 implies App 2; the reverse isn't true — the architecture keeps App 2 runnable standalone (read-only) without App 1's editable surface.
- **The YAML on disk is the hard truth** between the two: App 1 writes the YAML on every save; App 2's data is generated *from* the YAML. App 1 may hold the loaded `L2Instance` in memory for speed, but it writes back to the file on every save — there is no second source of truth.
- **Validation-error UX:** a bad PUT → 400 + the validator error rendered inline *in the form fragment only* — keeps the user's typed-but-invalid content so they can fix it; the diagram + other entity cards stay untouched (targeted HTMX swap). Validation failure ⇒ the change is rejected, not partially applied.
- **One diagram with mode-switched overlays** (edit / coverage / trainer) — but the exact shape "falls out of the implementation"; not over-planning it.
- **The "Deploy changes" button is global** (App 1, any mode), and App 2 auto-reloads *its current URL* after — so the user's navigation state (sheet, filters) mostly survives because App 2 is URL-driven. "This rail disappeared" / dangling-reference refresh cases get handled when we hit them. The killer demo: change the day in App 1 → App 2 refreshes → the new day's data + planted errors just pop onto the screen.
- **ETL coverage = binary presence** per L2 primitive (has rows / no rows), tinted on the diagram, with the row-count on hover. No saturation gradient, no "covered adequately" judgment — the generated data is synthetic anyway; the integrator just needs "are the feeds landing?"
- **Editor forms = additive build order**: Account → Rail → Theme → Chain → TransferTemplate, details shaken out as we go (the structurally-richer Chain/Template forms come once the flat-form pattern is proven).
- **No graphviz↔d3 parity requirement.** The d3 projection is its own thing; if it ends up the more useful view, graphviz gets cut later ("go look in the tool", not a static render).
- **X.6 (later):** shrink long-form reading; link out to explanations rather than inlining them; the *per-institution doc personalization* mostly goes away because the tool IS the personalization (load your YAML, look at your stuff).

## The diagram is the riskiest piece — spike it first, timeboxed

The current `ForceGraph` is "barely a tech demo." A force-directed view of a real-ish L2 (`sasquatch_pr` — meatier than `spec_example`: dozens of accounts/rails, templates, chains) that's actually *legible* is a real design+build problem, not an afternoon's wiring. So X.4 **starts with a timeboxed diagram spike**:

- Render the *full* L2 graph for `sasquatch_pr` — roles + internal/external scope, rails (bundled — `topology.py` already collapses same-direction rails between a pair), SingleLegRail self-loops, TransferTemplates, Chains (required/xor-group) — and make it readable: parents-above-children layout, spread-to-fill, merge same-direction edges (and their labels), toggle entity types on/off, click a node → focus its connected subgraph.
- The renderer question is **D3 + d3-force vs enhanced graphviz**. (ELK was a candidate but is OUT — it's a large Java library which would drag a JVM dependency/build step into the runtime, a whole new complexity tier that doesn't fit a Python+JS tool. Mermaid was already out from the Phase S spike.) The spike's job is to figure out whether d3-force tuning gets us to "legible on `sasquatch_pr`," or whether the enhanced-graphviz path (post-process the SVG with click handlers + data-attrs) wins by inheriting `dot`'s already-legible layout for free.
- **Fallback if D3 just isn't getting there:** enhance what graphviz already produces. The existing `topology.py` graphviz output is a perfectly legible static SVG (the user has called the existing training diagrams "amazing" — those are graphviz). What it lacks is *interactivity*. Adding click-to-focus / type-toggle on a post-processed graphviz SVG (data-attrs per node + JS event handlers) keeps the great hierarchical layout we already have for free, just adds interaction on top. Try this BEFORE giving up on the requirement — it preserves the layout quality d3-force struggles to match.
- "Good enough" defined up front: legible on `sasquatch_pr`, toggles work, focus works. Ship that — then **stop polishing**. This is exactly where X.2's "took way too long" risk concentrates.
- One spike, three payoffs: the integrator's comprehension view, the ETL coverage overlay (same renderer, a tint mode), and the trainer's explaining aid. So it's the right first move *and* the highest-uncertainty one — do it before anything in X.4/X.5 commits.

## OQ4 articulated — Claude's read-back of the data flow

Two data sources now; `demo_database_url` (existing) is what App 2 reads. New `config.yaml` keys (env-specific — fit the allowlist's spirit):
- `etl_hook` — optional shell command. App 1's "Deploy changes" runs it first; the ETL engineer makes it do whatever populates their `etl_datasource`. **Local-dev-only** — never expose on a hosted App 1 (it's "run an arbitrary command from a button").
- `etl_datasource` — optional: a connection URL + which tables to pull (`transactions` / `daily_balances`). App 1 *reads* it; never writes it.

**"Deploy changes" pipeline** (App 1 button, any mode; every step conditional):
1. `etl_hook` set? → run it as a shell command.
2. `etl_datasource` set? → copy its rows into `demo_database_url`, filtered to `≤ end_date`. Cross-dialect copy (ETL DB may be PG/Oracle; demo DB may be SQLite) — reuse the existing dialect machinery + Oracle INSERT-ALL batcher.
3. test-data-generator enabled? → run the generator with the current shaping params (which plants, end_date, scoped template, …) into `demo_database_url` — **additively**, on top of whatever step 2 put there.
4. refresh matviews → App 2's open tab reloads its current URL.

Trainer path: no `etl_hook`, no `etl_datasource`, SQLite `demo_database_url` → steps 1–2 skipped → generator → refresh → reload. Really fast. ETL-engineer path: `etl_hook` runs their pipeline → pull into demo DB → (maybe) generator plants teaching exceptions on top → refresh → reload. App 1 = thin orchestrator, **NOT a generic ETL tool**.

**Small open bits under OQ4 — SETTLED (2026-05-12):**
- **Defaults preserve today's behavior, period.** No auto-mode-switch when `etl_datasource` is set; the generator at default = full 90-day baseline + all plants (byte-identical to the locked seeds). The narrow-down behaviors (next bullet) are *modes the user dials in*, not implicit reactions to whether ETL is configured.
- **Generator scope = a `scope` knob with three modes**, all dial-able by the user:
  - `full` — today's 90-day baseline + planted exceptions (default).
  - `uncovered_rails` — generator inspects `demo_database_url` (post step 2 of the pipeline) and only generates baseline rows for rails that *don't* have data yet. Pairs symmetrically with the ETL coverage overlay (one shows what's missing; the other fills only what's missing). Natural choice for the ETL engineer.
  - `exceptions_only` — skip the 90-day baseline, only emit the planted violations on top of whatever's already there. Natural choice for the trainer planting teaching scenarios on top of real data.
- **Shaping params persist in `config.yaml` under a `test_generator:` block.** All operator-machine-local (which knobs THIS operator's local dev wants), so they fit `config.yaml`'s allowlist spirit alongside `etl_hook` / `etl_datasource`. Whatever fields it ends up with — `enabled`, `scope`, `end_date`, `plants`, `only_template`, etc. — every field's *default* (= "as if absent") must keep the locked-seed determinism test green.
- **`config.yaml` allowlist expands by 3 keys**: `etl_hook`, `etl_datasource`, `test_generator`. All operator-machine-local (env-ish), not L2-institution structure. Note this in V.1.b's allowlist when we update it.
- **PK collisions: deferred** — if real `transaction_id`s from `etl_datasource` ever clash with the generator's synthetic IDs, the natural fix is to exclude the colliding accounts from generation (which is exactly what `scope: uncovered_rails` already does). Don't pre-build a guard.
- **`end_date` default** — absent ⇒ "the full window" (= today's behavior, byte-identical to the locked seeds).
- **Random seed is a knob.** The generator is deterministic; expose its seed as `test_generator.seed` (default = today's locked-seed value, byte-matches the locked seeds). Trainers scrub through seeds to find a planted-exception layout that suits their lesson; integrators pin a seed to repro a specific scenario across machines. Same byte-identical-on-default rule applies.
- **"Plant timeline" view in Studio.** A vertical timeline column — one row per day in the generation window, the planted exceptions annotated at the day they hit (under the *current* shaping config: scope, plants, seed, only_template). So the trainer SEES before they advance: "drift on day 12, overdraft on day 23, stuck-pending starts day 7 → set end_date to 23 to demo all three; set to 11 to demo none yet." Click a day → `end_date` jumps to that day → "Deploy changes." Re-renders as the knobs change. The scenario object already encodes plant-on-day-N — this is a UI projection of data we already have, not new generator logic.

## Naming — SETTLED: Studio + Dashboards (2026-05-12)

- **Studio** = the maker surface (was "App 1") — the YAML editor, the data-shaping orchestrator, the ETL coverage view, the diagram. Read-write.
- **Dashboards** = the viewer surface (was "App 2") — the four self-hosted L1 / L2FT / Investigation / Executives apps + the embedded `/docs` handbook. Read-only.
- **CLI verbs:**
  - `quicksight-gen studio -c config.yaml --l2 <yaml>` → launches Studio (with Dashboards mounted under it). The combined-process front door for the integrator / trainer / ETL engineer.
  - `quicksight-gen dashboards -c config.yaml --l2 <yaml>` → launches Dashboards alone (read-only). The standalone "I just want the dashboards" entry point.
  - `serve app2 apply` is **removed outright** when `dashboards` lands — no deprecation window, no alias. The user is the only one who ever saw it; the deprecation cycle is pure cost. The `serve` Click group goes away with it.
- **Vocabulary going forward:** the SPEC + the PLAN re-cut + commits / RELEASE_NOTES / docs use Studio + Dashboards; the App 1 / App 2 references in the iteration log above stay (they're history) — anything else gets the new names.
