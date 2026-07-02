# Executives Dashboard

*Executive scorecard for the L2-fed institution. Currently rendered
against **{{ vocab.institution.name }}** ({{ l2_instance_name }}).*

The Executives Dashboard is the board-cadence view of the same shared
base ledger the L1 Reconciliation, L2 Flow Tracing and Investigation
dashboards read. L1 surfaces invariant violations and Investigation
answers "where did this money come from?"; Executives answers "how is
the institution doing this month / quarter / year?" — account coverage,
transaction volume and money-moved totals, plus a one-tile
ledger-health tripwire.

## Dataflow — which datasets feed which sheets

{{ diagram("dataflow", app="executives") }}

## The sheets

### Getting Started

Landing page that summarises each tab so a first-time reader knows
where to look. No filters or visuals — a navigation index.

### Program Health

Single-tile read on whether the ledger is healthy right now. Counts
open L1 invariant violations inside the dashboard's 30-day window —
green at zero, amber on any violation, red at the 20-violation systemic
mark. Drill into the L1 Dashboard for per-row triage.

KPI: the threshold-banded violation count, sourced from
`<prefix>_l1_exceptions` and narrowed by the date picker. Below it, a
hyperlink to the L1 Dashboard.

### Account Coverage

How many accounts the institution has on its books and how many of them
actually moved money in the window, split by `account_type` (DDA, GL
control, external counterparty, etc.) so the deposit base sits next to
the GL control accounts that drive operations. Active = at least one
transaction during the date filter window. Useful for a CEO asking "how
many customers do we have?" or a COO asking "is the GL chart growing?"

KPIs: total open accounts + active accounts in the period. Bar charts:
open + active counts by account type. Detail table: per-account last
activity date and count.

### Transaction Volume Over Time

Transaction throughput over time, sliced by `rail_name` so you can see
which rails are growing or contracting. Sudden drops flag ETL outages;
sudden spikes are either real growth or duplicate-loading bugs.

KPIs: total transactions (posted, per-transfer), transfer legs (all
statuses) and average daily volume. Stacked bar: daily
transaction count coloured by `rail_name`. Clustered bar: period total
per `rail_name`.

### Money Moved

Dollar volume moving across the institution, by rail, over the window.
Net (signed sum — flows into the institution are positive) and gross
(sum of absolute `amount_money` values — total handle regardless of
direction) sit side by side; the per-rail bar shows where the volume is
coming from. Useful for a CFO asking "how much did we settle today?" or
a board member asking "what's our run-rate?"

KPIs: net money moved (Σ signed) + gross money moved (Σ |signed|).
Stacked bar: daily gross dollars coloured by `rail_name`. Clustered
bar: period total per `rail_name` (log-scale Y so the long tail stays
readable when one rail dominates).

### Info

Standard App Info canary — matview row counts + deploy stamp. See
the [App Info convention](../concepts/index.md) for the diagnostic
ladder when a sheet renders blank.

## Viewing the dashboard

```bash
# Serve all four bundled apps (Executives included)
recon-gen dashboards -c run/config.yaml

# Or full Studio — the dashboards plus the L2 editor + data-shaping panel
recon-gen studio -c run/config.yaml
```

Defaults to the bundled `{{ l2_instance_name }}` L2 fixture. To target a
different L2 instance, see the
[customization handbook](customization.md).

[See it live](https://recon-gen-spec.hotchkiss.io/)
