# How do I add an app-specific metadata key?

*Customization walkthrough — Developer / Product Owner. Reskinning + extending.*

## The story

Your ETL team added a new attribute to
`{{ l2_instance_name }}_transactions.metadata` — say `originating_branch` on
sales, or `risk_score` on external transfers. The key is landing
in the JSON; you can see it in the database. Now you need to
surface it on the dashboards: as a column in a table, a filter in
a sheet control, a grouping dimension in a pivot, or a category
axis on a bar chart.

The dashboard side of the metadata-key contract is shorter than
the ETL side and uses no schema migration: `JSON_VALUE(metadata,
'$.your_key')` in a dataset SQL, an entry in the matching
`DatasetContract`, and the visual reference picks it up. Because
every dataset is direct-query (not SPICE), a new column appears
the moment your next deploy completes — no refresh step.

This walkthrough covers the dashboard-side read pattern and the
load-bearing decision: when to surface a metadata key as a
column vs a filter vs a grouping dimension. The ETL-side write
pattern (what your ETL team does to get the key into `metadata`
in the first place) is in the
[Data Integration Handbook](../etl/how-do-i-add-a-metadata-key.md).

## The question

"My team's ETL is now writing `originating_branch` into the
`metadata` JSON on sale rows. How do I expose it on the
dashboards so end users can filter and group by branch?"

## Where to look

Three reference points:

- **[Data Integration Handbook → How do I add a metadata key?](../etl/how-do-i-add-a-metadata-key.md)** —
  the producer side. Read this first if you're not yet certain
  the key is being written. The dashboard read pattern below
  assumes the data is already there.
- **`src/quicksight_gen/apps/<app>/datasets.py`** — the dataset
  SQL files. Every existing `JSON_VALUE` extraction is a model
  for the one you're adding. Grep for `JSON_VALUE(metadata` to
  see them.
