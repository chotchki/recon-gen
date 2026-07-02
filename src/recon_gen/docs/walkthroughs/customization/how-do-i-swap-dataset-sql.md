# How do I swap the SQL behind a dataset without breaking the visuals?

*Customization walkthrough — Developer / Product Owner. Reskinning + extending.*

## The story

Your data lands in `{{ l2_instance_name }}_transactions` and
`{{ l2_instance_name }}_daily_balances` per
[How do I map my production database?](how-do-i-map-my-database.md).
Most bundled datasets work out of the box — they read directly
from the two prefixed base tables (or from the L1 invariant views
emitted by `common/l2/schema.py`). But for one dataset (say
sub-ledger overdraft), your team has already built an optimized
warehouse view that pre-joins the right columns, applies your
bank's overdraft-grace-period policy and runs in 20 ms. You want
the dashboard to read THAT view, not the default SQL the product
ships with.

You can. Swap the SQL behind any single dataset without touching
the visual layer — the visuals don't bind to the SQL, they bind
to a `DatasetContract` (a column-name + type list) the dataset
must produce. Emit the same column names with the same types,
the contract test passes and every visual keeps working.

The catch: the contract is what the visuals depend on, not the
SQL. Match the column shape and the swap is one line. Miss it
(typo in a column name, INTEGER where the contract says DECIMAL)
and the dataset still runs but the visuals stop rendering with no
good error. So this walkthrough covers three things:

- the safe-swap pattern,
- the test that catches breakage,
- the breaking-change recipe for when your column shape genuinely
  needs to differ.

## The question

"For one specific dataset, can I point it at MY warehouse view
instead of the default SQL the product ships with — without
breaking anything downstream?"

## Where to look

Three reference points:

- **`src/recon_gen/common/dataset_contract.py`** — the
  `DatasetContract` and `ColumnSpec` dataclasses. Every dataset
  declares one. The `build_dataset()` function takes the SQL
  and the contract together and produces the dataset the
  dashboards read.
- **`src/recon_gen/apps/<app>/datasets.py`** — every
  dataset's contract declaration sits next to its
  `build_*_dataset()` function. Read the contract first; it's
  the interface. Read the SQL second; it's the default
  implementation.
- **`tests/unit/test_dataset_sql_contract_projection.py`** — the
  regression test. Sweeps every dataset the 4 app builders emit,
  scans each `BuiltDataset.sql` SELECT list and asserts every
  declared contract column NAME appears (name-presence,
  order-agnostic). This is the test that catches a projection bug
  before it reaches the dashboards. (The per-builder InputColumn
  gate retired with the QS emitter in DW.8.1.b —
  `test_dataset_contract.py` now covers only the `DatasetContract`
  primitives + the Oracle case-fold wrapper.)

## What you'll see in the demo

Pick the L1 overdraft dataset as the worked example. Its contract
sits in `apps/l1_dashboard/datasets.py`:

```python
OVERDRAFT_CONTRACT = DatasetContract(columns=[
    ColumnSpec("account_id", "STRING", shape=ColumnShape.ACCOUNT_ID),
    ColumnSpec("account_name", "STRING"),
    ColumnSpec("account_display", "STRING", shape=ColumnShape.ACCOUNT_DISPLAY),
    ColumnSpec("account_role", "STRING"),
    ColumnSpec("account_parent_role", "STRING"),
    ColumnSpec("business_day_start", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("business_day_end", "DATETIME", shape=ColumnShape.DATETIME_DAY),
    ColumnSpec("stored_balance", "DECIMAL"),
])
```

That's the interface every visual on the L1 Overdraft sheet
reads. The `shape=` tags only matter for drill-eligible columns
(they keep a drill source from being wired to the wrong
parameter); a plain column omits `shape`. The default SQL pulls
these columns from `{{ l2_instance_name }}_overdraft` (a view
emitted by `common/l2/schema.py`).

To swap the implementation, edit `build_overdraft_dataset()` and
change the SQL — leaving the contract untouched:

```python
def build_overdraft_dataset(cfg: Config, l2_instance: L2Instance) -> BuiltDataset:
    sql = """\
SELECT
    account_id,
    account_name,
    account_name || ' (' || account_id || ')' AS account_display,
    account_role,
    account_parent_role,
    business_day_start,
    business_day_end,
    stored_balance
FROM treasury.subledger_overdraft_v          -- your warehouse view
WHERE bank_unit = 'your-unit-id'             -- your scope filter
"""
    return build_dataset(
        cfg, cfg.aws.prefixed("l1-overdraft-dataset"),
        "L1 Overdraft", "l1-overdraft",
        sql, OVERDRAFT_CONTRACT,
        visual_identifier=DS_OVERDRAFT,
    )
```

One caveat the simplified SQL above drops: the shipped builder
also threads the dashboard's Account / Account-Role / date
controls into the WHERE via `<<$param>>` placeholders. Keep those
clauses (and the matching `dataset_parameters=` argument) if you
want those controls to narrow your warehouse view — strip them
and the view returns every row regardless of what the user picks.

