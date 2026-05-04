# How do I extend the schema with a new transfer_type or account_type?

*Customization walkthrough — Developer / Product Owner. Reskinning + extending.*

## The story

Your bank does a kind of money movement the demo doesn't model
— say `repo` for repurchase agreements, `mortgage_servicing` for
mortgage payment passthrough, or `correspondent_settlement` for
nostro/vostro flows. You want it on the dashboards: filterable,
groupable, drill-able, the whole experience the existing
`transfer_type` values get out of the box.

The good news: `transfer_type` is a value in the data, not an enum
in the dashboard code. The Transfer Type dropdown filter on the L1
Transactions sheet auto-populates from the distinct values present
in the dataset. Add a row to `{{ l2_instance_name }}_transactions` with
`transfer_type = 'repo'` and the next dashboard load shows `repo`
as a filterable value with no dashboard code change.

The catch: the canonical `transfer_type` values your dashboards
treat semantically (which rails fire on which type, which chains
expect which parent type, which type is single-leg vs multi-leg)
are declared in your **L2 instance YAML**. Adding a new value
that participates in any of those flows means an L2 update — not
a schema migration in the historical sense, but a structural
change to the L2 declaration that drives the dashboards. This
walkthrough covers both the value-only case (the dashboards
auto-pick it up) and the L2 update case (you also extend the L2's
rails / templates / chains).

## The question

"My bank's data has a movement type the demo doesn't model.
What's the minimum I need to change to surface it as a
first-class value on the dashboards?"

## Where to look

Three reference points:

- **Your L2 instance YAML** — the `transfer_templates:`,
  `rails:`, and `chains:` blocks declare which `transfer_type`
  values your institution participates in and how they interact.
  The L2 Flow Tracing dashboard renders these declarations
  directly, and `common/l2/schema.py::emit_schema` inlines them
  into the prefixed L1 invariant views (limit caps, aging
  windows, etc.).
- **[Schema_v6.md → canonical account_type values](../../Schema_v6.md#table-1-prefix_transactions)** —
  the cataloged `account_type` set: `gl_control`, `dda`,
  `merchant_dda`, `external_counter`, `concentration_master`,
  `funds_pool`. New `account_type` values are convention-only,
  not enforced by any CHECK constraint.
- **`common/l2/schema.py`** — the source of truth for the
  prefixed DDL. Read this to see how the L2 vocabulary becomes
  inline CASE branches in the L1 invariant views.

## What you'll see in the demo

The demo's `transfer_type` set is whatever the active L2
instance declares. Inspect via Python:

```python
from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.schema import emit_schema

l2 = load_instance("tests/l2/{{ l2_instance_name }}.yaml")
ddl = emit_schema(l2)
print([line for line in ddl.splitlines() if "transfer_type" in line][:10])
```

to see the values your L2 instance accepts. The L1 Transactions
sheet's Transfer Type filter (and every type-scoped exception
check) reads the column directly — no separate enum file in code,
no per-value visual config. New values surface the moment they
appear in `{{ l2_instance_name }}_transactions`.

The `account_type` column is unconstrained at the schema level:

```sql
account_role VARCHAR(50) NOT NULL,
```

