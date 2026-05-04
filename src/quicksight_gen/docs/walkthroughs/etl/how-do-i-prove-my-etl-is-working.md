# How do I prove my ETL is working before going live?

*Engineering walkthrough — Data Integration Team. Foundational.*

## The story

You've populated `{{ l2_instance_name }}_transactions` and `{{ l2_instance_name }}_daily_balances`
from your upstream feed. The morning cut runs at 6 AM and the
dashboards open at 8. Before you cut the load tag and go to bed,
you'd like to know your feed is *internally consistent* — not "the
dashboards render" (that's surface-level), but "the invariants the
dashboards depend on actually hold".

Three invariants matter on day one. Each one is testable from a
single SQL query against the two base tables, and each one
corresponds to a specific exception check on the L1 Reconciliation
Dashboard. If your ETL violates the invariant, the check will fire
— but it'll fire at 8 AM in front of an operator. Better to fire it
at 6:05 AM in your own pipeline.

## The question

"Before I open the dashboards, what SQL can I run against my newly
loaded `{{ l2_instance_name }}_transactions` and `{{ l2_instance_name }}_daily_balances` to
know the feed is sound — and what does each check correspond to on
the dashboard if it's not?"

## Where to look

Three reference points:

- **`docs/Schema_v6.md`** — the per-column failure-mode notes
  ("If you skip this, what dashboard breaks?") tell you which
  invariant a column violation will trip.
- **`common/l2/schema.py`** — the prefixed L1 invariant views
  (`{{ l2_instance_name }}_drift`, `{{ l2_instance_name }}_overdraft`, `{{ l2_instance_name }}_limit_breach`,
  `{{ l2_instance_name }}_stuck_pending`, `{{ l2_instance_name }}_stuck_unbundled`,
  `{{ l2_instance_name }}_expected_eod_balance_breach`) are the dashboard-side
  consequence of the invariants below. If your pre-flight passes,
  the L1 dashboard's Today's Exceptions KPI reads zero on the demo
  data.
- **L1 Reconciliation Dashboard → Today's Exceptions sheet** —
  the unified roll-up. UNION ALL across all 5 L1 invariant views
  scoped to the most recent business day. If pre-flight is green,
  this sheet's KPI is `0`.

## What you'll see in the demo

Run the three pre-flight checks against the seeded demo database:

```bash
quicksight-gen schema apply -c run/config.yaml --execute && \
    quicksight-gen data apply -c run/config.yaml --execute && \
    quicksight-gen data refresh -c run/config.yaml --execute && \
    quicksight-gen json apply -c run/config.yaml -o run/out/ --execute
psql "$DEMO_DATABASE_URL" -f /tmp/preflight.sql
```

Where `/tmp/preflight.sql` is the three queries below. On a clean
demo seed, all three return zero rows — that's the green-light
signal. The seeded "planted failures" (drift scenarios, stuck
suspense, etc.) are at the *check* layer, not the *invariant*
layer; the invariants always hold for the seed because the
generator is deterministic and self-consistent.

## What it means

Each query asserts one invariant. A non-empty result means a row
in your feed contradicts what the schema and dashboards assume.

### Invariant 1 — non-failed transfer legs net to zero

```sql
-- Pre-flight: transfers whose Posted legs do NOT sum to zero.
-- Single-leg types (sale, external_txn) are excluded by construction.
SELECT
    transfer_id,
    SUM(amount_money) AS net,
    COUNT(*)          AS leg_count
FROM {{ l2_instance_name }}_transactions
WHERE status = 'Posted'
  AND transfer_type NOT IN ('external_txn', 'sale')   -- single-leg types
GROUP BY transfer_id
HAVING SUM(amount_money) <> 0;
```

A row here means a multi-leg transfer (`internal`, `payment`,
`settlement`, `clearing_sweep`, `ach`, `wire`, etc.) has legs that
don't balance. Either you projected the wrong sign on one leg,
dropped a leg, or set `status = 'Posted'` on a leg that didn't
post.

**Dashboard consequence**: rows surface in the L1 Drift sheet (the
mismatch shows up at the account level once the daily balance
recompute runs) and the Today's Exceptions roll-up KPI fires.

### Invariant 2 — `{{ l2_instance_name }}_daily_balances.money` matches the recomputed cumulative sum

The L1 Drift view (`{{ l2_instance_name }}_drift`) does this recompute internally
per (account, business_day). The pre-flight version below is the
same shape, scoped to one day:

```sql
-- Pre-flight: ledger rows whose stored EOD balance disagrees with
-- the cumulative SUM of postings to that account.
SELECT
    db.account_id,
    db.business_day_start,
    db.money                                         AS stored,
    COALESCE(SUM(t.amount_money), 0)                 AS recomputed,
    db.money - COALESCE(SUM(t.amount_money), 0)      AS drift
FROM {{ l2_instance_name }}_daily_balances db
LEFT JOIN {{ l2_instance_name }}_transactions t
  ON t.account_id          = db.account_id
 AND t.business_day_start <= db.business_day_start
 AND t.status              = 'Posted'
WHERE db.business_day_start = CURRENT_DATE
GROUP BY db.account_id, db.business_day_start, db.money
HAVING db.money - COALESCE(SUM(t.amount_money), 0) <> 0;
```

A row here means the balance feed and the transaction feed
disagree on the same account-day. Either a posting is missing /
extra in `{{ l2_instance_name }}_transactions`, or the EOD `money` value in
`{{ l2_instance_name }}_daily_balances` is stale.

