# Volume Anomalies

> **What this sheet teaches.** Detect sender-recipient pairs whose money flow spikes outside their rolling baseline. The volume anomalies sheet flags unusual transfer patterns by comparing each 2-day window against the population's mean and standard deviation.

## What you're looking at

A KPI strip at the top shows *Flagged at current σ* — the count of pair-windows meeting the sigma threshold you've set. Below that, a *Pair-Window σ Distribution* bar chart breaks the entire population into sigma buckets, showing where the full dataset sits relative to your threshold. The chart stays unfiltered as you move the slider, so you can see the population shape before deciding how tight to set the cutoff. Finally, a *Flagged Pair-Windows — Ranked* table shows every (sender, recipient, window) that crossed your threshold, ranked by z-score descending (furthest from the population mean at the top). Filters across the top let you narrow the window by date range and adjust the minimum sigma cutoff with the slider.

## How to read the numbers

The sheet reads from the `<prefix>_inv_pair_rolling_anomalies` matview ([materialized view](../_glossary.md#matview--materialized-view)), which computes a 2-day rolling window for every (sender, recipient) pair and calculates its z-score against the population.

- `recipient_account_id`, `recipient_account_name`, `recipient_account_type` — identifying the receiving account. Only leaf internal [accounts](../_glossary.md#account) whose parent [account role](../_glossary.md#account-role) is set qualify; control accounts and sweeps are excluded so genuine signal dominates the population.
- `sender_account_id`, `sender_account_name`, `sender_account_type` — identifying the sending account.
- `window_start`, `window_end` — the 2-day window bounds. For activity on day N, the window covers [N-1, N]. Sparse days (when a pair had no activity the prior day) show a 1-day window.
- `window_sum` — the sum of all posted transaction amounts for this (sender, recipient) pair over the window (in dollars).
- `transfer_count` — the count of distinct transfers in that window.
- `pop_mean`, `pop_stddev` — the population mean and sample standard deviation across every pair-window in the matview. A single scalar pair; every row reads the same values so you can understand where any single window falls relative to the full distribution.
- `z_score` — `(window_sum − pop_mean) / pop_stddev`. How many standard deviations this window is from the population mean. A z-score of 0 means this pair's window sum equals the population average; a z-score of 3 means it's three standard deviations above (or below, if negative).
- `z_bucket` — bucketed z-score for visualization: "0-1 sigma", "1-2 sigma", …, "4+ sigma". The distribution chart groups rows into these buckets so you can spot the tail at a glance.

The *Flagged at current σ* KPI counts rows where `z_score >= <<$pInvAnomaliesSigma>>` (the slider's current value). The *Pair-Window σ Distribution* chart groups the full population by `z_bucket` so you see the shape unfiltered. The *Flagged Pair-Windows* table applies the sigma filter and ranks by `z_score` descending.

## Common patterns

### High z-score on a normally-quiet pair

One row, z-score of 3+ or 4+, a pair that's usually under-the-radar. This is a **genuine anomaly** — the pair's window sum is far outside its population baseline. Drill into the table row to see which transfers fired in that window and whether they're legitimate or suspicious.

### Entire population clustered in "0-1 sigma"

The distribution chart shows almost all pair-windows bunched in the leftmost bucket. Either the seed data has very low variance (all pairs move similarly) or the population is genuinely stable. The sigma slider is intentionally high (default 2) to avoid flagging noise. If your operators expect anomalies but the chart shows the whole population quiet, the institution's sender-recipient patterns may be genuinely stable — no false alarm, just low-volatility movement.

### Pairs flagged across many window dates

The table has dozens of rows for the same (sender, recipient), dates spanning weeks or months. This is **recurring behavior**, not a spike — the pair's transfers consistently exceed the population baseline. It may be legitimate (a high-volume partner that trades frequently) or suspicious (a relationship worth deeper audit). Cross to the Money Trail sheet with the sender or recipient account to trace where the money originated and where it lands.

### Distribution shows tail but z-score KPI is zero

The bar chart clearly renders high-sigma buckets ("3-4 sigma", "4+ sigma") but the *Flagged at current σ* KPI shows zero. The threshold you've set is **tighter than the extreme data points** — you're asking for pairs 4+ sigmas away while the tail sits at 2–3. Drag the sigma slider left to widen the net and flag the data you see in the chart.

## What "no rows" means

A clean anomalies sheet means every (sender, recipient) pair-window in the window sits within your sigma cutoff. That is the expected steady-state — the sheet flags *outliers*, not routine activity. If you see zero rows:

- **Check the sigma slider position.** The default is 2σ (the second bucket from the left). If the *Pair-Window σ Distribution* chart shows data up to 2–3σ but nothing beyond, and the slider is set to 3 or higher, you're outside the data tail — lower the slider to flag what exists.
- **Check the date window filter.** A narrow date range may contain no flagged activity. Widen to trailing 7 or 30 days to see whether anomalies are recurring.
- **Confirm the matview is fresh.** Cross to *App Info* and check the `inv_pair_rolling_anomalies` row in the matview-status table. If `last_refresh_at` is stale and new transfers have posted since, the matview may need a manual refresh.
- **Verify the population has variance.** If the distribution chart shows zero rows at any sigma (the chart itself is empty), the matview may not have computed yet. App Info's matview-status table should show a positive row count for `inv_pair_rolling_anomalies` — if it shows zero, the ETL pipeline hasn't loaded the Investigation schema yet.

## Cross-sheet drills

The Volume Anomalies sheet has no drills defined. The table rows are reference data for further investigation; navigate to other sheets (Money Trail, Account Network) via their anchor or root-transfer controls once you've identified an account pair of interest.

## Related handbook pages

- [Money Trail](money-trail.md) — trace a single transfer's chain from source to destination; useful after you've spotted a suspicious pair on this sheet.
- [Account Network](account-network.md) — visualize the graph of money flows into and out of a single anchor account; complements the pair-centric view here.
- [Recipient Fanout](recipient-fanout.md) — which recipients are fed by unusually many senders; a different angle on anomalous recipient behavior.

---

*First time here? See the [Vocabulary](../_glossary.md) for `matview`, `account_role`, and the other project-specific terms.*
