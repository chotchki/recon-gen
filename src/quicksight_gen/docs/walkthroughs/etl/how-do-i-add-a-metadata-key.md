# How do I add a metadata key without breaking the dashboards?

*Engineering walkthrough — Data Integration Team. Extension.*

## The story

The 11-column `{{ l2_instance_name }}_transactions` contract intentionally
doesn't carry every per-`rail_name` attribute as its own column
— `card_brand` belongs on sales but is meaningless on internal
transfers; `settlement_type` matters on settlements but not on
payments; `statement_line_id` belongs on Fed force-posts only. The
schema's answer is the `metadata` JSON column: each `rail_name`
carries its own grab-bag of typed extras inside JSON, and dataset
SQL extracts via `JSON_VALUE(metadata, '$.your_key')`.

That's powerful — and easy to misuse. Two failure modes show up
when teams add a new metadata key:

- **The wrong JSON dialect**: someone reaches for PostgreSQL's
  native `metadata->>'key'` operator and the query works in dev
  but fails to port. Or they reach for `JSONB`, breaking the
  schema constraint.
- **Visual references a key the data doesn't carry**: a Pivot or
  Table column reads `JSON_VALUE(metadata, '$.your_new_key')` for
  rows that pre-date the new key, and the cell renders blank
  (or worse, the visual silently filters those rows out).

## The question

"My team needs to add a new attribute (`originating_branch`,
`risk_score`, `fx_rate`) to a subset of `{{ l2_instance_name }}_transactions`
rows. What's the contract for adding it without breaking existing
dashboards or the portability of the SQL?"

## Where to look

Three reference points:

- **`docs/Schema_v6.md` → metadata catalog tables** — the existing
  per-`rail_name` key inventory. New keys should slot into the
  same shape (key name, type, what it drives).
- **`src/quicksight_gen/apps/<app>/datasets.py`** — the SQL
  patterns. Every metadata extraction looks like
  `JSON_VALUE(metadata, '$.<key>') AS <alias>`; new keys follow
  the same shape. The L1 Reconciliation Dashboard's datasets are
  the densest reference.
- **CLAUDE.md → "Database portability constraint"** — the
  forbidden-pattern list (`JSONB`, `->>`, `->`, `@>`, `?`, GIN
  indexes). If you reach for any of these, the new key won't
  port.

## What you'll see in the demo

Existing demo rows already exercise the pattern. Grep one out:

```python
from quicksight_gen.common.l2.loader import load_instance
from quicksight_gen.common.l2.seed import emit_seed
from quicksight_gen.common.l2.auto_scenario import default_scenario_for

l2 = load_instance("tests/l2/{{ l2_instance_name }}.yaml")
sql = emit_seed(l2, default_scenario_for(l2).scenario)
print(next(line for line in sql.splitlines() if "card_brand" in line))
```

You'll see a `JSON_OBJECT(... 'card_brand' VALUE 'visa', ...)`
literal in the INSERT. The matching dataset SQL:

```bash
grep -rn "JSON_VALUE(metadata, '\\$.card_brand')" \
     src/quicksight_gen/apps/
```

shows the consumer side: `JSON_VALUE(metadata, '$.card_brand') AS
card_brand` in the dataset projection. That pair —
`JSON_OBJECT(... 'key' VALUE 'val')` on the producer side,
`JSON_VALUE(metadata, '$.key')` on the consumer side — is the only
shape allowed.

## What it means

The contract for any new metadata key has four parts:

1. **JSON value type must be a portable scalar**. Strings,
   numbers, booleans, and dates are fine. Nested objects work for
   well-defined sub-payloads. Arrays work in principle but no
   current dataset reads one — exercise caution. **No binary, no
   Postgres-specific types**.
2. **Use `JSON_OBJECT(... 'key' VALUE 'value')` to write, not
   PostgreSQL row-to-JSON shortcuts**. Row-to-JSON casts emit a
   shape that breaks `JSON_VALUE` parsing on stricter dialects.
