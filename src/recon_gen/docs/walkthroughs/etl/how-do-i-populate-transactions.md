# How do I populate `{{ l2_instance_name }}_transactions` from my core banking system?

*Engineering walkthrough — Data Integration Team. Foundational.*

## The story

You've got an upstream core banking system with a `gl_postings`
table (or its local equivalent — `general_ledger.entry`,
`accounting.posting_detail`, etc.). It carries one row per posting
leg already, which is the natural granularity of our
`{{ l2_instance_name }}_transactions` table. You need to write the ETL job that
lands it in our two-table schema by the morning cut so the four
L2-fed dashboards (L1 Reconciliation, L2 Flow Tracing,
Investigation, Executives) work.

The good news: it's mostly a column-rename. The contract is small
(11 mandatory columns + a handful of conditional ones — see
[Schema_v6.md → ETL contract / minimum viable feed](../../Schema_v6.md#etl-contract-minimum-viable-feed)).
The bad news: skip the wrong column and a downstream check goes
silent. So this walkthrough covers the canonical projection plus
the per-column failure modes.

## The question

"For my core banking system's `gl_postings` table, what's the
canonical projection that maps it to `{{ l2_instance_name }}_transactions`? What
columns must I populate, and what columns can wait until v2?"

## Where to look

Two reference points:

- **`docs/Schema_v6.md`** — column-level contract and per-column
  failure modes ("If you skip this, what dashboard breaks?").
- **`common/l2/schema.py::emit_schema(l2_instance)`** — the source
  of truth for the prefixed DDL. Every base table, view, and
  matview your dashboards read is emitted here, all under the L2
  instance prefix. Call it from Python (or apply directly via
  `recon-gen schema apply --execute`) to see the rendered
  output for your L2 instance.

The `{{ l2_instance_name }}` in this walkthrough's SQL is your L2 instance name
(e.g., `{{ l2_instance_name }}`); your ETL substitutes it once when wiring
the projection.

## What you'll see in the demo

Run (substitute your own L2 path for the bundled fixture below):

```python
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema

l2 = load_instance("tests/l2/{{ l2_instance_name }}.yaml")
print(emit_schema(l2)[:4000])
```

The first `CREATE TABLE` block is `{{ l2_instance_name }}_transactions` itself
— the column list, types, and constraints your projection has to
satisfy. The second is `{{ l2_instance_name }}_daily_balances`. Read both
end-to-end before writing the projection; they're the contract.

For an end-to-end mapping from `core_banking.gl_postings` →
`{{ l2_instance_name }}_transactions`, see **Example 1** in `docs/Schema_v6.md`
(the SQL block under "Populating customer DDA postings from core
banking"). It's a real `INSERT INTO ... SELECT FROM` against a
hypothetical core-banking source schema.

## What it means

For every row your ETL writes, you're committing to a contract:

1. **The 11 mandatory columns** (per [Schema_v6.md → minimum
   viable feed](../../Schema_v6.md#etl-contract-minimum-viable-feed)) get
   the row visible on the dashboards at all.
2. **`transfer_parent_id`** populated only for chained transfers
   (e.g., `external_txn → payment → settlement → sale` or any
   reversal chain your L2 declares). Skip it and pipeline-traversal
   walkthroughs (Money Trail, Account Network) silently return
   nothing for the affected rows.
3. **`origin = 'ExternalForcePosted'`** on Fed / processor
   force-posts. Skip it and the L1 drift split between
   bank-initiated activity and force-posted activity collapses
   (rows look like normal operator postings, drift checks
   under-fire).
4. **`metadata` JSON** — the universal extras container. Skip it
   on day 1 if your downstream consumer doesn't need it; populate
   it in priority order (`source` first, then per-`rail_name`
   keys per the catalog). The catalog tables in Schema_v6 list the
   keys + what each one drives.

Everything else (`account_parent_role`, `transfer_completion`,
`template_name`, `bundle_id`, `supersedes`) is conditional —
populate when the downstream consumer demands it. See [Schema_v6.md
→ ETL contract / minimum viable feed](../../Schema_v6.md#etl-contract-minimum-viable-feed)
for the full column-by-column gate.

## Drilling in

The mapping pattern looks like this for a customer-DDA posting
(from Schema_v6 Example 1, abbreviated):

```sql
INSERT INTO {{ l2_instance_name }}_transactions (
    id, transfer_id, rail_name, origin,
    account_id, account_name, account_parent_role, account_role,
    account_scope, amount_money, amount_direction, status,
    posting, metadata
)
SELECT
    p.posting_id                         AS id,        -- your PK (column is `id`, not `transaction_id`)
    p.transfer_id,                                     -- your transfer grouping
    p.rail_name,                                       -- map your enum to ours
    'InternalInitiated'                  AS origin,    -- or 'ExternalForcePosted' for Fed
    p.account_number                     AS account_id,
    a.account_name,
    a.parent_role                        AS account_parent_role,
    a.account_role,
    a.account_scope,                                   -- 'internal' / 'external'
    CAST(ROUND(p.signed_amount * 100) AS BIGINT)                                   AS amount_money,      -- dollars → cents (AO.1)
    CASE WHEN p.signed_amount < 0 THEN 'Debit' ELSE 'Credit' END                   AS amount_direction,  -- NOT NULL; must agree with amount_money's sign
    CASE WHEN p.posting_status = 'P' THEN 'Posted' ELSE 'Failed' END               AS status,
    p.posting_timestamp                  AS posting,
    JSON_OBJECT('source' VALUE 'core_banking')                                     AS metadata
FROM core_banking.gl_postings p
JOIN core_banking.accounts a ON a.account_number = p.account_number
WHERE p.posting_date >= CURRENT_DATE - INTERVAL '7 days';
```

A few things to note about this projection:

- **`status`** maps from your status enum to ours. Anything that's
  not `Posted` MUST be `Pending` or `Failed` (no fourth state) —
  the drift check and net-zero check both `WHERE status = 'Posted'`
  to exclude in-flight or rejected legs.
- **`amount_money`** is signed by the v6 sign convention:
  `Credit ⇒ amount_money ≥ 0` (money IN to the account),
  `Debit ⇒ amount_money ≤ 0` (money OUT). The schema enforces this
  pairing via a CHECK constraint, so a row with conflicting sign +
  direction won't INSERT. `{{ l2_instance_name }}_daily_balances.money`
  for any account-day equals `SUM(amount_money)` up to that day, so
  getting this sign right is what makes the drift check honest. If
  your upstream uses the opposite sign convention, flip it here, not
  later in a view — every downstream check assumes the v6 convention.
  **Stored as integer cents** (Phase AO.1) — the projection above
  multiplies by 100 + casts to BIGINT at the ETL boundary; see
  [Schema_v6 → Money is stored as integer cents](../../Schema_v6.md#money-is-stored-as-integer-cents).
  Python ETLs should reach for `recon_gen.common.money.Cents`
  instead of the inline SQL CAST — `Cents.from_dollars(...).value`
  rejects float-init Decimals that re-introduce float dust.
- **`amount_direction`** is a required `'Debit' | 'Credit'` enum.
  Derive it from your upstream's signed amount via the
  `CASE WHEN p.signed_amount < 0 THEN 'Debit' ELSE 'Credit' END`
  shape above. The base table's CHECK pairs direction with money's
  sign (Debit ⇒ money ≤ 0, Credit ⇒ money ≥ 0), so the two columns
  *must* agree or the row fails to land.
- **`metadata`** carries `source` on every row from this projection
  (driven by the `JSON_OBJECT(... VALUE 'core_banking')` literal).
  That single key is enough to satisfy the Investigation
  provenance walks on day 1.

## Next step

Once your projection is wired up:

1. **Populate a small slice** — one day, one source system. Don't
   try to backfill 90 days on the first run.
2. **Wire the companion daily_balances feed**
   ([How do I populate `{{ l2_instance_name }}_daily_balances` from my core banking system?](how-do-i-populate-daily-balances.md)).
   Drift checks compare *stored* balance (daily_balances.money)
   against *recomputed* (SUM amount_money) — without both feeds,
   the drift surface is meaningless.
3. **Run the validation walkthrough**
   ([How do I prove my ETL is working before going live?](how-do-i-prove-my-etl-is-working.md))
   — it walks you through the net-zero, drift-recompute, and
   parent-chain integrity checks you should run before declaring
   the load complete.
3. **Open the L1 Reconciliation Dashboard's Today's Exceptions
   sheet** — if the KPI reads 0 with no detail rows, your feed
   landed and the contract holds. If KPIs spike unexpectedly, see
   [What do I do when the demo passes but my prod data fails?](what-do-i-do-when-demo-passes-but-prod-fails.md)
   for the symptom-organized debug recipes.
4. **Iterate on metadata** — once the minimum feed is stable,
   layer in `transfer_parent_id` and the per-`rail_name`
   metadata keys per the
   [Metadata JSON columns](../../Schema_v6.md#metadata-json-columns)
   contract.

If your upstream source isn't a `gl_postings` table — say it's a
processor report, a Fed statement file, or a sweep-engine log —
the same projection shape applies, but the inbound columns differ.
Schema_v6.md's examples cover Fed-statement and processor-feed
ingest; the same `INSERT INTO {{ l2_instance_name }}_transactions` pattern
applies regardless of source.

## Related walkthroughs

- [How do I populate `{{ l2_instance_name }}_daily_balances` from my core banking system?](how-do-i-populate-daily-balances.md) —
  the **sibling walkthrough**. Drift checks need both feeds; ship
  them together.
- [How do I prove my ETL is working before going live?](how-do-i-prove-my-etl-is-working.md) —
  the **next step** after writing the projection. Validates the
  invariants the dashboards depend on.
- [How do I tag a force-posted external transfer correctly?](how-do-i-tag-a-force-posted-transfer.md) —
  the canonical pattern for Fed-statement ingest, which is the one
  case where `origin = 'ExternalForcePosted'`.
- [How do I add a metadata key without breaking the dashboards?](how-do-i-add-a-metadata-key.md) —
  the extension contract for when your team needs a new metadata
  field.
- [Schema_v6 → ETL contract / minimum viable feed](../../Schema_v6.md#etl-contract-minimum-viable-feed) —
  the column-by-column day-1 minimum the projection must satisfy.
- [Investigation: Where did this transfer originate?](../investigation/where-did-this-transfer-originate.md) —
  a **downstream consumer** walkthrough: what an analyst does with
  the `{{ l2_instance_name }}_transactions` rows your projection lands.
