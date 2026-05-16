# What do I do when the demo passes but my prod data fails?

*Engineering walkthrough — Data Integration Team. Debug.*

## The story

The demo dashboards work. You ran the demo flow (`schema apply --execute`, `data apply --execute`, `data refresh --execute`, `json apply --execute`), opened the four
L2-fed dashboards (L1 Reconciliation, L2 Flow Tracing,
Investigation, Executives), and saw the planted exception
scenarios light up the way they should. You then wrote your own
ETL against your own upstream feed, loaded a slice into the same
`{{ l2_instance_name }}_transactions` and `{{ l2_instance_name }}_daily_balances` tables, and
the dashboards look *off* — KPIs at zero where they shouldn't be,
KPIs spiking where they shouldn't, visuals rendering "N/A" where
there should be values.

Almost every "demo works, prod doesn't" failure traces back to a
small set of root causes. This walkthrough is organized by
*symptom* — what you're seeing on the dashboard — so you can jump
to the matching diagnosis and check.

## The question

"My data is loaded but the dashboards don't look right. Where do
I start?"

## Where to look

Start at the symptom. Each section below names the visual
behavior, the most-likely root cause, and a one-shot SQL or CLI
check to confirm.

If a symptom matches more than one section, work top to bottom —
the earlier sections are more common and have cheaper checks.

## What you'll see (and what it means)

### Symptom 1 — "Every KPI on a sheet shows 0; the table is empty"

**Most likely**: the date filter on the sheet excludes everything
your load covers. Sheets default to a recent window (typically
the last 7 days for the L1 dashboard) and your load may have used
`posting` / `business_day_start` values outside that window.

**Check**:

```sql
SELECT MIN(business_day_start), MAX(business_day_start), COUNT(*)
FROM {{ l2_instance_name }}_transactions
WHERE -- your scope filter, e.g.,
      account_id LIKE 'your-prefix-%';
```

If the date range is older than the sheet's default window, either
adjust the date filter on the sheet (top of the page) or backfill
your load with `business_day_start` values inside the dashboard's
window.

### Symptom 2 — "An L1 KPI shows 0 but I know exceptions exist in my data"

**Most likely**: a `rail_name` value in your data isn't in the
canonical L2 vocabulary, so dataset SQL filters reject it — or, for
the L1 net-zero classification specifically, the row is a single-
leg type (`sale` or `external_txn`), which the schema flags as
`expected_net_zero = 'not_expected'` and the check excludes by
intent.

**Check 1 — values in your data vs the L2 vocabulary**:

```sql
SELECT rail_name, COUNT(*)
FROM {{ l2_instance_name }}_transactions
WHERE -- your scope filter
GROUP BY rail_name
ORDER BY COUNT(*) DESC;
```

Compare against the `rail_name` values your L2 instance
declares (open the L2 instance YAML's `transfer_templates:` and
`rails:` blocks; the union of declared `rail_name` values is
your canonical set). Anything not in that set surfaces unfiltered
in raw views like the L1 Transactions sheet, but type-scoped
checks (drift split, limit breach, aging) won't fire on it.

**Check 2 — Drift / Net-Zero specifically**: query the L1 Drift
view directly to see which (account, day) pairs are flagged:

```sql
SELECT account_id, business_day_start, money, recomputed, drift
FROM {{ l2_instance_name }}_drift
WHERE -- your scope filter
ORDER BY ABS(drift) DESC;
```

The drift view subtracts `recomputed` (cumulative SUM of
`amount_money`) from `money` (stored EOD value). A non-zero row
here is a real drift; an empty result on data you know is broken
usually means your `rail_name` slipped through the canonical
set and the matview filter dropped it.

### Symptom 3 — "A visual cell shows N/A or a column is blank"

**Most likely**: the visual reads a metadata key the rows don't
carry. Common when a new dataset is wired up against historical
rows that pre-date a key, or when an upstream feed inconsistently
populates an optional key.

**Check**: pick a metadata key the visual references — say
`card_brand` — and count rows missing it:

```sql
SELECT COUNT(*) AS rows_missing_key
FROM {{ l2_instance_name }}_transactions
WHERE rail_name = 'sale'
  AND -- your scope filter
  AND NOT JSON_EXISTS(metadata, '$.card_brand');
```

A non-zero count means the visual will render N/A for those rows.
Either backfill the key (one-shot UPDATE, see the metadata-key
walkthrough) or make the visual filter to rows that have it.

### Symptom 4 — "L1 Drift KPI fires unexpectedly"

**Most likely**: your `{{ l2_instance_name }}_daily_balances.money` value
disagrees with the cumulative SUM of `amount_money` in
`{{ l2_instance_name }}_transactions`. Three sub-causes, in order of frequency:

1. **Sign-flip on one leg** — your upstream uses opposite sign
   convention from ours and the projection caught most legs but
   missed one branch.
2. **Missing posting** — the balance feed lands postings that
   never made it to the transactions feed (or vice versa).
3. **`business_day_start` mismatch** — the balance row's
   `business_day_start` doesn't line up with the
   `business_day_start` your transactions used. Common when one
   feed snapshots at midnight UTC and the other at a local-time
   EOD.

**Check**: the L1 drift view does this recompute internally; run
it scoped to the offending account-day to see the magnitude:

```sql
-- Substitute your account_id and business_day_start.
SELECT
    db.money                                         AS stored,
    COALESCE(SUM(t.amount_money), 0)                 AS recomputed,
    db.money - COALESCE(SUM(t.amount_money), 0)      AS drift
FROM {{ l2_instance_name }}_daily_balances db
LEFT JOIN {{ l2_instance_name }}_transactions t
  ON t.account_id          = db.account_id
 AND t.business_day_start <= db.business_day_start
 AND t.status              = 'Posted'
WHERE db.account_id          = 'your-account-id'
  AND db.business_day_start  = DATE 'your-date'
GROUP BY db.money;
```

