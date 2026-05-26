# Data Integration Handbook

*Upstream-to-two-tables mapping and validation for the Data
Integration Team. Currently rendered against
**{{ vocab.institution.name }}** ({{ l2_instance_name }}).*

This handbook backs the **feeds behind every dashboard** — the
upstream ETL that populates `transactions` and `daily_balances` at
{{ vocab.institution.name }}. Each entry here is a task the Data
Integration Team actually does: map a source table, prove the feed
is sound, tag a forced Fed posting, extend the metadata contract,
or debug a load that made it to the tables but not to the
dashboards.

## The team's job

The Data Integration Team owns the projection from {{ vocab.institution.acronym }}'s
upstream systems (core banking, Fed statements, processor reports,
sweep engines) into the two base tables every shipped dashboard
reads. Their attitude, in their own words:

> *What do I have a database server that can do fancy queries for
> unless I use it?*

That attitude drives the whole schema. The contract is
deliberately small — two tables, ~11 mandatory columns,
JSON-string metadata for per-`rail_name` extras — so the
team can spend their effort on projection correctness instead of
wrangling a sprawling normalized schema. The fancy queries
(drift, rollups, transfer net-zero) live in computed views the
database runs on demand.

## The contract

Two tables feed everything:

- **`transactions`** — one row per money-movement leg. 11 mandatory
  columns + conditional extras + a `metadata` JSON column.
- **`daily_balances`** — one row per `(account_id, balance_date)`.
  Stored EOD balance + a `metadata` JSON column for per-day
  configuration (limit-schedule payloads live here).

Every shipped dashboard (L1, L2 Flow Tracing, Investigation,
Executives) reads from these two tables. `account_type` and
`rail_name` discriminate which slice each app cares about;
the schema itself is shared. Full column contract, per-column
failure modes, metadata catalog, and ETL examples:

- [Schema v6 — Data Feed Contract](../Schema_v6.md) — the
  source-of-truth document. Read the *Getting Started for Data
  Teams* preamble first.

### Optional: `expected_complete_at` (lateness)

`transactions` carries an optional `expected_complete_at TIMESTAMP`
column. Populate it when your ETL knows the rail's settlement
window — instant rails (Fed wire, on-us internal) same-day, ACH
T+2, cards T+3. When NULL, downstream views fall back to
`posted_at + INTERVAL '1 day'` via COALESCE, so omitting the
column is safe.

