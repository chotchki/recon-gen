# Getting Started

> **What this dashboard is for.** The Investigation app answers compliance and anti-money-laundering questions about account behavior, not ledger integrity. It surfaces recipient-fanout patterns, volume anomalies, money-movement trails, and peer-to-peer account networks across your shared base ledger.

## How this dashboard is organized

This dashboard comprises four analysis sheets plus a diagnostic canary. The Getting Started sheet (this page) has no visuals or filters — it exists to frame the app's vocabulary and guide you to the question-shaped sheet that matches your investigation focus. Each of the four main sheets drills back into Account Reconciliation (L1 Dashboard) or Payment Reconciliation (L2 Flow Tracing) when you need row-level evidence.

The sheets are:

- **Recipient Fanout** — Who is receiving money from an unusual number of distinct senders? Set a threshold using the slider; the table ranks qualifying recipients by sender count (funnel width). Rows with anomalously broad recipient networks may signal layering or structuring.

- **Volume Anomalies** — Which sender-to-recipient pair just spiked above its rolling 2-day baseline? The sheet compares each pair's recent SUM against the population mean and standard deviation. Drag the σ slider to flag the tail. Single-day spikes that fall outside historical norms warrant review.

- **Money Trail** — Where did this transfer actually originate, and where does it go? Pick a chain root from the dropdown; the Sankey diagram renders the source-to-target flow ribbons for that chain's legs, and the hop-by-hop table lists every edge in sequence. Follow a transfer backward to its origin or forward to its destination.

- **Account Network** — Who does this account exchange money with, in either direction? Pick an anchor account; the left Sankey shows counterparties sending IN, the right shows counterparties receiving OUT. The touching-edges table below lists every interaction by amount. Click a source or target node to walk the anchor over to that counterparty and re-render the diagram.

## Common workflows on this dashboard

### Recipient flagged; need to understand the network

Start on **Recipient Fanout**. You've pinned a high-value recipient with many senders above the threshold. Click through to **Account Network**, anchor that recipient, and observe the inbound Sankey: which counterparties are the senders? Are they clustered by geography, entity type, or time window? Right-click the touching-edges table to walk the anchor back to each sender and observe *their* inbound networks — layering chains often have a signature of intermediaries with narrow, linear networks.

### Pair-wise spike detected; is it real or noise?

Start on **Volume Anomalies**. You've spotted a sender-recipient pair whose 2-day rolling sum cleared your σ threshold. The distribution chart on the right shows you where that pair's z-score falls in the full population shape — is the spike an outlier, or is the population so noisy that the threshold is picking up normal variance? If you trust the spike, drill to **Money Trail** using the sender-recipient pair's transfer IDs to walk that particular flow backward to its origin.

### Following a transfer end-to-end

Start on **Money Trail**. You know a specific transfer ID that concerns you. Pick the root transfer from the dropdown; the Sankey renders its entire forward path (source through settlement, aggregate, and delivery accounts). The hop-by-hop table on the right lists every leg with posting timestamps — use it to spot missing or delayed legs (a gap in the depth sequence). For each leg, you can drill back into Payment Reconciliation to see the raw posting detail.

### Investigating account behavior at scale

Start on **Account Network**. You've identified an anchor account of interest. The two Sankeys show its peer graph (who sends to it, who receives from it). The table lists every edge with amounts. If the anchor is an aggregator or custodian, you'll see many inbound edges and few outbound (funds pooling). If it's a pass-through, you'll see balanced in/out. Anomalies (unexpected concentration on one side, new counterparties, counterparties with zero other touching accounts) are your drill points.

## Where to start

If you're new to the dashboard, decide which question fits your investigation:

- **"This account is receiving from too many places."** → Go to **Recipient Fanout**.
- **"This pair just moved way more than usual."** → Go to **Volume Anomalies**.
- **"I need to trace a specific transfer."** → Go to **Money Trail**.
- **"I want to understand who this account talks to."** → Go to **Account Network**.

If you have no specific hypothesis, start with **Recipient Fanout** at the default threshold (minimum 5 distinct senders). That sheet shows your institution's widest-funnel recipients — accounts concentrating inbound from many counterparties. Those accounts are your highest-risk surface for money-laundering typologies like layering.

The sheet descriptions in the *Sheets in this dashboard* section above (on this very page) also link you directly — click the sheet name to jump there.

## Related handbook pages

- [Recipient Fanout](recipient-fanout.md) — deep dive into the sender-concentration question.
- [Volume Anomalies](volume-anomalies.md) — time-series spikes and baseline drift.
- [Money Trail](money-trail.md) — following a transfer from origin to final posting.
- [Account Network](account-network.md) — peer-graph analysis and counterparty discovery.

---

*First time here? See the [Vocabulary](../_glossary.md) for `account_role`, `rail`, `chain`, `internal` / `external` scope, and other project-specific terms.*
