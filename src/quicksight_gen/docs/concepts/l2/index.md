# Concepts — L2 model

The modeling vocabulary an integrator uses to declare an institution's
shape. The L2 YAML is the single source of truth for every shipped
dashboard — feeding a different YAML changes the dashboards' contents
without touching code.

Each page below explains one primitive in isolation, then shows a
concrete example pulled from the active L2 instance (or
``spec_example`` as a fallback if the active L2 has no entity of that
type yet).

## Primitives

- [Account](account.md) — the singleton, 1-of-1 GL or external
  counterparty.
- [Account template](account-template.md) — the SHAPE of an
  N-of-many account class (per-customer DDA, per-merchant
  settlement) materialized at posting time.
- [Rail](rail.md) — a single money-movement primitive (TwoLeg
  posts debit + credit, SingleLeg posts one leg reconciled by a
  template or aggregating rail).
- [Transfer template](transfer-template.md) — multi-rail bundle
  with an ``expected_net`` close-out (e.g. "ACH origination cycle").
- [Chain](chain.md) — parent → child firing rule. A row with one
  child = required; two or more children = XOR alternation
  ("exactly one of these MUST fire").
- [Limit schedule](limit-schedule.md) — daily outbound-flow cap
  per (parent_role, transfer_type).

For the broader institution-tour view that walks every entity at
once, see [Training Story](../../scenario/index.md). For the data-
side feed contract those entities project into, see
[Schema v6](../../Schema_v6.md).
