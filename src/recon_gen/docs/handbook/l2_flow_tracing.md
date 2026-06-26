# L2 Flow Tracing Dashboard

*Is my L2 declaration alive? Per-instance reconciliation between what
the YAML declares and what the runtime data actually does. Currently
rendered against **{{ vocab.institution.name }}** ({{ l2_instance_name }}).*

L2 Flow Tracing asks one question L1 never does: is my L2 declaration
alive? L1 asks whether the postings are internally consistent — drift,
overdraft, limit breach, aging. L2 Flow Tracing steps up one level.
Every Rail, Chain, TransferTemplate, BundlesActivity selector,
MetadataKey and LimitSchedule the L2 instance YAML declares should be
backed by some actual runtime activity. When it isn't, that's an L2
HYGIENE problem — the declaration drifted from reality, or reality
drifted from the declaration — and it surfaces nowhere on the L1
dashboard.

See it live: https://recon-gen-spec.hotchkiss.io/

## Dataflow — which datasets feed which sheets

{{ diagram("dataflow", app="l2_flow_tracing") }}

## L2 chain DAG for this institution

{{ diagram("l2_topology", kind="chains") }}

## What the L2 Flow Tracing dashboard reconciles

Four interactive sheets, each a different lens on the
declaration-vs-runtime gap.

<div class="snb-card-grid">
  <div class="snb-card">
    <h3>Rails</h3>
    <p>Postings ledger filtered by date range, rail, status, bundle status and a cascading metadata-key + value pair. The "are any rails dead?" surface — pick a rail, see if it fired.</p>
  </div>
  <div class="snb-card">
    <h3>Chains</h3>
    <p>One row per declared parent-firing transfer. <code>completion_status</code> reads <code>Completed</code> when every Required child fired against the parent's <code>transfer_id</code> AND every XOR group fired exactly one member, <code>Incomplete</code> otherwise (a Required child missing, or an XOR group with zero or more than one firing). The old <code>No Required Children</code> state is gone — the L2 validator rejects all-optional chains at load, so the SQL never produces it.</p>
  </div>
  <div class="snb-card">
    <h3>Transfer Templates</h3>
    <p>Sankey of multi-leg flow per declared TransferTemplate: debit legs flow into the template node, credit legs flow out to destination accounts. Per-instance balance table beside the Sankey reads <code>Balanced</code> / <code>Imbalanced</code> against the template's <code>ExpectedNet</code>.</p>
  </div>
  <div class="snb-card">
    <h3>L2 Exceptions</h3>
    <p>All six L2 hygiene checks unified into one row-per-violation view. KPI = total open violations; bar chart breaks down by <code>check_type</code>; the detail table sorts by count (descending) and right-clicks drill back to the offending Rail or Chain row.</p>
  </div>
</div>

A separate **Getting Started** sheet anchors the dashboard with the L2
instance's top-level description prose.

## The six L2 hygiene checks

Every row in the L2 Exceptions sheet's detail table carries a
`check_type` discriminator. Six values, each a different
"declaration vs runtime" mismatch the L1 dashboard doesn't catch:

| `check_type` | What it catches |
|---|---|
| **Chain Orphans** | A chain edge whose parent fired more times than its expected child(ren). Per-edge `orphan_count` = parent firings minus matched-child firings (clamped at zero). For a singleton-children chain the expected child is unique; for a multi-children (XOR) chain any of the listed children counts as a match. |
| **Unmatched Transfer Type** | A `transactions` row whose `rail_name` doesn't match any declared `Rail.rail_name`. Catches new feeds emitting types the L2 doesn't yet know about. |
| **Dead Rails** | A declared `Rail` with zero `current_transactions` postings in the entire data window. Either the rail is genuinely unused (→ delete the declaration) or ETL stopped feeding it. |
| **Dead Bundles Activity** | An aggregating Rail's `BundlesActivity` selector that never matches any actual rail name or rail_name in the data. The bundler silently bundles nothing. |
| **Dead Metadata Declarations** | A declared `Rail.metadata_keys` field name that no leg of that rail ever carries a non-NULL value for. Either the field was mis-declared or ETL is dropping it. |
| **Dead Limit Schedules** | A declared `LimitSchedule` whose `(parent_role, rail_name)` combination has no matching rail+account-role pair in the data — the cap can never bind. |

