# Sweep / net / settle

*Background concept — how intraday activity accumulates, then clears
in one external settlement.*

{{ diagram("conceptual", name="sweep-net-settle") }}

## What it is

A **sweep account** holds intraday activity. Each individual posting
lands there; at end of day the accumulated net balance moves to the
bank's authoritative position account in one transfer. The sweep
account is expected to end every day at ZERO.

A typical sweep cycle:

1. Throughout the day: debits / credits accumulate in the sweep
   account as customer activity posts.
2. End of day: the net balance is wired to the position account,
   clearing the sweep to zero.
3. The external counterparty (the Fed, a processor, etc.)
   confirms the settlement on their side.

## The problem it solves

Sweeping rolls many small movements into one external settlement.
Without a sweep, every individual ACH origination would need its own
external wire (slow and expensive, and each one reconciled on its
own). With a sweep the bank emits one wire a day, representing the
day's net activity.

The price of that aggregation is a reconciliation burden — three
things all have to hold:

1. The sweep account has to reach zero EOD (activity actually
   cleared, not just looks like it did).
2. The sweep amount has to match the sum of the day's intraday
   postings (the aggregation is right).
3. The counterparty has to confirm the wire (the external side
   landed).

Any ONE of those failing is a distinct class of exception.

## How L1 surfaces this

Sweep accounts in the L2 instance carry `expected_eod_balance: 0`
on the account declaration, and each of those three failure shapes
lands on its own L1 invariant. **Expected EOD Balance** flags any day
the sweep didn't fully clear to zero. **Drift** catches the second
shape — posted legs that sum to something other than the stored
balance. The missing counterparty confirmation shows up as an
aggregating rail's **PostedRequirements** check.

See [L1 Reconciliation Dashboard](../../handbook/l1.md) for the visual
surface.
