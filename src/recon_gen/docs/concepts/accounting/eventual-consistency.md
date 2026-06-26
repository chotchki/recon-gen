# Eventual consistency

*Background concept — money that lands over days, not seconds.*

{{ diagram("conceptual", name="eventual-consistency") }}

## What it is

Most money movement in a retail banking system doesn't settle the
instant it posts. A sale posts today, the settlement to the
merchant's bank fires tomorrow, the card network clears two days
later, the external bank confirms receipt on day four. At any
moment large amounts of money are IN FLIGHT — legitimately posted
on one side of a transfer, not yet on the other.

A system is eventually consistent when, given enough time and no
new activity, every in-flight balance clears and the books agree
across parties.

## The problem it solves

Non-instant settlement is a feature, not a bug. Batch processing
beats real-time on cost for most retail volumes, and the external
settlement calendars (ACH windows, Fed cutoffs) are fixed facts
every institution lives with.

In an eventually consistent system the operator's job splits in
two:

1. **In-flight vs stuck.** A transfer one day out isn't broken; one
   that's been "pending" for two weeks probably is. The threshold —
   whether a leg even lands on the sheet — comes from the rail's
   declared `max_pending_age` / `max_unbundled_age`.
2. **Aging.** Once something IS stuck, how long has it been stuck?
   Aging drives escalation — a three-day-old exception is routine
   follow-up, a thirty-day-old one is a structural problem.

## How L1 surfaces this

The **Pending Aging** and **Unbundled Aging** sheets bucket each
violation by age since posting. The two band sets DIFFER —
Pending Aging is hour-grained at the low end (`0-6h`, `6-24h`,
`1-3d`, `3-7d`, then a `>7d` overflow), Unbundled Aging stays
day-grained (`<1d`, `1-2d`, `2-7d`, `>7d`) — because the rails
feeding each clear on different cadences. The bands turn the
eventual-consistency story into something an operator acts on:

- **First band or two** (hours out, a day or two for unbundled) is
  usually still in-flight; no action unless it's a transfer type
  that should already have cleared.
- **Middle bands** (a few days out) are starting to stick —
  escalate.
- **The `>7d` overflow** is structural; stop working rows and ask
  why the automation hasn't cleared them.

See [L1 Reconciliation Dashboard](../../handbook/l1.md) for the
operator workflow, and the per-rail `max_pending_age` /
`max_unbundled_age` declarations in the L2 instance YAML.