The canonical list (`gl_control`, `dda`, `merchant_dda`,
`external_counter`, `concentration_master`, `funds_pool`) is
documented in
[Schema_v6.md](../../Schema_v6.md#table-1-prefix_transactions)
but enforced only by convention. Adding a new account_type is
zero-DDL.

## What it means

The "extend" surface depends on which column you're touching:

### Adding a new `transfer_type` value

Two cases:

**Case A — value-only extension (no rails / chains needed).**
Your bank emits a new movement type whose rows you only want to
surface in raw views (L1 Transactions sheet, Investigation
queries). No type-scoped exception check applies semantically;
the new type doesn't fund or get funded by any other type.

1. **Wire your ETL to write the new value.** Whatever upstream
   feed produces the new movement type now writes
   `transfer_type = 'repo'` (or whatever you named it). The
   filter dropdown picks it up automatically; no L2 changes,
   no dashboard code changes.
2. **Decide the metadata-key payload.** Per-`transfer_type`
   metadata keys are documented in Schema_v6's catalog. Decide
   what goes in `metadata` for your new value and document it.

**Case B — full L2 extension (your new type participates in rails
or chains).** Your bank's new movement type has caps, aging
windows, parent-chain expectations, or sweep semantics that the
L1 invariant views need to know about.

1. **Update your L2 instance YAML.** Add a `TransferTemplate` for
   the new type; if it flows through a rail with caps or aging
   windows, declare or extend the relevant `Rail`; if it
   participates in a parent → child chain, declare or extend the
   relevant `Chain`.
2. **Re-emit the schema.** `quicksight-gen schema apply -c run/config.yaml --execute && quicksight-gen data apply -c run/config.yaml --execute && quicksight-gen data refresh -c run/config.yaml --execute`
   regenerates the prefixed L1 invariant views (limit-breach
   caps, pending/unbundled aging caps) with your new type
   inlined.
3. **Wire your ETL to write the new value.** Same as Case A.
4. **Run the L2 Flow Tracing dashboard.** It surfaces every
   declared transfer template, rail, chain, and bundle activity.
   Your new type should appear; if it doesn't, the L2 declaration
   has a hygiene issue (caught by the L2 Hygiene Exceptions sheet).

### Adding a new `account_type` value

One step (no L2 change, no schema change):

1. **Document the new value.** Update
   [Schema_v6.md → Canonical account_type values](../../Schema_v6.md#table-1-prefix_transactions)
   with the new role and what it means. The list is the
   convention; without it, future-you will guess.
2. **Wire your ETL to write the new value.** Whatever feed
   creates the new account role writes `account_type =
   'broker_dealer'` (or whatever you named it). The dashboards
   surface it automatically.

## Drilling in

A few patterns to know once the basic addition works:

### Filter dropdowns auto-populate from data

QuickSight's multi-select filter doesn't enumerate values in
its config — it reads them from the dataset's column at query
time. The wiring in `apps/l1_dashboard/app.py` looks like:

```python
return _multi_select_filter_group(
    fg_id="fg-l1-transfer-type",
    title="Transfer Type",
    column_name="transfer_type",        # ← column reference, no values
    sheet_ids=_TRANSFER_TYPE_SCOPED_SHEETS,
)
```

Add a new value, dashboard renders it. Drop a value, the
dropdown stops showing it. No deploy step required after the
ETL writes the new value.

### Why no new tables

The four shipped apps share the same two prefixed base tables. A
new `transfer_type` is a new *value* in the existing
`{{ l2_instance_name }}_transactions.transfer_type` column — not a new table,
not a new dataset, not a new sheet. This is the single
load-bearing decision behind the schema: denormalization-by-default
keeps the surface small enough that "add a movement type" is a
value-write, not a schema migration.

When you're tempted to add a per-type table (`repo_transactions`,
`mortgage_servicing_transactions`), push back. The pattern is
to encode the type in `transfer_type` and put per-type extras
in `metadata`.

### Existing exception checks may or may not apply to your new type

The L1 invariant views (`{{ l2_instance_name }}_drift`, `{{ l2_instance_name }}_overdraft`,
`{{ l2_instance_name }}_limit_breach`, `{{ l2_instance_name }}_stuck_pending`,
`{{ l2_instance_name }}_stuck_unbundled`,
`{{ l2_instance_name }}_expected_eod_balance_breach`) read from
`{{ l2_instance_name }}_transactions` and `{{ l2_instance_name }}_daily_balances` without
filtering on `transfer_type` for most account-level checks —
they apply to *every* transfer that lands in the affected
account. So your new `transfer_type = 'repo'` rows will
participate in every account-level check:

- **Drift (`{{ l2_instance_name }}_drift`, `{{ l2_instance_name }}_ledger_drift`)** — apply
  universally. A repo leg that doesn't net to zero with its
  counter-leg surfaces here, just like an ACH leg.
- **Overdraft (`{{ l2_instance_name }}_overdraft`)** — applies universally. A
  repo that drives a sub-ledger negative surfaces here.
- **Type-scoped checks (limit breach, aging windows)** — read
  caps and ages declared per-`Rail` in the L2 instance. Won't
  fire on your new type unless the L2 declares the relevant
  rail / cap. See "Case B" above.

The decision per check: does the *semantic intent* of the check
apply to your new type? If yes, ensure the L2 declares the
relevant rail; if no, the value-only path (Case A) is enough.

### Single-leg vs multi-leg transfers

The L2 instance declares each `TransferTemplate`'s leg shape.
Single-leg types (`sale`, `external_txn` in the demo) don't have
a counter-leg in `{{ l2_instance_name }}_transactions`; their counterparty
sits in an external system. Multi-leg types (`ach`, `wire`,
`internal`, etc.) have legs that net to zero.

The L1 net-zero check (Invariant 1 in
[How do I prove my ETL is working?](../etl/how-do-i-prove-my-etl-is-working.md))
already excludes single-leg types by name. If you add a new
single-leg type, you must extend the exclusion list in the
pre-flight query, or it'll false-positive on every row of the
new type.

## Next step

Once your new canonical value is wired:

1. **Run pytest.** The contract tests
   (`tests/test_dataset_contract.py`) don't enumerate
   `transfer_type` values, so they'll pass without changes.
   But if you extended a type-scoped exception check's WHERE
   clause via an L2 update, the contract test for *that* dataset
   will catch any column-shape drift.
2. **Seed a few demo rows for the new type.** Add a generator
   branch in your L2 instance's auto-scenario module that emits
   a handful of `transfer_type = 'repo'` rows. The
   `TestScenarioCoverage` pattern in the demo-data tests
   (see CLAUDE.md "Demo Data Conventions") makes this a
   one-line assertion: ≥N rows of the new type. Without
   demo coverage, the dashboard "works" but the new value's
   visual treatment never gets exercised in the e2e tests.
3. **Re-deploy.** Chain `quicksight-gen schema apply -c
   config.yaml --execute && quicksight-gen data apply -c
   config.yaml --execute && quicksight-gen data refresh -c
   config.yaml --execute` to rewrite seed data, then
   `quicksight-gen json apply -c config.yaml -o out/ --execute`
   to push the schema and dashboard changes. The new
   value appears in the Transfer Type filter dropdown on the
   first dashboard refresh.

## Related walkthroughs

- [How do I add an app-specific metadata key?](how-do-i-add-a-metadata-key.md) —
  paired pattern. New `transfer_type` values almost always
  carry per-type metadata keys; the metadata-key walkthrough
  covers the read pattern.
- [How do I swap the SQL behind a dataset?](how-do-i-swap-dataset-sql.md) —
  for when you need to extend a type-scoped exception check
  to fire on your new value via a dataset SQL change rather
  than (or in addition to) an L2 update.
- [Schema_v6 → Canonical account_type values](../../Schema_v6.md#table-1-prefix_transactions) —
  the documented convention for `account_type`. Update the
  table when you add a new role.