Why bother populating it? The `is_late` predicate that the
L1 Exceptions sheets project fires off the same COALESCE
expression. A populated `expected_complete_at` gives the analyst a
per-rail-accurate deadline; an unpopulated one falls back to the
conservative one-day default (which over-fires, surfacing things
that aren't really late yet, rather than hiding overdue rows).
Adopt incrementally: pick the rail your team gets the most
"is this really late or just slow?" questions about, populate that
one first, leave the rest NULL.

For multi-leg transfers, downstream views collapse to the
**earliest debit leg's** `expected_complete_at` as the
transfer-level deadline. You don't need to denormalize this across
all legs of a transfer — just populate the leg(s) you have rail
data for, and the views work the join.

See [Lateness as data](../Schema_v6.md#lateness-as-data) for the
default formula, the `is_late` predicate SQL, and the
multi-leg tie-breaker query.

Several materialized views sit on top of these tables — the L1
invariant matviews (`{{ l2_instance_name }}_drift`, `{{ l2_instance_name }}_overdraft`,
`{{ l2_instance_name }}_limit_breach`, `{{ l2_instance_name }}_stuck_pending`,
`{{ l2_instance_name }}_stuck_unbundled`, `{{ l2_instance_name }}_todays_exceptions`) plus
the Investigation cluster (`{{ l2_instance_name }}_inv_pair_rolling_anomalies`
feeds Volume Anomalies; `{{ l2_instance_name }}_inv_money_trail_edges` feeds
Money Trail and Account Network — recursive walk over
`parent_transfer_id`). None are auto-refreshed: every ETL load
must run `REFRESH MATERIALIZED VIEW` on each, or the operator-
facing aging / anomaly / chain columns will lag. The dependency-
ordered statements come from
`common/l2/schema.refresh_matviews_sql(l2_instance)`. See
[Materialized views](../Schema_v6.md#the-layered-model) for the
full refresh contract.

## Foundational walkthroughs

<p class="snb-section-label">Start here — populate and validate the feed</p>

<div class="snb-card-grid">
  <a class="snb-card" href="../../walkthroughs/etl/how-do-i-populate-transactions/">
    <h3>How do I populate <code>transactions</code> from my core banking system?</h3>
    <p>Canonical projection from a hypothetical `gl_postings` source table into the two-table target. The first walkthrough a new team member reads.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/etl/how-do-i-populate-daily-balances/">
    <h3>How do I populate <code>daily_balances</code> from my core banking system?</h3>
    <p>The companion feed to transactions — stored end-of-day balances, integer cents, day-boundary alignment. Drift checks need both.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/etl/how-do-i-prove-my-etl-is-working/">
    <h3>How do I prove my ETL is working before going live?</h3>
    <p>Three pre-flight invariants (net-to-zero, balance recompute, orphan chains) with copy-paste SQL. Run these before the dashboard sees the data.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/etl/how-do-i-validate-a-single-account-day/">
    <h3>How do I validate a single account-day after a load?</h3>
    <p>Open the Daily Statement sheet on a specific `(account_id, balance_date)` to confirm opening, debits, credits, closing, and zero drift — the per-row companion to the universal pre-flight invariants.</p>
  </a>
</div>

## Extension walkthroughs

<p class="snb-section-label">When the feed shape changes — new source, new key</p>

<div class="snb-card-grid">
  <a class="snb-card" href="../../walkthroughs/etl/how-do-i-tag-a-force-posted-transfer/">
    <h3>How do I tag a force-posted external transfer correctly?</h3>
    <p>The `origin` column + `parent_transfer_id` chain mechanics for Fed-statement ingest. Why force-posted matters for L1 exception classification.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/etl/how-do-i-add-a-metadata-key/">
    <h3>How do I add a metadata key without breaking the dashboards?</h3>
    <p>Extension contract: portable scalar types, `JSON_OBJECT` writes / `JSON_VALUE` reads, Schema_v6 catalog update. Walks an `originating_branch` addition end-to-end.</p>
  </a>
</div>

## Debug walkthroughs

<p class="snb-section-label">When the feed looks right but the dashboard doesn't</p>

<div class="snb-card-grid">
  <a class="snb-card" href="../../walkthroughs/etl/what-do-i-do-when-demo-passes-but-prod-fails/">
    <h3>What do I do when the demo passes but my prod data fails?</h3>
    <p>Six symptom-organized debug recipes (date filter, rail_name filter, missing metadata key, drift KPI spike, broken parent chain, status enum drift).</p>
  </a>
</div>

## The exemplary helper

`recon-gen data etl-example` emits canonical INSERT patterns
the team can copy from when building a new ETL job:

```bash
recon-gen data etl-example -o etl-examples.sql
```

The current pattern set covers every base-table shape Investigation
walks; the L1 + L2 Flow Tracing apps read the same base tables and
need no app-specific patterns at this writing.

Every block carries a `-- WHY:` header that names the business
invariant the pattern protects, and a `-- Consumed by:` header that
names the dashboard view that reads the resulting rows. Strip the
`-EXAMPLE` sentinel suffix and wire the column projections to your
upstream feed's source fields.

## Troubleshooting

### Symptom: dashboards show data older than expected

A sheet is rendering, but the most recent rows look stale —
yesterday's postings are missing, the Drift KPI hasn't budged
since the last load, the Daily Statement walks stop a day or two
behind the date you just ETL'd. Your first instinct is to suspect
an ETL bug or a query bug. Before you go there, check whether the
matviews are simply behind.

### First diagnostic step: open the App Info sheet

Every shipped dashboard's last tab is **Info** (the App Info
canary). Open it. The right-hand visual is the **Matview Status**
table — one row per base table and per matview the dashboard
reads, with three columns:

- **`view_name`** — the table or matview name.
- **`row_count`** — total rows in that table / matview.
- **`latest_date`** — `MAX(<date_col>)` for that row, i.e. the
  most recent business day the table / matview knows about.

Compare the `latest_date` values:

- The two base tables — `{{ l2_instance_name }}_transactions` and
  `{{ l2_instance_name }}_daily_balances` — reflect how fresh the source data
  is. Their `latest_date` is the high-water mark your ETL has
  loaded.
- Every matview row below them should carry the **same**
  `latest_date` (or within one ETL cycle of it).
- If a matview's `latest_date` is significantly older than the
  base tables, that matview is stale relative to the loaded data.
  The dashboard sheets reading that matview will look like the
  load never happened.

### Root cause

PostgreSQL and Oracle materialized views do **not** auto-refresh.
They only update when explicitly told to via
`REFRESH MATERIALIZED VIEW`. An ETL load that writes new rows to
`{{ l2_instance_name }}_transactions` and `{{ l2_instance_name }}_daily_balances` but doesn't
follow up with the refresh statements leaves every matview pointed
at the *previous* load — the dashboards visualize a stale snapshot
and look unchanged.

### Fix

Run the refresh, then reload the dashboard:

- **Demo flow:** `recon-gen data refresh --execute`.
- **Production ETL:** include the output of
  `refresh_matviews_sql(l2_instance)` after every
  `transactions` / `daily_balances` write. The helper emits the
  dependency-ordered statements for the L2 instance you pass it;
  call it from your load orchestrator.

A browser reload is enough — every dataset is Direct Query, no
SPICE invalidation step.

### Related: the Liveness KPI is also blank

While you're on the **Info** sheet, glance at the **Liveness** KPI
on the left. It runs a real query against the database catalog. If
it shows a number, QuickSight's rendering pipeline is healthy and
any blank visual elsewhere on the dashboard is a data or SQL issue
(stale matviews, an empty filter, an unexpected `WHERE` narrowing).
If the Liveness KPI is **also** blank, QuickSight itself has
hung — every visual on every sheet stuck on the spinner with no
error banner. That's a separate failure mode; the fix is to wait
it out, open in a fresh incognito window, or force a full
delete-then-create of the QuickSight resource graph (theme,
datasource, datasets, analysis, dashboard) plus a clean re-seed
and matview refresh.

## Reference

- [Schema v6 — Data Feed Contract](../Schema_v6.md) — column specs,
  metadata keys, ETL examples. The source of truth this handbook
  points at.
- [Account Structure](../scenario/index.md) — the bank, customers,
  accounts, and money flows the populated data represents.
- [L1 Reconciliation Dashboard](l1.md) — the operator-facing
  dashboard your feeds serve.
- [Investigation](investigation.md) — the AML/compliance dashboard
  your feeds serve.
