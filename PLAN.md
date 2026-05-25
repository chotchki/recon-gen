# QuickSight Generator — Active Plan

## Phase AI

Per `feedback_build_verbs_not_skip`: when an editor verb's underlying UI is missing, BUILD the UI (and the verb that wires to it), don't skip the test param.
The dashboards-match assertion is the user-facing acceptance: "the editor produces an L2 that drives identical dashboards".
- [ ] AI.0 - Locks (decisions before AI.1 fires, 2026-05-19).
  - **Scope split for missing editor surfaces** (Lock 1, confirmed 2026-05-19). If the AI.1 audit reveals a non-trivial editor UI gap (no widget for a primitive that some test-input L2 uses), AI.2 BUILDS the missing UI inline as a sub-task per entity kind. Phase ships only when the editor can recreate the full corpus. No "scope to current surface; defer gaps" escape hatch — the dogfood claim has zero acceptable noise floor.
  - **L2Instance equivalence granularity.** Compare via parsed `L2Instance` dataclass equality, not byte-equality on the YAML file. Editor-emitted YAML may diff in formatting (field ordering, comment placement, indentation) but the L2Instance struct MUST match. Use existing `tests/unit/test_l2_loader.py`-style structural asserts.
  - **Dashboard equivalence granularity** (Lock 2, confirmed 2026-05-19). Per-sheet, per-visual: visual titles + table row content (rows + cell text) + KPI numeric values. Skip DOM byte-equality + screenshot diffs. Hermetic comparison: same L2 + seed + anchor must yield identical `DashboardDriver.visual_titles()` + `table_rows()` + `kpi_value()` outputs. Filter unstable fields (analysis_id / sheet_internal_id / wall-clock timestamps) from the comparison dict.
  - **Test layer = browser** (Lock 3, confirmed 2026-05-19). New file `tests/e2e/test_studio_dogfood.py` gated behind `QS_GEN_E2E=1`. Runs under `./run_tests.sh up_to=browser`. Marker: `@pytest.mark.browser` (existing convention).
  - **Transport: hybrid** (Lock 3 amendment, decided 2026-05-21 after the AI.1 audit revealed the editor surface is large — rail subtype picker, multi-selects, `multi_select_groups`, chain per-child checkboxes, 3 YAML-block singletons). `StudioEditorDriver` is one verb protocol with two transports: (a) `StudioBrowserEditorDriver` (Playwright) drives ONE full pass on the deterministic `spec_example` for real form-render+submit fidelity; (b) `StudioHttpEditorDriver` (httpx form-POST against a running studio server) drives `sasquatch_pr` + the fuzz-sampled bulk — fast, deterministic, fuzz-scalable. Rationale: the unit route tests (`test_studio_editor_routes.py`) already prove server-side coerce→create→serialize→save end-to-end, so HTTP covers the same fidelity; Playwright's marginal value (a form that renders-but-mis-submits) is captured by the one spec_example browser pass rather than a full-corpus×fuzz browser rebuild.
  - **Determinism + reproducibility.** Anchor `date(2030, 1, 1)`; `RECON_GEN_FUZZ_SEED` pinned per CI cell. Editor mutations are exact sequences (no waits-for-element ambiguity); fail loudly if a mutation widget reports unexpected state. Studio cfg is ephemeral (tmpdir-rooted) per test invocation so studio's `.studio-state.yaml` doesn't pollute across runs.
  - **No mutation of source yamls.** Dogfood'd YAML writes to `tmp/dogfood_<instance_name>.yaml` (test-scoped tmp dir). The shipping `tests/l2/spec_example.yaml` + `tests/l2/sasquatch_pr.yaml` are reference; the test asserts dogfood matches reference.
  - **Fuzz axis sample size.** Locked-input seeds: 5 per CI cell. Override via `QS_GEN_AI_FUZZ_SAMPLE_N=N` env var (default 5, runner pins this to a known-good value for the deterministic suite; ad-hoc local testing can crank it). The fuzz pool itself is `tests/l2/fuzz.py::random_l2_yaml(seed)` — already produces validator-passing L2 instances. Add an opt-in nightly run that bumps the sample to 100+ once the deterministic 5-seed sample is green.
  - **Editor save-to-yaml route.** The editor needs a single POST route that serializes the current in-memory L2Instance to a yaml file (file path supplied or returned). If no such route exists, AI.2 builds it (preferred path: `POST /l2/export?path=<dest>` returns 204 after writing). Save-on-mutate (every edit re-flushes to disk) is the current behavior; AI confirms or adjusts as needed.
