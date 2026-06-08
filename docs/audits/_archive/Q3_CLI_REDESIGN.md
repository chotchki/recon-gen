# Q.3 — CLI / yaml ergonomics redesign (design doc)

*Status: draft. Needs review before any execution beyond what already
landed in the Q.3.a piecewise commands.*

This is the design document the user asked for after Q.3 scoping
surfaced that the 2-line PLAN entry (`Materialize SPEC's Workflow Ideas`)
no longer matches reality. The aim is to settle a coherent CLI shape
across all four artifacts the tool produces, with first-class testing,
before writing more code.

## Background

### What the tool produces

The repo generates four discrete artifacts:

1. **Schema** — per-prefix DDL (tables, views, materialized views) for
   an L2 instance.
2. **Demo data** — seed SQL (90-day baseline + plant overlays) for an
   L2 instance.
3. **JSON for QuickSight** — datasets, analyses, dashboards, themes
   per app.
4. **Documentation** — a mkdocs handbook site rendered against an L2
   instance.

Each artifact has the same lifecycle question: *do you want to inspect
it, run it for me, or undo it?*

### What the CLI shape is today

After the Q.3.a piecewise commit (the in-flight 6 new `demo emit-*` /
`demo apply-*` commands) the CLI surface is:

| Top-level verb | What it produces | Inconsistency |
|---|---|---|
| `generate <app>` / `--all` | JSON for QuickSight | Verb is "generate", paired with "deploy" |
| `deploy <app>` / `--all` | applies JSON to AWS QuickSight | Verb is "deploy", paired with "generate" |
| `cleanup` | deletes managed QuickSight resources | One-off verb, only applies to JSON quadrant |
| `demo apply [app]` | bundles schema + seed + matview refresh + JSON | "demo" prefix is misleading; this is the integrator path |
| `demo emit-schema` | schema DDL → stdout/file | Just added (Q.3.a) |
| `demo emit-seed` | seed SQL → stdout/file | Just added (Q.3.a) |
| `demo emit-refresh` | refresh SQL → stdout/file | Just added (Q.3.a) |
| `demo apply-schema` | schema DDL → DB | Just added (Q.3.a) |
| `demo apply-seed` | seed SQL → DB | Just added (Q.3.a) |
| `demo apply-refresh` | refresh → DB | Just added (Q.3.a) |
| `demo seed-l2` | narrow contract-test seed (hash-locked) | Predates `demo emit-seed`; different scope |
| `demo etl-example` | example INSERT patterns for ETL authors | Documentation artifact, lives under `demo` |
| `demo topology` | L2 topology SVG | Documentation artifact, lives under `demo` |
| `export docs` | extracts mkdocs source for hand-build | Only docs-emit; no built-site command |
| `export screenshots` | dashboard PNG captures | Documentation artifact, lives under `export` |
| `probe` | sanity-checks deployed dashboards via Playwright | Test-shaped, no group |

Problems with this shape:

- **No uniform vocabulary.** Schema/data use `apply`, JSON uses
  `deploy`, docs uses `export`, dashboard cleanup uses `cleanup`.
  Same conceptual operation across artifacts gets a different verb
  every time.
- **No top-level grouping by artifact.** Integrators have to learn
  per-artifact which verb maps to which lifecycle stage. There's no
  `<artifact> --help` to see the whole picture for one quadrant.
- **No uniform emit-vs-apply pattern.** Q.3.a added it for schema /
  data; JSON has it as `generate` (always emits) + `deploy
  --generate` (composite); docs has only `export` (extracts
  source).
- **No uniform clean.** Only `cleanup` exists, only for JSON. There's
  no schema-drop, no seed-truncate, no docs-purge.
- **No first-class test surface.** `pytest tests/test_X.py` is the
  contract; integrators have to learn which test file maps to which
  artifact.

## Proposal: four artifacts × four operations

Group every command by **artifact** at the top level. Within each
artifact group, expose four operations: **apply**, **clean**,
**test**, plus artifact-specific extras. Each operation defaults to
*do it*; passing `-o FILE` (or `--stdout`) emits the script/JSON
instead of executing.

