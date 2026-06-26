# Recipient Fanout

> **What this sheet teaches.** Which accounts are receiving inbound transfers from an unusually large number of distinct senders. This is a recipient-concentration pattern: money is flowing into a single receiver from many dispersed sources, a behavior worth flagging for compliance review.

## What you're looking at

The sheet opens on a strip of three KPIs across the top — *Qualifying Recipients* (distinct recipient accounts meeting your threshold), *Distinct Senders (Union)* (the total population of unique senders feeding those recipients) and *Total Inbound* (the aggregate dollar value of money flowing in). Below sits a full-width table titled *Recipient Fanout — Ranked*, listing one row per qualifying recipient, sorted by the number of distinct senders from widest funnel to narrowest. Each row shows the recipient's account ID and name, how many distinct senders are feeding that recipient, how many distinct transfers came in and the total inflow dollar amount. Two controls sit at the top: a *Date Range* picker (narrows `posted_at`) and a *Min distinct senders* slider (the fanout threshold).

## How to read the numbers

The sheet's visuals bind to a dataset derived directly from the `transactions` base table, filtered to completed recipient and sender legs. The dataset performs a two-CTE join (inflows × outflows matching on `transfer_id`) to pair every recipient leg with its senders, then computes per-recipient distinct sender count. The threshold you set on the slider pushes down into the dataset's SQL via the `<<$pInvFanoutThreshold>>` parameter — rows where `distinct_senders < threshold` are filtered out before the data leaves the database.

The three KPIs are:

- **Qualifying Recipients** — `COUNT(DISTINCT recipient_account_id)` over all rows meeting the threshold. If this is zero, no recipient in your date window has as many distinct senders as your slider requires; lower the threshold or widen the date range.
- **Distinct Senders (Union)** — `COUNT(DISTINCT sender_account_id)` across ALL the qualifying recipients' rows, deduped. This is NOT the sum of the per-recipient sender counts in the table (a sender feeding two recipients counts once here, but appears on two rows). This KPI answers "how many unique source accounts are feeding this fanout population as a whole."
- **Total Inbound** — `SUM(amount)` across all qualifying recipient legs. The dataset computes amount per (recipient leg, sender leg) pair; to avoid double-counting when a single inflow receives from multiple senders, it divides by the per-(recipient, transfer) row count before summing.

The table reads from the joined inflows + outflows CTEs, aggregated `GROUP BY recipient_account_id`. Each row's columns are:

- `recipient_account_id`, `recipient_account_name`, `recipient_account_type` — the receiving account's identity and role (e.g. `CustomerDDA`, `MerchantDDA`).
- *Senders Feeding This Recipient* — `MAX(distinct_senders)` per recipient. `distinct_senders` is computed once per recipient by a `COUNT(DISTINCT sender_account_id)` GROUP BY in the dataset's `distinct_per_recipient` CTE, so it's constant within a recipient and the table's `MAX` just surfaces that single value.
- Column 2 (implicit column header in the app) — `COUNT(DISTINCT transfer_id)` — how many distinct transfers touched this recipient.
- Column 3 — `SUM(amount)` — total inflow to this recipient in the window.

The filter — `WHERE distinct_senders >= <<$pInvFanoutThreshold>>` — lives in the dataset SQL and is evaluated before aggregation.

## Common patterns

### Single high-fanout recipient, hundreds of senders

One row at the top of the table with a very large "Senders Feeding This Recipient" value; *Qualifying Recipients* KPI shows 1; *Distinct Senders (Union)* KPI also shows roughly that same count. This is a **classic recipient concentration** — one account is a hub receiving from a large dispersed sender population. The intent matters: is this a sweep account, a payables consolidation point or a known correspondent? Check the *Account* (recipient) name and account type (`recipient_account_type`). Cross-reference with your compliance matrix and the business function that account serves. If the account should not be a hub, flag it for the AML/compliance team with the sender account list (drill into individual rows if needed).

### Moderate fanout across many recipients

*Qualifying Recipients* KPI shows 5–20 rows; *Distinct Senders (Union)* is much larger than any single row's sender count. This is a **distributed fanout pattern** — money is coming in from many sources and flowing to many destinations. The structure could be legitimate (multi-customer sweep into separate ledgers, multiple counterparty feeds), or it could be structuring (intentionally splitting flows to evade monitoring). Look at the table's time distribution — are the transfers clustered on certain days, or steady daily? Are the senders and recipients in the same institution, or is there cross-institution movement? The pattern name doesn't tell you the risk; your compliance matrix and transaction narrative do.

### Fanout with zero qualifying recipients

*Qualifying Recipients* KPI is zero. This means no recipient has at least *N* distinct senders in the current date window and threshold. This is NOT a data problem — it's often the right answer. Check three things:

- **Date window.** If you've picked a very narrow range (e.g., a single day), a real fanout pattern may just fall outside it. Widen to the trailing 7 days.
- **Threshold setting.** If your slider is at 10+ distinct senders and the institution's legitimate recipient count is small, zero is expected. Lower the threshold to 3–5 and re-examine.
- **Freshness.** Cross to the *App Info* sheet and check the *Matview Status* table. The dataset reads `transactions` directly, not a [matview](../_glossary.md#matview--materialized-view) (materialized view), so stale matviews are not the issue here. But if `transactions` has no recent rows (e.g., `business_day_start` is 2 weeks old), the underlying data may simply be stale.

## Cross-sheet drills

This sheet defines no drill actions. Use the account name and sender details to navigate to other dashboards (e.g., Account Reconciliation's Drift sheet, Payment Reconciliation's Money Trail) for row-level investigation.

## Related handbook pages

- [Volume Anomalies](anomalies.md) — complementary pattern: which sender → recipient pairs spiked above rolling baseline?
- [Money Trail](money-trail.md) — trace a single transfer's full hop-by-hop path.
- [Account Network](account-network.md) — visualize the bidirectional graph of who pays whom, centered on a single account.

---

*First time here? See the [Vocabulary](../_glossary.md) for `internal` / `external` scope, `account_role` and other project-specific terms.*
