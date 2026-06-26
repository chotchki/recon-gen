# Concepts — Accounting

The banking vocabulary the rest of the handbook assumes you already
know. The L1 invariants and the L2 model both lean on these terms, and
so do the per-app sheets — none of them re-define the words.

Audience is anyone touching the dashboards for the first time —
operators, integrators, ETL engineers, executives. Read these before the
per-app reference material whenever a term ("invariant", "escrow",
"sweep", "vouchering") doesn't land yet.

## Pages

- [Double-entry posting](double-entry.md) — debit / credit pair as the
  L1 invariant root.
- [Escrow with reversal](escrow-with-reversal.md) — three-state
  lifecycle for an in-flight transfer that holds in suspense.
- [Sweep / net / settle](sweep-net-settle.md) — the daily cycle behind
  concentration accounts.
- [Vouchering](vouchering.md) — voucher → settlement materialization.
- [Eventual consistency](eventual-consistency.md) — multi-day clear
  timelines and the aging-watch shape that surfaces them.
- [Open vs. closed loop](open-vs-closed-loop.md) — system-boundary
  distinction that shapes which reconciliation problems are even
  possible.

For the modeling primitives the L2 YAML uses to describe an
institution, see [Concepts → L2 model](../l2/index.md).