3. **Use `JSON_VALUE(metadata, '$.key')` to read, never `->>`**.
   The `->>` operator is PostgreSQL-only; `JSON_VALUE` is the
   SQL/JSON standard form.
4. **Document the new key in `Schema_v6.md`'s metadata catalog
   for that `rail_name`**. Otherwise the schema-doc drift
   tests fail the next time anyone touches the catalog.

A subtle constraint on dataset visuals: if a visual *expects* the
key to be present (e.g., uses it as a filter or grouping
dimension), all rows the visual sees must carry the key. The
options for handling rows without the key:

- **Filter the visual to rows that have it**:
  `WHERE JSON_EXISTS(metadata, '$.your_key')`. Cleanest when the
  key is genuinely optional.
- **Coalesce in the projection**: `COALESCE(JSON_VALUE(metadata,
  '$.your_key'), 'unknown') AS your_key`. Keeps the row visible
  but renders an explicit sentinel.
- **Backfill the key on existing rows**: a one-shot UPDATE to add
  `'your_key' VALUE '<derived>'` to the existing JSON. Right
  answer when the key has a sensible default for historical
  rows.

## Drilling in

A worked example. Suppose your team needs to add an
`originating_branch` key on sale rows so a downstream Executives
sheet can group by branch.

**Step 1 — write it on the producer side (your ETL).** Add to the
existing `JSON_OBJECT` literal in your sale-projection INSERT:

```sql
JSON_OBJECT(
    'source'              VALUE 'core_banking',
    'merchant_id'         VALUE p.merchant_id,
    -- existing keys ...
    'originating_branch'  VALUE p.branch_code   -- new key
)
```

**Step 2 — read it on the consumer side (the dataset SQL).** In
the relevant `datasets.py` builder, add a projected column:

```sql
SELECT
    -- existing columns ...
    JSON_VALUE(metadata, '$.originating_branch') AS originating_branch
FROM {{ l2_instance_name }}_transactions
WHERE rail_name = 'sale';
```

Update the matching `DatasetContract` to add `("originating_branch",
"STRING")` so the contract test stays green.

**Step 3 — document it.** Add a row to the `sale` metadata
catalog table in `Schema_v6.md`:

```markdown
| `originating_branch` | string | Branch code that handled the sale | Branch grouping in downstream sheets |
```

**Step 4 — wire the visual.** Direct query (not SPICE) means new
columns show up immediately after `quicksight-gen json apply
--execute`. No refresh step. Open the relevant sheet, drag
`originating_branch` into the Pivot grouping or Table column
list.

## Next step

Once the key is producing, consuming, and rendering:

1. **Run the unit + integration tests**:
   `.venv/bin/pytest tests/test_demo_etl_examples.py
   tests/test_dataset_contract.py`. The schema-contract test
   verifies your new key is in the catalog; the dataset-contract
   test verifies the SQL projection matches.
2. **Re-run the pre-flight invariants** from the validation
   walkthrough. Adding a metadata key shouldn't break any of
   them, but if you backfilled rows via UPDATE, double-check that
   the cumulative-sum invariant still holds (UPDATEs on
   `amount_money` are the danger; UPDATEs on `metadata` are
   safe).
3. **Deploy to the QuickSight environment**:
   `quicksight-gen json apply -c run/config.yaml -o run/out/
   --execute`. The new column appears on next dashboard open — no
   SPICE refresh needed.

## Related walkthroughs

- [How do I populate `{{ l2_instance_name }}_transactions` from my core banking system?](how-do-i-populate-transactions.md) —
  the foundational projection. This walkthrough adds keys to that
  projection's `metadata` literal.
- [How do I prove my ETL is working before going live?](how-do-i-prove-my-etl-is-working.md) —
  re-run the three invariants after any metadata addition.
- [What do I do when the demo passes but my prod data fails?](what-do-i-do-when-demo-passes-but-prod-fails.md) —
  the "visual shows N/A" symptom in the debug recipes is usually
  a metadata-key contract violation.
- [Schema_v6 → metadata catalog](../../Schema_v6.md#metadata-json-columns) —
  the per-`rail_name` key inventory and its forbidden-syntax
  rules.
