# Escrow with reversal

*Background concept — holding accounts that must net to zero, even
when a transfer fails.*

{{ diagram("conceptual", name="escrow-with-reversal") }}

## What it is

An **escrow** (or "suspense") account is a temporary holding spot
for an in-flight transfer. A multi-step transfer moves money *into*
escrow from the originator, then *out* of escrow to the recipient.
If the recipient-side leg fails — a bad account, a compliance hold, an NSF return
— the transfer **reverses** and the money moves back from escrow to
the originator.

A complete transfer cycle has three possible shapes:

- **Success**: originator → escrow → recipient. Escrow nets to zero.
- **Failure + reversal**: originator → escrow → back to originator. Escrow nets to zero.
- **Stuck**: originator → escrow, then nothing. Escrow is non-zero and the money is in limbo.

The healthy state: escrow account balance = zero every EOD.

## The problem it solves

Escrow lets a transfer post atomically from the originator's
perspective ("my money is gone") while the recipient side clears
asynchronously. You NEED that split whenever the recipient leg
involves external validation — a compliance check, an
account-status lookup, or a funds-availability check that can't happen inline.

The failure mode to watch: the recipient leg fails silently, or the
reversal never posts. The originator's money sits "in escrow"
indefinitely, nobody tells them, and the escrow account quietly
accumulates stuck balances.

## How L1 surfaces this

Two L1 invariants catch the two failure shapes:

- **Stuck Pending** (rail-level `max_pending_age` watch) — a transfer
  entered escrow but neither settled nor reversed in time.
- **Stuck Unbundled** (rail-level `max_unbundled_age` watch) — a Posted
  leg sits past its rail's cap without getting picked up into a bundle
  (`bundle_id` still null).

Plus the universal **Drift** + **Expected EOD Balance** checks: any
escrow / suspense account with `expected_eod_balance: 0` declared in
the L2 instance surfaces as a violation when EOD ≠ 0.

See [L1 Reconciliation Dashboard](../../handbook/l1.md) for the visual
surface.
