# Investigation Handbook

*AML triage and provenance walks for the Compliance / Investigation
team. Currently rendered against **{{ vocab.institution.name }}**
({{ l2_instance_name }}).*

This handbook backs the **Investigation** dashboard — the
compliance / AML view of {{ vocab.institution.name }}. Each entry
here is framed around the investigative question an analyst opens
with, and walks them from a typed question to row-level evidence on
the same shared base ledger that the L1 dashboard reads.

## The team

{{ vocab.institution.name }}'s Investigation team sits between
Treasury (GL Recon) and the regulator. Their day is reactive — a SAR draft, a
counterparty referral, a model alert — and each case has the same
shape: pose a question about a person, a pair, or a transfer; pull
the rows that answer it; preserve the chain that ties evidence back
to the underlying postings.

Unlike L1 Reconciliation (a continuous matview-driven exception
surface read in a fixed morning rotation) and L2 Flow Tracing (the
integrator's runtime evidence map for every declared Rail / Chain /
TransferTemplate), Investigation is **question-shaped**.
Four sheets, four questions, in no particular order:

- *Recipient Fanout* — who is receiving money from too many distinct
  senders?
- *Volume Anomalies* — which sender → recipient pair just spiked
  above its rolling baseline?
- *Money Trail* — where did this transfer actually originate, and
  where does it go?
- *Account Network* — what does this account's money network look
  like, on either side?