- **[Schema_v6.md → metadata text column contract](../../Schema_v6.md#metadata-json-columns)** —
  the cataloged keys, their per-`rail_name` placement, and
  the forbidden-syntax list (`->>`, `->`, `@>`, `?` are all
  out — only `JSON_VALUE` / `JSON_QUERY` / `JSON_EXISTS`).

## What you'll see in the demo

An existing dataset reads sale-row metadata. Grep an L1 dataset
SQL for `JSON_VALUE(metadata`:

```sql
SELECT
    transaction_id,
    transfer_id,
    posting,
    amount_money,
    JSON_VALUE(metadata, '$.merchant_name')   AS merchant_name,
    JSON_VALUE(metadata, '$.card_brand')      AS card_brand,
    JSON_VALUE(metadata, '$.cashier')         AS cashier,
    JSON_VALUE(metadata, '$.payment_method')  AS payment_method
FROM {{ l2_instance_name }}_transactions
WHERE rail_name = 'sale'
```

The matching `DatasetContract` declares each extracted column:

```python
SALES_CONTRACT = DatasetContract(columns=[
    ColumnSpec("transaction_id",   "STRING"),
    ColumnSpec("transfer_id",      "STRING"),
    ColumnSpec("posting",          "DATETIME"),
    ColumnSpec("amount_money",     "DECIMAL"),
    ColumnSpec("merchant_name",    "STRING"),
    ColumnSpec("card_brand",       "STRING"),
    ColumnSpec("cashier",          "STRING"),
    ColumnSpec("payment_method",   "STRING"),
])
```

That's the pattern. Adding a column is a SQL line + a
`ColumnSpec` line. The visual layer picks it up via the dataset
reference; no schema migration, no rebuild step.

## What it means

The dashboard-side metadata-key contract has three parts:

### Part 1 — Read it in dataset SQL via `JSON_VALUE`

Add the extraction to the relevant `build_*_dataset()` function:

```sql
SELECT
    -- existing columns ...
    JSON_VALUE(metadata, '$.originating_branch') AS originating_branch
FROM {{ l2_instance_name }}_transactions
WHERE rail_name = 'sale';
```

`JSON_VALUE` returns NULL for rows that don't carry the key.
Decide upfront whether NULL is acceptable in your visual:

- **Yes (most cases)**: leave the SQL as-is. Filter / group
  visuals show NULL as a "(blank)" bucket; KPIs / counts
  ignore NULL rows.
- **No (key is mandatory for the visual's question)**: add
  `WHERE JSON_EXISTS(metadata, '$.originating_branch')` to
  filter out the un-keyed rows.
- **No, but show a sentinel**: wrap with `COALESCE(JSON_VALUE(...),
  'unknown') AS originating_branch`. The cell renders
  `unknown` instead of blank.

### Part 2 — Add the column to the `DatasetContract`

```python
SALES_CONTRACT = DatasetContract(columns=[
    # existing entries ...
    ColumnSpec("originating_branch", "STRING"),
])
```

Run `tests/test_dataset_contract.py` — the contract test verifies
your SQL projection emits exactly the contract columns in the
declared order. Green = the column shape is consistent. See
[How do I swap dataset SQL?](how-do-i-swap-dataset-sql.md) for the
contract / projection relationship in detail.

### Part 3 — Decide how the visual surfaces the new column

This is the load-bearing UX decision. The metadata key is the
data; the visual treatment is what end users actually
experience. Three patterns from the existing dashboards:

- **As a table column.** Drag `originating_branch` into a Table
  visual's Field Wells → Group By. Shows up as a sortable,
  filterable cell in every row. Right answer when the user's
  question is *"show me one row per sale, including the branch."*
- **As a sheet-level filter (dropdown / multi-select).** Add a
  ParameterControl + linked Filter group. Shows up as a sheet
  header control that scopes every visual on the sheet. Right
  answer when the user's question is *"show me everything for
  the West Hills branch."*
- **As a grouping / category dimension.** Drag into a
  BarChart's Group By or a Pivot's Row dimension. Visualizes
  the metric *across* branches. Right answer when the user's
  question is *"compare sales volume across branches."*

The same metadata key can appear in all three forms on the same
sheet — they're not mutually exclusive. But add them in priority
order: usually the table column first (lowest cost, highest
clarity), then the filter, then the grouping visual.

## Drilling in

A few patterns to know once the basic addition works:

### When to promote a metadata key to a first-class column

Metadata is the right home for *new* keys and *per-transfer-type*
keys. But if a key is read in many `WHERE` / `GROUP BY` clauses
across many datasets, the constant `JSON_VALUE(metadata, '$.key')`
becomes friction:

- More keystrokes per dataset.
- Slower at scale (`JSON_VALUE` is fine for direct query but
  costs more than a native column on big tables).
- Easier to typo (`'$.cardbrand'` silently returns NULL forever).

The promotion path is a schema migration: add the column to
`{{ l2_instance_name }}_transactions` (or `{{ l2_instance_name }}_daily_balances`), update
the ETL projection to write the new column directly, and update
dataset SQL to reference the column instead of `JSON_VALUE`. The
`DatasetContract` doesn't change — same column name, same type,
just a different upstream source. Don't pre-promote: keep keys
in metadata until the friction is real.

### When to surface a metadata key as a filter vs a column

Rule of thumb from the existing dashboards:

- **Cardinality < 10 (card_brand, payment_method)**: filter
  works well. End user picks one or two values from a
  dropdown. Drives the whole sheet.
- **Cardinality 10-100 (cashier, originating_branch)**: filter
  is OK but the dropdown gets long. A table column +
  click-to-filter (left-click drill on the cell) is often
  better — the user sees the available values inline rather
  than picking from a list.
- **Cardinality > 100 (merchant_account_id, statement_line_id)**:
  table column only. Filtering by a high-cardinality value is a
  search problem, not a dropdown problem; QuickSight's
  search-in-filter is functional but not great UX. Surface as
  a column and let the user use the table's Find function.

### Why the demo data isn't the constraint

The demo seeds these keys for every applicable row, so the
demo always shows non-blank cells. Production data may not.
If your ETL adds `originating_branch` going forward but didn't
backfill historical rows, every dataset reading the key
returns NULL for old rows. Three options, in order of
preference:

1. **Backfill in the warehouse.** A one-shot UPDATE to add
   the key to old rows with a derived value. Cleanest if the
   value is recoverable.
2. **Coalesce in the projection.** `COALESCE(JSON_VALUE(...),
   'pre-2026') AS originating_branch`. Old rows render as
   `pre-2026`; new rows show their actual branch. Honest about
   the data shape change.
3. **Filter in the visual.** Add a sheet-level filter
   defaulting to the date range where the key exists. Hides
   the issue but makes the dashboard less useful for
   historical analysis.

## Next step

Once the new column is reading and rendering:

1. **Run the contract test.**
   `.venv/bin/pytest tests/test_dataset_contract.py -k <dataset_name>`.
   Green = projection and contract agree on the column shape.
   Red = something drifted; usually a typo in the column alias
   (`originating_branch` vs `originating_brnach`).
2. **Deploy and verify the visual.** `quicksight-gen json apply
   -c config.yaml -o out/ --execute`. Open the dashboard,
   confirm the new column / filter / dimension renders with
   non-blank values.
3. **Decide whether to document.** If the key is bank-specific
   (your own `originating_branch` schema), document in your
   internal customization README; if it's a candidate for
   upstream contribution, add a row to
   [Schema_v6.md → metadata catalog](../../Schema_v6.md#metadata-json-columns)
   and propose it as part of the canonical key set.

## Related walkthroughs

- [Data Integration Handbook → How do I add a metadata key?](../etl/how-do-i-add-a-metadata-key.md) —
  the **producer side** of the same pattern. Read this if the
  key isn't yet being written into `metadata` upstream.
- [How do I swap the SQL behind a dataset?](how-do-i-swap-dataset-sql.md) —
  the contract / projection relationship. Adding a metadata
  column is a contract change; the swap walkthrough explains
  why that's not free and what the test catches.
- [Schema_v6 → metadata text column contract](../../Schema_v6.md#metadata-json-columns) —
  the canonical per-`rail_name` metadata key inventory and
  the forbidden-syntax list (no `->>`, no JSONB, no
  Postgres-specific operators).
