# How do I add a new Rail or account_role?

*Customization walkthrough — Developer / Product Owner. Reskinning + extending.*

## The story

Your bank does a kind of money movement the demo doesn't model
— say `RepoSettlement` for repurchase agreements,
`MortgageServicingPassthrough` for mortgage passthrough or
`CorrespondentSettlement` for nostro/vostro flows. You want it on
the dashboards: filterable, groupable, drill-able, the whole
experience the existing rails get out of the box.

The rail's `name` IS the type identifier — the symmetric grammar
collapse dropped the separate `transfer_type` field. To add a new
movement type you add a new Rail in your L2 instance YAML and
reference it from the appropriate Templates / Chains /
LimitSchedules. The `{{ l2_instance_name }}_transactions.rail_name`
column is the single binding between a posted leg and its
declaring Rail.

## The question

"My bank's data has a movement type the demo doesn't model.
What's the minimum I need to change to surface it as a
first-class value on the dashboards?"

## Where to look

Three reference points:

- **Your L2 instance YAML** — the `rails:`, `transfer_templates:`
  and `chains:` blocks declare every movement type your
  institution participates in. The L2 Flow Tracing dashboard
  renders these declarations directly, and
  `common/l2/schema.py::emit_schema` inlines them into the
  prefixed L1 invariant views (limit caps, aging windows, etc.).
- **[Schema_v6.md → `{{ l2_instance_name }}_transactions`](../../Schema_v6.md#table-1-prefix_transactions)** —
  the `account_role` column contract: `VARCHAR(100) NOT NULL`,
  FREE-FORM ("the L2 role this account materializes"). There is NO
  canonical account_role enum — each L2 declares its own roles on its
  `accounts:` + `account_templates:` (live-inspect them, see below).
  New values are convention-only, not enforced by any CHECK constraint.
- **`common/l2/schema.py`** — the source of truth for the
  prefixed DDL. Read this to see how the L2 vocabulary becomes
  inline CASE branches in the L1 invariant views.

## What you'll see in the demo

The demo's rail set is whatever the active L2 instance declares.
Inspect via Python:

```python
from recon_gen.common.l2.loader import load_instance

l2 = load_instance("tests/l2/{{ l2_instance_name }}.yaml")
print(sorted(str(r.name) for r in l2.rails))
```

to see the rail names your L2 instance declares. The L1
Transactions sheet's Rail filter (and every rail-scoped exception
check) reads the `rail_name` column directly — no separate enum
file in code, no per-value visual config. New rail names surface
the moment they appear in `{{ l2_instance_name }}_transactions`.

The `account_role` column is unconstrained at the schema level:

```sql
account_role VARCHAR(100) NOT NULL,
```

The `account_role` values are whatever the active L2 declares — no
canonical list, no CHECK constraint. Inspect the real set the same way
you inspect rails:

```python
l2 = load_instance("tests/l2/{{ l2_instance_name }}.yaml")
print(sorted({str(a.role) for a in l2.accounts}
             | {str(t.role) for t in l2.account_templates}))
```

Adding a new account_role is zero-DDL.

