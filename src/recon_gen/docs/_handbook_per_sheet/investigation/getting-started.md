# Getting Started

> **What this sheet teaches.** The Investigation Dashboard is a compliance and anti-money-laundering triage surface. It surfaces three question-shaped sheets (recipient fanout, volume anomalies, money trail) that drill back into account and payment reconciliation for row-level evidence, plus an Account Network visualization for peer-graph analysis.

## What you're looking at

A landing page with no visuals or filter controls. The top text box introduces the Investigation Dashboard and its scope: compliance / AML analysis across the shared base ledger. The *Sheets in this dashboard* section below lists the four analysis sheets you can navigate to — each one answers a specific question about account behavior and money-movement patterns. Click any sheet name to jump directly to that analysis.

## How to read the sheets

The Investigation Dashboard consists of four sheets:

- **Recipient Fanout** — Who is receiving money from too many distinct senders? Slide a threshold; the table ranks recipients by inbound sender count. Accounts with wide-funnel networks are higher-risk surfaces for layering typologies.

- **Volume Anomalies** — Which sender-to-recipient pair just spiked above rolling baseline? Rolling 2-day SUM vs. population mean + standard deviation. The distribution chart shows your threshold's position in the full population shape; the table flags only the pairs that exceed your σ cutoff.

- **Money Trail** — Where did this transfer originate, and where does it go? Select a chain root from the dropdown; the Sankey diagram renders source-to-target flow for that chain's legs, and the table lists every hop in sequence.

- **Account Network** — Who does this account exchange money with, in either direction? Select an anchor account; the left Sankey shows inbound counterparties, the right shows outbound. The table below lists every interaction by amount.

## Common workflows on this dashboard

### Recipient flagged; need to understand the network

Start on **Recipient Fanout**. You've pinned a high-value recipient with many senders above the threshold. Click through to **Account Network**, anchor that recipient, and observe the inbound Sankey: which counterparties are the senders? Are they clustered by geography, entity type or time window? Walk the anchor back to each sender and observe THEIR inbound networks — layering chains often have a signature of intermediaries with narrow, linear networks.

### Pair-wise spike detected; is it real or noise?

Start on **Volume Anomalies**. You've spotted a sender-recipient pair whose 2-day rolling sum cleared your σ threshold. The distribution chart shows where that pair's z-score falls in the full population shape — is the spike an outlier, or is the population so noisy that the threshold is picking up normal variance? If you trust the spike, drill to **Money Trail** using the sender-recipient pair's transfer IDs to walk that particular flow backward to its origin.

### Following a transfer end-to-end

Start on **Money Trail**. You know a specific transfer ID that concerns you. Select the root transfer from the dropdown; the Sankey renders its entire forward path (source through settlement and delivery accounts). The table lists every leg with posting timestamps — use it to spot missing or delayed legs. For each leg, you can drill back into Payment Reconciliation to see the raw posting detail.

### Investigating account behavior at scale

Start on **Account Network**. You've identified an anchor account of interest. The two Sankeys show its peer graph (who sends to it, who receives from it). The table lists every edge with amounts. If the anchor is an aggregator or custodian, you'll see many inbound edges and few outbound (funds pooling). If it's a pass-through, you'll see balanced in/out. Anomalies (unexpected concentration, new counterparties, counterparties with zero other touching accounts) are your drill points.

## Where to start

If you're new to the dashboard, decide which question fits your investigation:

- **"This account is receiving from too many places."** → Go to **Recipient Fanout**.
- **"This pair just moved way more than usual."** → Go to **Volume Anomalies**.
- **"I need to trace a specific transfer."** → Go to **Money Trail**.
- **"I want to understand who this account talks to."** → Go to **Account Network**.

If you have no specific hypothesis, start with **Recipient Fanout** at the default threshold (minimum 5 distinct senders). That sheet shows your institution's widest-funnel recipients — accounts concentrating inbound from many counterparties. Those accounts are your highest-risk surface for money-laundering typologies like layering.

## Related handbook pages

- [Recipient Fanout](fanout.md) — deep dive into the sender-concentration question.
- [Volume Anomalies](anomalies.md) — time-series spikes and baseline drift.
- [Money Trail](money-trail.md) — following a transfer from origin to final posting.
- [Account Network](account-network.md) — peer-graph analysis and counterparty discovery.

---

*First time here? See the [Vocabulary](../_glossary.md) for `account_role`, `rail`, `chain`, `template` and other project-specific terms.*
