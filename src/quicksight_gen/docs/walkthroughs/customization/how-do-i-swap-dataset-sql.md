# How do I swap the SQL behind a dataset without breaking the visuals?

*Customization walkthrough — Developer / Product Owner. Reskinning + extending.*

## The story

Your data lands in `{{ l2_instance_name }}_transactions` and
`{{ l2_instance_name }}_daily_balances` per
[How do I map my production database?](how-do-i-map-my-database.md).
Most of the 32+ datasets work out of the box — they read directly
from the two prefixed base tables (or from the L1 invariant views
emitted by `common/l2/schema.py`). But for one specific dataset
(say sub-ledger overdraft), your team has already built a heavily
optimized warehouse view that pre-joins the right columns,
applies your bank's overdraft-grace-period policy, and runs in
20 ms. You want the dashboard to read *that view* instead of the
default SQL the product ships with.

The good news: you can swap the SQL behind any single dataset
without touching the visual layer. The visuals don't bind to the
SQL — they bind to a **DatasetContract** (a column name + type
list) that the dataset must produce. Your warehouse view emits
the same column names with the same types, the contract test
goes green, and every visual continues to work.

The bad news: the contract is the load-bearing surface, not the
SQL. Get the column shape right and a swap is one-line. Get it
wrong (typo in a column name, INTEGER where the contract says
DECIMAL) and the dataset deploys but visuals stop rendering with
no good error. So this walkthrough covers the safe-swap pattern,
the test that catches breakage, and the breaking-change recipe
for when your column shape genuinely needs to differ.

## The question

"For one specific dataset, can I point it at *my* warehouse view
instead of the default SQL the product ships with — without
breaking anything downstream?"

## Where to look

Three reference points:

- **`src/quicksight_gen/common/dataset_contract.py`** — the
  `DatasetContract` and `ColumnSpec` dataclasses. Every dataset
  declares one. The `build_dataset()` function takes the SQL
  and the contract together and produces the QuickSight DataSet
  JSON.
- **`src/quicksight_gen/apps/<app>/datasets.py`** — every
  dataset's contract declaration sits next to its
  `build_*_dataset()` function. Read the contract first; it's
  the interface. Read the SQL second; it's the default
  implementation.
- **`tests/test_dataset_contract.py`** — the regression test.
  For every dataset, it builds the DataSet, extracts the
  `InputColumn` list QuickSight will see, and asserts it matches
  the declared contract. This is the test that catches a
  projection bug before deploy.

## What you'll see in the demo

Pick the L1 overdraft dataset as the worked example. Its contract
sits in `apps/l1_dashboard/datasets.py`:

```python
OVERDRAFT_CONTRACT = DatasetContract(columns=[
    ColumnSpec("account_id",            "STRING"),
    ColumnSpec("account_name",          "STRING"),
    ColumnSpec("account_parent_role",   "STRING"),
    ColumnSpec("business_day_start",    "DATETIME"),
    ColumnSpec("business_day_str",      "STRING"),
    ColumnSpec("stored_balance",        "DECIMAL"),
    ColumnSpec("days_outstanding",      "INTEGER"),
    ColumnSpec("aging_bucket",          "STRING"),
])
```

That's the interface every visual on the L1 Overdraft sheet
reads. The default SQL just pulls these columns from
`{{ l2_instance_name }}_overdraft` (a view emitted by `common/l2/schema.py`).

To swap the implementation, edit the `build_overdraft_dataset()`
function and change the SQL — leaving the contract untouched:

```python
def build_overdraft_dataset(cfg: Config) -> DataSet:
    sql = """\
SELECT
    account_id,
    account_name,
    account_parent_role,
    business_day_start,
    TO_CHAR(business_day_start, 'YYYY-MM-DD') AS business_day_str,
    stored_balance,
    days_outstanding,
    aging_bucket
FROM treasury.subledger_overdraft_v          -- your warehouse view
WHERE bank_unit = 'your-unit-id'             -- your scope filter
"""
    return build_dataset(
        cfg, cfg.prefixed("l1-overdraft-dataset"),
        "L1 Overdraft", "l1-overdraft",
        sql, OVERDRAFT_CONTRACT,
        visual_identifier=DS_L1_OVERDRAFT,
    )
```

Run the contract test:

```bash
.venv/bin/pytest tests/test_dataset_contract.py -k overdraft
```

Green = your projection emits the contract columns in the right
order. Deploy with `quicksight-gen json apply -c config.yaml -o
out/ --execute`. The Overdraft KPI, table, and aging bar
chart all keep working — they don't know your SQL changed.

## What it means

The contract is a binding interface, not documentation. Three
properties of the swap to internalize:

1. **Column names must match exactly.** The visuals reference
   columns by name (`account_name`, `aging_bucket`,
   `stored_balance`). If your warehouse view calls it
   `subledger_name`, alias it: `subledger_name AS account_name`.
   The alias is part of the projection contract — keep it in
   the SQL, not in a downstream view.
