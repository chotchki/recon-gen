# Vouchering

*Background concept — pay-out instructions delivered via an external
payment system.*

{{ diagram("conceptual", name="vouchering") }}

## What it is

With vouchering the paying institution doesn't originate the ACH
itself — it hands a pay-out instruction (a "voucher") to an
external payment system, and THAT system originates the ACH that
pulls (or pushes) money from the institution's pool account.

The shape, left-to-right:

1. Sale / settlement / fee accrual completes on the institution's
   books — the intent.
2. Institution generates a voucher (a structured pay-out
   instruction) and hands it to the external payment system.
3. The external system originates an ACH against the institution's
   pool account, sometime later — same day, next day or whenever
   the system's schedule fires.
4. The institution sees the ACH land against its pool account and
   reconciles it back to the originating voucher.

## Why it exists

Some operators don't originate outbound ACH directly — in
government contexts especially. Either they're not an ACH
originator at all, or they're required to route large classes of
disbursement through a central government payment rail (an
audit / compliance constraint). Vouchering lets the institution
own the economic intent (party X is owed $Y) while the external
system owns the rail origination.

The reconciliation problem falls out of that split: the voucher
and the resulting external ACH are separated by time AND by
system. Until the ACH lands the voucher is "in flight" — the
institution can see the money promised, but the pool account shows
no matching debit yet. When the voucher amount and the external
ACH disagree, or a voucher fires and no ACH ever arrives, the
operator has to trace both sides.

## How L1 surfaces this

Vouchering lands in the L2 model as a `TransferTemplate` whose
`transfer_key` groups the voucher to its eventual ACH legs. Three
L1 invariants then catch what goes wrong:

- **Conservation** — the bundled transfer's legs sum to its
  `expected_net`; a missing ACH leg shows up as an imbalance.
- **Stuck Unbundled** — a voucher leg posted but no matching ACH
  leg joined the bundle before the rail's `max_unbundled_age`
  elapsed (surfaced as Unbundled Aging on the dashboard).
- **Limit Breach** — voucher-driven outflow on a
  `(parent_role, rail_name)` cell blew past its cap.

See [L1 Reconciliation Dashboard](../../handbook/l1.md) for the visual
surface.
