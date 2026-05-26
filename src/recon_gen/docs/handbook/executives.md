# Executives Dashboard

*Executive scorecard for the L2-fed institution. Currently rendered
against **{{ vocab.institution.name }}** ({{ l2_instance_name }}).*

The **Executives Dashboard** is the high-level view of the same shared
base ledger that the L1 Reconciliation, L2 Flow Tracing, and
Investigation dashboards read. Where L1 surfaces invariant violations
and Investigation answers "where did this money come from?", the
Executives dashboard answers **"how is the institution doing this
month / quarter / year?"** — coverage, volume, and money-moved totals.

## Dataflow — which datasets feed which sheets

{{ diagram("dataflow", app="executives") }}

## The four sheets

### Getting Started

Landing page that summarises each tab so a first-time reader knows
where to look. No filters, no visuals — just a navigation index.

### Account Coverage

How many accounts the institution actively serves, broken down by
`account_type` (DDA, GL control, external counterparty, etc.).
Active = at least one transaction during the date filter window.

KPI: total active accounts. Bar chart: per-account-type active vs
total. Useful for a CEO asking "how many customers do we have?" or a
COO asking "is the GL chart growing?"

### Transaction Volume Over Time

Aggregate transaction counts per day, optionally split by
`rail_name` or `origin`. Surfaces volume trends — sudden drops
indicate ETL outages; sudden spikes indicate either real growth or
duplicate-loading bugs.

Line chart: transactions per day. Stacked bar: transfer-type
contribution.

### Money Moved

Aggregate money-moved totals per day, broken down by `rail_name`
(ACH origination, wire settlement, internal transfer, etc.). Sums
absolute `amount_money` values across all postings in the window.

KPI: total money moved during window. Stacked bar: per-transfer-type
contribution. Useful for a CFO asking "how much did we settle today?"
or a board member asking "what's our run-rate?"

### Info

Standard App Info canary — matview row counts + deploy stamp. See
the [App Info convention](../concepts/index.md) for the diagnostic
ladder when a sheet renders blank.

## Generation + deployment

```bash
# Generate JSON for all four bundled apps to run/out/
recon-gen json apply -c run/config.yaml -o run/out

# Same emit, then deploy to AWS (delete-then-create)
recon-gen json apply -c run/config.yaml -o run/out --execute
```

Defaults to the bundled `{{ l2_instance_name }}` L2 fixture. To target a
different L2 instance, see the
[customization handbook](customization.md).