- [x] AI.1 - Editor surface coverage audit. Walk every entity kind in the test-input corpus (spec_example + sasquatch + 5 fuzz seeds) and inventory whether each kind has an "add/edit/delete" widget in the Studio editor. Cover:
  - Account (with all optional fields: name, role, parent_role, expected_eod_balance, description)
  - AccountTemplate (with instance_id_template / instance_name_template)
  - SingleLegRail (with origin variants, metadata_keys, leg_role, leg_direction)
  - TwoLegRail (source_role, destination_role, source_origin, destination_origin)
  - AggregatingRail (cadence, bundles_activity)
  - TransferTemplate (leg_rails, transfer_key, completion, leg_rail_xor_groups)
  - Chain (parent, children with ChainChildSpec including fan_in + expected_parent_count + mixed-cardinality)
  - LimitSchedule (per-rail + per-account_type caps)
    Produce `docs/audits/ai_1_editor_surface_audit.md` listing per-entity gaps + "needed widget" punch list. This is the discovery step — without it, AI.2 has no driver-verb scope. Run the audit BEFORE locking AI.2 effort.
- [ ] AI.2 - StudioEditorDriver verbs + missing UI builds. Extend the App2Driver protocol (or subclass it as `StudioEditorDriver` in `tests/e2e/_drivers/`) with editor verbs keyed off the AI.1 audit. Per Lock 1, build any missing editor UI inline as AI.2.x sub-tasks. Driver shape:
  - `create_account(id, role, scope, **opts)` + similar for other entity kinds
  - `set_template_leg_rail_xor_groups(template, groups)` (AB.3 surface)
  - `create_chain(parent, children: list[ChainChildSpec])` (AB.6 surface — mixed-cardinality)
  - `save_l2_to_path(path)` — invoke editor's serialize-to-yaml route, write to disk
  - Bulk-create helper `create_l2(reference: L2Instance)` that walks reference entities in dependency order and creates each via the verb-per-entity-kind path
  - [x] AI.2.a - AI.2.a Fix create-path field drops (rail cadence/amount_typical_range/firings_typical_per_period + chain per-child fan_in/epc)
  - [x] AI.2.b - AI.2.b TransferTemplate transfer_key field (FieldSpec + create wiring)
  - [x] AI.2.c - AI.2.c Top-level L2 editor for description + role_business_day_offsets (new singleton kind)
  - [ ] AI.2.d - AI.2.d StudioEditorDriver verbs + create_l2(reference) bulk helper
    - [x] AI.2.d.1 - AI.2.d.1 Protocol + HTTP transport + create_l2 bulk — **DONE via Phase BB** (commits c1939621 + 934b274e). Dogfood round-trip un-skipped + passes for spec_example AND sasquatch_pr through HTTP form-POSTs with full validate() on every save.
      - [x] AI.2.d.1.a - Defer-validation bilateral. **Resolved via Phase BB** (surgical reconciler form-pairing; no validator split, no defer-validation cheat). Catalog: `docs/audits/bb_0_a_validator_circulars.md`. Server-side composite handler (BB.1) + Reconciler picker UI (BB.2) + driver wiring (BB.3) + dogfood un-skip (BB.4).
    - [ ] AI.2.d.2 - AI.2.d.2 Playwright transport (spec_example pass)
  - [x] AI.2.e - AI.2.e Route diagram edit/add affordance to dedicated screens (drop inline-on-diagram editing)
- [ ] AI.3 - Test harness — `tests/e2e/test_studio_dogfood.py`. Parameterized over L2 yaml input:
  ```python
  @pytest.mark.parametrize("l2_source", [
      pytest.param("tests/l2/spec_example.yaml", id="spec_example"),
      pytest.param("tests/l2/sasquatch_pr.yaml", id="sasquatch_pr"),
      *[
          pytest.param(_fuzz_yaml(seed), id=f"fuzz_{seed:010d}")
          for seed in _fuzz_seeds_for_run()
      ],
  ])
  ```
  Sequence per test case: (a) load reference L2 via `load_instance(l2_source)`; (b) start `recon-gen studio` on a tmpdir-rooted cfg + empty L2; (c) StudioEditorDriver creates every entity from the reference IN DEPENDENCY ORDER (AccountTemplates → Accounts → Rails → TransferTemplates → Chains → LimitSchedules); (d) save via `save_l2_to_path(dogfood_yaml_path)`; (e) shutdown studio. Failures surface the FIRST missing editor verb + entity + cell.
