# L1 Invariants

The L1 SPEC declares a small set of SHOULD-constraints that any
healthy ledger feed must satisfy. The L1 library
(`common.l2.emit_schema`) materializes each constraint as a
prefixed PostgreSQL view; rows in any of these views ARE the
constraint violations. Healthy = empty.

This page is the authoritative reference for what each
`{{ l2_instance_name }}_*` view returns, the SHOULD-constraint motivation, and
what the matview surfaces against the canonical
demo seed.

## How the views are layered

```
base tables
  ├── {{ l2_instance_name }}_transactions
  └── {{ l2_instance_name }}_daily_balances
                  ↓
Current* matviews (M.1.5 — max-Entry-per-logical-key projection)
  ├── {{ l2_instance_name }}_current_transactions
  └── {{ l2_instance_name }}_current_daily_balances
                  ↓
Helper matviews (computed-balance derivation)
  ├── {{ l2_instance_name }}_computed_subledger_balance
  └── {{ l2_instance_name }}_computed_ledger_balance
                  ↓
L1 invariant matviews (the SHOULD-constraint surfaces)
  ├── {{ l2_instance_name }}_drift
  ├── {{ l2_instance_name }}_ledger_drift
  ├── {{ l2_instance_name }}_overdraft
  ├── {{ l2_instance_name }}_expected_eod_balance_breach
  ├── {{ l2_instance_name }}_limit_breach
  ├── {{ l2_instance_name }}_stuck_pending          (M.2b.8)
  └── {{ l2_instance_name }}_stuck_unbundled        (M.2b.9)
                  ↓
Dashboard-shape matviews (UI convenience)
  ├── {{ l2_instance_name }}_daily_statement_summary
  └── {{ l2_instance_name }}_todays_exceptions      (UNION over the 5 baseline L1s)
```

13 matviews total. Refresh contract: every batch insert into the
base tables MUST be followed by `refresh_matviews_sql(instance)`
to recompute every dependent matview in dependency order. The
refresh is deterministic — leaves first, helpers second, L1
invariants third, dashboard-shape last.

## The seven L1 SHOULD-constraints

### 1. `{{ l2_instance_name }}_drift` — Sub-ledger drift

> For every CurrentStoredBalance where `Account.Scope = Internal`
> and `¬IsParent(Account)`,
> `Drift(Account, BusinessDay)` SHOULD equal 0.

Each leaf-account day where the stored balance disagrees with the
cumulative net of every Posted Money record posted to that account
through the BusinessDay's end. The disagreement is the *drift*; a
non-zero value signals the feed diverged from the underlying
ledger.

**Columns:** `account_id`, `account_name`, `account_role`,
`account_parent_role`, `business_day_start`, `business_day_end`,
`stored_balance`, `computed_balance`, `drift`.

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** `bigfoot-brews +$75` planted at
`days_ago=5` surfaces with `drift=75.00`.
{% endif %}

### 2. `{{ l2_instance_name }}_ledger_drift` — Parent-account roll-up drift

> For every CurrentStoredBalance where `Account.Scope = Internal`
> and `IsParent(Account)`,
> `LedgerDrift(Account, BusinessDay)` SHOULD equal 0.

Each parent-account day where the stored balance disagrees with
the sum of its child accounts' stored balances. Surfaces a child
posting that didn't roll up correctly to its parent.

**Columns:** same as `_drift` minus `account_parent_role`
(parents ARE the parents).

### 3. `{{ l2_instance_name }}_overdraft` — Non-negative balance

> For every CurrentStoredBalance,
> `money` SHOULD be ≥ 0.

Each internal account day where the stored balance is negative.
External counterparties are excluded by construction (the
asymmetry is intentional: banks may legitimately overdraft *us*;
we MUST NOT overdraft *them*).

**Columns:** `account_id`, `account_name`, `account_role`,
`account_parent_role`, `business_day_start`, `business_day_end`,
`stored_balance`.

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** `sasquatch-sips -$1500` planted at
`days_ago=6` surfaces with `stored_balance=-1500.00`.
{% endif %}

### 4. `{{ l2_instance_name }}_expected_eod_balance_breach` — Expected EOD

> For every CurrentStoredBalance where `expected_eod_balance` is
> set, `money` SHOULD equal `expected_eod_balance`.

Each account day where the stored EOD balance differs from the
L2-declared expected EOD balance. Surfaces accounts that didn't
clean up to their expected zero / target by end-of-day.

**Columns:** `account_id`, `account_name`, `account_role`,
`business_day_start`, `business_day_end`, `stored_balance`,
`expected_eod_balance`, `variance`.

### 5. `{{ l2_instance_name }}_limit_breach` — Outbound flow cap

> For every CurrentStoredBalance where `Limits` is set, for every
> `(Rail, limit)` in `Limits`, for every child Account whose
> `Parent = this account`,
> `OutboundFlow(child, rail, businessDay)` SHOULD be ≤ `limit`.

