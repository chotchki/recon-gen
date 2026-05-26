# How do I populate `{{ l2_instance_name }}_daily_balances` from my core banking system?

*Engineering walkthrough — Data Integration Team. Foundational.*

## The story

Once your transactions feed is landing (see
[How do I populate `{{ l2_instance_name }}_transactions` from my core banking system?](how-do-i-populate-transactions.md)),
the dashboards still won't agree until the *stored* balance is
populated too. `{{ l2_instance_name }}_daily_balances` carries one
row per `(account_id, business_day_start)` — your core banking
system's end-of-day stored figure. The L1 Drift check compares it
against the recomputed `SUM(amount_money)` over your transactions;
without daily_balances rows, every account looks like a fresh-zero
opening balance and the drift surface is meaningless.

## The question

"For my core banking system's `gl_eod_balances` (or equivalent)
table, what's the canonical projection that maps it to
`{{ l2_instance_name }}_daily_balances`? What columns are mandatory
versus optional, and how do I avoid the false-positive drift
surface that comes from getting the day boundary wrong?"

## Where to look

Two reference points (the same pair as the transactions walkthrough):

- **`docs/Schema_v6.md` → Table 2 — `{{ l2_instance_name }}_daily_balances`**
  for the column-level contract and per-column failure modes.
- **`common/l2/schema.py::emit_schema(l2_instance)`** is the source
  of truth for the prefixed DDL. The second `CREATE TABLE` block it
  emits is `{{ l2_instance_name }}_daily_balances`.

For runnable INSERT examples (not just the projection shape but a
self-contained SQL block you can adapt), look at
**`recon-gen data etl-example`** — Patterns 8 and 9 cover the
baseline daily-balance row and the per-day Limit Schedule override
respectively. They use sentinel `-EXAMPLE` IDs so the SQL doesn't
collide with your seeded rows.

## What it means

For every row your ETL writes, you're committing to a contract:

1. **The 8 mandatory columns** (per [Schema_v6.md → ETL contract / minimum viable feed](../../Schema_v6.md#etl-contract-minimum-viable-feed))
   get the row visible to the drift check at all.
2. **`(account_id, business_day_start)`** is the supersession key.
   Re-inserting with the same pair creates a higher-`entry` row
   that wins the read; older rows stay for audit. **Pick the day
   boundary consistently with `posting`** in your transactions
   feed (see the gotchas section below).
3. **`money`** is the *stored* end-of-day figure from your core
   system, in integer cents. The L1 Drift matview joins this
   against `SUM(amount_money)` from transactions for the same
   `(account_id, business_day_start)`. Drift = stored − computed.
4. **`expected_eod_balance`** is optional — set it when the L2
   instance declares a target for this account. NULL = no
   expectation, the constraint doesn't apply. Same cents storage
   as `money`.
5. **`metadata` JSON** — open container. Per-day Limit Schedule
   overrides go under `metadata.limits` (see Pattern 9 from the
   etl-example output); scenario tags will live alongside per AV.5.

Everything else (`account_parent_role`, `supersedes`) is conditional
— populate when the downstream check needs it. See [Schema_v6.md
→ ETL contract / minimum viable feed](../../Schema_v6.md#etl-contract-minimum-viable-feed)
for the full column-by-column gate.

## Drilling in

The mapping pattern looks like this for a customer-DDA stored
end-of-day balance (adapted from `recon-gen data etl-example`
Pattern 8):

```sql
INSERT INTO {{ l2_instance_name }}_daily_balances (
    account_id, account_name, account_role, account_scope,
    account_parent_role, expected_eod_balance,
    business_day_start, business_day_end, money, metadata
)
SELECT
    b.account_number                     AS account_id,
    a.account_name,
    a.account_role,
    a.account_scope,                                   -- 'internal' / 'external'
    a.parent_role                        AS account_parent_role,
    NULL                                 AS expected_eod_balance,  -- or your target in cents
    b.business_day_start_ts              AS business_day_start,
    b.business_day_end_ts                AS business_day_end,
    CAST(ROUND(b.stored_eod_balance * 100) AS BIGINT) AS money,    -- dollars → cents (AO.1)
    NULL                                 AS metadata               -- or '{"limits": {...}}' for per-day overrides
FROM core_banking.gl_eod_balances b
JOIN core_banking.accounts a ON a.account_number = b.account_number
WHERE b.business_day_start_ts >= CURRENT_DATE - INTERVAL '7 days';
```

A few things to note about this projection:

- **`business_day_start` / `business_day_end`** define the day
  window for this account-day. They're TIMESTAMPs, not DATEs — the
  schema CHECK enforces `business_day_end > business_day_start`.
  Most banks run a midnight-to-midnight day; some run a 6pm-to-6pm
  business day. **The boundary you pick MUST match what you used
  for the `posting` column in transactions** — the drift check
  groups transactions by their `posting`-derived business day and
  compares against the daily_balance row keyed on the same boundary.
  Day-boundary disagreement = false-positive drift on every row.
- **`money`** is stored as integer cents, BIGINT (Phase AO.1) —
  same convention as `transactions.amount_money`. CAN go negative
  (overdraft is observable per the L1 Non-negative Stored Balance
  SHOULD constraint). Python ETLs should reach for
  `recon_gen.common.money.Cents.from_dollars(...).value` instead of
  the inline `CAST(ROUND(x * 100) AS BIGINT)` shape — it rejects
  float-init Decimals that re-introduce float dust.
- **`expected_eod_balance`** when set must be in the same cents
  unit as `money`. NULL = "no expectation declared." The L1
  ExpectedEODBalance invariant only fires on rows where it's set.
- **`metadata`** carries per-day overrides. Most rows leave it
  NULL; populate it only when you need to override a static
  LimitSchedule cap for a specific account-day (Pattern 9). The
  shape: `{"limits": {"<rail_name>": <cap_dollars>, ...}}`.
  Static caps come from the L2 YAML's `LimitSchedule` block;
  daily_balances.metadata only enters the picture for one-day
  exceptions. AV.5 will write scenario tags under the same
  `metadata` column — pick a sibling key like `"scenario_id"`,
  don't smear it into the `limits` map.
- **`account_*` denormalization** (name / role / scope /
  parent_role) is intentional — the same redundancy your
  transactions feed carries. Lets the dashboard's Drift and
  Daily Statement sheets render account context without a join.
  Source it from the same `core_banking.accounts` lookup your
  transactions ETL uses; the values for a given `account_id` MUST
  agree between the two tables on any given day.

## Common pitfalls

- **Skipping the row entirely** — if you have transactions for an
  account-day but no daily_balance, the L1 Drift matview joins
  with no match and the drift surface stays silent for that
  account-day (no row = no comparison = no surfaced drift). You
  won't see it in the demo but production ETLs miss it routinely.
  Drive a "every account that had transactions today has a
  daily_balance row" sanity gate in your ETL job before declaring
  the load complete.
- **Day boundary drift between feeds** — `posting` and
  `business_day_start` MUST share the same convention. The
  walkthrough at
  [How do I prove my ETL is working before going live?](how-do-i-prove-my-etl-is-working.md)
  includes a query that surfaces day-boundary mismatch as
  recomputed-vs-stored drift.
- **Sign confusion** — `money` is the stored end-of-day balance,
  *signed* (negative on overdraft). It's NOT a magnitude. Don't
  ABS it. Don't take `signed_amount`'s sign from the matching
  transaction and stick it on `money` — they're independent
  quantities. `money` is what your core banking system says the
  account *is*; transactions tell you how it *got there*.

## Next step

Once your daily_balances projection is wired up alongside your
transactions projection:

1. **Run the validation walkthrough**
   ([How do I prove my ETL is working before going live?](how-do-i-prove-my-etl-is-working.md))
   — it covers the drift-recompute check that compares the two
   feeds against each other on every account-day.
2. **Open the L1 Reconciliation Dashboard's Drift sheet** for a
   real account-day with known activity. Drift should be `$0` if
   both feeds agree. A small fraction of a cent ⇒ you have a
   rounding bug in the dollars→cents conversion. Whole dollars or
   more ⇒ either a transaction is missing from the feed, or your
   day-boundary convention disagrees between the two feeds.
3. **Iterate** — populate `expected_eod_balance` /
   `metadata.limits` when downstream checks need them.

## See also

- [How do I populate `{{ l2_instance_name }}_transactions` from my core banking system?](how-do-i-populate-transactions.md) — the sibling walkthrough.
- [How do I prove my ETL is working before going live?](how-do-i-prove-my-etl-is-working.md) — drift + net-zero validation.
- [How do I validate a single account-day?](how-do-i-validate-a-single-account-day.md) — when one row looks wrong.
- [Schema_v6 — Table 2 daily_balances](../../Schema_v6.md#table-2-prefix_daily_balances)
- `recon-gen data etl-example -o demo/etl-examples.sql` — runnable SQL with `-EXAMPLE` sentinel IDs. Patterns 8 and 9 cover daily_balances.