Healthy = empty across all six. The bar chart's job is to surface "which
check_type kind dominates today" so analysts know whether they're
chasing a single broken feed (one tall bar) or systemic declaration
rot (many short bars).

## The analyst journey

The dashboard is structured for two workflows:

**Hygiene sweep** — open **L2 Exceptions** first. The KPI answers "did
anything new go dead overnight?" The bar chart shows the dominant
`check_type` immediately. From any row in the detail table:

- **Right-click → "View in Rails"** → opens the Rails sheet pre-filtered
  to the offending rail (so you see what activity exists, or doesn't).
- **Right-click → "View in Chains"** → opens the Chains sheet pre-filtered
  to the parent chain (so you see which firings landed and which
  orphaned).

**Per-instance walk** — open the right sheet for the question:

- *Did this rail fire today?* → **Rails** sheet, filter by rail name +
  date range. The metadata cascade lets you narrow further by any
  declared `Rail.metadata_keys` field — pick a Key, the Value dropdown
  populates with the distinct values currently in the data, pick one
  or more Values to slice the table.
- *Which chain firings closed?* → **Chains** sheet. One row per parent
  firing; `completion_status` tells you `Completed` or `Incomplete` at
  a glance. Same metadata cascade is wired here.
- *Did this multi-leg template balance?* → **Transfer Templates** sheet.
  The Sankey shows the flow shape per declared template; the table
  beside it shows `Balanced` / `Imbalanced` per shared Transfer (the
  L1 ExpectedNet invariant, projected onto the template's grouping).

## The L2-instance contract

Like the L1 dashboard, everything the L2 Flow Tracing dashboard knows
about your institution comes from the L2 instance YAML — the same
YAML the L1 dashboard reads. The instance declares:

- Accounts + their roles, scopes, parents
- Account templates (role classes that materialize at runtime)
- Rails (one-leg, two-leg, aggregating; with metadata_keys, posting
  requirements, aging caps)
- Transfer templates (multi-leg shared transfers with TransferKey
  grouping + ExpectedNet closure)
- Chains (parent → child relationships, XOR groups)
- LimitSchedules (per-(parent_role × rail_name) daily caps)

The same `common.l2.emit_schema(instance)` that powers L1 also powers
L2 Flow Tracing — the per-instance prefixed DDL (emitted per dialect:
PostgreSQL, Oracle or DuckDB) produces the
`{{ l2_instance_name }}_current_transactions` matview every L2 Flow Tracing
dataset reads.

Switching the L2 instance switches the dashboard. The same
dashboard renders against any L2 instance without code changes. A 5-rail shop and a 50-rail
shop get the same five sheets (Getting Started plus the four lenses)
and the same six check_types; the data populates per-instance.

## Cross-app integration

L1 and L2 Flow Tracing are sibling dashboards over one L2 instance,
not layered ones. They share the same `{{ l2_instance_name }}_*` matviews on the
same database schema and produce
their dashboard IDs as `<deployment_name>-l1-dashboard` and
`<deployment_name>-l2-flow-tracing` respectively — `cfg.aws.deployment_name`
namespaces both apps under one stable prefix, so an integrator deploying
both apps against the same L2 instance (the typical case) gets clean,
non-colliding QS resource IDs.

The natural workflow: **L2 Flow Tracing first to confirm the L2
declaration is alive**; **L1 second to confirm the postings are
internally consistent**. An integrator standing up a new L2 should
expect L2 Exceptions to fire freely on day one (most rails are
"dead" until the first ETL load) and quiet down as data fills in.
L1 violations are the second-order signal — they only matter once
the declaration itself is healthy.

## Generation + deployment

```bash
# Generate JSON for all four bundled apps to run/out/
recon-gen json apply -c run/config.yaml -o run/out

# Target a specific L2 YAML (substitute your own path)
recon-gen json apply \
  --l2 tests/l2/<your-l2-instance>.yaml \
  -c run/config.yaml -o run/out

# Same emit, then deploy to AWS (delete-then-create)
recon-gen json apply -c run/config.yaml -o run/out --execute
```

The L2 instance defaults to the canonical `spec_example` fixture.
Use `--l2 PATH` to target any other YAML; per-instance prefix
isolation means multiple L2 instances can deploy into the same
QuickSight account without colliding.