**Dashboard consequence**: the L1 Drift sheet flags the offending
(account, business_day); the Drift Timelines sheet shows the
account drifting persistently if the gap survives multiple days.

### Invariant 3 — `transfer_parent_id` chains have no orphans

```sql
-- Pre-flight: transactions whose transfer_parent_id points at a
-- transfer_id that doesn't exist in our base table.
SELECT DISTINCT
    t.transfer_id,
    t.transfer_type,
    t.transfer_parent_id   AS missing_parent
FROM {{ l2_instance_name }}_transactions t
WHERE t.transfer_parent_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM {{ l2_instance_name }}_transactions p
      WHERE p.transfer_id = t.transfer_parent_id
  );
```

A row here means a child transfer (a settlement child, payment
child, or any reversal child your L2 declares) names a parent that
wasn't loaded in the same cut. Most often this is an ordering bug:
the child landed before the parent, or you trimmed the parent out
with a narrow `WHERE` clause on the source feed.

**Dashboard consequence**: the Investigation Money Trail sheet
silently returns nothing for the orphaned chains (the
`WITH RECURSIVE` walk over `transfer_parent_id` terminates short).
No L1 KPI fires, but the "trace this dollar" experience breaks.

## Drilling in

Three patterns, all violating the same shape — your ETL trusted
something it shouldn't have:

- **Sign-flip on leg 2.** Most common Invariant 1 violation:
  upstream uses opposite sign convention from ours, and you flipped
  the sign in *some* projections but not all. Audit all branches of
  your `amount_money` mapping.
- **Lagging balance feed.** Most common Invariant 2 violation: the
  balance file lands an hour after the postings file, and your ETL
  processes them in the order they arrive. Either wait for both or
  re-stamp `business_day_start` on the postings feed to match the
  authoritative EOD batch.
- **Narrow WHERE clause.** Most common Invariant 3 violation: a
  `WHERE posting_date >= CURRENT_DATE - INTERVAL '7 days'` filter
  on a child table cuts off parents from older days. For chained
  types, either pull all transfers in the chain together, or
  expand the lookback to cover the longest expected chain age.

A "what should I see on the dashboard if everything's good"
checklist:

- [ ] **L1 Reconciliation Dashboard → Getting Started** sheet
  renders with a date range for today's cut.
- [ ] **L1 Today's Exceptions** KPI = 0; no rows in the detail
  table for accounts your real ETL touched today (planted demo
  scenarios may still surface — those are the demo's job).
- [ ] **L1 Drift** KPI = 0 for any account whose `money` you
  populated today.
- [ ] **L1 Overdraft / Limit Breach / Pending Aging / Unbundled
  Aging** sheets show no rows for the accounts and rails your real
  ETL touched today (planted demo failures will appear — those are
  the demo's job, not yours).

## Next step

Once your three pre-flight queries all return zero rows:

1. **Wire them into your DAG**. Run them as a smoke-test step
   between the load and the "publish" tag. Treat any non-empty
   result as a hard failure — don't publish a load with broken
   invariants.
2. **Backfill, one day at a time**. With pre-flight wired up, you
   can now safely load older days. Run the load + pre-flight per
   day; if any day fails an invariant, fix and re-run that day in
   isolation.
3. **Add app-specific checks for your metadata keys**. The three
   invariants above are *universal*. If you populate
   `transfer_parent_id` for chained transfers, also assert that
   every child row has a non-NULL `transfer_parent_id` (since
   children without a parent won't appear in Investigation's
   Money Trail walk). The pattern is the same — one SELECT,
   `HAVING ... <> 0` or `WHERE ... IS NULL`, fail the DAG on
   non-empty.
4. **Open the dashboard with an analyst on the call**. The pre-
   flight verifies the *contract*; the analyst verifies the
   *meaning*. They'll catch things like "the merchant exists but
   the volume looks 10x too high" that no SQL invariant can.

If any pre-flight query is non-empty and you can't trace it, see
[What do I do when the demo passes but my prod data fails?](what-do-i-do-when-demo-passes-but-prod-fails.md)
for the symptom-organized debug recipes.

## Related walkthroughs

- [How do I populate `{{ l2_instance_name }}_transactions` from my core banking system?](how-do-i-populate-transactions.md) —
  the **prior step**: writing the projection these invariants check.
- [How do I validate a single account-day after a load?](how-do-i-validate-a-single-account-day.md) —
  the single-account-day version of these invariants. Once
  pre-flight passes, this is how you confirm the right thing
  landed for a specific `(account_id, business_day_start)`.
- [How do I tag a force-posted external transfer correctly?](how-do-i-tag-a-force-posted-transfer.md) —
  Invariant 1 + Invariant 3 both interact with `origin` and the
  parent chain on Fed-statement ingest.
- [What do I do when the demo passes but my prod data fails?](what-do-i-do-when-demo-passes-but-prod-fails.md) —
  the symptom-organized debug companion when an invariant fails
  and you can't immediately see why.
- [Schema_v6 → minimum viable feed](../../Schema_v6.md#etl-contract-minimum-viable-feed) —
  the column contract whose failure modes drive the invariants.
- [L1 Reconciliation Dashboard: Drift](../l1/drift.md) —
  the dashboard-side view of what Invariant 2 catches when it fires
  in production.
