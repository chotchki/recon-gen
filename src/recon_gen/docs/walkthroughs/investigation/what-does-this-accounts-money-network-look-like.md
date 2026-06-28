# What does this account's money network look like?

*Question-shaped walkthrough — Investigation dashboard, Account Network sheet.*

## The story

Compliance has an account on a watchlist — it landed there as the
recipient of a fanout hit, the terminal of a layering chain or a
referral from another bank's investigations team. Before opening a
case the analyst needs the account's full counterparty graph: every
party that's sent it money (and how much), every party it's sent
money to (and how much), laid out so the geometry makes the
relationships obvious. The investigator wants the whole network around
one anchor — both sides — and the ability to walk to a counterparty
when the picture demands it.

## The question

"Show me everything touching this account, on either side."

## Where to look

Open the **Investigation** dashboard, **Account Network** sheet.

See it live: https://recon-gen-spec.hotchkiss.io/

The sheet has two controls in the top-right panel:

- **Anchor account** — dropdown of every account that appears as a
  source or target in the matview. Format is `name (id)` so accounts
  with the same display name disambiguate by ID. The dropdown is
  backed by a small dedicated dataset (`inv-anetwork-accounts-ds`)
  that pre-deduplicates the display strings, so it opens fast even
  on a large matview.
- **Min hop amount** — drops noise edges below the threshold. Default
  is $0; raise to filter out small bookkeeping legs.

Three visuals:

- **Inbound — counterparties → anchor** (top-left, half-width) — the
  inbound Sankey. Counterparties on the left, anchor on the right.
  Ribbon thickness encodes the SUM of money each counterparty sent
  the anchor.
- **Outbound — anchor → counterparties** (top-right, half-width) — the
  outbound Sankey. Anchor on the left, counterparties on the right.
  Ribbon thickness encodes the SUM of money the anchor sent each
  counterparty.
- **Account Network — Touching Edges** (full-width, below) — table of
  every edge touching the anchor (either side), ordered by amount
  descending. Each row carries source + target + amount + the
  computed counterparty (the side of the edge that ISN'T the
  anchor).

The two Sankeys are intentionally SIDE-BY-SIDE with the anchor
meeting in the middle. Direction is encoded by LAYOUT — inbound on
the left, outbound on the right. The geometry is the contract.

## Walking the anchor

Account Network is a WALKABLE sheet. Three ways to move the
anchor:

- **Anchor dropdown.** Pick a new account; the page re-renders.
- **Right-click any row in the touching-edges table** → "Walk to
  other account on this edge". The anchor moves to the
  counterparty (the non-anchor side of the clicked edge); both
  Sankeys and the table re-render around the new anchor.
- **Left-click any node in either Sankey.** The Sankey-level walks
  exist because each directional Sankey only has one possible walk
  target (the counterparty side), so the menu disambiguation a
  right-click would provide is gone. Left-click matches the "click
  the thing to drill" mental model.