Per-`(account, day, rail_name)` cells where cumulative outbound
debit exceeded the cap. Caps come from the L2 instance's
LimitSchedules and are inlined into the view as CASE branches at
schema-emit time. (Z.B 2026-05-15: keyed on `rail_name` now —
previously `transfer_type` — under the symmetric grammar collapse.)

**Columns:** `account_id`, `account_name`, `account_role`,
`account_parent_role`, `business_day`, `rail_name`,
`outbound_total`, `cap`.

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** `big-meadow-dairy $22k wire`
planted at `days_ago=4` surfaces with `outbound_total > cap` for
`rail_name='CustomerOutboundWire'`.
{% endif %}

### 6. `{{ l2_instance_name }}_stuck_pending` — Per-rail pending aging (M.2b.8)

> For every Rail with `max_pending_age` set, every Transaction
> on that rail SHOULD transition `Pending → Posted` before
> `posting + max_pending_age`.

Transactions stuck in `status='Pending'` past their rail's
configured cap. Caps come from the per-Rail `max_pending_age`
field and are inlined as CASE branches keyed on `rail_name`. Rails
without an aging watch contribute no branch and are excluded.

**Columns:** `transaction_id`, `account_id`, `account_name`,
`account_role`, `account_parent_role`, `transfer_id`,
`rail_name`, `amount_money`, `amount_direction`,
`posting`, `max_pending_age_seconds`, `age_seconds`.

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** `bigfoot-brews ACH at days_ago=2`
(172800s) surfaces with `age_seconds > max_pending_age_seconds`
(86400s for the `CustomerInboundACH` rail's PT24H cap).
{% endif %}

### 7. `{{ l2_instance_name }}_stuck_unbundled` — Per-rail unbundled aging (M.2b.9)

> For every Rail with `max_unbundled_age` set, every Posted leg
> on that rail SHOULD be picked up by an AggregatingRail
> (`bundle_id` set) before `posting + max_unbundled_age`.

Posted transactions whose `bundle_id IS NULL` past their rail's
cap. Per validator R8, `max_unbundled_age` is only meaningful on
rails appearing in some AggregatingRail's `bundles_activity`.

**Columns:** same shape as `_stuck_pending` with
`max_unbundled_age_seconds` instead of `max_pending_age_seconds`.

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** `sasquatch-sips fee accrual at
days_ago=35` surfaces with `age_seconds > max_unbundled_age_seconds`
(2,678,400s for the `CustomerFeeAccrual` rail's P31D cap).
{% endif %}

## Diagnostic surface — Supersession Audit

`{{ l2_instance_name }}_supersession_*` is **not** a SHOULD-constraint — it's a
diagnostic view that surfaces logical keys with multiple `entry`
versions (the audit trail for `TechnicalCorrection` /
`BundleAssignment` / `Inflight` rewrites). Reads from BASE tables
(not Current*) since Current* hides superseded entries by
construction. See M.2b.12 dashboard for the visualization.

{% if vocab.fixture_name == "sasquatch_pr" %}
the matview should surface a planted TechnicalCorrection on
`bigfoot-brews` (2 entries on the same logical id at `days_ago=3`)
surfaces with `entry_count > 1`.
{% endif %}

## Refresh + extend contracts

- **Refresh:** `refresh_matviews_sql(instance)` returns a single
  SQL string with 26 statements (13 REFRESH + 13 ANALYZE) in
  dependency order. Caller splits on `;` and executes
  per-statement (psycopg2's `cursor.execute` can't run multiple
  statements separated by `;` reliably).
- **Per-instance hot-path indexes:** every L1 invariant matview
  ships indexes on the dashboard's filter dropdowns
  (`account_id`, `rail_name`, `business_day`, etc) so the deployed
  dashboard's per-visual SELECTs hit indexed lookups.
- **Adding a new SHOULD-constraint:** declare the underlying L1
  primitive in the SPEC, add the matview to
  `common.l2.schema._L1_INVARIANT_VIEWS_TEMPLATE`, register it in
  `_L1_INVARIANT_VIEWS_DROPS_TEMPLATE` + `refresh_matviews_sql`,
  and write a `_render_<name>_cases` helper if it needs L2 data
  inlined at schema-emit time (mirror of
  `_render_pending_age_cases`). Then surface it via a new dataset
  + sheet on the L1 dashboard.

## See also

- [Schema v6 — Data Feed Contract](Schema_v6.md) — the column
  contract for the two base tables.
- [L1 Reconciliation Dashboard](handbook/l1.md) — the analyst's
  view of these invariants.
- [Customization Handbook — L2-fed pattern](handbook/customization.md#the-l2-fed-pattern-m2b)
  — how to point the dashboard at your own L2 instance.
