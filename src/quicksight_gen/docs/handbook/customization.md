# Customization Handbook

*Reshape the dashboards onto your own backend without rewriting the
visual layer. Currently rendered against
**{{ vocab.institution.name }}** ({{ l2_instance_name }}).*

This handbook is for the **developer or product owner** dropping
the four shipped dashboards (L1 Reconciliation, L2 Flow Tracing,
Investigation, Executives) onto their own data — not the Data
Integration ETL engineer loading the two base tables (that's the
[Data Integration Handbook](etl.md)).

The product is built around a small, deliberate set of
*customer-mutable* surfaces. Swap the SQL behind a dataset, swap
the colors on a theme, point the deploy at a different AWS
account, or extend the metadata contract — each happens in one
place, with one test that catches the regression. The visual,
filter, and drill layer above the data binds to a stable column
contract; you change *what fills the contract*, not *how the
visuals consume it*.

## What stays stable

These are the surfaces this handbook documents. They're the parts
of the product that are deliberately small and don't churn under
new persona work or dashboard redesigns:

- **Two base tables** — `transactions` + `daily_balances`. Every
  app reads from these. Adding a new persona or a new exception
  check doesn't add a new base table; it adds a new dataset SQL
  view over the same two tables.
- **`DatasetContract`** — column name + type list per dataset.
  The SQL query is *one* implementation; you can swap the SQL
  while preserving the contract and the visual layer keeps
  working untouched.
- **`metadata` JSON column** — the per-app extension point.
  Add keys without schema migrations; read them with
  portable `JSON_VALUE` syntax.
- **Theme presets** — color tokens, fonts, naming prefix.
  Your brand drops in via one preset registration.
- **`config.yaml` + CLI** — account, region, principals,
  resource prefix, datasource ARN, all configurable from one
  file (or env vars). The CLI itself (`generate` / `deploy` /
  `cleanup` / `demo`) is the customer-facing surface and won't
  change shape without a major version bump.

## What this handbook does *not* cover

- **Per-visual customization.** Each shipped app's visuals
  evolve as the L1/L2 model + Investigation/Executives stories
  iterate. Document specific visuals once they stabilize.
- **Per-dataset SQL enumeration.** Each dataset's SQL is in
  `apps/<app>/datasets.py` (e.g. `apps/l1_dashboard/datasets.py`,
  `apps/investigation/datasets.py`); read it as the source of
  truth. The pattern for *replacing* it is documented here once.
- **Per-sheet layout.** Sheet structure is part of the active
  product surface and may shift under integrator-driven redesigns.

## How filters work

A filter — a date range, a status dropdown, a sigma-threshold slider — is a **parameter substituted into the dataset's SQL `WHERE` clause**, not a UI-side filter applied after the fact. The dataset query already carries a `<<$paramName>>` placeholder (or, for the universal date range, a `{date_filter}` slot the `build_dataset` helper fills); when the analyst moves a control, the renderer substitutes the value into that query and the database returns the narrowed set. The two renderers do the same thing the same way:

- **QuickSight** substitutes the literal value into the dataset's CustomSql at fetch time, bridged from an analysis-level parameter via `MappedDataSetParameters`.
- **The self-hosted renderer** translates the same `<<$paramName>>` into a `:param_name` bind before running the query.

Two consequences worth knowing if you're swapping SQL behind a dataset:

1. **Keep the placeholders.** If the dataset you're replacing has a `{date_filter}` slot or a `<<$pSomething>>` in its `WHERE`, your replacement SQL needs the equivalent — that's how the date control / dropdown / slider stays wired. The `DatasetContract` still locks the *projection* (column names + types); the placeholders are the parameter contract on top of it.
2. **It narrows at the database.** A filtered view fetches fewer rows, not "all rows then hide some" — relevant when you're sizing matview/index work for your own data volumes.

This is internal QuickSight/self-hosted-renderer architecture (a Phase Y / v9.0.0 change). It does **not** touch your `config.yaml` or your L2 instance YAML — the `theme:` block, the optional `persona:` block, your rails / chains / accounts / limit schedules are all unchanged. If you carried a customization across the v9.0.0 line, your YAML didn't need to move; only the generated QuickSight definitions did.

## Setup