- [ ] AI.4 - L2Instance equivalence assertion. Load both `l2_source` and `dogfood_yaml_path` via `load_instance`. Assert structural equality: `original.accounts == dogfood.accounts`, `original.rails == dogfood.rails`, etc. Use dataclass `__eq__`. Fail with a focused diff (which entity differs in which field). This is the FIRST acceptance gate — if it fails, the editor lost information during the round-trip.
- [ ] AI.5 - Dashboard equivalence assertion (SQLite-only, App2 only). For each of `(original L2, dogfood L2)`:
  1. Apply schema against a fresh SQLite db (one per L2; tmpdir-rooted)
  2. `data apply --execute` against the same anchor + seed
  3. `data refresh` to materialize L1 + Investigation matviews
  4. Start `recon-gen dashboards -c <ephemeral cfg> --l2 <yaml>` on a distinct port; mount in App2Driver
  5. Walk every dashboard's every sheet's every visual; collect `(dashboard_id, sheet_name, visual_title) → {row_count, rows, kpi_value}` into a comparison dict
  6. Compare the two collected dicts; assert byte-equal modulo dashboard-internal-id randomness
- [ ] AI.6 - Fuzz axis wiring. Extend the runner so `./run_tests.sh up_to=browser` honors `QS_GEN_AI_FUZZ_SAMPLE_N` (default 5 per cell; nightly opt-in cranks to 100+). Fuzz seeds derive from the run-id-hash so reruns at the same commit see the same seed pool (per `feedback_fuzzer_as_property_testing.md` reproducibility contract). Failed fuzz seeds get re-runnable via `./run_tests.sh up_to=browser --variants=fNNNNN_sl_lo`.
- [ ] AI.7 - Re-verify + commit. Run `./run_tests.sh up_to=browser --scenarios=sp --dialects=sl --targets=lo` locally to confirm the 5-seed deterministic dogfood suite passes; gate CI on it via the existing browser-job pipeline (`e2e.yml` or analog). Phase history one-liner: "AI — Studio editor dogfood: ANY L2 yaml (spec_example + sasquatch_pr + fuzz-sampled) rebuilt via browser-driven editor matches reference structurally + in dashboard output (SQLite/App2 only)".
- [x] AH.8 - Docs builds: isolate into run-folder sandbox (kills xdist flakes)
- [ ] AH.9 - Tailwind output.css scan-scoping — stop whole-repo prose pollution
- [ ] AH.10 - demo-publish on-release refresh broken (trigger + runner-user)
- [ ] AI.0 - Studio editor dogfood: any L2 yaml rebuilt via browser-driven editor

## Phase AM - Standardize on tailwind *(prefer after: AI)*
The HTML surface is split across two styling systems: App2 dashboards + rich-text render via Tailwind utilities (`output.css`), but the Studio editor + diagram pages are styled by hand-written `editor.css` + `diagram.css` (semantic classes — `.create-page`, `.studio-header`, `.create-form`, …). Standardize the whole surface on Tailwind so there's ONE system (no drift between two CSS approaches; the editor screens already LOAD `output.css`, so the utilities are right there).

**Sequenced after Phase AI deliberately** (user, 2026-05-21): the AI dogfood round-trip (structural + dashboard-equivalence, browser-driven) pins the editor's behavior + emitted HTML structure, so the CSS refactor lands with a regression safety net and doesn't churn the editor markup mid-test-build.

