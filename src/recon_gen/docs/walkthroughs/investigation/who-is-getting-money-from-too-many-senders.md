# Who's getting money from too many senders?

*Question-shaped walkthrough — Investigation dashboard, Recipient Fanout sheet.*

## The story

A model alert fires on an account taking small ACH deposits from a
long list of unrelated counterparties — the classic structuring
shape: many small inbounds funnel into one collection account so each
deposit stays under the reporting threshold. The investigator needs
to ask "show me every account whose inbound side looks like a funnel"
(not just the one the alert pointed at) and rank them worst-first.

## The question

"Whose account is receiving money from an unusual number of distinct
senders this week?"

## Where to look

Open the **Investigation** dashboard, **Recipient Fanout** sheet.

[See it live](https://recon-gen-spec.hotchkiss.io/)

The sheet has two controls in the top-right panel:

- **Date range** — limits the analysis window via `posting`. Default
  is the trailing window the demo plants scenarios into; widen for
  longer-tail patterns, narrow to "this week" for a focused review.
- **Min distinct senders** — the threshold a recipient must clear to
  appear in the table. Default is 5; drag down to 2–3 to surface
  early-stage accumulation, drag up to 10+ to focus on dense clusters.

Three KPIs across the top answer "how big is the problem":

- **Qualifying Recipients** — count of recipient accounts past the
  threshold.
- **Distinct Senders (Union)** — total distinct senders feeding the
  qualifying recipients, deduped across them (an inflated count vs a
  healthy baseline is the concern).
- **Total Inbound** — sum of all inbound dollars across the
  qualifying recipients.

The full-width table below ranks recipients by distinct sender count
descending. Each row carries the recipient account (id, name and
type), its distinct sender count for the window, the inbound-transfer
count and the total inbound amount. The recipient pool is filtered to
customer DDAs and merchant DDAs only — administrative sweeps and
clearing transfers don't dominate the ranking.

{% if vocab.demo.has_investigation_plants %}
??? example "Worked example: {{ vocab.fixture_name }}"
    The bundled `{{ vocab.fixture_name }}` fixture plants a fanout cluster:
    {{ vocab.demo.investigation.fanout_sender_count }} individual depositors,
    each sending two small ACH transfers ($50–$500) to
    **{{ vocab.demo.investigation.anchor.name }}**
    (`{{ vocab.demo.investigation.anchor.id }}`) over the trailing four
    weeks. With the default threshold (5),
    {{ vocab.demo.investigation.anchor.name }} sits at the top of the
    table with **{{ vocab.demo.investigation.fanout_sender_count }}
    distinct senders** and a total inbound around $4,000–$8,000
    (rounded by the seed's RNG).

    Drag the threshold slider down to 2 or 3 — additional accounts may
    appear if the broader L2 instance seed has any incidental inbound
    diversity (merchant DDAs typically will, since their normal sales
    flow involves many distinct customer cards). Drag it up to 10+ —
    {{ vocab.demo.investigation.anchor.name }} stays alone, since the
    rest of the seed's inbound graphs are narrower.
{% endif %}

## What it means

Recipient Fanout is a SHAPE DETECTOR, not a verdict. A high
distinct-sender count is consistent with structuring but also with
plenty of normal patterns:

- A merchant DDA legitimately collects from many distinct customers
  every day.
- A landlord's deposit account legitimately receives from many tenants
  on the first of the month.
- A church or non-profit DDA legitimately receives from many small
  donors.

The investigator's job is to RULE THOSE OUT before treating the
shape as suspicious. The ranking gives you the candidates; the
context (account type, business line, prior history) tells you which
ones to escalate.

A clean fanout finding includes: the recipient's name + account ID,
the time window, the distinct sender count, the total dollar amount
and a one-line reason the shape is or isn't expected for that
account.

## Drilling in

The Recipient Fanout sheet is one of four question-shaped views over
the same base ledger. Once you have a candidate, the next step
depends on what you want to know:

- **"Which sender is the biggest contributor to this fanout?"** →
  Volume Anomalies sheet. Set the date range the same way; the table
  there is per-(sender, recipient, window) so you can spot the
  outsized senders within the cluster.
- **"How does this account exchange money with everyone, not just
  these senders?"** → Account Network sheet. Set the anchor to the
  flagged recipient; the inbound Sankey shows the fanout senders, and
  the outbound Sankey shows where the money goes next.
- **"Show me the actual posting rows behind one of these inbound
  transfers."** → L1 Reconciliation Dashboard, Transactions sheet,
  filtered to the recipient `account_id`.

## Next step

Pick the recipient that doesn't have an obvious benign explanation,
then walk it through Account Network + Money Trail to build the case.
The default shape of a Compliance investigation:

1. Recipient Fanout flags a candidate at the top of the ranked table.
2. Open Account Network, set anchor to that recipient. The inbound
   Sankey shows the fanout senders; the outbound Sankey shows where
   the money goes next.
3. Right-click any inbound row in the touching-edges table to walk
   the anchor to that counterparty and re-render the network around
   the new center.
4. Open Money Trail to walk the chain end-to-end: pick the upstream
   transfer as the chain root, and watch the layering hops (if any)
   surface as a multi-hop Sankey.

The four sheets are ordered to support that sequence — fanout
detection, network sweep, trail walk — without having to leave the
dashboard.

{% if vocab.demo.has_investigation_plants and vocab.demo.investigation.layering_chain and vocab.demo.investigation.anomaly_pair_sender %}
??? example "Worked example: {{ vocab.fixture_name }}"
    Walking the bundled fixture's planted scenario end-to-end:

    1. Recipient Fanout flags
       {{ vocab.demo.investigation.anchor.name }} at
       {{ vocab.demo.investigation.fanout_sender_count }} senders.
    2. Open Account Network, set anchor to
       {{ vocab.demo.investigation.anchor.name }}. The inbound
       Sankey shows the
       {{ vocab.demo.investigation.fanout_sender_count }} individual
       depositors plus the
       {{ vocab.demo.investigation.anomaly_pair_sender.name }}
       anomaly sender. The outbound Sankey shows
       {% for hop in vocab.demo.investigation.layering_chain %}{{ hop.name }}{% if not loop.last %}, {% endif %}{% endfor %}
       — the layering destinations.
    3. Right-click the
       {{ vocab.demo.investigation.anomaly_pair_sender.name }}
       inbound row — the anchor walks to that counterparty and the
       chart re-renders.
    4. Open Money Trail and pick the
       {{ vocab.demo.investigation.anomaly_pair_sender.name }} →
       {{ vocab.demo.investigation.anchor.name }} transfer as the
       chain root; the layering hops surface as a multi-hop Sankey.
{% endif %}

## Related walkthroughs

- [Which sender → recipient pair just spiked?](which-pair-just-spiked.md) —
  the next sheet over. Same base table, but ranks (sender, recipient,
  window) tuples by z-score instead of recipients by distinct sender
  count. Use it to find which sender within a fanout cluster is
  biggest.
- [What does this account's money network look like?](what-does-this-accounts-money-network-look-like.md) —
  the right step after Recipient Fanout flags an account. Anchor the
  Account Network sheet on the flagged recipient to see the full
  counterparty graph on both sides.
- [Where did this transfer actually originate?](where-did-this-transfer-originate.md) —
  the right step when one of the inbound transfers needs a
  provenance walk. Pick the transfer's chain root from the Money
  Trail dropdown.