### CLI surface

```
quicksight-gen schema apply  --l2 PATH [-c CONFIG] [-o FILE] [--execute]
quicksight-gen schema clean  --l2 PATH [-c CONFIG] [-o FILE] [--execute]
quicksight-gen schema test   [--pytest-args "..."]

quicksight-gen data apply    --l2 PATH [-c CONFIG] [-o FILE] [--execute]
quicksight-gen data refresh  --l2 PATH [-c CONFIG] [-o FILE] [--execute]
quicksight-gen data clean    --l2 PATH [-c CONFIG] [-o FILE] [--execute]
quicksight-gen data test     [--pytest-args "..."]

quicksight-gen json apply    --l2 PATH [-c CONFIG] [-o DIR] [--execute]
quicksight-gen json clean    [-c CONFIG] [--execute]
quicksight-gen json test     [--pytest-args "..."]
quicksight-gen json probe    [-c CONFIG]

quicksight-gen docs apply    [--l2 PATH] [-o DIR]      # always emits a built site
quicksight-gen docs serve    [--l2 PATH] [--port N]    # mkdocs serve, live reload
quicksight-gen docs clean    [-o DIR]                  # rm -rf the built site
quicksight-gen docs test     [--pytest-args "..."]
quicksight-gen docs export   [-o DIR]                  # extract source for hand-build
quicksight-gen docs screenshot --app NAME [-c CONFIG] -o DIR
```

The integrator's mental model: *pick the artifact, pick the verb,
optionally redirect output. Run with `--execute` when you actually
want the side effect.*

### Operation semantics

| Operation | Default behavior | With `--execute` |
|---|---|---|
| `apply` (schema/data/json) | Emit the script/JSON to stdout (or `-o FILE`) | Connect + execute against DB / AWS |
| `refresh` (data only) | Emit the REFRESH SQL | Run REFRESH MATERIALIZED VIEW |
| `clean` (schema/data/json) | Emit the cleanup script | Connect + drop / truncate / delete |
| `test` | Run the artifact's contract test suite | n/a |
| `serve` (docs only) | Live-reload mkdocs server | n/a |
| `probe` (json only) | Playwright sanity walk on deployed dashboard | n/a |
| `apply` (docs only) | Build the site to `site/` (or `-o DIR`) | n/a — building a site isn't a side effect |
| `export` (docs only) | Copy mkdocs source to DIR | n/a |
| `screenshot` (docs only) | Capture deployed dashboards to PNG | n/a |

**The default for any apply/clean is emit-only.** Pass `--execute`
when you actually want the side effect to land in the DB / AWS.
This makes the safe path the default and forces an explicit opt-in
for anything destructive — nothing accidentally drops a table or
redeploys a dashboard.

(Note: `docs apply` doesn't need `--execute` because building a
static site to a directory isn't a destructive side effect — the
"emit" and the "do it" are the same operation.)

### Artifact-specific notes

#### `schema`

- **clean**: drops every per-prefix object the L2 emits (matviews →
  views → tables, in dependency order). Mirrors what
  `tests/e2e/_harness_cleanup.py` already does — lift that to a
  public emitter on `common/l2/schema.py`.
- **test**: runs `tests/test_l2_schema.py` plus the dialect snapshot
  test against `--dialect`. This is the "did the emitted DDL change
  bytes" gate.

#### `data`

- **clean**: emits `TRUNCATE` statements for `<prefix>_transactions`
  and `<prefix>_daily_balances` (and any matviews that depend on them
  — REFRESH them after). Schema-preserving; if you want the schema
  gone, run `schema clean` after.
- **refresh**: lifted from today's `demo apply-refresh`.
- **test**: hash-lock verification. Runs `tests/test_l2_baseline_seed.py`
  hash check against the canonical anchor date.

#### `json`

- **apply**: emits JSON for all four apps to ``out/`` (or ``-o DIR``).
  With ``--execute``: also deploys to AWS QuickSight. No ``--app``
  filter — always operates on the full set of four bundled apps
  (investigation / executives / l1-dashboard / l2-flow-tracing).
  Per-app development was useful during M / N / O when each app was
  iterating independently; today they ship as a bundle and finer
  grain just adds ceremony.
