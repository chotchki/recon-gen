# Which sender → recipient pair just spiked?

*Question-shaped walkthrough — Investigation dashboard, Volume Anomalies sheet.*

## The story

A counterparty that's been wiring routine amounts to the same
beneficiary for weeks suddenly sends a wire that's an order of
magnitude bigger. By itself the wire is unremarkable — well within
the bank's daily limit, fully authorized, posts cleanly. What makes
it interesting is that **this pair**, on a normal week, doesn't move
that kind of money. The investigator needs a way to spot pair-windows
that just spiked above their own baseline, separate from the
absolute-dollar checks the bank already runs.

## The question

"Which (sender, recipient) pair just moved a lot more money than
this pair usually moves?"

## Where to look

Open the **Investigation** dashboard, **Volume Anomalies** sheet.

The sheet has two controls in the top-right panel:

- **Date range** — limits the analysis window via `window_end`.
  Default covers the trailing month; narrow to "this week" for a
  focused review, widen for back-tests.
- **σ threshold** — the cutoff above which a pair-window appears in
  the KPI + table. Default is 2σ; drag to 1σ to surface marginal
  spikes (and a lot of noise), or to 3–4σ to focus on the extreme
  tail.

Three visuals:

- **Flagged Pair-Windows KPI** (top-left, third-width) — count of
  (sender, recipient, window-end) tuples past the σ threshold.
- **Pair-Window σ Distribution** (top-right, two-thirds-width) — a
  bar chart of every pair-window in the population bucketed into 5
  bands (`0-1 sigma`, `1-2 sigma`, `2-3 sigma`, `3-4 sigma`,
  `4+ sigma`). The chart **intentionally ignores the σ filter** so the
  distribution shape stays visible — your cutoff lands in context.
- **Flagged Pair-Windows — Ranked** (full-width below) — table of the
  flagged tuples sorted by z-score descending. Each row carries
  sender + recipient names, the window end date, the rolling 2-day
  SUM, the z-score, and the σ bucket label.

## The math, briefly

The matview `{{ l2_instance_name }}_inv_pair_rolling_anomalies` computes, per
(sender, recipient) pair, a 2-day rolling SUM (today + yesterday's
transfer amounts). It then computes the **population mean** and
**sample standard deviation** of that rolling SUM across every
pair-window in the matview, and projects each row's z-score
(`(value - population_mean) / population_stddev`) plus a 5-band
bucket label.

So the threshold is "this pair moved enough money in a 2-day window
that, compared to every other pair-window the bank saw, this one is
N standard deviations out." A pair that always moves $1M will not
flag at 2σ unless this particular window is much more than $1M; a
pair that usually moves $300 will flag at 2σ if it suddenly moves
$5,000.

The window length is fixed at 2 days for now (changing it would
require multiple matviews or a generate_series at dataset time —
deferred to a later phase).

The matview **does not auto-refresh**. After every ETL load, the
operator runs
`REFRESH MATERIALIZED VIEW {{ l2_instance_name }}_inv_pair_rolling_anomalies;`
— see [Refresh contract](../../Schema_v6.md#refresh-contract).
A skipped refresh means the z-scores reflect yesterday's population.

{% if vocab.demo.has_investigation_plants and vocab.demo.investigation.anomaly_pair_sender %}
??? example "Worked example: {{ vocab.fixture_name }}"
    The bundled `{{ vocab.fixture_name }}` fixture plants an anomaly pair:
    **{{ vocab.demo.investigation.anomaly_pair_sender.name }}** wires
    {{ vocab.demo.investigation.anchor.name }} routine amounts
    ($300–$700) for eight contiguous days, then a single $25,000 wire
    on day −10. With the default 2σ threshold:

    - **KPI** — typically 1 (the spike day is the lone flagged
      pair-window in the planted scenario; incidental flags from the
      broader baseline seed may add 1–2 more).
    - **Distribution chart** — the leftmost bucket (`0-1 sigma`)
      holds almost the entire population; the spike sits alone in
      the rightmost bucket (`4+ sigma`).
    - **Table** — {{ vocab.demo.investigation.anomaly_pair_sender.name }}
      → {{ vocab.demo.investigation.anchor.name }} for the spike-day
      window at the top, with a z-score well above 4 and a 2-day
      SUM of $25,000+ vs. the baseline ~$500.

    Drag σ down to 1 — the table fills with marginal flags, mostly
    incidental to the broader baseline. Drag σ up to 4 — the table
    empties back down to just the spike.
{% endif %}

## What it means

Volume Anomalies is a **deviation detector**. A high z-score is
consistent with money laundering but also with plenty of normal
patterns:

- A merchant's monthly settlement landing on a single day after a
  long quiet stretch.
- A customer's annual bonus / tax refund / insurance settlement
  hitting a DDA that normally sees small payroll deposits.
- A counterparty that's been quiet for weeks resuming normal
  activity in a single batch.

The investigator's job is to **rule those out** before treating the
spike as suspicious. The chart is the first step — if the rest of
the population is dense and your flag sits alone in the right tail
(as the demo's $25,000 spike does), that's a stronger signal than a
flag at the edge of a populated bucket.

A clean anomaly finding includes: the (sender, recipient) names +
account IDs, the window end date, the 2-day SUM, the z-score, and a
one-line reason the deviation is or isn't expected for the pair.

## Drilling in

Once you have a flagged pair-window, the next step depends on what
you want to know:

- **"Show me every transfer between this pair, not just the spike."** →
  Account Network sheet. Set the anchor to either side; the touching-
  edges table lists every edge, and the directional Sankeys split
  inbound from outbound.
- **"Where did this specific transfer come from?"** → Money Trail
  sheet. Pick the chain root (typically the spike transfer itself if
  it's chain-rooted, or the parent_transfer_id chain it sits on).
- **"Show me the underlying posting rows."** → L1 Reconciliation
  Dashboard, Transactions sheet, filtered to the sender or
  recipient `account_id`.

## Next step

The fastest path from a 4σ flag to "is this a SAR or not" usually
goes:

1. Confirm the spike on this sheet's table — copy the sender,
   recipient, window end, and z-score for the case file.
2. Switch to Account Network with the recipient as the anchor —
   confirm whether the rest of the pair's history is consistent
   (regular wires) or sparse (one-shot relationship).
3. Switch to Money Trail with the spike transfer's chain root —
   confirm whether the money moved on after landing (layering) or
   stayed put.
4. Drop into the L1 Transactions sheet for the row-level postings
   if the case needs evidence at the leg level.

If the spike is the only data point — sender appeared once, money
moved on the same day to a downstream account — it's a Money Trail
case. If the spike is one of many irregular transfers from the same
pair, it's an Account Network case. Volume Anomalies is the entry
point, not the destination.

## Related walkthroughs

- [Who's getting money from too many senders?](who-is-getting-money-from-too-many-senders.md) —
  the previous sheet. A pair-window spike on a recipient who *also*
  shows up there is a stronger signal than either alone.
- [Where did this transfer actually originate?](where-did-this-transfer-originate.md) —
  the right step when the spike transfer is part of a chain. Money
  Trail walks the chain end-to-end.
- [What does this account's money network look like?](what-does-this-accounts-money-network-look-like.md) —
  the right step when you need to see the full graph around the
  recipient (or sender) of the flagged pair.