Run the contract test:

```bash
.venv/bin/pytest tests/unit/test_dataset_sql_contract_projection.py -k overdraft
```

Pass = your projection emits the contract columns in the right
order. Serve the dashboards with `recon-gen dashboards -c
config.yaml` and the L1 Overdraft
sheet reads your new SQL on the next request — Direct Query, so
the change shows immediately, no deploy step. Every visual on
the sheet keeps working — they don't know your SQL changed.

[See it live](https://recon-gen-spec.hotchkiss.io/)

## What it means

The contract is a binding interface, not documentation. Three
properties of the swap that matter:

1. **Column names must match exactly.** The visuals reference
   columns by name (`account_name`, `account_role`,
   `stored_balance`). If your warehouse view calls it
   `subledger_name`, alias it: `subledger_name AS account_name`.
   The alias is part of the projection contract — keep it in
   the SQL, not in a downstream view.
2. **Column types must match exactly.** `STRING` / `DECIMAL` /
   `INTEGER` / `DATETIME` / `BIT` are the `DatasetContract` type
   alphabet. If you emit `DECIMAL` where the contract says
   `INTEGER`, the renderer still reads it but visual formatting
   (axes, KPI display, category sort order) can silently
   degrade. The contract test enforces column-name presence
   (name-only, order-agnostic) — but it does NOT check the type
   your SQL actually returns, so
   a type mismatch surfaces only when the dashboard formats the
   column wrong (a currency shown as a bare number, dates sorted
   as strings).
3. **Column order isn't enforced.** `DatasetContract.columns` is
   a list, but the projection gate checks column-NAME presence
   only (`re.search` per contract column — order-agnostic).
   Reorder columns in your SELECT and the test still passes;
   SELECT-list order is not a tested surface. Rendered column
   order comes from the visual field wells, NOT the SELECT list
   (`contract.columns` only feeds order-insensitive
   label/format/hidden lookups) — so a reorder is invisible
   downstream.

## Drilling in

A few patterns to know when the swap goes deeper than a one-line
SQL substitution:

### Same-shape swap (safe)

Your warehouse view emits all contract columns with the right
types. Edit one `build_*_dataset()` function's SQL, run the
contract test, serve the dashboards. No other code changes. No
version bump necessary on the dashboard side.

### Add a column

You want the overdraft table to also display a new
`overdraft_grace_period_days` column from your bank's policy
config. This is a contract change, not a SQL swap:

1. Add `ColumnSpec("overdraft_grace_period_days", "INTEGER")`
   to `OVERDRAFT_CONTRACT`.
2. Add the column to the SELECT in
   `build_overdraft_dataset()`.
3. Run the contract test — it passes again because contract
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
emit a sentinel value: `'unknown' AS account_role` or
`0 AS stored_balance`. The visual renders with the sentinel
value and the contract test keeps passing. Removing the column
from the contract entirely is a breaking change to every
downstream visual that reads it — and it removes the option of
ever surfacing the data again without re-tracing every visual
reference.

### Cast a column the SQL can't pin down

If your SQL returns a column whose type is ambiguous (e.g., a
`CASE` expression returning mixed types), the renderer formats
it by guesswork and the column sorts or displays wrong. Fix at
the SQL: cast explicitly (`CAST(... AS DECIMAL)`) to match the
contract's declared type. The contract test does not catch
this — it asserts column NAMES, not the type your query
actually returns. The dashboard render is the boundary that
catches the type mismatch.

## Next step

Once you've swapped one dataset's SQL and confirmed the
dashboard still renders cleanly:

1. **Add a unit test for your custom SQL.** Don't rely solely
   on the shipped contract test — it asserts the CONTRACT is
   intact, not that your specific SQL produces correct numbers.
   Write a test that connects to your warehouse, runs the new
   SQL against a known fixture and asserts row counts /
   aggregate values. The
   [How do I test my customization?](how-do-i-test-my-customization.md)
   walkthrough covers the pytest pattern.
2. **Document why you swapped.** Add a one-line comment above
   the SQL in `build_overdraft_dataset()` pointing at the
   warehouse view (`-- Reads treasury.subledger_overdraft_v;
   our overdraft policy view`). Future-you (or a colleague
   merging upstream) will need to know the SQL is intentional
   custom code, not a sync drift.
3. **Stay on the contract for upstream merges.** When you pull
   a new release of `recon-gen`, the contract may evolve
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
  the L1 invariant views (`{{ l2_instance_name }}_drift`, `{{ l2_instance_name }}_ledger_drift`,
  `{{ l2_instance_name }}_overdraft`, `{{ l2_instance_name }}_limit_breach`,
  `{{ l2_instance_name }}_stuck_pending`, `{{ l2_instance_name }}_stuck_unbundled`,
  `{{ l2_instance_name }}_expected_eod_balance_breach`) the default SQL
  reads. Read these to decide whether to redirect at the
  dataset level or recreate the views in your warehouse with
  the same shape.
