# For the executive

*Audience — leadership at **{{ vocab.institution.name }}**
checking the institution's run-rate weekly or monthly.*

## What you do today

You don't open the dashboard daily — you open it before a board
meeting, a quarterly review, an investor call. The questions are
aggregate: how many customers do we serve, how much money moved
through us last month, what's the growth trajectory, how does this
quarter compare to the last one?

Today those answers come from a slide deck someone else built —
prepared a week ahead, fact-checked twice and sometimes already
stale by the time you present it. Ask a follow-up ("is that growth
uniform across product types?" / "what was the wire vs ACH split?")
and the answer is "let me get back to you" instead of a click on the
same view.

## What this tool does differently

The **Executives Dashboard** is the same underlying ledger — every
posting and balance behind the totals — projected to four
plain-language sheets:

- **Program Health** — one green / amber / red tile answering "is
  anything wrong with the ledger RIGHT NOW?". It counts L1 invariant
  violations open in the dashboard's window (amber on any, red at the
  20-violation systemic mark) so you get the headline without opening
  the operator dashboard. Drill into L1 when you want the per-row why.
- **Account Coverage** — how many accounts you carry and how many
  actually moved money in the period, split by type.
- **Transaction Volume Over Time** — daily counts, sliced by rail.
- **Money Moved** — daily dollar volume by rail, net (flows IN are
  positive) next to gross (total handle, regardless of direction).

The cells are clickable — accent-coloured rows and bars drill into
the operational sheets filtered to that account or rail — but the
default executive read is just totals over the date window you pick,
no exception triage required.

See it live on the [spec_example demo](https://recon-gen-spec.hotchkiss.io/).

The payoff lands the first time someone asks a follow-up in a meeting
and you answer it live off the dashboard, instead of "let me get back
to you".

## What we are NOT asking you to learn

- **Not the daily operational view.** L1 Exceptions, Drift,
  Limit Breach — those are operator surfaces. You don't need to know
  what a "stuck pending" row is to answer the executive questions
  (Program Health gives you the yes/no; if a number genuinely needs
  your eyes the operator team escalates).
- **Not the L2 model.** Rails, chains, transfer templates,
  metadata keys — that's the integrator's surface.
- **Not the underlying SQL or schema.** Every visual on Executives
  is one filter window away from the answer you want.

## How to start

1. Read the
   [Executives Dashboard handbook](../handbook/executives.md). It
   walks the sheets in display order and explains how Executives
   sits alongside the other three dashboards.
2. Open the dashboard against the current month. Glance at
   **Program Health** first — green means the ledger reconciles,
   anything else is worth a word with the operator team.
3. Eyeball **Account Coverage** — does the active account count match
   what product / sales reports would predict?
4. Eyeball **Transaction Volume Over Time** — sudden drops or
   spikes are either real signal (growth, seasonality) or ETL
   anomalies (your data team will tell you which).
5. Eyeball **Money Moved** — the per-rail breakdown tells you which
   channels carried the volume. ACH vs wire vs internal-transfer
   mix is a useful product-strategy signal.

## Cadence

The dashboard is always live, you don't have to ask anyone to
prepare it. Suggested cadences:

- **Weekly** — open Money Moved + Transaction Volume during
  Monday standups, spot any week-over-week shifts.
- **Monthly** — open Account Coverage at month-close, reconcile
  against your customer-success / sales-ops tracker.
- **Quarterly / annual** — date-range filter to the period,
  export as needed for board materials.

## The concepts you'll want grounded

- [Sweep / net / settle](../concepts/accounting/sweep-net-settle.md) — why
  the daily money-moved totals batch into one external wire (the
  number you see is the aggregate, not individual settlements).
- [Eventual consistency](../concepts/accounting/eventual-consistency.md) —
  why money moved on day T might not show on day T's
  counterparty side, and why end-of-month numbers beat mid-month
  snapshots.

## What's NOT in Executives (and where to go for it)

- "What exactly broke and how do I fix it?" — Program Health gives you
  the green/amber/red headline, but the per-row triage is the
  [L1 Reconciliation Dashboard](../handbook/l1.md). The operator team
  owns it.
- "Is anyone laundering money?" — that's the
  [Investigation Dashboard](../handbook/investigation.md). The
  compliance / AML team owns it.
- "Is our L2 model healthy?" — that's the
  [L2 Flow Tracing Dashboard](../handbook/l2_flow_tracing.md).
  The integrator owns it.

## When it's working

After a few weeks on Executives:

- You answer aggregate questions in meetings without scheduling a
  "prep this for me" with the data team.
- Slide-deck prep is one screenshot per visual instead of one
  manual query per visual.
- You catch non-financial signals (flat customer count two months
  running) in time to act, not at the next quarterly review.

The tool has done its job when {{ vocab.institution.acronym }}'s
leadership trusts the live ledger over the assembled report.