<p class="snb-section-label">Get the dashboards landed against your data</p>

<div class="snb-card-grid">
  <a class="snb-card" href="../../walkthroughs/customization/how-do-i-map-my-database/">
    <h3>How do I map my production database to the two base tables?</h3>
    <p>Pattern-level mapping from your source system to <code>transactions</code> + <code>daily_balances</code>. The first walkthrough a new product owner reads.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/customization/how-do-i-configure-the-deploy/">
    <h3>How do I configure the deploy for my AWS account?</h3>
    <p><code>config.yaml</code> fields, environment-variable overrides, production datasource ARN vs. demo connection string, principals + tags + naming prefix.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/customization/how-do-i-run-my-first-deploy/">
    <h3>How do I run my first deploy?</h3>
    <p>The <code>generate</code> + <code>deploy</code> + <code>cleanup</code> loop, idempotent delete-then-create, dry-run before live, <code>ManagedBy</code> tag scoping.</p>
  </a>
</div>

## Reskinning + extending

<p class="snb-section-label">Make the product fit your environment</p>

<div class="snb-card-grid">
  <a class="snb-card" href="../../walkthroughs/customization/how-do-i-reskin-the-dashboards/">
    <h3>How do I reskin the dashboards for my brand?</h3>
    <p>Theme preset registry, color tokens (accent / primary_fg / link_tint), font sizes, the <code>analysis_name_prefix</code> for demo-vs-prod naming.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/customization/how-do-i-swap-dataset-sql/">
    <h3>How do I swap the SQL behind a dataset without breaking the visuals?</h3>
    <p>The <code>DatasetContract</code> binding contract, the contract test that locks projection-vs-contract, when SQL swap is safe and when it forces a contract change.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/customization/how-do-i-add-a-metadata-key/">
    <h3>How do I add an app-specific metadata key?</h3>
    <p>Reading metadata from dataset SQL, when to surface a key as a column vs. a filter, cross-link to the ETL-side walkthrough for the write path.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/customization/how-do-i-extend-canonical-values/">
    <h3>How do I extend the schema with a new rail_name or account_type?</h3>
    <p>Adding to the canonical value lists, downstream impact on filter dropdowns, why no new tables are needed.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/customization/how-do-i-brand-my-handbook-prose/">
    <h3>How do I brand my handbook prose?</h3>
    <p>The optional <code>persona:</code> YAML block — institution name + acronym, stakeholders, GL account labels, merchants, flavor literals. Substitutes via <code>vocab</code> Jinja references at mkdocs render. Skip the block to keep neutral fallback prose.</p>
  </a>
</div>

## Testing your customization

<p class="snb-section-label">Catch regressions before they ship</p>

<div class="snb-card-grid">
  <a class="snb-card" href="../../walkthroughs/customization/how-do-i-test-my-customization/">
    <h3>How do I run the test suite against my customized dataset SQL?</h3>
    <p>pytest layout, the <code>DatasetContract</code> assertion pattern, when to add an e2e test vs. a unit test for your custom SQL.</p>
  </a>
</div>

## Optional ETL extensions

A small set of feed columns are *optional* — leave them NULL and
the downstream views fall back to a sensible default; populate
them when you can give the dashboard rail-accurate signal:

