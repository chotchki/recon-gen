# How do I run my first deploy?

*Customization walkthrough — Developer / Product Owner. Setup.*

## The story

Your data is landing in `{{ l2_instance_name }}_transactions` +
`{{ l2_instance_name }}_daily_balances`
([How do I map my production database?](how-do-i-map-my-database.md)),
your `config.yaml` is in place
([How do I configure the deploy?](how-do-i-configure-the-deploy.md)),
and you're ready to push the dashboards to QuickSight for the
first time. This walkthrough is the actual deploy invocation —
what runs, what to watch for, and how to roll back if something
looks off.

The deploy is **idempotent and delete-then-create**: every run
deletes existing resources by ID and creates fresh ones. There's
no concept of "update" in this tool. Schema drift between an old
deploy and the current generate output never causes weird
half-updated states because nothing is updated — everything is
re-created from scratch on every run. The trade-off is a deploy
takes ~3-5 minutes (the asynchronous CREATE_ANALYSIS /
CREATE_DASHBOARD calls poll to terminal state); the win is no
state-divergence debugging, ever.

## The question

"What's the actual command sequence to get the dashboards
deployed to my AWS account, and how do I confirm they landed
cleanly?"

## Where to look

Three reference points:

- **`recon-gen --help`** — the CLI surface. Four artifact
  groups: `schema`, `data`, `json`, `docs`. Each has at minimum
  `apply` / `clean` / `test`; everything destructive defaults to
  emit (print SQL, write JSON to `out/`) and only runs against the
  DB or AWS when you pass `--execute`. `json apply` always emits
  all four apps' JSON — there's no per-app filter.
- **`src/recon_gen/common/deploy.py`** — the deploy
  implementation. Read `deploy()` to see the delete-then-create
  order.
- **The QuickSight console** (`https://quicksight.aws.amazon.com`)
  — the visual target. After deploy, your analyses + dashboards
  appear here under the configured `cfg.deployment_name` (Z.C —
  required cfg field, no default).

## What you'll see in the demo

The minimum end-to-end run for all four apps:

```bash
recon-gen json apply -c config.yaml -o out/ --execute
```

`json apply` always writes the JSON tree (theme + per-app
analyses, dashboards, and datasets) into `out/`. The
`--execute` flag adds the AWS deploy step on top — it never
gates the local JSON emit. Drop `--execute` to write JSON to
`out/` only without touching AWS (useful for inspecting the
generated output, or for piping into a different deploy
pipeline).

The output stream has two phases. First, the per-app emit:

```
Generating JSON for all four apps into out/...
Investigation: account=111122223333, region=us-east-2, l2_instance=spec_example
  wrote out/theme.json
  wrote out/datasets/recon-prod-inv-recipient-fanout-dataset.json
  ... (~7 datasets)
  wrote out/investigation-analysis.json
  wrote out/investigation-dashboard.json

Generated 10 files in out/
Executives: account=111122223333, region=us-east-2, l2_instance=spec_example
  ... (~5 datasets + analysis + dashboard)
L1 Dashboard: account=111122223333, region=us-east-2, l2_instance=spec_example
  ... (~16 datasets + analysis + dashboard)
L2 Flow Tracing: account=111122223333, region=us-east-2, l2_instance=spec_example
  ... (~5 datasets + analysis + dashboard)
```

Then, with `--execute`, the deploy:

```
Deploying to AWS QuickSight...
Deploying QuickSight resources from out
  Account: 111122223333
  Region:  us-east-2

==> Dashboard: recon-prod-l1-dashboard
    Deleting existing dashboard...
... (per dashboard, then analyses, then datasets, then theme,
     then datasource — all delete first)

--- Recreating all resources ---

==> Datasource: ...
==> Theme: recon-prod-theme
==> Dataset: recon-prod-l1-todays-exceptions-dataset
... (~27 datasets total across all four apps)
==> Analysis: recon-prod-l1-dashboard-analysis
... (one per app)
==> Dashboard: recon-prod-l1-dashboard
... (one per app)

--- Waiting for async resources ---

==> Checking Analysis: recon-prod-l1-dashboard-analysis
    Status: CREATION_SUCCESSFUL
... (one per analysis + dashboard)

Done. All resources deployed to 111122223333 in us-east-2.
```

Total wall time on a fresh account: 3-5 minutes. Most of it is
the analysis + dashboard polls (the ~27 datasets are synchronous
and complete in seconds).

## What it means

The deploy runs a fixed order of operations
(`common/deploy.py`):

### Phase 1 — Delete existing (in dependency order)

1. **Dashboards** — leaf resources, no dependents. Deleted first.
2. **Analyses** — backed by datasets. Deleted second.
3. **Datasets** — backed by theme + datasource. Deleted third.
4. **Theme** — referenced by datasets. Deleted fourth.
5. **Datasource** — referenced by datasets (demo only).
   Deleted last.

Each delete is best-effort — a `ResourceNotFoundException`
("nothing to delete") is treated as success. Fresh accounts
skip past the delete phase entirely; on the second deploy
they tear down what the first one created.

### Phase 2 — Create (in dependency order, reverse of delete)

1. **Datasource** (demo only) — created from
   `out/datasource.json`.