The dashboard reads from the same `{{ l2_instance_name }}_transactions` base table
that L1 Reconciliation and L2 Flow Tracing read, plus two
materialized views (`inv_pair_rolling_anomalies` and
`inv_money_trail_edges`) that pre-compute the rolling-window
statistics and recursive chain walk respectively. See
[Materialized views](../Schema_v6.md#the-layered-model) for the
refresh contract — these matviews **do not auto-refresh**, so a
skipped REFRESH after ETL load means the anomaly z-scores and chain
edges lag the source data.

## The investigator's posture

The walkthroughs below are organized around the question an analyst
holds in their head when they open the dashboard:

- *Whose account looks like a collection point?* → Recipient Fanout
- *Did anything just spike this week?* → Volume Anomalies
- *Where did this specific transfer come from?* → Money Trail
- *Show me everything touching this account.* → Account Network

The four sheets are deliberately disjoint — pick the one shaped like
your question. Many cases pivot through several of them: a Recipient
Fanout hit on an account becomes a Money Trail walk on its largest
inbound transfer, then an Account Network sweep around the same
anchor to understand the full counterparty graph. Each walkthrough
flags those natural transitions at the bottom.

## Dataflow — which datasets feed which sheets

{{ diagram("dataflow", app="investigation") }}

## The four sheets

<p class="snb-section-label">One question per sheet — pick by the shape of your question</p>

<div class="snb-card-grid">
  <a class="snb-card" href="../../walkthroughs/investigation/who-is-getting-money-from-too-many-senders/">
    <h3>Who's Getting Money from Too Many Senders?</h3>
    <p>Rank recipients by their distinct sender count. Drag the threshold slider to control where "too many" starts. The fanout-cluster shape — many small inbounds → one account — is a classic structuring footprint.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/investigation/which-pair-just-spiked/">
    <h3>Which Sender → Recipient Pair Just Spiked?</h3>
    <p>Rolling 2-day SUM per (sender, recipient) pair vs. the population mean / standard deviation, exposed as a per-row z-score. σ slider sets the cutoff; the distribution chart shows the full population so the cutoff lands in context.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/investigation/where-did-this-transfer-originate/">
    <h3>Where Did This Transfer Actually Originate?</h3>
    <p>Pick a chain root from the dropdown — the Sankey renders that chain's source-to-target ribbons; the hop-by-hop table beside it lists every edge ordered by depth. Layering chains and split-deposit funnels surface here.</p>
  </a>
  <a class="snb-card" href="../../walkthroughs/investigation/what-does-this-accounts-money-network-look-like/">
    <h3>What Does This Account's Money Network Look Like?</h3>
    <p>Pick an anchor account — the LEFT Sankey shows counterparties sending money INTO the anchor; the RIGHT Sankey shows the anchor sending money OUT. Right-click any table row to walk the anchor to the counterparty and re-render around the new center.</p>
  </a>
</div>

## What you'll see in the demo

{% if vocab.demo.has_investigation_plants %}
The bundled `{{ vocab.fixture_name }}` fixture plants three
converging scenarios on a single anchor account,
**{{ vocab.demo.investigation.anchor.name }}**
(`{{ vocab.demo.investigation.anchor.id }}`), so every sheet has a
non-empty answer to its question — and the sheets connect:

- **Fanout cluster** —
  {{ vocab.demo.investigation.fanout_sender_count }} individual
  depositors each ACH 2 small amounts to
  {{ vocab.demo.investigation.anchor.name }}. Recipient Fanout flags
  the anchor at the default 5-sender threshold; the table ranks it at
  the top with {{ vocab.demo.investigation.fanout_sender_count }}
  distinct senders.
{% if vocab.demo.investigation.anomaly_pair_sender %}
- **Anomaly pair** —
  {{ vocab.demo.investigation.anomaly_pair_sender.name }} wires
  {{ vocab.demo.investigation.anchor.name }} routine amounts
  ($300–$700) for eight days, then a single $25,000 wire on day −10.
  Volume Anomalies flags that pair-window past the default 2σ
  threshold; the σ Distribution chart shows the spike sitting alone
  in the right-tail bucket.
{% endif %}
{% if vocab.demo.investigation.layering_chain %}
- **Money trail** — the same upstream wire that drives the anomaly
  continues as a multi-hop layering chain:
  {% if vocab.demo.investigation.anomaly_pair_sender %}{{ vocab.demo.investigation.anomaly_pair_sender.name }} → {% endif %}{{ vocab.demo.investigation.anchor.name }}{% for hop in vocab.demo.investigation.layering_chain %} → {{ hop.name }}{% endfor %}.
  Money Trail's chain-root dropdown surfaces the upstream leg;
  picking it renders all hops as a Sankey with a slight residue per
  hop (layering rarely round-trips clean numbers).
{% endif %}

Account Network's anchor dropdown lands on the first account
alphabetically; setting it to
{{ vocab.demo.investigation.anchor.name }} shows the full picture —
the inbound depositors on the left, the outbound destinations on the
right, the anchor meeting in the middle.
{% else %}
This L2 instance has no planted Investigation scenarios, so each
sheet renders against whatever shape the underlying transactions
take. To see the dashboard's intended worked example — a fanout
cluster + anomaly pair + multi-hop layering chain converging on one
anchor — point the docs build at the bundled `sasquatch_pr` fixture
(`QS_DOCS_L2_INSTANCE=tests/l2/sasquatch_pr.yaml mkdocs serve`) and
re-render this page.
{% endif %}

## Reference

- [Account Structure](../scenario/index.md) — the bank, customers,
  accounts, and money flows behind every walkthrough on this page.
- [Schema v6 — Data Feed Contract](../Schema_v6.md) — column specs,
  metadata keys, and ETL examples for the upstream feeds. The
  [Materialized views](../Schema_v6.md#the-layered-model) section
  documents `{{ l2_instance_name }}_inv_pair_rolling_anomalies` (Volume Anomalies)
  and `{{ l2_instance_name }}_inv_money_trail_edges` (Money Trail / Account
  Network) plus the REFRESH cadence contract.
- [Data Integration Handbook](etl.md) — the team that populates the
  data behind every walkthrough on this page. Read it when an anomaly
  z-score, fanout count, or chain-walk result disagrees with what you
  see in the source feed.
- [L1 Reconciliation Dashboard](l1.md) — Treasury's view of the same
  base tables. When a Money Trail edge needs row-level posting
  evidence, the L1 Transactions sheet is the next stop.