The sign of `drift` tells you which side is wrong:
positive = stored balance is higher than the postings explain
(missing debit posting, or a credit posting got dropped);
negative = the opposite.

For an interactive view of the same recompute scoped to one
account-day, open the L1 Reconciliation Dashboard's **Daily
Statement** sheet and pick the offending `(account_id,
business_day_start)`. The Drift KPI shows the same number this
query returns, and the Transaction Detail table shows every leg
the recompute summed — side-by-side with the stored opening and
closing balances. See
[How do I validate a single account-day after a load?](how-do-i-validate-a-single-account-day.md)
for the screen-level walkthrough.

### Symptom 5 — "Investigation Money Trail returns nothing for my chain root"

**Most likely**: the `transfer_parent_id` chain has a gap. The
Money Trail sheet relies on the `WITH RECURSIVE` walk over
`transfer_parent_id`. If any link is NULL where it shouldn't be,
the trace stops short.

**Check**: run Invariant 3 from the validation walkthrough scoped
to your subset:

```sql
SELECT t.transfer_id, t.rail_name, t.transfer_parent_id
FROM {{ l2_instance_name }}_transactions t
WHERE -- your scope filter, e.g., a merchant_id metadata key
      JSON_VALUE(t.metadata, '$.merchant_id') = 'your-merchant-id'
  AND t.rail_name IN ('payment', 'settlement', 'sale')
  AND (
      t.transfer_parent_id IS NULL
      OR NOT EXISTS (
          SELECT 1 FROM {{ l2_instance_name }}_transactions p
          WHERE p.transfer_id = t.transfer_parent_id
      )
  );
```

Rows here are gaps. NULL means the link was never written
(common projection bug). Non-NULL but missing parent means the
parent landed in a different load batch and got cut by your
window filter.

### Symptom 6 — "A two-leg transfer doesn't net to zero in L1 Drift"

**Most likely**: one of the legs has `status = 'Posted'` and the
other has `status = 'Failed'` (or some third value the schema
doesn't recognize). The drift recompute filters
`WHERE status = 'Posted'` before summing, so a single-leg "Posted"
looks unbalanced.

**Check**:

```sql
SELECT transfer_id, status, COUNT(*), SUM(amount_money)
FROM {{ l2_instance_name }}_transactions
WHERE transfer_id IN ( -- the offending transfer_ids
)
GROUP BY transfer_id, status;
```

If a transfer has mixed statuses, the schema's expectation is that
both legs share status. Pick the right one (usually `Failed` for
both if the transfer was rejected; `Posted` for both if it
posted) and republish.

## Drilling in

A few patterns that recur across symptoms:

- **Window filters on the load are the #1 cause of "missing
  parent" / "missing balance" failures.** When in doubt, expand
  your load window to cover the longest expected chain age (5
  business days for ACH, 30 days for unsettled sales).
- **`status` enum drift is the #1 cause of unexpected
  exceptions.** Anything that's not `Posted` MUST map to
  `Pending` or `Failed`. A fourth value (`void`, `reversed`,
  arbitrary text) lands rows that downstream views can't
  classify.
- **Clock skew between feeds is the #1 cause of L1 Drift KPI
  surprises.** Standardize `posting` and `business_day_start` on a
  single timezone before writing — don't let two feeds disagree
  on what "today" means.

## Next step

Once you've identified the root cause:

1. **Fix it in the projection, not in a one-shot patch.** A
   patched-up data state without a fixed projection regresses on
   the next load.
2. **Re-run the three pre-flight invariants** from the
   [validation walkthrough](how-do-i-prove-my-etl-is-working.md).
   They catch most of the symptoms above before the dashboards
   see them.
3. **Add a regression query for your specific failure** to your
   ETL DAG. The pre-flight covers universal invariants; your
   feed has its own per-source invariants worth pinning.
4. **If you can't find the root cause**, capture: (a) one
   offending row from your feed, (b) the pre-flight query result
   that caught it, (c) the dashboard state. The combination is
   what someone needs to help you triage.

## Related walkthroughs

- [How do I populate `{{ l2_instance_name }}_transactions` from my core banking system?](how-do-i-populate-transactions.md) —
  the foundational projection that most fixes go back to.
- [How do I prove my ETL is working before going live?](how-do-i-prove-my-etl-is-working.md) —
  the universal pre-flight checks. Most symptoms here are
  invariant violations the pre-flight would have caught.
- [How do I tag a force-posted external transfer correctly?](how-do-i-tag-a-force-posted-transfer.md) —
  the `origin` tag covers Symptom 4's drift surprises around
  Fed-statement ingest.
- [How do I add a metadata key without breaking the dashboards?](how-do-i-add-a-metadata-key.md) —
  Symptom 3 is most often a metadata-key contract violation.
- [Schema_v6 → minimum viable feed](../../Schema_v6.md#etl-contract-minimum-viable-feed) —
  the column-by-column failure modes are the source-of-truth for
  the symptoms above.
- [Investigation: Where did this transfer originate?](../investigation/where-did-this-transfer-originate.md) —
  the analyst-side traversal that depends on Symptom 5's
  `transfer_parent_id` chain being intact.
- [L1 Reconciliation Dashboard: Drift](../l1/drift.md) —
  the analyst-side view of Symptom 4's drift KPI spike.
