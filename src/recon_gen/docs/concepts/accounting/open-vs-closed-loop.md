# Open vs. closed loop

*Background concept — the system-boundary distinction that shapes
which reconciliation problems are even possible.*

{{ diagram("conceptual", name="open-vs-closed-loop") }}

## What it is

Money moves in one of two regimes, split by whether it ever leaves the
institution's own ledger:

- **Open-loop** — at least one leg touches an external counterparty
  (the Fed, a card processor, a partner bank). The internal-side leg posts right away;
  the counterparty side clears asynchronously and confirms separately.
- **Closed-loop** — both legs sit entirely inside the institution's own
  ledger. Full visibility the moment both legs post, no external
  authority to confirm.

One account lives in BOTH regimes — a customer DDA sends payments out
(open-loop) while accepting internal transfers from other DDAs
(closed-loop). The regime is a property of the transfer, not the account.

## The problem it solves

Splitting open-loop from closed-loop activity lets the operator reason
about **system boundaries** cleanly:

- Open-loop activity eventually touches someone else's books.
  Reconciliation has to match the institution's ledger against theirs,
  and the lag between internal-leg-posted and external-confirm is real
  in-flight risk.
- Closed-loop activity never leaves the system. Reconciliation is easier
  (no external authority to chase) but the two legs can still disagree
  internally — and that's drift.

The interesting failures sit at the boundary — when value crosses from
closed-loop to open-loop, both sides have to move in lockstep or the two
regimes disagree.

## How L1 surfaces this

The L2 instance declares each account's `scope` (`internal` or
`external`), and L1 invariants apply differently across that line:

- **Drift** + **Expected EOD Balance** run on internal accounts — the
  institution owns those books, so they should add up.
- **Limit Breach** gates outbound flow on a rail (the cap rides the
  rail, typically one pointed at an external counterparty).
- **Pending Aging** + **Unbundled Aging** mostly fire on open-loop
  transfers stuck waiting on a counterparty confirmation that never came.

See [L1 Reconciliation Dashboard](../../handbook/l1.md) for the visual
surface and the `scope` attribute on each Account in the L2 YAML
for the boundary declaration.