- **clean**: today's `cleanup`. Connects to AWS and deletes every
  resource tagged ``ManagedBy:quicksight-gen`` for the active L2
  instance. With ``--execute``: actually deletes; without, dry-runs
  + lists what would be deleted (matches the historical ``cleanup
  --dry-run`` default).
- **test**: runs the per-app contract test suite for all four
  apps. Pytest covers ``test_l1_*.py``, ``test_inv_*.py``,
  ``test_exec_*.py``, ``test_l2ft_*.py`` together; ``--browser``
  flag adds the Playwright e2e tests.
- **probe**: today's `probe`. Stays; runtime sanity check against
  every deployed dashboard, complementary to `test`.

#### `docs`

- **apply**: wraps `mkdocs build`. Default output is `site/`.
  Honors `QS_DOCS_L2_INSTANCE` env var or `--l2 PATH`. Pulls in
  mkdocs as a hard dep (already a dev dep; promote to a runtime
  extra `[docs]`).
- **serve**: wraps `mkdocs serve`. Useful for integrator-side
  authoring of `persona:` blocks etc. Honors the same `--l2` arg.
- **clean**: removes `site/` (or whatever DIR was passed).
- **test**: today's `tests/test_docs_links.py` +
  `tests/test_docs_persona_neutral.py`. Both gates surface as one
  CLI invocation.
- **export**: today's `export docs`. The hand-build path is still
  useful for integrators who want their own mkdocs config / theme.
- **screenshot**: today's `export screenshots`. Lives under `docs`
  because the screenshots end up embedded in the rendered handbook.

### Naming the verbs uniformly

Open question: is the right verb `apply` or `build`?

