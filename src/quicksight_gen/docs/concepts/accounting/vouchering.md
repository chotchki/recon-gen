# Vouchering

*Background concept — pay-out instructions delivered via an external
payment system.*

{{ diagram("conceptual", name="vouchering") }}

## What it is

**Vouchering** is a payment mechanism where the paying institution
doesn't originate an ACH itself; instead, it **sends a pay-out
instruction** (a "voucher") to an external payment system. That
external system then originates an ACH that pulls (or pushes) the
money from the paying institution's pool account.

The shape, left-to-right:

1. Sale / settlement / fee accrual completes on the paying
   institution's books — the **intent**.
2. Institution generates a voucher — a structured pay-out
   instruction — and hands it to the external payment system.
3. The external system originates an ACH against the institution's
   pool account, sometime later (same day, next day, or further
   out depending on the system's schedule).
4. The institution observes the ACH landing against its pool
   account and reconciles it back to the originating voucher.

## The problem it solves

Some operators — particularly in government contexts — don't
originate outbound ACH directly. Either they're not an ACH
originator at all, or they're required to route large classes of
disbursement through a central government payment rail for
audit / compliance reasons. Vouchering lets the institution
initiate the economic intent (party X is owed $Y) while the
external system owns the actual rail origination.

The reconciliation challenge: **the voucher and the resulting
external ACH are separated by time and by system**. Until the ACH
lands, the voucher is "in flight" — the recipient is owed money
that the institution can see promised, but the pool account
doesn't show a matching debit yet.

When the voucher amount and the external ACH disagree, or when a
voucher fires but no ACH ever arrives, the operator needs to
trace both sides.

## How L1 surfaces this

Vouchering shows up in the L2 model as a **TransferTemplate** with
a `transfer_key` grouping the voucher to the eventual ACH legs.
L1 invariants then catch:

- **Conservation** — the bundled transfer's legs sum to its
  `expected_net`; a missing ACH leg surfaces as an imbalance.
- **Stuck Unbundled** — a voucher leg posted but no matching ACH
  legs joined the bundle past the rail's `max_unbundled_age`.
- **Limit Breach** — voucher-driven outflow on a
  `(parent_role, rail_name)` cell exceeds its cap.

See [L1 Reconciliation Dashboard](../../handbook/l1.md) for the visual
surface.