{% if vocab.demo.has_investigation_plants and vocab.demo.investigation.layering_chain and vocab.demo.investigation.anomaly_pair_sender %}
??? example "Worked example: {{ vocab.fixture_name }}"
    The bundled `{{ vocab.fixture_name }}` fixture's planted scenarios
    converge on **{{ vocab.demo.investigation.anchor.name }}**
    (`{{ vocab.demo.investigation.anchor.id }}`). Set the anchor to
    {{ vocab.demo.investigation.anchor.name }}:

    - **Inbound Sankey (left)** —
      {{ vocab.demo.investigation.fanout_sender_count }} individual
      depositors (the fanout cluster) sending small ACH amounts, plus
      {{ vocab.demo.investigation.anomaly_pair_sender.name }} sending
      the routine wire baseline + the spike.
      {{ vocab.demo.investigation.anomaly_pair_sender.name }}'s ribbon
      is the thickest because of the $25,000 spike; the depositor
      ribbons are thin and visually similar.
    - **Outbound Sankey (right)** —
      {{ vocab.demo.investigation.layering_chain[0].name }} (the
      layering chain's first hop). One ribbon, full width — the
      chain's first outbound hop is
      {{ vocab.demo.investigation.anchor.name }}'s only outbound
      activity in the seed.
    - **Touching-edges table (below)** — every edge involving
      {{ vocab.demo.investigation.anchor.name }}, ordered by amount
      descending. The
      {{ vocab.demo.investigation.anomaly_pair_sender.name }} spike
      sits at the top; the
      {{ vocab.demo.investigation.layering_chain[0].name }} outbound
      is high; the depositor inbounds occupy the long tail.

    Right-click the
    {{ vocab.demo.investigation.anomaly_pair_sender.name }} inbound
    row → "Walk to other account on this edge". The anchor walks to
    {{ vocab.demo.investigation.anomaly_pair_sender.name }}; the
    inbound Sankey now shows whatever upstream sources that account
    has (in the seed: nothing — it's an external counterparty so its
    inbound side isn't modeled), and the outbound Sankey shows
    {{ vocab.demo.investigation.anomaly_pair_sender.name }} →
    {{ vocab.demo.investigation.anchor.name }}. Walk back via the
    dropdown; right-click the
    {{ vocab.demo.investigation.layering_chain[0].name }} row → walk
    to {{ vocab.demo.investigation.layering_chain[0].name }}. Now
    {{ vocab.demo.investigation.layering_chain[0].name }} is the
    anchor; the inbound Sankey shows
    {{ vocab.demo.investigation.anchor.name }} →
    {{ vocab.demo.investigation.layering_chain[0].name }}; the
    outbound shows
    {{ vocab.demo.investigation.layering_chain[0].name }} →
    {% if vocab.demo.investigation.layering_chain | length > 1 %}{{ vocab.demo.investigation.layering_chain[1].name }}{% else %}the next hop{% endif %}
    (the next layering hop).

    That's the walk-the-flow interaction — every walk re-anchors the
    entire view, so multi-hop investigations don't require leaving
    the sheet.
{% endif %}

## What it means

Account Network is a graph VIEW, not a verdict. A dense inbound
Sankey + sparse outbound Sankey is consistent with collection (or
with a normal merchant DDA on a slow week). A sparse inbound + dense
outbound is consistent with disbursement (or with a payroll account
on payday). The shape gives you the question; the context (account
type, business line, prior history) gives you the answer.

A clean network finding includes: the anchor account, the trailing
window, the inbound counterparty list with amounts, the outbound
counterparty list with amounts and a one-line characterization of
the shape (collection / disbursement / pass-through / mixed).

## Drilling in

Once you have the network sketched, the next step depends on what
you want to know:

- **"Walk the chain back from the biggest inbound."** → Money Trail
  sheet. Find the biggest inbound transfer's chain root and pick it
  from the Money Trail dropdown.
- **"Was that biggest inbound also a pair-spike?"** → Volume
  Anomalies sheet. Check whether the inbound counterparty appears
  in the flagged-pair-windows table for the same window.
- **"Is the anchor on the Recipient Fanout list?"** → Recipient
  Fanout sheet. Set the threshold low; if the anchor surfaces, the
  inbound side has a structuring shape worth documenting.
- **"Show me the actual posting rows for one of these edges."** →
  L1 Reconciliation Dashboard, Transactions sheet, filtered to the
  edge's `transfer_id`.

## Next step

The fastest path from "watchlist alert" to "case opened or closed"
usually goes:

1. Set the anchor to the watchlisted account.
2. Read the geometry — is this collection, disbursement, pass-
   through or mixed?
3. Pick the largest inbound and the largest outbound from the table;
   for each, decide whether they're benign (known relationship,
   normal cadence) or warrant a drill.
4. For each drill, walk to the counterparty (right-click the table
   row), repeat the geometry read, and decide whether to keep
   walking or pop back.
5. When the picture is complete, switch to Money Trail (for chain
   provenance) or the L1 Transactions sheet (for posting evidence)
   to attach row-level evidence to the case file.

The walk-the-flow drill is the difference between Account Network
and a static counterparty report: you're not generating one report
per anchor, you're traversing a graph one click at a time. A
seasoned analyst will walk a 4–6 hop network in under a minute.

## Related walkthroughs

- [Who's getting money from too many senders?](who-is-getting-money-from-too-many-senders.md) —
  the right entry point when you don't have an anchor yet. Recipient
  Fanout flags a candidate; come here next to characterize the
  network.
- [Which sender → recipient pair just spiked?](which-pair-just-spiked.md) —
  the right entry point when you started from a pair-window alert.
  Anchor on either side here to see the rest of that pair's network.
- [Where did this transfer actually originate?](where-did-this-transfer-originate.md) —
  the right next step when an Account Network walk lands on an edge
  whose chain provenance you want end-to-end.
