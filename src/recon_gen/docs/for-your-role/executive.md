# For the executive

*Audience — leadership at **{{ vocab.institution.name }}**
checking the institution's run-rate weekly or monthly.*

## What you do today

You don't open the dashboard daily — you open it before a board
meeting, a quarterly review, an investor call. The questions you
ask are aggregate: how many customers do we serve, how much money
moved through us last month, what's our growth trajectory, how
does this quarter compare to the prior one?

Today, those answers come from a slide deck someone else built —
prepared a week ahead, fact-checked twice, and sometimes already
out-of-date by the time you're presenting. When you ask a
follow-up ("is that growth uniform across product types?" /
"what was the wire vs. ACH split?"), the answer is "let me get
back to you" rather than a click on the same view.

## What this tool does differently

The **Executives Dashboard** is the same underlying ledger — every
posting, every account, every settlement — projected up to four
plain-language sheets:

- **Account Coverage** — how many active accounts, broken down by
  type.
- **Transaction Volume Over Time** — daily counts, with optional
  splits by transfer type and origin.
- **Money Moved** — daily aggregate $ moved, by transfer type.

No drill into individual transactions; no exception triage; just
totals over the date window you pick.

**The first time you ask a follow-up question in a meeting and
answer it from the dashboard in real-time — that's the proof
this tool puts the ledger in your hands, not in the slide deck's
hands.**

## What we are *not* asking you to learn

- **Not the daily operational view.** L1 Exceptions, Drift,
  Limit Breach — those are operator surfaces. You don't need to
  know what a "stuck pending" row is to answer the executive
  questions. (If something genuinely needs your attention, the
  operator team escalates.)
- **Not the L2 model.** Rails, chains, transfer templates,
  metadata keys — that's the integrator's surface.
- **Not the underlying SQL or schema.** Every visual on Executives
  is one filter window away from the answer you want.

## How to start

1. Read the
   [Executives Dashboard handbook](../handbook/executives.md). It
   walks the 4 sheets in display order and explains how
   Executives sits alongside the other 3 dashboards.
2. Open the dashboard against the current month. Eyeball
   **Account Coverage** — does the active account count match
   what you'd expect from product / sales reports?
3. Eyeball **Transaction Volume Over Time** — sudden drops or
   spikes are either real signal (growth, seasonality) or ETL
   anomalies (your data team will tell you which).
4. Eyeball **Money Moved** — the per-transfer-type breakdown
   tells you which channels carried the volume. ACH vs. wire vs.
   internal-transfer mix is a useful product-strategy signal.

## Cadence

The dashboard is always live; you don't have to ask anyone to
prepare it. Suggested cadences:

- **Weekly** — open Money Moved + Transaction Volume during
  Monday standups; spot any week-over-week shifts.
- **Monthly** — open Account Coverage at month-close; reconcile
  against your customer-success / sales-ops tracker.
- **Quarterly / annual** — date-range filter to the period;
  export as needed for board materials.

The QuickSight visuals all support per-user saved views — bookmark
the date ranges you use most so you're one click from each
cadence.

## The concepts you'll want grounded

- [Sweep / net / settle](../concepts/accounting/sweep-net-settle.md) — why
  the daily money-moved totals batch into one external wire (the
  number you see is the aggregate, not individual settlements).
- [Eventual consistency](../concepts/accounting/eventual-consistency.md) —
  why money moved on day T might not show on day T's
  counterparty side, and why end-of-month numbers are more
  reliable than mid-month snapshots.

## What's NOT in Executives (and where to go for it)

- "Is anything broken?" — that's the
  [L1 Reconciliation Dashboard](../handbook/l1.md). The operator
  team owns it; ask them, don't try to read L1 yourself.
- "Is anyone laundering money?" — that's the
  [Investigation Dashboard](../handbook/investigation.md). The
  compliance / AML team owns it.
- "Is our L2 model healthy?" — that's the
  [L2 Flow Tracing Dashboard](../handbook/l2_flow_tracing.md).
  The integrator owns it.

## What "good" looks like

After a few weeks of using Executives:

- You're answering aggregate questions in meetings without
  scheduling a "prep this for me" with the data team.
- Slide-deck prep takes one screenshot per visual instead of one
  manual query per visual.
- You spot non-financial signals (e.g., flat customer count two
  months in a row) in time to act, not at the next quarterly
  review.

That's the acceptance bar. The tool works when {{ vocab.institution.acronym }}'s
leadership trusts the live ledger more than the assembled
report.
