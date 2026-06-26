# Double-entry posting

*Background concept — the invariant every L1 reconciliation check rests
on.*

{{ diagram("conceptual", name="double-entry") }}

## What it is

Every money movement is recorded as two equal and opposite postings,
one debit and one credit. Sum them and they net to ZERO. The "transfer"
is the logical money-movement event; a "posting" (or "leg") is one side
of it.

Move $100 from Account A to Account B and the transfer produces two
rows:

- Account A: `signed_amount = -100` (money out)
- Account B: `signed_amount = +100` (money in)

Sum across the whole transfer and you land on zero. That's L1
Conservation. (Strictly the invariant is Σ legs = a declared
expected-net — ZERO for a classic two-leg transfer, NONZERO for a
template-materialized bundle that lands net money somewhere on
purpose.)

## The problem it solves

Money can't be created or destroyed silently. A row with no counterpart
means one of a few things:

- it hasn't posted yet (in-flight)
- it failed — flag it, don't quietly drop it
- someone's books have a bug

Double-entry turns "where did this money come from?" from a free-text
question into a database query.

It also makes eventual consistency OBSERVABLE. A transfer that clears
over three days still has to net to zero across all its legs on day
three — on day one it looks "imbalanced" because only the originator's
leg has posted, and that temporary imbalance is itself diagnostic.

## In the schema

- The `transactions` table stores one row per posting leg.
  `transfer_id` groups the legs of one transfer; `signed_amount`
  carries the sign (`+` money in, `−` money out, from the
  account-holder's perspective).
- Every L1 invariant works off sums of `signed_amount`:
    - L1 Conservation — net-to-zero per transfer
    - L1 Drift — sum-of-postings vs the stored balance
    - L1 Limit Breach — sum-of-outbound vs the limit cap
- The shared base layer is L1-clean by construction. Drift / overdraft
  / limit-breach all rest on the double-entry invariant holding.

See also: [L1 Reconciliation Dashboard](../../handbook/l1.md) for the
visual surface, and the [Schema v6 contract](../../Schema_v6.md) for the
column definitions.