- [ ] AM.0 - Standardize the Studio + editor surface on Tailwind *(future — sequenced AFTER Phase AI)*
  - [ ] AM.0.1 - Audit + spike: inventory every hand-written class in `editor.css` + `diagram.css`; map each to a Tailwind utility set (or an `@apply` component class). Lock utility-inline vs `@apply`-component (lean: `@apply` for repeated structures like `.create-form`, inline utilities for one-offs). Spike the Tailwind build + scan-scoping so `output.css` covers the `_studio_assets` templates (ties to AH.9 / #176 output.css scan-scoping).
- [ ] AM.1 - Convert the editor screens (create / edit / list / singleton / read-card) to Tailwind; drop the `editor.css` rules they replace. **Fold in the AI.2.e part-2 stretch** — richer subtype-aware per-field requirement hints beyond the existing `*` markers + entity intro (e.g. a subtype requirements banner on the rail form).
- [ ] AM.2 - Convert the diagram + Studio chrome (`studio-header`, nav, data-knob panel) to Tailwind; drop `diagram.css` rules.
- [ ] AM.3 - Verify: re-screenshot create / edit / diagram / dashboards before↔after for visual parity; AI dogfood + browser e2e stay green; output.css scan-scoping doesn't regress.
- [ ] AM.4 - Drop `editor.css` / `diagram.css` (or reduce to the irreducible non-utility remainder); update asset links; commit + Phase AM history one-liner.

## Phase BA

- **Dashboard pickers sourced from `<prefix>_config.l2_yaml` (post-AW
  follow-on).** Surfaced 2026-05-23 during AW.3. Today's pickers come
  from two places: (1) hardcoded option lists baked into the QS dataset
  JSON at emit time (rail-name picker, direction picker, parent-role
  picker — Python reads cfg/L2, emits `StaticValues`); (2) dataset-
  derived `SELECT DISTINCT <col> FROM <dataset>`. AW landed a third
  option: the `<prefix>_config` table is SQL-queryable, so dashboards
  can JOIN to `l2_yaml` for picker options — `SELECT JSON_VALUE(rail.
  value, '$.name') FROM <prefix>_config, JSON_TABLE(l2_yaml, '$.rails
  [*]' COLUMNS (value json PATH '$')) rail WHERE JSON_VALUE(rail.value,
  '$.max_pending_age_seconds') IS NOT NULL`. What this unlocks: pickers
  that show only L2-DECLARED values (vs. dataset-derived which shows
  whatever's in the data); pickers that JOIN to L2 metadata
  (descriptions, types, classifications); pickers that survive deploys
  without re-emitting JSON (cfg row updates; pickers re-read); cross-
  dashboard consistency without re-encoding the L2 in each dataset.
  **Caveat**: requires changing the dashboard JSON's filter shape from
  `StaticValues` to `LinkToDataSetColumn` (or equivalent). Not free
  with AW; this is the dashboard-side migration that exercises AW's
  payoff. *(Sized as its own phase when picker complexity surfaces as
  friction.)*

## Phase BE - Cross-corpus duplication lint (test ↔ src), paired approaches 1+3

**Motivation (promoted from backlog AA.A.11 2026-05-24).** Every duplicated SQL string between `tests/` and `src/` is a second codebase that can pass while production breaks, or vice versa. User-flagged 2026-05-17 as "huge structural win." Paired-approach design:
- **Approach 1 — content-based AST lint.** Walk `tests/`, extract string literals with SQL fingerprints (`SELECT` / `FROM` / `<<$p` / `WHERE` / etc.) over a length threshold. For each, check if a normalized substring appears in `src/`. Flags inline SQL copies, repeated fixture strings, hand-mirrored constants. *Catches content-equality drift today.*
- **Approach 3 — provenance lint.** Same AST walker; for any test assertion that touches a SQL string OR a known production constant (sentinel value, parameter name, column name), require the value to be obtained via an `import` from `src/recon_gen/...`. Inline string literals in those assertions are the smell. *Catches future drift before it can happen.*

Land both, not either alone — they catch different failure modes and reinforce each other.

**Why exploratory** (locked 2026-05-24): unlike the no-raw-str-args lint (mechanical migration with a known shape), BE's spike produces genuine unknowns — false-positive rate at each length threshold, whether the dual approach actually reinforces or is redundant, whether structural bugs (test re-defines a constant rather than importing it) actually exist in this codebase. Either spike result is signal: real drift found → BE catches it before it ships; clean baseline → BE pins it so future drift trips loudly.

Approach 2 (token-stream tool like jscpd/PMD CPD) deliberately not pursued — both are non-Rust (Node + Java), against the standalone-binary preference. The content + provenance pair (1+3) covers the same regression class with codebase-native tooling that fits the existing `b.15.lint.*` AST infrastructure.

- [x] BE.0 - **Spike**. Doc at `docs/audits/be_0_cross_corpus_lint_spike.md`. Headline findings: **approach 1 = 0 hits at threshold 100+** (codebase is empirically disciplined on long-form SQL); **approach 3 = 144 hits** dominated by sheet-name + sentinel + dataset-identifier categories (the high-leverage win). Both checks ~0.6s walk; prelude-friendly. Open decisions D1-D7 for sign-off before BE.1 implements. Sequencing recommendation: BE.1 ships approach 1 enabled (0-hit baseline locked); BE.2 ships approach 3 staged-disabled until BE.4 sweep clears the 144 hits.
- [ ] BE.1 - Implement approach 1 (content-based) in `tests/unit/test_typing_smells.py::no-test-src-sql-duplication`. SQL-fingerprint heuristic + length threshold from BE.0's spike. Allowlist via sibling comment. `# typing-smell: ignore[no-test-src-sql-duplication]: <why>` escape with required WHY.
- [ ] BE.2 - Implement approach 3 (provenance) in `tests/unit/test_typing_smells.py::no-inline-production-constants`. AST-walk test assertions; flag string literals that match a known production constant from a registry (or, simpler: literals that appear in `src/recon_gen/**` as module-level constants). Allowlist same shape.
- [ ] BE.3 - Decide prelude vs opt-in mode based on BE.0's runtime measurement. If corpus walk is <1s, fold into the unit-prelude `test_no_typing_smells` like every other lint. If >5s, opt-in via `./run_tests.sh lint` mode. In-between: prelude with a "skip if --fast" flag.
- [ ] BE.4 - **Sweep + fix true positives.** Triage BE.1 + BE.2 findings against the actual corpus; fix duplications where the production constant can be imported (preferred); allowlist genuine exceptions (DDL fragments that intentionally live in tests for clarity, etc.) with a one-line WHY per the BC.1 pattern. Catalog the sweep + allowlist decisions in `docs/audits/be_4_corpus_duplication_sweep.md` so future maintainers know the disposition.
- [ ] BE.5 - **Cross-corpus scope question** (decide post-sweep). Does this lint extend to `tests/e2e/` ↔ `tests/e2e/_drivers/` to catch driver-internals leakage into tests? Probably yes — same drift class, same fix shape. BE.5 either includes it (one more allowlist + one more walk) or defers to a follow-on (BF) based on what BE.4 reveals about how much extra signal vs noise.
- [ ] BE.6 - Verify + commit + tag. Lint stays green in the prelude (or runs cleanly on opt-in). The catalog from BE.4 + the spike from BE.0 land in the same PR. No version bump unless the sweep fixed a real production-affecting bug — usually this kind of work doesn't shift the release tag.

# Backlog (not yet phased)

- **BC.12 deferred: `l2_yaml_raw` / `cfg_yaml_raw` opaque-provenance kv rows.**
  Surfaced 2026-05-24 during BC.12 integration. The original design
  said the kv would hold both per-field decompositions AND opaque-raw
  rows (`key='l2_yaml_raw'`, value=<full JSON>) so the operator could
  SELECT the original yaml back from the DB. **Deferred** because the
  Oracle chunked literal shape (`TO_CLOB(c1) || TO_CLOB(c2) || ...`,
  required to dodge ORA-01704's ~4000-char string-literal cap)
  collides with `batch_oracle_inserts`'s quote-aware coalescer —
  the batcher counts quotes per row to detect VALUES tuple boundaries,
  and the chunked TO_CLOB form has multiple quoted segments per row
  that confuse the count.

  Operators retain access via the `--l2 <path>` source file (the yaml
  on disk is the canonical artifact); matview consumption goes through
  the typed projection views. The provenance feature is the loss.

  Fix shape (when prioritized): either (a) teach `batch_oracle_inserts`
  to handle multi-quoted-segment VALUES tuples correctly, OR (b) emit
  the provenance rows as single-row INSERTs that skip the batcher
  entirely (~1KB SQL per deploy event; negligible cost).

- **`no-raw-str-args` AST lint — extend BC.1's D8 family to bare `str` parameters.**
  Surfaced 2026-05-24 by user during BC.0 sign-off. The codebase already has
  typed string newtypes (`SheetId`, `VisualId`, `FilterGroupId`, `ParameterName`,
  `DashboardId` in `common/ids.py`; `AccountId`, `RailName`, etc.) but a sweep
  for bare `str` parameters across `src/recon_gen/**` would surface every
  callsite that slipped through. Same shape as D8 (`no-raw-temporal-args`):
  AST-walk function/method signatures, flag any param annotated `str` (or
  `str | None`), require migration to a NewType wrapper OR a
  `# typing-smell: ignore[raw-str-arg]: WHY` escape.

  Expected to be a BIG migration — `str` is everywhere. Whitelist surface
  needs care:
  - SQL fragments + column names (low value to wrap; they're already
    SQL-injected territory if the caller is wrong).
  - User-facing display strings (titles, descriptions, error messages —
    these are content, not identifiers).
  - Dataclass field annotations stay unaffected (point values, not policy).
  - Stdlib-facing seams (Click string opts, env-var reads, file paths via
    `os.PathLike[str]`).
  - The `# typing-smell: ignore[raw-str-arg]: WHY` escape with required
    WHY.

  Staged like D8 — disabled during the migration, enabled at end. Probably
  a dedicated phase rather than a sub-task: scale similar to a full
  Phase BC × N.

  Sequenced after BC + BD land (those validate the AST-lint + named-
  constructor pattern is workable on a smaller migration before scaling).

- **BB.2.b — Reconciler picker: inline "create new" sub-form.** BB.2
  shipped attach-existing only (operator picks from existing TTs /
  aggregating Rails). The PLAN's BB.2 spec also called for an inline
  "create new" sub-form so the operator can author the reconciler
  alongside the rail in one atomic save (single picker selection +
  the new reconciler's required fields nested in the rail form).
  Deferred because attach-existing was sufficient to unblock BB.3 /
  BB.4 dogfood (the reference L2's reconcilers always exist; the
  driver test never needs create-new). Operator-UX nice-to-have:
  without this, the operator must create the reconciler separately
  first, then create the rail. Surfaced 2026-05-24.

- **Studio / Dashboards rethink under the post-AW DB-projected
  L2/cfg.** Surfaced 2026-05-23 after AW completed. AW lifted L2 + cfg
  yaml into `<prefix>_config` as DB-resident JSON; matviews now JOIN to
  it for per-L2 values. That changes a bunch of Studio/Dashboard
  questions that deserve evaluation as a unit, not piecemeal:
  - **Studio editing model.** Studio currently writes the L2 yaml file
    only; deploy regenerates the schema + populates the config table.
    Options: (a) yaml stays the only source of truth; deploy projects;
    (b) Studio also UPDATEs `<prefix>_config` on save for "live-reflect"
    semantics; (c) Studio writes the DB; exports back to yaml on
    operator request. AW makes (b) + (c) feasible; whether they're
    desirable is the open question.
  - **"Deployed-vs-edited" Studio view.** Studio could show the
    DB-resident `<prefix>_config` row alongside the WIP yaml edit — a
    "what's deployed vs what you're authoring" diff surface. Useful for
    "this rail's cap was 5000 in prod; my edit sets it to 7000."
  - **Dashboard pickers from L2 yaml** (the originally-queued item):
    pickers that show L2-DECLARED values (vs dataset-derived which
    shows whatever's in the data); pickers that JOIN to L2 metadata
    (descriptions, types, classifications); pickers that survive
    deploys without re-emitting JSON. Caveat: requires changing
    dashboard JSON's filter shape from `StaticValues` to
    `LinkToDataSetColumn`.
  - **Cross-tabular L2 context in dashboards.** Per-rail
    descriptions / per-account roles surfaced in tooltips, drill
    contexts, etc. — directly JOIN to `<prefix>_config.l2_yaml` from
    the dataset SQL rather than baking at emit time.
  - **Customer ETL access.** Operators / customer pipelines may want
    SQL access to "the same L2 values the matview uses" — DB-resident
    is friendlier than parsing yaml.
  - **Auditability.** `<prefix>_config` could carry timestamps for
    "when did the config last replace?" — Studio could surface "L2
    last updated 3 hours ago" as a deploy-state indicator.
  Scope this as its own evaluation phase (likely audit + spike +
  decision per surface); each Studio/Dashboard surface gets a "use
  DB / don't use DB / hybrid" call. Sized after picking a driver
  goal (analyst-friendliness vs operator ergonomics vs Studio
  iteration speed).



- **Q.6 — CLI shape revisit: cfg ⇄ L2 dual-yaml factoring.** Surfaced 2026-05-08 during `Y.2.gate.h.6`. The runner reads `cfg.default_l2_instance` and threads `QS_GEN_TEST_L2_INSTANCE` to subprocesses, making the CLI's dual-arg shape (`-c <cfg.yaml> --l2 <l2.yaml>`) partially redundant. Spike-before-implement (per `feedback_spike_before_locking_implementation`); CLI-surface change touches every operator command + doc example + tests.
  - **Q.6.0 SPIKE: combined-yaml vs cfg-with-L2-pointer vs status-quo** (LOCKED 2026-05-08; deferred from PLAN 2026-05-19). Output `docs/audits/y_11_cli_shape_spike.md`. Four candidates: **(A)** status quo + `--l2` defaults from `cfg.default_l2_instance` (smallest delta, mostly additive); **(B)** single combined yaml (eliminates dual-yaml friction but env-only fields co-mingle with institution-flavor — Q.5 separation existed for a reason); **(C)** cfg-with-L2-pointer + `--l2` removed entirely (forces multi-instance operators to duplicate cfg); **(D)** `--l2 <name>` indexed against an `l2_instances:` registry in cfg + `default_l2_instance:` (named ergonomics, one indirection layer). Constraints: (1) no-args `json apply --execute` deploys the default L2; (2) multi-L2 operators don't copy cfg files; (3) existing `--l2 <yaml>` keeps working or has a documented migration; (4) doc examples shrink; (5) tests pass without env-var passthrough. Likely outcome: A or D — A smallest delta, D cleanest if multi-L2-per-cfg becomes common.
  - **Q.6.1–Q.6.3 implement per spike result.** Updates `cli/{json,schema,data,audit}.py`, `cli/_helpers.py::resolve_l2_for_demo`, every CLAUDE.md / README / handbook example, every `runner.invoke([..., "--l2", ...])` test, every CI workflow YAML that uses `--l2`. Migration warning ≥1 minor version. Sweep memory entries + docs for stale `--l2 <yaml>` refs. Update CLAUDE.md "Commands" block to show the new shape as canonical; keep explicit `--l2` form as the multi-instance/override sub-pattern.
  - **Q.6.4 bump version (breaking CLI change — post-v9.0.0) + RELEASE_NOTES** (deferred from PLAN 2026-05-19) — entry highlighting the simplification + migration recipe.
- **Dashboards-local L1 dashboard render errors (surfaced 2026-05-10, X.2.g.4 territory, NOT a Y.2.g regression).** With the Y.2.g.2.d pool-lifespan fix landed, `dashboards --app l1_dashboard` starts cleanly + the drift KPI fetches data from the live matview, but other L1 visuals throw render errors in Dashboards (smoke + drift KPI work, broader rendering doesn't). Per-visual coverage in `_tree_fetcher` / `wrap_for_visual` — investigation/L2FT shipped via X.2.g.{2,3} with the same pattern, so the gap is L1-specific visual kinds the renderer hasn't grown arms for yet. Triage: capture the failing visual_ids + renderer error, extend `wrap_for_visual` with the missing arms, mirror the Investigation/L2FT shape. Out of Y.2.g scope (Dashboards visual coverage ≠ pushdown SQL); on the X.2.g roadmap. *(deferred from PLAN 2026-05-19)*
- **CI/release cleanup steps target the wrong scope — `database-2` + QS leak (captured 2026-05-10).** Three related bugs, all "the cleanup ran but cleaned the wrong thing," all functionally harmless (no impact on release publishing / e2e passing) but they leak resources. *(deferred from PLAN 2026-05-19; pick a fix before doing.)*
- **IAM: widen the `rds-start-stop` inline policy → create/destroy on `recon-gen-local` + `Github_e2e_testing`** (`rds:Create*/Delete*DBCluster/DBInstance`, DBSubnetGroup, `iam:PassRole` for monitoring, `ec2:` SG/subnet describe+authorize). *(deferred from PLAN 2026-05-20)*
- **CI: create a fresh DB per run → apply the current locked seed → drop after** (NO snapshot — seeds churn every commit). Runner `up`/`down` lifecycle per `feedback_ephemeral_aws_infra`. *(deferred from PLAN 2026-05-20)*
- **Local scripts: same pattern — own ephemeral DB per local run, pointed at the one standing Enterprise QS account.** *(deferred from PLAN 2026-05-20)*
- **Per-run cleanup verification:** after deploy→teardown, both the DB and any QS resources tagged with the run's `deployment_name` are gone (no orphans). `tests/e2e/test_cleanup_completeness.py` shape. *(deferred from PLAN 2026-05-20)*
- **Verify chain + commit; confirm idle RDS → ~$0 between runs in CE.** *(deferred from PLAN 2026-05-20)*
- **X.10 — Runner: intra-cell layer DAG (deploy starts right after seed).** The per-cell chain `unit → seed_variant → db → app2 → deploy → api → browser` runs strictly serially today, but `db` / `app2` / `deploy` only depend on `seed_variant` — they're siblings. `deploy` is the long pole (~2 min QS async creation); `db` (~45 s) + `app2` (~30 s) fit inside it. Fan `{db, app2, deploy}` with `asyncio.gather` after seed, then gather `{api, browser}` after `deploy` → ~75 s saved per `aw`-target cell (~6 min off a full-matrix run). The `asyncio` plumbing exists (`Y.2.gate.c.6.async`). Sub-tasks: (a) `cell_chain` returns a `{layer: frozenset[deps]}` DAG; (b) `_run_one_variant` topo-sorts + gathers siblings; (c) failure semantics for in-flight `deploy` (boto3 isn't cleanly cancellable — let it finish, report `db` failure, skip downstream); (d) unit tests for the DAG dispatch (topo/sibling-gather/truncation/failure-skips-downstream, mocked layer dispatch); (e) live wall-clock check, pyright, commit; update CLAUDE.md "Commands" chain description.
- **AA.0 Dashboard UX + exception literacy** *(COMPLETE — shipped v10.1.0a1; full plan archived in `PLAN_ARCHIVE.md` → "Phase AA")*. Six follow-ups still queued from that work:
  - **AA.A.10 (stretch) Tree-walk picker→column derivation.** `PickerSpec.column` still hand-mapped; the tree carries the wiring formally (`ParameterControl.parameter → Parameter.mapped_dataset_params → (dataset, dataset_param_name) → SQL <<$p>> site → column`). Either parse the dataset SQL or annotate `DataSetParameter` with a `narrows_column` field at construction. Spike before locking annotation-vs-parse.
  - ~~**AA.A.11 Cross-corpus duplication lint**~~ → promoted to **Phase BE** (2026-05-24). See above.
  - **AA.A.l2ft rails-inverse.4 Type-encode the `table_rows()` invariant.** `table_rows()` for narrowing-assertion sites is a smell — picker-row-survival is about SQL row count, not DOM visibility. Deprecate `len(table_rows())` for assertion use, or rename to `dom_visible_rows()`.
  - **AA.A.daterange.3 Structural refactor: single DATE_RANGE control.** Replace each sheet's `(DateTimePickerControl from + to + TimeRangeFilter)` triplet with one `FilterDateTimePickerControl(Type="DATE_RANGE")`. Closes "from > to" footgun; aligns L1 / L2FT / Exec with Investigation. **Wall:** L1's multi-dataset-per-sheet model needs a sharing mechanism (Investigation's filter-bound widget binds to ONE filter on ONE dataset). Options: (a) consolidate L1 datasets, (b) one widget per dataset per sheet, (c) QS mechanism driving multiple parameter-bound filters via intermediates. Spike before locking.
  - **AA.A.daterange.4 App2 renderer for widget-bound DATE_RANGE.** Already proven for Investigation; extends to the new L1/L2FT/Exec range controls. Follows .3.
  - **AA.A.daterange.5 Test infra.** `apply_anchor_to_pickers` becomes "set the range to span anchor's date ±1 day" instead of separate from/to. Single picker spec. Follows .3.
- **Model-driven docs (drift reduction).** Headline carried forward; design TBD.
- **Mobile / responsive.** Tailwind handles the layout primitives but no explicit mobile-first design pass. Promote when there's a customer story. Note: dashboards are dense by nature; mobile may always be a worse experience than desktop, regardless of effort.
- **Per-table CSV / XLSX export.** Operators expect "export to spreadsheet" on tables (QS has it). Lower priority than feature parity — punt unless it's a small agent task. The audit PDF already covers the "regulator-ready snapshot" case; spreadsheet export is for analyst self-serve.
- ~~**Fold the biome JS lint into the test runner, like pyright.**~~ *(done, 2026-05-12.)* `conftest.py::pytest_sessionstart` now runs `biome check --max-diagnostics=400` alongside the pyright gate — `biome check` exits non-zero on lint *errors* (e.g. `noInnerDeclarations`) and zero on warnings, so the gate fires before any test collects (`pytest.exit(returncode=2)`); opt out with `QS_GEN_SKIP_BIOME=1`. `biome` is a standalone Rust binary (brew locally; `biomejs/setup-biome@v2` in CI), not an npm/pip package — when it's not on `PATH` the gate skips cleanly (same posture pyright has if it's missing). Bare `pytest tests/`, `./run_tests.sh up_to=unit`, and `ci.yml::test` all enforce it. (Why not a `[dev]` dep like `pyright` / `pytailwindcss`? The Biome project hasn't published an *official* PyPI package yet — in flight at biomejs/biome#8818. The unofficial `biome-js` wrapper bundles the Rust binary like `ruff` does, but ships only a `manylinux_2_28_x86_64` wheel — no macOS / arm64 / sdist, and it's a stale single release on Biome 2.3.x — so adding it would break `uv sync --extra dev` off linux-x86_64. Biome therefore stays a system binary; the `[dev]` block carries a NB comment recording this + a "revisit when biomejs/biome#8818 merges" pointer. `dev_setup`: `brew install biome`, or any of biome's install methods. **Follow-on when biomejs/biome#8818 lands:** add the official package to `[dev]`, drop the `setup-biome` CI step + the system-binary fallback in conftest / install.md.)
- **Drop `_oracle_lowercase_alias_wrapper`; emit dialect-natural identifier case from the generator** (was Y.3.f, parked 2026-05-09). DDL is emitted unquoted (PG folds lowercase, Oracle UPPERCASE → divergent storage); `_oracle_lowercase_alias_wrapper` (`common/dataset_contract.py`) bolts an outer `SELECT qs_inner."ACCOUNT_ID" AS "account_id" ...` so QuickSight (which builds `SELECT "account_id" FROM (...)` from its declared lowercase Columns) finds matching aliases. The proper fix — generator emits dialect-natural case in `DatasetContract.to_input_columns()`, QS quotes UPPERCASE on Oracle natively, wrapper gone — is bigger than it looks: QuickSight's analysis-side validation is *case-sensitive* against `Dataset.Columns` (Y.3.f.2's reverted Oracle-deploy probe surfaced 45 column-missing errors), so it requires case-folding ~30+ analysis-side column refs per dialect (visuals / filters / calc-fields / drills), not just the Columns declaration. The original App2 Oracle column-casing bug it would have fixed was instead fixed narrowly by Y.3.f.alt (`wrap_for_visual` quotes its column refs). Re-spike if the dialect-helper count grows past ~60, or if SQLite gets dropped from the matrix. See `project_qs_analysis_validates_columns_case_sensitive` memory.