[See it live](https://recon-gen-spec.hotchkiss.io/)

## What it means

The "extend" surface depends on which column you're touching:

### Adding a new Rail

Every new movement type is a new Rail — there's no value-only
path. The Rail must exist in the L2 declaration so the L1
invariant views know what to do with its rows.

1. **Update your L2 instance YAML.** Add the new `Rail` (single-
   or two-leg). If it carries an outbound cap, add a
   `LimitSchedule` referencing it by name. If it participates in
   a parent → child chain, declare or extend the relevant
   `Chain`. If a `TransferTemplate` owns it as a leg, list it in
   `leg_rails`.
2. **Re-emit the schema.** `recon-gen schema apply -c run/config.yaml --execute && recon-gen data apply -c run/config.yaml --execute && recon-gen data refresh -c run/config.yaml --execute`
   regenerates the prefixed L1 invariant views (limit-breach
   caps, pending/unbundled aging caps) with your new rail
   inlined.
3. **Wire your ETL to write the new value.** Whatever upstream
   feed produces the new movement type now writes
   `rail_name = 'RepoSettlement'` (or whatever you named it).
4. **Run the L2 Flow Tracing dashboard.** It surfaces every
   declared rail, transfer template, chain and bundle activity.
   Your new rail should appear; if it doesn't, the L2 declaration
   has a hygiene issue (caught by the L2 Hygiene Exceptions sheet).

### Adding a new `account_role` value

Two steps, no L2 or schema change:

1. **Pick the value.** No doc to update — account_role is free-form
   `VARCHAR(100)` declared per-L2 (there is no canonical registry to
   append to). Keep it consistent with your L2's existing roles
   (live-inspect the set as shown above).
2. **Wire your ETL to write the new value.** Whatever feed
   creates the new account role writes `account_role =
   'broker_dealer'` (or whatever you named it). The dashboards
   surface it automatically.

## Drilling in

A few patterns to know once the basic addition works:

### Filter dropdowns auto-populate from data

The dashboard's multi-select filter doesn't enumerate values in
its config — it reads them from the dataset's column at query
time. The wiring in `apps/l1_dashboard/app.py` references the
`rail_name` column directly; add a new value, dashboard renders
it. Drop a value, the dropdown stops showing it. No deploy step
required after the ETL writes the new value.

### Why no new tables

The four shipped apps share the same two prefixed base tables. A
new rail is a new VALUE in the existing
`{{ l2_instance_name }}_transactions.rail_name` column — not a
new table, dataset or sheet. This is the one decision the whole
schema rests on: denormalization-by-default keeps the surface
small enough that "add a movement type" is a value-write, not a
schema migration.

When you're tempted to add a per-rail table
(`repo_transactions`, `mortgage_servicing_transactions`), push
back. The pattern is to encode the rail in `rail_name` and put
per-rail extras in `metadata`.

### Existing exception checks may or may not apply to your new rail

The L1 invariant views (`{{ l2_instance_name }}_drift`,
`{{ l2_instance_name }}_overdraft`,
`{{ l2_instance_name }}_limit_breach`,
`{{ l2_instance_name }}_stuck_pending`,
`{{ l2_instance_name }}_stuck_unbundled`,
`{{ l2_instance_name }}_expected_eod_balance_breach`) read from
`{{ l2_instance_name }}_transactions` and
`{{ l2_instance_name }}_daily_balances` without filtering on
`rail_name` for most account-level checks — they apply to EVERY
posted leg that lands in the affected account. So your new
`rail_name = 'RepoSettlement'` rows will participate in every
account-level check:

- **Drift (`{{ l2_instance_name }}_drift`,
  `{{ l2_instance_name }}_ledger_drift`)** — apply universally. A
  repo leg that doesn't net to zero with its counter-leg surfaces
  here, just like an ACH leg.
- **Overdraft (`{{ l2_instance_name }}_overdraft`)** — applies
  universally. A repo that drives a sub-ledger negative surfaces
  here.
- **Rail-scoped checks (limit breach, aging windows)** — read
  caps and ages declared per-`Rail` in the L2 instance. Won't
  fire on your new rail unless the L2 declares the relevant cap
  or aging field on it.

The decision per check: does the SEMANTIC intent of the check
apply to your new rail? If yes, ensure the L2 declares the
relevant cap / aging window; if no, the bare-Rail declaration
without those fields is enough.

### Single-leg vs multi-leg rails

Each `Rail` is declared as either a `TwoLegRail` (debit + credit
on different roles, sums to zero per firing) or a `SingleLegRail`
(one-sided posting, reconciled by an aggregating rail or a
TransferTemplate). The L1 net-zero invariant excludes
single-leg rails by construction (the validator's S3 rule
guarantees they have a reconciliation path).

If your new rail is single-leg, the L2 validator will require
either:

- The rail appears in some `TransferTemplate.leg_rails`, OR
- The rail's name appears in some aggregating Rail's
  `bundles_activity` list.

Otherwise the validator rejects the L2 with an S3 error.

## Next step

Once your new rail is wired:

1. **Run pytest.** The contract tests
   (`tests/json/test_dataset_contract.py`) don't enumerate rail
   names, so they'll pass without changes. But if you extended a
   rail-scoped exception check's WHERE clause via an L2 update,
   the contract test for THAT dataset will catch any
   column-shape drift.
2. **Seed a few demo rows for the new rail.** Add a generator
   branch in your L2 instance's auto-scenario module that emits
   a handful of `rail_name = 'RepoSettlement'` rows. The
   `TestScenarioCoverage` pattern in the demo-data tests
   (see CLAUDE.md "Demo Data Conventions") makes this a
   one-line assertion: ≥N rows of the new rail. Without
   demo coverage, the dashboard "works" but the new value's
   visual treatment never gets exercised in the e2e tests.
3. **Re-seed and serve.** Chain `recon-gen schema apply -c
   config.yaml --execute && recon-gen data apply -c
   config.yaml --execute && recon-gen data refresh -c
   config.yaml --execute` to rewrite seed data, then serve
   it with `recon-gen dashboards -c config.yaml`. The
   dashboards direct-query the DB, so the new rail appears in
   the Rail filter dropdown on the first dashboard refresh — no
   separate push step.

## Related walkthroughs

- [How do I add an app-specific metadata key?](how-do-i-add-a-metadata-key.md) —
  paired pattern. New rails almost always carry per-rail
  metadata keys; the metadata-key walkthrough covers the read
  pattern.
- [How do I swap the SQL behind a dataset?](how-do-i-swap-dataset-sql.md) —
  for when you need to extend a rail-scoped exception check
  to fire on your new value via a dataset SQL change rather
  than (or in addition to) an L2 update.
- [Schema_v6 → `{{ l2_instance_name }}_transactions`](../../Schema_v6.md#table-1-prefix_transactions) —
  the `account_role` column contract (free-form `VARCHAR(100)`, no
  canonical enum). Roles are declared per-L2, not registered here.