- **`expected_complete_at`** (TIMESTAMP on `transactions`) — when
  your ETL knows the rail's settlement window (instant: same-day;
  ACH: T+2; cards: T+3), set it per leg. The dashboard's
  data-driven `is_late` predicate fires off this column with a
  `posted_at + INTERVAL '1 day'` fallback when it's NULL. Adopt
  one rail at a time; until then, every row uses the one-day
  default. Full contract: [Lateness as data](../Schema_v6.md#lateness-as-data)
  in the schema doc, plus the
  [`expected_complete_at` ETL section](etl.md#optional-expected_complete_at-lateness)
  in the ETL handbook.
- **`metadata`** (JSON TEXT on `transactions` and
  `daily_balances`) — the per-app extension column. Add
  app-specific keys without schema migrations; the
  *How do I add an app-specific metadata key?* walkthrough above
  is the read/write contract.

## The L2-fed pattern (M.2b)

The above sections cover the v5 customization path — `mapping.yaml`
substitution onto a hand-rolled per-app dashboard. The newer
**L2-fed pattern** is the recommended approach going forward: declare
your institution as an L2 instance YAML once, and the L1 dashboard
renders against it generically.

### 1. Write your L2 instance YAML

Mirror `tests/l2/{{ l2_instance_name }}.yaml` for shape. The L2 declares:

- **Accounts** + roles, scopes (internal/external), parents
- **Account templates** (role classes that materialize at runtime)
- **Rails** — one-leg / two-leg / aggregating; per-rail aging caps
  (`max_pending_age`, `max_unbundled_age`)
- **Transfer templates** — multi-leg shared transfers with closure
- **Chains** — transfer-of-transfers ordered flows; XOR groups
- **LimitSchedules** — per-`(parent_role × rail_name)` daily caps
- A `description` field on every primitive (surfaces as TextBox
  prose on the dashboard)

Rich descriptions matter — the M.2a.7 prose seam pulls them straight
into the dashboard's Getting Started, Drift, Limit Breach, and
Today's Exceptions text boxes. Switching the L2 instance switches
the prose without touching dashboard code.

### 2. Apply the prefixed schema

```python
from quicksight_gen.common.l2 import emit_schema, load_instance

instance = load_instance("path/to/myorg.yaml")
sql = emit_schema(instance, prefix="myorg")  # Z.C — prefix is now a kwarg
# Pipe to psql, or:
import psycopg2
conn = psycopg2.connect(your_db_url)
with conn.cursor() as cur:
    cur.execute(sql)
```

Every table, view, and matview in the emitted DDL is prefixed by
the `prefix=` value you pass (typically `cfg.db_table_prefix` — Z.C),
producing e.g. `myorg_transactions`, `myorg_drift`,
`myorg_stuck_pending`. Multiple deployments of the same L2 instance
coexist in one database via distinct prefixes.

### 3. Refresh the matviews after every load

The L1 invariant views are MATERIALIZED (M.1a.9) for dashboard
performance. After every batch insert into `{{ l2_instance_name }}_transactions`
or `{{ l2_instance_name }}_daily_balances`, refresh the dependent matviews:

```python
from quicksight_gen.common.l2 import refresh_matviews_sql
sql = refresh_matviews_sql(instance)
# 13 matviews × 2 statements each = 26 (REFRESH + ANALYZE) per call
```

### 4. Deploy the L1 dashboard against your instance

The CLI defaults to the bundled `{{ l2_instance_name }}` fixture; swap to your
own instance by editing the build call site or providing your own
`l2_instance` kwarg via a small wrapper script. Then:

```bash
quicksight-gen json apply -c run/config.yaml -o run/out
quicksight-gen json apply -c run/config.yaml -o run/out --execute
```

### 5. Verify with the test runner

The `./run_tests.sh up_to=db --variants=sp_pg_aw` chain (or `sp_or_aw`
for Oracle) applies the schema, plants the canonical seed scenarios,
refreshes matviews, and asserts each L1 invariant view returns rows
for every planted scenario via the e2e harness tests. For your own
instance, write a sibling `myorg_seed.py` declaring your scenarios
via the generic plant primitives (`DriftPlant`, `OverdraftPlant`,
`LimitBreachPlant`, `StuckPendingPlant`, `StuckUnbundledPlant`,
`SupersessionPlant`); register it as the L2 instance in your config,
then run the chain to PASS-gate your customization before touching
the dashboard.

For the full L1 invariant inventory (what each `{{ l2_instance_name }}_*` view
returns + its SHOULD-constraint motivation), see
[L1 Invariants](../L1_Invariants.md).

## Reference

- [Schema v6 — Data Feed Contract](../Schema_v6.md) — the column
  contract for the two base tables. Read this before mapping
  your source system.
- [L1 Invariants](../L1_Invariants.md) — what each `{{ l2_instance_name }}_*`
  view returns and what it asserts. The L1-fed dashboard reads
  these directly.
- [L1 Reconciliation Dashboard](l1.md) — the L2-fed dashboard's
  analyst view.
- [Data Integration Handbook](etl.md) — the ETL-engineer view of
  the same surface. Useful when your customization spans both
  product wiring and the upstream feed.