2. **Column types must match exactly.** `STRING` / `DECIMAL` /
   `INTEGER` / `DATETIME` / `BIT` are the QuickSight type
   alphabet. If you emit `DECIMAL` where the contract says
   `INTEGER`, QuickSight may still ingest it but visual
   formatting (axes, KPI display, aging-bucket sort order) can
   silently degrade. The contract test enforces both name and
   ordering — but type mismatches surface only at deploy time
   when QuickSight rejects the InputColumn list.
3. **Column order matters.** `DatasetContract.columns` is a
   list, not a set. The contract test asserts list equality. If
   you reorder columns in your SELECT, the test fails. This is
   intentional — column order is part of the dataset's public
   surface (it drives the field-list ordering in the QuickSight
   authoring UI), and reordering is a breaking change customers
   should be conscious of.

## Drilling in

A few patterns to know when the swap goes deeper than a one-line
SQL substitution:

### Same-shape swap (safe)

Your warehouse view emits all contract columns with the right
types. Edit one `build_*_dataset()` function's SQL, run the
contract test, deploy. No other code changes. No version bump
necessary on the dashboard side.

### Add a column

You want the overdraft table to also display a new
`overdraft_grace_period_days` column from your bank's policy
config. This is a contract change, not a SQL swap:

1. Add `ColumnSpec("overdraft_grace_period_days", "INTEGER")`
   to `OVERDRAFT_CONTRACT`.
2. Add the column to the SELECT in
   `build_overdraft_dataset()`.
3. Run the contract test — it goes green again because contract
   and projection agree.
4. Add the column to the visual that displays it (in the
   relevant L1 sheet populator).

The contract test catches step 1 + step 2 drift. The visual
edit (step 4) is the actual UX work.

### Rename a column

Don't. Rename in your warehouse view (or alias in the SELECT)
to keep the contract name stable. Renaming a contract column
cascades into every visual that references it by name —
column-formatting, conditional-formatting, drill-action target
columns, filter group field references, parameter bindings.
The blast radius is hard to test exhaustively. Alias at the
projection boundary instead.

### Remove a column

If your warehouse can't supply a column the contract demands,
emit a sentinel value: `'unknown' AS counter_account_name` or
`0 AS days_outstanding`. The visual will render with the
sentinel value; the contract test stays green. Removing the
column from the contract entirely is a breaking change to
every downstream visual that reads it — and removes the option
of ever surfacing the data again without re-tracing every
visual reference.

### Add a column QuickSight can't infer

If your projection's column type can't be inferred from the SQL
(e.g., a `CASE` expression returning mixed types), QuickSight
will reject the InputColumn list at deploy time with a vague
error. Fix at the SQL: cast explicitly (`CAST(... AS DECIMAL)`)
to match the contract's declared type. The contract test does
not catch this — it asserts column *names*, not the type
QuickSight will actually infer at ingest. Deploy is the
boundary that catches the type mismatch.

## Next step

Once you've swapped one dataset's SQL and confirmed the
dashboard still renders cleanly:

1. **Add a unit test for your custom SQL.** Don't rely solely
   on the shipped contract test (it asserts the *contract* is
   intact, not that your specific SQL produces correct
   numbers). Write a test that connects to your warehouse,
   runs the new SQL against a known fixture, and asserts row
   counts / aggregate values. The
   [How do I test my customization?](how-do-i-test-my-customization.md)
   walkthrough covers the pytest pattern.
2. **Document why you swapped.** Add a one-line comment above
   the SQL in `build_overdraft_dataset()` pointing at the
   warehouse view (`-- Reads treasury.subledger_overdraft_v;
   our overdraft policy view`). Future-you (or a colleague
   merging upstream) will need to know the SQL is intentional
   custom code, not a sync drift.
3. **Stay on the contract for upstream merges.** When you pull
   a new release of `quicksight-gen`, the contract may evolve
   (new columns added). If your custom SQL is missing a newly
   added column, the contract test fails immediately. That's
   the signal to add it to your projection — same pattern as
   "Add a column" above.

## Related walkthroughs

- [How do I map my production database to the two base tables?](how-do-i-map-my-database.md) —
  the upstream prerequisite. SQL swaps assume your data is
  already in `{{ l2_instance_name }}_transactions` +
  `{{ l2_instance_name }}_daily_balances` (or in warehouse views you've
  decided to read directly).
- [Schema_v6 → The layered model](../../Schema_v6.md#the-layered-model) —
  the L1 invariant views (`{{ l2_instance_name }}_drift`, `{{ l2_instance_name }}_overdraft`,
  `{{ l2_instance_name }}_limit_breach`, `{{ l2_instance_name }}_stuck_pending`,
  `{{ l2_instance_name }}_stuck_unbundled`,
  `{{ l2_instance_name }}_expected_eod_balance_breach`) the default SQL
  reads. Read these to decide whether to redirect at the
  dataset level or recreate the views in your warehouse with
  the same shape.
