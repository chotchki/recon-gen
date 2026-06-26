# Volume Anomalies

> **What this sheet teaches.** Detect sender-recipient pairs whose money flow spikes outside their own rolling baseline. The volume anomalies sheet flags unusual transfer patterns by comparing each 2-day window against the **per-pair** mean and standard deviation across all that pair's history.

## What you're looking at

A KPI strip at the top shows *Flagged at current σ* — the count of pair-windows meeting the sigma threshold you've set. Below that, a *Pair-Window σ Distribution* bar chart breaks the entire population into sigma buckets, showing where the full dataset sits relative to your threshold. The chart stays unfiltered as you move the slider, so you can see the population shape before deciding how tight to set the cutoff. Finally, a *Flagged Pair-Windows — Ranked* table shows every (sender, recipient, window) that crossed your threshold, ranked by z-score descending (furthest from the population mean at the top). A *Window End Date* picker lets you narrow by date range; a *Min sigma* slider adjusts the threshold.

## How to read the numbers

The sheet reads from the `<prefix>_inv_pair_rolling_anomalies` [matview](../_glossary.md#matview--materialized-view), which computes a 2-day rolling window for every (sender, recipient) pair and calculates its z-score against that pair's own historical distribution.

- `recipient_account_id`, `recipient_account_name`, `recipient_account_type` — identifying the receiving account. Only leaf internal [accounts](../_glossary.md#account) whose parent [account role](../_glossary.md#account-role) is set qualify; control accounts and sweeps are excluded so genuine signal dominates the population.
- `sender_account_id`, `sender_account_name`, `sender_account_type` — identifying the sending account.
- `window_start`, `window_end` — the 2-day window bounds. For activity on day N, the window covers [N-1, N]. Sparse days (when a pair had no activity the prior day) show a 1-day window.
- `window_sum` — the sum of all posted transaction amounts for this (sender, recipient) pair over the window (in dollars).
- `transfer_count` — the count of distinct transfers in that window.
- `pop_mean`, `pop_stddev` — the **per-pair** mean and sample standard deviation. Every row for the same (sender, recipient) pair carries the same `pop_mean` / `pop_stddev` — that pair's own historical distribution. Different pairs carry different values. The column names are preserved from the pre-CV global-population shape for backwards compatibility with App2 / audit PDF consumers; semantically they're per-pair statistics.
- `z_score` — `(window_sum − pop_mean) / pop_stddev`. How many standard deviations this window is from the pair's own historical mean. A z-score of 0 means this pair's window sum equals its average; a z-score of 3 means it's three standard deviations above its baseline.
- `z_bucket` — bucketed z-score for visualization: "0-1 sigma", "1-2 sigma", …, "4+ sigma". The distribution chart groups rows into these buckets so you can spot the tail at a glance.

### Per-pair z and the min-n floor

The matview uses a **per-pair PARTITION BY** z formula (introduced in Phase CV, 2026-06-09). For each (sender, recipient) pair the matview computes `AVG(window_sum) OVER (PARTITION BY pair)` and `STDDEV_SAMP(window_sum) OVER (PARTITION BY pair)` — so each window's z-score is measured against its own pair's history, not a global population.

This is the semantically correct shape — a pair's biggest day relative to its own pattern IS the most anomalous — but it requires enough history per pair to compute a meaningful divisor. The matview enforces a **min-n floor**: pairs with fewer than `_INV_MIN_HISTORICAL_WINDOWS=3` total observations get `z_score = 0` regardless of magnitude. This collapses one-shot pairs (no signal) and very-shallow-history pairs (estimate unreliable) into the '0-1 sigma' bucket.

There's a mathematical consequence: under the inclusive PARTITION BY frame, a single outlier's z-score asymptotes at `N / sqrt(N+1)` where N is the pair's window count. For a pair with 20 prior windows + 1 spike, the spike's z caps at ≈4.36. For pairs with longer history (e.g. 90 days of seed data), the cap is higher. This bounds noise: under the pre-CV global-z formula spec_example's top observed z was 8.62 and sasquatch_pr's was 25.84; post-CV, both L2s' top observed z is ≈6.

The *Flagged at current σ* KPI counts rows where `z_score >= <<$pInvAnomaliesSigma>>` (the slider's current value). The *Pair-Window σ Distribution* chart groups the full population by `z_bucket` so you see the shape unfiltered. The *Flagged Pair-Windows* table applies the sigma filter and ranks by `z_score` descending.

## Common patterns

### High z-score on a normally-quiet pair

One row, z-score of 3+ or 4+, a pair that's usually under-the-radar. This is a **genuine anomaly** — the pair's window sum is far outside its population baseline. Drill into the table row to see which transfers fired in that window and whether they're legitimate or suspicious.

### Entire population clustered in "0-1 sigma"

The distribution chart shows almost all pair-windows bunched in the leftmost bucket. Under per-pair z this means most pairs have low day-to-day variance — each window is close to its pair's baseline. The sigma slider's default is 2; widen it left to flag the borderline cases or right to focus on the extreme tail.

Pairs with very shallow history (< 3 windows total) automatically land in '0-1 sigma' due to the matview's min-n floor — there's not enough signal to compute a meaningful z-score. If your operators see a brand-new pair that has only fired once or twice and expect it to surface as anomalous, the floor is doing its job; widen the time window so the pair accumulates enough observations.

### Pairs flagged across many window dates

The table has dozens of rows for the same (sender, recipient), dates spanning weeks or months. This is **recurring behavior**, not a spike — the pair's transfers consistently exceed the population baseline. It may be legitimate (a high-volume partner that trades frequently) or suspicious (a relationship worth deeper audit). Cross to the Money Trail sheet with the sender or recipient account to trace where the money originated and where it lands.

### Distribution shows tail but z-score KPI is zero

The bar chart clearly renders high-sigma buckets ("3-4 sigma", "4+ sigma") but the *Flagged at current σ* KPI shows zero. The threshold you've set is **tighter than the extreme data points** — you're asking for pairs 4+ sigmas away while the tail sits at 2–3. Drag the sigma slider left to widen the net and flag the data you see in the chart.

## What "no rows" means

A clean anomalies sheet means every (sender, recipient) pair-window in the window sits within your sigma cutoff. That is the expected steady-state — the sheet flags OUTLIERS, not routine activity. If you see zero rows:

- **Check the sigma slider position.** The default is 2σ (the second bucket from the left). If the *Pair-Window σ Distribution* chart shows data up to 2–3σ but nothing beyond, and the slider is set to 3 or higher, you're outside the data tail — lower the slider to flag what exists.
- **Check the date window filter.** A narrow date range may contain no flagged activity. Widen to trailing 7 or 30 days to see whether anomalies are recurring.
- **Confirm the matview is fresh.** Cross to *App Info* and check the `inv_pair_rolling_anomalies` row in the Matview Status table. The matview is stale when its `latest_date` lags the base tables' `latest_date` — both sit side by side on that table, so a matview whose `latest_date` trails the base tables means new transfers posted but the matview wasn't refreshed since the last ETL load. A NULL `latest_date` just means a matview with no date dimension NOT staleness.
- **Verify the population has variance.** If the distribution chart shows zero rows at any sigma (the chart itself is empty), the matview may not have computed yet. App Info's matview-status table should show a positive row count for `inv_pair_rolling_anomalies` — if it shows zero, the ETL pipeline hasn't loaded the Investigation schema yet.

## Cross-sheet drills

The Volume Anomalies sheet has no drills defined. The table rows are reference data for further investigation; navigate to other sheets (Money Trail, Account Network) via their anchor or root-transfer controls once you've identified an account pair of interest.

## Related handbook pages

- [Money Trail](money-trail.md) — trace a single transfer's chain from source to destination; useful after you've spotted a suspicious pair on this sheet.
- [Account Network](account-network.md) — visualize the graph of money flows into and out of a single anchor account; complements the pair-centric view here.
- [Recipient Fanout](fanout.md) — which recipients are fed by unusually many senders; a different angle on anomalous recipient behavior.

---

*First time here? See the [Vocabulary](../_glossary.md) for `matview`, `account_role` and the other project-specific terms.*