- **apply**: matches schema/data semantics (apply DDL to a database).
  Less natural for `docs` (you don't "apply" a static site).
- **build**: matches docs and JSON semantics (you build a site, you
  build a JSON output). Less natural for schema (you don't "build"
  DDL — you apply it).

Recommendation: **apply** for the consistency of *the integrator does
this thing intending to put it somewhere real*. Phrase the docs
help text as "build and write the documentation site" so the verb
discoverable name is "apply" but the behavior matches the docs idiom.

Alternative considered: split into `build` (always-emit, no side
effects) and `deploy` (always-apply, side effects) per artifact.
Rejected because it forces the integrator to remember TWO verbs per
artifact for the same conceptual action; the `-o FILE` flag pattern
is shorter and discoverable from `<verb> --help`.

**Answer**: agree on apply

### Migration / rollout

This is a breaking change to the CLI surface. Plan:

1. **v7.4.0** — ship the new `<artifact> <verb>` shape as the canonical
   surface. Old verbs (`generate`, `deploy`, `cleanup`, `demo apply`,
   `demo emit-*`, `demo apply-*`, `export *`, `probe`) become
   **deprecated aliases**. Each old verb prints a one-line stderr
   warning pointing at the new equivalent, then runs.
2. **v7.4.x** — minor releases keep the aliases. Update every script
   in `scripts/`, `run_e2e.sh`, `tests/`, and the rendered handbooks
   to use the new shape.
3. **v8.0.0** — drop the old verbs. Aliases become hard errors with
   the same redirect message.

The aliases let scripts continue to work for one release cycle while
the integrator migrates.

**Answer**: no one is using this yet, break without aliases. Apathetic to the version number.

### Testing as a first-class concern

Beyond the `<artifact> test` CLI surface, restructure the test tree
to mirror the four-artifact split (a separate, larger task):

```
tests/
  schema/      # test_l2_schema.py, snapshots, dialect parsers
  data/        # test_l2_baseline_seed.py (hash lock), seed contract
  json/        # test_l1_*.py, test_inv_*.py, test_exec_*.py, test_l2ft_*.py
  docs/        # test_docs_links.py, test_docs_persona_neutral.py
  e2e/         # cross-artifact integration (today's e2e/ stays)
  unit/        # tree primitives, common helpers (the rest)
```

This is a chunky reorg (renames + import path adjustments). Defer
to a Q.3.c sub-phase; the v7.4.0 cut can ship the CLI redesign
without it.

### Out of scope

- **Q.3.b yaml/config boundary review.** The split between
  `config.yaml` (deploy account, region, datasource ARN) and the L2
  YAML (rails, accounts, persona, theme) deserves its own scoping
  pass after the CLI redesign settles. The CLI shape decided here
  doesn't constrain that work.
- **Publishing docs.** Beyond `docs apply` (build to `site/`), no
  CLI command for `gh-pages push` / `S3 sync` etc. Integrator
  pluggable; out of scope for v7.4.0.
- **Workflow shortcuts.** A `quicksight-gen all-up` / `down` that
  composes every artifact's apply/clean is tempting but not
  required by the four-artifact model. Punt.

## Open questions for the user

Before I execute:

1. **Verb choice.** Stick with **apply** as the canonical do-it verb
   even when it reads slightly off for docs? (Recommendation: yes,
   for consistency.)
  - yes, consistency is better
2. **`--stdout` vs only `-o FILE`.** Need both? (Recommendation:
   yes; `-o -` is unobvious.)
  - I'm good with that
  - **Update post-Q.3.a.5 partial:** dropped `--stdout` because the
    new default is *always* emit (apply/clean default to printing
    the script, not running it). Stdout becomes the no-flag default;
    pass `-o FILE` to write to a file; pass `--execute` to actually
    run it. Result: emit-vs-execute is one explicit flag instead of
    two implicit modes.
3. **`docs apply` defaults to `site/` or always requires `-o`?**
   (Recommendation: default to `site/` so integrators don't need
   to think about it; emit a one-liner naming the dir.)
 - I'm good with that
4. **`json clean` scope.** Today's `cleanup` deletes everything
   tagged `ManagedBy:quicksight-gen` not in the current `out/`. Keep
   that semantics? Add `--app` filter? (Recommendation: keep
   default semantics, add `--app` filter.)
 - I need it to have an option to nuke everything with the tag. I think that's the more user friendly approach.
5. **Test wrappers run pytest internally?** Or just print the
   pytest command to run? (Recommendation: run pytest internally
   with sensible defaults; expose `--pytest-args` for power users.)
 - Yes call into pytest and the typing checks
6. **Migration speed.** Aliases for one minor cycle (v7.4.0 →
   v8.0.0) or longer?
 - Clean break, we're going to work it through.
7. **Test reorg.** Bundle with v7.4.0 or defer to Q.3.c? (Risk: if
   we defer, the test suite stays at the old shape while the CLI
   advertises a new one — confusing for integrators reading the
   docs.)
 - Clean break, reorg. We will need to do a full test run.

## Estimated work

- **Q.3.a.5 — CLI shell** (2-3 hours): top-level groups, the 14 new
  commands, deprecated-alias plumbing.
- **Q.3.a.6 — `data clean` + `schema clean`** (1-2 hours): lift
  cleanup emitters from `tests/e2e/_harness_cleanup.py` to public
  `common/l2/schema.py` API.
- **Q.3.a.7 — `docs build/serve/clean/test`** (1 hour): mkdocs
  wrappers + reuse existing tests.
- **Q.3.a.8 — `<artifact> test` wrappers** (1 hour): one click
  command per artifact that pytest-shells the right test files.
- **Q.3.a.9 — Handbook + walkthrough rewrite** (3-4 hours): every
  doc that names an old verb gets updated; new "CLI tour"
  section in `handbook/customization.md`.
- **Q.3.a.10 — Script updates** (1-2 hours): `scripts/`,
  `run_e2e.sh`, `tests/test_l2_runtime_assertions.py` docstring,
  `RELEASE_NOTES.md`.
- **Q.3.a.11 — Tests for new CLI** (2 hours): one test per command
  that exercises `--help` + the emit path against `spec_example`.

Total: ~12-16 hours of focused work, plus the iteration that always
follows a CLI redesign once the integrator hits friction.

---

*Review action items:* answer the 7 open questions above, sign off
on the proposed surface, and I'll execute Q.3.a.5 onwards.