2. **Theme** — created from `out/theme.json`.
3. **Datasets** — created from `out/datasets/*.json` (32+ files).
4. **Analyses** — created from `out/<app>-analysis.json`.
5. **Dashboards** — created from `out/<app>-dashboard.json`.

Analyses and dashboards return immediately with a
`CREATION_IN_PROGRESS` status; the deploy then polls
`describe_analysis` / `describe_dashboard` every 5 seconds until
each reaches `CREATION_SUCCESSFUL` (success) or
`CREATION_FAILED` (failure). 60-attempt cap = 5-minute timeout
per resource.

### Phase 3 — Report

Exit code 0 on full success; 1 if any analysis or dashboard
ended in `CREATION_FAILED`. The error messages live in the
poll output — scroll back to find which resource failed and
why.

## Drilling in

A few patterns to know once the basic deploy works:

### Dry-run before live with `json clean`

Before your first real deploy on an existing account, run:

```bash
recon-gen json clean -c config.yaml
```

This enumerates every QuickSight resource tagged
`ManagedBy:recon-gen` in the account and prints what
*would* be deleted on a `json clean --execute`. The deploy itself
also deletes-then-creates the resources it manages, but
`json clean` finds *orphans* — resources from a previous deploy
that the current generate output no longer produces (a
dataset you removed, an analysis you renamed). Run it before
the real deploy to spot any unexpected state.

If the dry-run lists things you don't recognize, *do not*
proceed with `json clean --execute` until you've investigated. The
`ManagedBy:recon-gen` tag scope is intentional — the
tool will never touch resources without that tag — but a
co-worker running a different prefix could have left
unrelated state.

### Iteration loop: `json apply --execute`

Once your first deploy works, the standard iteration loop is:

```bash
# Edit some Python (a visual, a SQL query, a theme color)
recon-gen json apply -c config.yaml -o out/ --execute
# Refresh the QuickSight dashboard in your browser
```

`json apply --execute` rewrites JSON to `out/` and deploys it in
one command. About 3-5 minutes per cycle — the new CLI always
emits and deploys all four apps as a bundle.

### Cleanup after dropping a dataset

If you remove a dataset from a `datasets.py` file (a contract
revision or a sheet consolidation), the next generate correctly
omits it from `out/datasets/`, but the deploy deletes only the
datasets it knows about — the orphan dataset in QuickSight
survives. Run:

```bash
recon-gen json clean -c config.yaml
```

This enumerates `ManagedBy:recon-gen` resources, compares
against current `out/` contents, and prints anything that's
no longer in the build. The default is dry-run; pass `--execute`
to actually delete.

### What happens if a deploy fails mid-cycle

QuickSight is mostly atomic at the per-resource level — a
failed `create_analysis` doesn't leave partial state on that
analysis ID. But across resources, a failure mid-cycle can
leave some datasets created and others not yet attempted. The
re-run is the recovery: `json apply --execute` again. The
delete-then-create model means the second run cleanly tears
down whatever the first run partially built and starts over.
No manual cleanup typically required.

If a deploy keeps failing on the same resource, read the poll
output for the `Errors` field on the failing resource —
QuickSight surfaces dataset-projection errors,
column-type-mismatch errors, and missing-field errors here
verbatim. The most common production failure is a custom
dataset SQL whose column shape drifted from the contract; the
contract test
([How do I swap dataset SQL?](how-do-i-swap-dataset-sql.md))
catches this before deploy, but only if you ran it.

## Next step

Once your first deploy completes with all
`CREATION_SUCCESSFUL`:

1. **Open the dashboard in QuickSight.** Console → Dashboards
   → `<deployment_name>-l1-dashboard` (where `<deployment_name>`
   is your `cfg.deployment_name` value).
   Click through the tabs. KPIs should populate; tables should
   render rows. Empty visuals usually mean the underlying
   dataset's SQL returned zero rows against your data — open
   the dataset directly to see the SQL and run it manually
   against your warehouse.
2. **Hand the dashboard URL to a small group of users first.**
   The principals you listed in `config.yaml` get edit + view
   access. Your treasury / GL recon team is the natural first
   audience for the L1 dashboard; your compliance team for
   Investigation; your CFO for Executives.
3. **Wire deploy into CI.** Once the deploy is reliable
   manually, automate it. The env-var override pattern from
   [How do I configure the deploy?](how-do-i-configure-the-deploy.md)
   lets one CI runner deploy to multiple environments by
   swapping `RECON_GEN_AWS_ACCOUNT_ID` /
   `RECON_GEN_DATASOURCE_ARN` per stage.

## Related walkthroughs

- [How do I configure the deploy for my AWS account?](how-do-i-configure-the-deploy.md) —
  the **prerequisite**: the `config.yaml` fields the deploy
  reads.
- [How do I swap the SQL behind a dataset?](how-do-i-swap-dataset-sql.md) —
  the most common deploy-failure root cause is a custom
  dataset whose column shape drifted from the contract. The
  contract test catches it pre-deploy.
- [How do I reskin the dashboards for my brand?](how-do-i-reskin-the-dashboards.md) —
  for when "the deploy worked but the colors are wrong."
